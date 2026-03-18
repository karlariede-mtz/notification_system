# 🥗 Food Tracker Notification Service

A lightweight, extensible Python notification service for food tracking applications. Consists of two standalone modules:

- **`notification_service.py`** — generates and delivers notifications (meal reminders, hydration alerts, calorie summaries, nutrient warnings, streaks)
- **`notification_orchestrator.py`** — defines *which* notifications are pushed *when*, using event-based and time-based rule classes

---

## Features

- 🍽️ **Meal Reminders** — alerts for scheduled meals not yet logged
- 💧 **Hydration Alerts** — time-aware urgency escalation based on daily water intake
- 📊 **Calorie Summaries** — end-of-day breakdown vs. goal
- ⚠️ **Nutrient Warnings** — flags low protein, fiber, iron, and calcium intake
- 🔥 **Streak Tracking** — milestone badges at 1, 7, 30, and 100 days
- 🔌 **Custom Handlers** — plug in push, email, or SMS delivery with one line
- 🎯 **Rule-based Orchestration** — code-driven event and time rules control all dispatch logic
- 🌙 **Quiet Hours** — global suppression window per user (midnight-wrapping supported)

---

## Requirements

- Python 3.10+
- No third-party dependencies — uses only the standard library

---

## Installation

```bash
git clone https://github.com/your-org/food-tracker-notifications.git
cd food-tracker-notifications
```

No virtual environment or `pip install` needed.

---

## Quick Start

### Notification Service (standalone)

```python
from notification_service import NotificationService, UserProfile, DailyLog

svc = NotificationService()

user = UserProfile(user_id="user_001", name="Alice", daily_cal_goal=1800)
log  = DailyLog(user_id="user_001", calories=1350, water_ml=400)

svc.run_all_checks(user, log)
svc.send_calorie_summary(user, log)
```

### Orchestrator (rule-driven dispatch)

```python
from notification_orchestrator import build_default_orchestrator, Event, EventType

orch = build_default_orchestrator()

# Event-based trigger
orch.dispatch_event(Event(EventType.MEAL_LOGGED, user, log, payload={"meal": "lunch"}))

# Time-based trigger — call this from a scheduler or cron
orch.dispatch_time(user, log)
```

Run either module directly to see the built-in demo:

```bash
python notification_service.py
python notification_orchestrator.py
```

---

## Project Structure

```
food-tracker-notifications/
├── notification_service.py      # Delivery layer — generates & dispatches notifications
├── notification_orchestrator.py # Orchestration layer — rule engine & dispatch logic
└── README.md
```

---

## Architecture

```
App / Scheduler
      │
      ▼
NotificationOrchestrator
  ├── dispatch_event(event)  ──▶  EventRules (filter by listens_to)
  └── dispatch_time(...)     ──▶  TimeRules  (filter by should_fire)
                                      │
                                      ▼
                               Notification objects
                                      │
                               Global Guards
                               (quiet hours, master toggle)
                                      │
                                      ▼
                               Handlers (console / push / email / SMS)
```

---

## Core Classes

### `UserProfile`

| Field | Type | Default | Description |
|---|---|---|---|
| `user_id` | `str` | — | Unique user identifier |
| `name` | `str` | — | Display name |
| `daily_cal_goal` | `int` | `2000` | Daily calorie target (kcal) |
| `water_goal_ml` | `int` | `2500` | Daily hydration target (ml) |
| `meal_times` | `list[str]` | `["08:00","13:00","19:00"]` | Scheduled meal times (HH:MM) |
| `streak_days` | `int` | `0` | Current logging streak |
| `quiet_start` | `time` | `22:00` | Start of quiet hours |
| `quiet_end` | `time` | `07:00` | End of quiet hours |
| `notifications_on` | `bool` | `True` | Master toggle |

### `DailyLog`

| Field | Type | Description |
|---|---|---|
| `user_id` | `str` | Owner of the log |
| `calories` | `int` | Total calories consumed |
| `water_ml` | `int` | Total water consumed (ml) |
| `meals_logged` | `list[dict]` | Logged meals with type, calories, time |
| `nutrients` | `dict[str, int]` | Nutrient totals e.g. `{"protein": 50, "fiber": 25}` |

### `Notification`

| Field | Type | Description |
|---|---|---|
| `type` | `NotificationType` | Category enum |
| `title` | `str` | Short heading |
| `message` | `str` | Full notification body |
| `priority` | `Priority` | `LOW`, `MEDIUM`, or `HIGH` |
| `timestamp` | `datetime` | Auto-set on creation |
| `metadata` | `dict` | Arbitrary extra data |

---

## Orchestrator API

### Dispatching

```python
orch.dispatch_event(event)                  # Run matching EventRules
orch.dispatch_time(user, log)               # Run all TimeRules (uses datetime.now())
orch.dispatch_time(user, log, now=dt)       # Pass explicit time (useful for testing)
```

### Registration

```python
orch.register(MyEventRule())    # Register an EventRule or TimeRule (chainable)
orch.add_handler(my_handler)    # Add a delivery handler (chainable)
```

### History

```python
orch.get_history()              # All notifications ever dispatched
orch.get_history(user_id)       # Filtered by user
```

---

## Built-in Rules

### Event Rules

| Rule | Listens To | Behaviour |
|---|---|---|
| `DayStartedRule` | `DAY_STARTED` | Good-morning message with streak status |
| `MealLoggedRule` | `MEAL_LOGGED` | All-meals badge; over-goal calorie warning |
| `NutrientCheckOnMealRule` | `MEAL_LOGGED` | Time-adjusted check for protein, fiber, iron, calcium |
| `WaterLoggedRule` | `WATER_LOGGED` | Congratulates user when daily water goal is crossed |

### Time Rules

| Rule | Fires When | Behaviour |
|---|---|---|
| `MealReminderTimeRule` | ±5 min of each scheduled meal | Reminds if meal not yet logged |
| `HydrationReminderTimeRule` | Odd hours 09:00–21:00 | Reminds only if user is behind on water |
| `DailySummaryTimeRule` | 20:00 daily | Full calorie + water recap; streak increment |

---

## Writing Custom Rules

### Custom EventRule

```python
class WeightLoggedRule(EventRule):
    listens_to = [EventType.WEIGHT_LOGGED]

    def build(self, event: Event) -> list[Notification]:
        weight = event.payload.get("weight_kg")
        return [Notification(
            type    = NotificationType.GOAL_ACHIEVED,
            title   = "⚖️ Weight Logged",
            message = f"Logged {weight}kg. Consistency is key, {event.user.name}!",
            user_id = event.user.user_id,
        )]
```

### Custom TimeRule

```python
class WeeklyReportRule(TimeRule):
    def should_fire(self, now, user, log) -> bool:
        return now.weekday() == 6 and now.hour == 9  # Sunday 9am

    def build(self, now, user, log) -> list[Notification]:
        return [Notification(
            type    = NotificationType.CALORIE_SUMMARY,
            title   = "📅 Weekly Report",
            message = f"Here's your week in review, {user.name}!",
            user_id = user.user_id,
        )]
```

### Registering rules

```python
orch = build_default_orchestrator()
orch.register(WeightLoggedRule())
orch.register(WeeklyReportRule())
```

---

## Custom Delivery Handlers

```python
def push_handler(n: Notification) -> None:
    push_api.send(token=n.user_id, title=n.title, body=n.message)

def db_handler(n: Notification) -> None:
    db.insert("notifications", {
        "user_id":   n.user_id,
        "type":      n.type.value,
        "title":     n.title,
        "message":   n.message,
        "priority":  n.priority.value,
        "timestamp": n.timestamp.isoformat(),
    })

orch.add_handler(push_handler).add_handler(db_handler)
```

---

## Notification Types & Priorities

| `NotificationType` | Trigger |
|---|---|
| `MEAL_REMINDER` | Scheduled meal time reached, meal not logged |
| `HYDRATION_ALERT` | Water intake too low relative to time of day |
| `CALORIE_SUMMARY` | End-of-day recap or manual call |
| `NUTRIENT_WARNING` | A tracked nutrient is below its daily minimum |
| `GOAL_ACHIEVED` | Water goal hit, all meals logged |
| `STREAK_UPDATE` | Daily goals met; streak milestone reached |

| `Priority` | Icon | When used |
|---|---|---|
| `LOW` | 🔵 | Positive updates, met goals |
| `MEDIUM` | 🟡 | Reminders, mild warnings |
| `HIGH` | 🔴 | Critical alerts, exceeded limits |

---

## License

MIT
