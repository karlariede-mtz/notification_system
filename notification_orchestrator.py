"""
Notification Orchestration Service
===================================
Standalone module that defines *which* notifications are pushed *when*,
using two complementary trigger models:

  • Event-based  — rules fire in response to app events (meal logged, water added, …)
  • Time-based   — rules fire on a cron-like schedule (daily summary at 20:00, …)

Usage
-----
    orchestrator = NotificationOrchestrator()
    orchestrator.register(MealLoggedRule())
    orchestrator.register(DailySummaryRule())

    # Event trigger
    orchestrator.dispatch_event(Event(EventType.MEAL_LOGGED, user, log, payload={...}))

    # Time trigger (call from a scheduler / APScheduler / cron)
    orchestrator.dispatch_time(user, log)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum, auto
from typing import Any

log = logging.getLogger(__name__)


# ── Notification primitives (self-contained, no dep on NotificationService) ──

class NotificationType(Enum):
    MEAL_REMINDER    = "meal_reminder"
    HYDRATION_ALERT  = "hydration_alert"
    CALORIE_SUMMARY  = "calorie_summary"
    NUTRIENT_WARNING = "nutrient_warning"
    STREAK_UPDATE    = "streak_update"
    GOAL_ACHIEVED    = "goal_achieved"


class Priority(Enum):
    LOW    = 1
    MEDIUM = 2
    HIGH   = 3


@dataclass
class Notification:
    type:      NotificationType
    title:     str
    message:   str
    priority:  Priority         = Priority.MEDIUM
    user_id:   str              = ""
    timestamp: datetime         = field(default_factory=datetime.now)
    metadata:  dict             = field(default_factory=dict)

    def __str__(self) -> str:
        icons = {Priority.LOW: "🔵", Priority.MEDIUM: "🟡", Priority.HIGH: "🔴"}
        return (f"[{self.timestamp:%H:%M}] {icons[self.priority]} "
                f"[{self.type.value}] {self.title}: {self.message}")


# ── Domain models ─────────────────────────────────────────────────────────────

@dataclass
class UserProfile:
    user_id:          str
    name:             str
    daily_cal_goal:   int        = 2000
    water_goal_ml:    int        = 2500
    meal_times:       list[str]  = field(default_factory=lambda: ["08:00", "13:00", "19:00"])
    streak_days:      int        = 0
    quiet_start:      time       = time(22, 0)
    quiet_end:        time       = time(7, 0)
    notifications_on: bool       = True


@dataclass
class DailyLog:
    user_id:      str
    date:         datetime       = field(default_factory=datetime.now)
    calories:     int            = 0
    water_ml:     int            = 0
    meals_logged: list[dict]     = field(default_factory=list)
    nutrients:    dict[str, int] = field(default_factory=dict)


# ── Events ────────────────────────────────────────────────────────────────────

class EventType(Enum):
    MEAL_LOGGED    = auto()
    WATER_LOGGED   = auto()
    DAY_STARTED    = auto()
    DAY_COMPLETED  = auto()
    WEIGHT_LOGGED  = auto()
    GOAL_UPDATED   = auto()


@dataclass
class Event:
    type:    EventType
    user:    UserProfile
    log:     DailyLog
    payload: dict[str, Any] = field(default_factory=dict)
    ts:      datetime        = field(default_factory=datetime.now)


# ── Rule base classes ─────────────────────────────────────────────────────────

class EventRule(ABC):
    """Base for event-driven rules. Override `triggers_on` and `build`."""

    #: Subclasses declare which EventTypes they handle
    listens_to: list[EventType] = []

    def evaluate(self, event: Event) -> list[Notification]:
        if event.type not in self.listens_to:
            return []
        return self.build(event) or []

    @abstractmethod
    def build(self, event: Event) -> list[Notification]:
        """Return notifications to dispatch, or an empty list to suppress."""


class TimeRule(ABC):
    """
    Base for time-driven rules.
    `should_fire` receives the current time so you can gate by hour/weekday/etc.
    """

    def evaluate(self, now: datetime, user: UserProfile,
                 log: DailyLog) -> list[Notification]:
        if not self.should_fire(now, user, log):
            return []
        return self.build(now, user, log) or []

    @abstractmethod
    def should_fire(self, now: datetime, user: UserProfile, log: DailyLog) -> bool:
        """Return True if this rule should produce notifications right now."""

    @abstractmethod
    def build(self, now: datetime, user: UserProfile,
              log: DailyLog) -> list[Notification]:
        """Return notifications to dispatch."""


# ── Built-in event rules ──────────────────────────────────────────────────────

class MealLoggedRule(EventRule):
    """
    Fires when a meal is logged.
    - Celebrates a full day of logging once all 3 main meals are in.
    - Warns if calories are already over the daily goal mid-day.
    """
    listens_to = [EventType.MEAL_LOGGED]

    def build(self, event: Event) -> list[Notification]:
        user, log = event.user, event.log
        notes: list[Notification] = []
        main_meals = {"breakfast", "lunch", "dinner"}
        logged_types = {m.get("type", "").lower() for m in log.meals_logged}

        # All three main meals logged → celebrate
        if main_meals <= logged_types:
            notes.append(Notification(
                type     = NotificationType.GOAL_ACHIEVED,
                title    = "✅ All Meals Logged!",
                message  = f"Great discipline, {user.name}! All main meals tracked today.",
                priority = Priority.LOW,
                user_id  = user.user_id,
                metadata = {"trigger": "all_meals_logged"},
            ))

        # Over calorie goal
        if log.calories > user.daily_cal_goal:
            over = log.calories - user.daily_cal_goal
            notes.append(Notification(
                type     = NotificationType.CALORIE_SUMMARY,
                title    = "⚠️ Calorie Goal Exceeded",
                message  = (f"You're {over} kcal over your daily goal of "
                            f"{user.daily_cal_goal} kcal."),
                priority = Priority.HIGH,
                user_id  = user.user_id,
                metadata = {"calories": log.calories, "over_by": over},
            ))

        return notes


class WaterLoggedRule(EventRule):
    """
    Fires when water is logged.
    - Sends a congratulatory nudge when the user hits their daily water goal.
    """
    listens_to = [EventType.WATER_LOGGED]

    def build(self, event: Event) -> list[Notification]:
        user, log = event.user, event.log
        prev_ml = event.payload.get("prev_water_ml", 0)

        # Goal crossed in this log entry
        if prev_ml < user.water_goal_ml <= log.water_ml:
            return [Notification(
                type     = NotificationType.GOAL_ACHIEVED,
                title    = "💧 Hydration Goal Reached!",
                message  = (f"You've hit your {user.water_goal_ml}ml water goal "
                            f"for today, {user.name}. Well done!"),
                priority = Priority.LOW,
                user_id  = user.user_id,
                metadata = {"water_ml": log.water_ml},
            )]
        return []


class DayStartedRule(EventRule):
    """
    Fires when the user opens the app at the start of the day.
    - Sends a personalised good-morning with yesterday's streak status.
    """
    listens_to = [EventType.DAY_STARTED]

    def build(self, event: Event) -> list[Notification]:
        user = event.user
        streak_msg = (f"You're on a {user.streak_days}-day streak 🔥 — keep it going!"
                      if user.streak_days > 0
                      else "Start your streak by logging all meals today!")
        return [Notification(
            type     = NotificationType.MEAL_REMINDER,
            title    = f"🌅 Good morning, {user.name}!",
            message  = f"Ready to track today? {streak_msg}",
            priority = Priority.LOW,
            user_id  = user.user_id,
        )]


class NutrientCheckOnMealRule(EventRule):
    """
    Fires when a meal is logged.
    - Checks cumulative nutrients and warns about any that are critically low
      relative to expected intake at this point in the day.
    """
    listens_to = [EventType.MEAL_LOGGED]

    # nutrient → (daily_min, unit)
    THRESHOLDS: dict[str, tuple[int, str]] = {
        "protein": (50,   "g"),
        "fiber":   (25,   "g"),
        "iron":    (8,    "mg"),
        "calcium": (1000, "mg"),
    }

    def build(self, event: Event) -> list[Notification]:
        log  = event.log
        hour = event.ts.hour
        # Scale expectation: how much of the day has passed (floor at 25%)
        day_fraction = max(hour / 24, 0.25)
        notes = []

        for nutrient, (daily_min, unit) in self.THRESHOLDS.items():
            val      = log.nutrients.get(nutrient, 0)
            expected = daily_min * day_fraction

            if val < expected * 0.5:       # critically behind
                priority = Priority.HIGH
                adverb   = "critically low"
            elif val < expected * 0.75:    # mildly behind
                priority = Priority.MEDIUM
                adverb   = "a bit low"
            else:
                continue

            notes.append(Notification(
                type     = NotificationType.NUTRIENT_WARNING,
                title    = f"⚠️ {nutrient.title()} {adverb.title()}",
                message  = (f"You've had {val}{unit} of {nutrient} — "
                            f"aim for {daily_min}{unit} by end of day."),
                priority = priority,
                user_id  = event.user.user_id,
                metadata = {"nutrient": nutrient, "value": val, "min": daily_min},
            ))
        return notes


# ── Built-in time rules ───────────────────────────────────────────────────────

class MealReminderTimeRule(TimeRule):
    """
    Fires at each scheduled meal time (±5-minute window).
    Suppressed if the meal was already logged.
    """
    MEAL_LABELS = {
        "08:00": ("breakfast", "🌅"),
        "13:00": ("lunch",     "☀️"),
        "19:00": ("dinner",    "🌙"),
    }

    def should_fire(self, now: datetime, user: UserProfile, log: DailyLog) -> bool:
        return any(self._in_window(now, t) for t in user.meal_times)

    def build(self, now: datetime, user: UserProfile,
              log: DailyLog) -> list[Notification]:
        logged = {m.get("type", "").lower() for m in log.meals_logged}
        notes  = []

        for t in user.meal_times:
            if not self._in_window(now, t):
                continue
            label, icon = self.MEAL_LABELS.get(t, ("meal", "🍽️"))
            if label in logged:
                continue
            notes.append(Notification(
                type     = NotificationType.MEAL_REMINDER,
                title    = f"{icon} {label.title()} Time",
                message  = f"Don't forget to log your {label}, {user.name}!",
                priority = Priority.MEDIUM,
                user_id  = user.user_id,
                metadata = {"meal": label, "scheduled": t},
            ))
        return notes

    @staticmethod
    def _in_window(now: datetime, meal_time_str: str, window_mins: int = 5) -> bool:
        h, m   = map(int, meal_time_str.split(":"))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        delta  = abs((now - target).total_seconds())
        return delta <= window_mins * 60


class HydrationReminderTimeRule(TimeRule):
    """
    Fires every 2 hours between 09:00–21:00 if the user is behind on water.
    Urgency escalates as the day progresses.
    """

    def should_fire(self, now: datetime, user: UserProfile, log: DailyLog) -> bool:
        in_hours  = 9 <= now.hour < 21
        on_2h     = now.minute < 5          # trigger in the first 5 min of each 2h slot
        on_slot   = now.hour % 2 == 1       # odd hours: 9, 11, 13, 15, 17, 19
        behind    = log.water_ml < user.water_goal_ml * (now.hour / 21)
        return in_hours and on_2h and on_slot and behind

    def build(self, now: datetime, user: UserProfile,
              log: DailyLog) -> list[Notification]:
        remaining = user.water_goal_ml - log.water_ml
        pct       = log.water_ml / user.water_goal_ml

        priority = Priority.HIGH if pct < 0.3 else Priority.MEDIUM
        urgency  = "way behind on" if pct < 0.3 else "behind on"

        return [Notification(
            type     = NotificationType.HYDRATION_ALERT,
            title    = "💧 Hydration Reminder",
            message  = (f"You're {urgency} your water goal. "
                        f"Drink {remaining}ml more today."),
            priority = priority,
            user_id  = user.user_id,
            metadata = {"water_ml": log.water_ml, "remaining_ml": remaining},
        )]


class DailySummaryTimeRule(TimeRule):
    """
    Fires once at 20:00 with a full-day summary and streak update.
    """
    FIRE_HOUR = 20

    def should_fire(self, now: datetime, user: UserProfile, log: DailyLog) -> bool:
        return now.hour == self.FIRE_HOUR and now.minute < 5

    def build(self, now: datetime, user: UserProfile,
              log: DailyLog) -> list[Notification]:
        remaining  = user.daily_cal_goal - log.calories
        water_pct  = int(log.water_ml / user.water_goal_ml * 100)
        notes      = []

        if log.calories > user.daily_cal_goal:
            cal_line = f"Calories: {log.calories} kcal ({abs(remaining)} over goal ⚠️)"
            priority = Priority.HIGH
        else:
            cal_line = f"Calories: {log.calories}/{user.daily_cal_goal} kcal"
            priority = Priority.MEDIUM

        notes.append(Notification(
            type     = NotificationType.CALORIE_SUMMARY,
            title    = "📊 Daily Summary",
            message  = f"{cal_line} | Water: {water_pct}% of goal",
            priority = priority,
            user_id  = user.user_id,
            metadata = {
                "calories": log.calories, "water_pct": water_pct,
                "meals_count": len(log.meals_logged),
            },
        ))

        # Streak check — full day: all meals + 80% water + within cal goal
        all_meals  = len(log.meals_logged) >= 3
        water_ok   = log.water_ml >= user.water_goal_ml * 0.8
        cal_ok     = log.calories <= user.daily_cal_goal * 1.1

        if all_meals and water_ok and cal_ok:
            user.streak_days += 1
            milestones = {1: "🌱", 7: "🔥", 30: "🏆", 100: "💎"}
            if user.streak_days in milestones:
                icon = milestones[user.streak_days]
                notes.append(Notification(
                    type     = NotificationType.STREAK_UPDATE,
                    title    = f"{icon} {user.streak_days}-Day Streak!",
                    message  = (f"Incredible, {user.name}! "
                                f"{user.streak_days} days of consistent tracking."),
                    priority = Priority.HIGH,
                    user_id  = user.user_id,
                    metadata = {"streak_days": user.streak_days},
                ))

        return notes


# ── Orchestrator ──────────────────────────────────────────────────────────────

class NotificationOrchestrator:
    """
    Central engine that:
      1. Holds a registry of EventRules and TimeRules.
      2. Routes events / time ticks to matching rules.
      3. Applies global guards (quiet hours, master toggle).
      4. Dispatches resulting Notifications to registered handlers.
    """

    def __init__(self):
        self._event_rules: list[EventRule]                      = []
        self._time_rules:  list[TimeRule]                       = []
        self._handlers:    list[callable[[Notification], None]] = [self._console_handler]
        self._history:     list[Notification]                   = []

    # Registration ─────────────────────────────────────────────────────────────

    def register(self, rule: EventRule | TimeRule) -> "NotificationOrchestrator":
        """Register a rule. Returns self for chaining."""
        if isinstance(rule, EventRule):
            self._event_rules.append(rule)
        elif isinstance(rule, TimeRule):
            self._time_rules.append(rule)
        else:
            raise TypeError(f"Expected EventRule or TimeRule, got {type(rule)}")
        return self

    def add_handler(self, handler: callable) -> "NotificationOrchestrator":
        self._handlers.append(handler)
        return self

    # Dispatch ─────────────────────────────────────────────────────────────────

    def dispatch_event(self, event: Event) -> list[Notification]:
        """Run all EventRules that listen to event.type."""
        if not self._global_guard(event.user, event.ts):
            return []
        notifications = []
        for rule in self._event_rules:
            for n in rule.evaluate(event):
                n.user_id = event.user.user_id
                self._deliver(n)
                notifications.append(n)
        return notifications

    def dispatch_time(self, user: UserProfile, log: DailyLog,
                      now: datetime | None = None) -> list[Notification]:
        """Run all TimeRules against the current (or supplied) time."""
        now = now or datetime.now()
        if not self._global_guard(user, now):
            return []
        notifications = []
        for rule in self._time_rules:
            for n in rule.evaluate(now, user, log):
                n.user_id = user.user_id
                self._deliver(n)
                notifications.append(n)
        return notifications

    # History ──────────────────────────────────────────────────────────────────

    def get_history(self, user_id: str | None = None) -> list[Notification]:
        if user_id:
            return [n for n in self._history if n.user_id == user_id]
        return list(self._history)

    # Guards ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _global_guard(user: UserProfile, now: datetime) -> bool:
        """Return False (suppress all) during quiet hours or if notifications off."""
        if not user.notifications_on:
            return False
        t = now.time()
        qs, qe = user.quiet_start, user.quiet_end
        # Quiet window may wrap midnight
        in_quiet = (t >= qs or t < qe) if qs > qe else (qs <= t < qe)
        if in_quiet:
            log.debug("Suppressed: quiet hours (%s – %s)", qs, qe)
            return False
        return True

    # Internal ─────────────────────────────────────────────────────────────────

    def _deliver(self, notification: Notification) -> None:
        self._history.append(notification)
        for handler in self._handlers:
            try:
                handler(notification)
            except Exception as exc:            # noqa: BLE001
                log.error("Handler %s raised: %s", handler.__name__, exc)

    @staticmethod
    def _console_handler(n: Notification) -> None:
        icons = {Priority.LOW: "🔵", Priority.MEDIUM: "🟡", Priority.HIGH: "🔴"}
        print(f"  {icons[n.priority]} [{n.type.value:<20}] {n.title}: {n.message}")


# ── Factory helper ────────────────────────────────────────────────────────────

def build_default_orchestrator() -> NotificationOrchestrator:
    """Returns an orchestrator pre-loaded with all built-in rules."""
    return (
        NotificationOrchestrator()
        .register(DayStartedRule())
        .register(MealLoggedRule())
        .register(NutrientCheckOnMealRule())
        .register(WaterLoggedRule())
        .register(MealReminderTimeRule())
        .register(HydrationReminderTimeRule())
        .register(DailySummaryTimeRule())
    )


# ── Demo ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    orch = build_default_orchestrator()

    alice = UserProfile(
        user_id        = "user_001",
        name           = "Alice",
        daily_cal_goal = 1800,
        water_goal_ml  = 2000,
        streak_days    = 6,
    )
    log_today = DailyLog(
        user_id   = "user_001",
        calories  = 0,
        water_ml  = 0,
        nutrients = {"protein": 0, "fiber": 0, "iron": 0},
    )

    separator = lambda title: print(f"\n{'─'*55}\n  {title}\n{'─'*55}")

    # ── 1. App opened in the morning
    separator("EVENT: Day Started")
    orch.dispatch_event(Event(EventType.DAY_STARTED, alice, log_today))

    # ── 2. Breakfast logged
    separator("EVENT: Breakfast Logged")
    log_today.meals_logged.append({"type": "breakfast", "calories": 420, "time": "08:10"})
    log_today.calories  += 420
    log_today.nutrients  = {"protein": 15, "fiber": 4, "iron": 2}
    orch.dispatch_event(Event(
        EventType.MEAL_LOGGED, alice, log_today,
        payload={"meal": "breakfast", "calories": 420},
    ))

    # ── 3. Lunch + water logged
    separator("EVENT: Lunch + Water Logged")
    log_today.meals_logged.append({"type": "lunch", "calories": 650, "time": "13:00"})
    log_today.calories   += 650
    log_today.nutrients   = {"protein": 38, "fiber": 11, "iron": 5}
    prev_water            = log_today.water_ml
    log_today.water_ml   += 1800
    orch.dispatch_event(Event(EventType.MEAL_LOGGED, alice, log_today,
                              payload={"meal": "lunch"}))
    orch.dispatch_event(Event(EventType.WATER_LOGGED, alice, log_today,
                              payload={"prev_water_ml": prev_water}))

    # ── 4. Dinner logged (all meals done, over calorie goal)
    separator("EVENT: Dinner Logged (over goal)")
    log_today.meals_logged.append({"type": "dinner", "calories": 900, "time": "19:30"})
    log_today.calories += 900
    orch.dispatch_event(Event(EventType.MEAL_LOGGED, alice, log_today,
                              payload={"meal": "dinner"}))

    # ── 5. Simulate 20:00 daily summary tick
    separator("TIME RULE: 20:00 Daily Summary")
    summary_time = datetime.now().replace(hour=20, minute=2)
    orch.dispatch_time(alice, log_today, now=summary_time)

    # ── 6. History
    separator(f"HISTORY  ({len(orch.get_history(alice.user_id))} notifications)")
    for n in orch.get_history(alice.user_id):
        print(f"  [{n.timestamp:%H:%M}] {n.title}")
