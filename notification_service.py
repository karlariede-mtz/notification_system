"""
Food Tracker Notification Service
Handles meal reminders, hydration alerts, calorie summaries, and goal achievements.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Callable


# ── Enums ────────────────────────────────────────────────────────────────────

class NotificationType(Enum):
    MEAL_REMINDER       = "meal_reminder"
    HYDRATION_ALERT     = "hydration_alert"
    CALORIE_SUMMARY     = "calorie_summary"
    GOAL_ACHIEVED       = "goal_achieved"
    NUTRIENT_WARNING    = "nutrient_warning"
    STREAK_UPDATE       = "streak_update"


class NotificationPriority(Enum):
    LOW    = 1
    MEDIUM = 2
    HIGH   = 3


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Notification:
    type:      NotificationType
    title:     str
    message:   str
    priority:  NotificationPriority = NotificationPriority.MEDIUM
    timestamp: datetime             = field(default_factory=datetime.now)
    user_id:   str                  = "default_user"
    read:      bool                 = False
    metadata:  dict                 = field(default_factory=dict)

    def mark_read(self) -> None:
        self.read = True

    def __str__(self) -> str:
        icon = {
            NotificationPriority.LOW:    "🔵",
            NotificationPriority.MEDIUM: "🟡",
            NotificationPriority.HIGH:   "🔴",
        }[self.priority]
        ts = self.timestamp.strftime("%H:%M")
        status = "✓" if self.read else "●"
        return f"[{ts}] {icon} {status} {self.title}: {self.message}"


@dataclass
class UserProfile:
    user_id:          str
    name:             str
    daily_cal_goal:   int   = 2000
    water_goal_ml:    int   = 2500
    meal_times:       list  = field(default_factory=lambda: ["08:00", "13:00", "19:00"])
    streak_days:      int   = 0
    notifications_on: bool  = True


@dataclass
class DailyLog:
    user_id:       str
    date:          datetime            = field(default_factory=datetime.now)
    calories:      int                 = 0
    water_ml:      int                 = 0
    meals_logged:  list                = field(default_factory=list)
    nutrients:     dict                = field(default_factory=dict)


# ── Notification Service ──────────────────────────────────────────────────────

class NotificationService:
    """Central service for generating and dispatching food-tracker notifications."""

    def __init__(self):
        self._notifications: list[Notification] = []
        self._handlers:      list[Callable]     = [self._default_handler]

    # Public API ──────────────────────────────────────────────────────────────

    def add_handler(self, handler: Callable[[Notification], None]) -> None:
        """Register an additional delivery handler (e.g. push, email, SMS)."""
        self._handlers.append(handler)

    def get_unread(self, user_id: str) -> list[Notification]:
        return [n for n in self._notifications if n.user_id == user_id and not n.read]

    def get_all(self, user_id: str) -> list[Notification]:
        return [n for n in self._notifications if n.user_id == user_id]

    def mark_all_read(self, user_id: str) -> None:
        for n in self._notifications:
            if n.user_id == user_id:
                n.mark_read()

    # Notification generators ─────────────────────────────────────────────────

    def check_meal_reminders(self, user: UserProfile, log: DailyLog) -> None:
        """Fire a reminder for any scheduled meal that hasn't been logged yet."""
        now = datetime.now().strftime("%H:%M")
        logged_names = {m.get("type", "").lower() for m in log.meals_logged}

        meal_labels = {
            "08:00": ("breakfast", "🌅"),
            "13:00": ("lunch",     "☀️"),
            "19:00": ("dinner",    "🌙"),
        }

        for scheduled_time in user.meal_times:
            label, icon = meal_labels.get(scheduled_time, ("meal", "🍽️"))
            if now >= scheduled_time and label not in logged_names:
                self._send(Notification(
                    type     = NotificationType.MEAL_REMINDER,
                    title    = f"{icon} {label.title()} Reminder",
                    message  = f"Time to log your {label}, {user.name}!",
                    priority = NotificationPriority.MEDIUM,
                    user_id  = user.user_id,
                    metadata = {"scheduled_time": scheduled_time, "meal": label},
                ))

    def check_hydration(self, user: UserProfile, log: DailyLog) -> None:
        """Alert the user if they're behind on their daily water goal."""
        pct        = log.water_ml / user.water_goal_ml
        remaining  = user.water_goal_ml - log.water_ml
        hour       = datetime.now().hour

        if pct < 0.25 and hour >= 12:
            priority, urgency = NotificationPriority.HIGH,   "critically low"
        elif pct < 0.5 and hour >= 15:
            priority, urgency = NotificationPriority.HIGH,   "quite low"
        elif pct < 0.75 and hour >= 18:
            priority, urgency = NotificationPriority.MEDIUM, "a bit low"
        else:
            return

        self._send(Notification(
            type     = NotificationType.HYDRATION_ALERT,
            title    = "💧 Hydration Alert",
            message  = (f"Your water intake is {urgency}. "
                        f"Drink {remaining}ml more to hit your goal."),
            priority = priority,
            user_id  = user.user_id,
            metadata = {"water_ml": log.water_ml, "goal_ml": user.water_goal_ml},
        ))

    def send_calorie_summary(self, user: UserProfile, log: DailyLog) -> None:
        """Send an end-of-day calorie summary."""
        remaining = user.daily_cal_goal - log.calories
        pct       = (log.calories / user.daily_cal_goal) * 100

        if log.calories > user.daily_cal_goal:
            msg = (f"You consumed {log.calories} kcal today — "
                   f"{abs(remaining)} kcal over your goal.")
            priority = NotificationPriority.HIGH
        elif pct >= 90:
            msg      = f"Great day! You hit {pct:.0f}% of your calorie goal."
            priority = NotificationPriority.LOW
        else:
            msg = (f"You had {log.calories} kcal today. "
                   f"{remaining} kcal remaining to reach your goal.")
            priority = NotificationPriority.MEDIUM

        self._send(Notification(
            type     = NotificationType.CALORIE_SUMMARY,
            title    = "📊 Daily Calorie Summary",
            message  = msg,
            priority = priority,
            user_id  = user.user_id,
            metadata = {"calories": log.calories, "goal": user.daily_cal_goal},
        ))

    def check_nutrient_warnings(self, user: UserProfile, log: DailyLog) -> None:
        """Warn about critically low essential nutrients."""
        thresholds = {"protein": 50, "fiber": 25, "iron": 8}

        for nutrient, min_val in thresholds.items():
            val = log.nutrients.get(nutrient, 0)
            if val < min_val:
                self._send(Notification(
                    type     = NotificationType.NUTRIENT_WARNING,
                    title    = f"⚠️ Low {nutrient.title()}",
                    message  = (f"You've only had {val}g of {nutrient} today. "
                                f"Aim for at least {min_val}g."),
                    priority = NotificationPriority.MEDIUM,
                    user_id  = user.user_id,
                    metadata = {"nutrient": nutrient, "value": val, "min": min_val},
                ))

    def check_streak(self, user: UserProfile, log: DailyLog) -> None:
        """Celebrate streak milestones."""
        all_logged   = len(log.meals_logged) >= 3
        water_ok     = log.water_ml >= user.water_goal_ml * 0.8
        calories_ok  = log.calories <= user.daily_cal_goal * 1.1

        if not (all_logged and water_ok and calories_ok):
            return

        user.streak_days += 1
        milestones = {1: "🌱", 7: "🔥", 30: "🏆", 100: "💎"}

        if user.streak_days in milestones:
            icon = milestones[user.streak_days]
            self._send(Notification(
                type     = NotificationType.STREAK_UPDATE,
                title    = f"{icon} {user.streak_days}-Day Streak!",
                message  = (f"Amazing, {user.name}! You've hit a "
                            f"{user.streak_days}-day tracking streak. Keep it up!"),
                priority = NotificationPriority.HIGH,
                user_id  = user.user_id,
                metadata = {"streak_days": user.streak_days},
            ))
        elif user.streak_days % 5 == 0:
            self._send(Notification(
                type     = NotificationType.STREAK_UPDATE,
                title    = "🔥 Streak Milestone",
                message  = (f"{user.streak_days} days in a row, {user.name}! "
                            f"You're building a great habit."),
                priority = NotificationPriority.MEDIUM,
                user_id  = user.user_id,
                metadata = {"streak_days": user.streak_days},
            ))

    def run_all_checks(self, user: UserProfile, log: DailyLog) -> None:
        """Convenience method — runs every check in one call."""
        if not user.notifications_on:
            return
        self.check_meal_reminders(user, log)
        self.check_hydration(user, log)
        self.check_nutrient_warnings(user, log)
        self.check_streak(user, log)

    # Internal helpers ────────────────────────────────────────────────────────

    def _send(self, notification: Notification) -> None:
        self._notifications.append(notification)
        for handler in self._handlers:
            handler(notification)

    @staticmethod
    def _default_handler(n: Notification) -> None:
        print(n)


# ── Demo ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    svc = NotificationService()

    alice = UserProfile(
        user_id        = "user_001",
        name           = "Alice",
        daily_cal_goal = 1800,
        water_goal_ml  = 2000,
        streak_days    = 6,   # one away from 7-day milestone
    )

    log = DailyLog(
        user_id      = "user_001",
        calories     = 1350,
        water_ml     = 400,   # critically low
        meals_logged = [
            {"type": "breakfast", "calories": 450, "time": "08:15"},
            {"type": "lunch",     "calories": 600, "time": "13:05"},
            {"type": "snack",     "calories": 300, "time": "16:00"},
        ],
        nutrients = {"protein": 35, "fiber": 12, "iron": 5},  # all low
    )

    print("=" * 55)
    print("  🥗 Food Tracker — Running Notification Checks")
    print("=" * 55)

    svc.run_all_checks(alice, log)
    svc.send_calorie_summary(alice, log)

    print("\n" + "=" * 55)
    print(f"  📬 Unread notifications: {len(svc.get_unread(alice.user_id))}")
    print("=" * 55)
