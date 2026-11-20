"""Spaced repetition (an SM-2 variant, as popularized by Anki) and study streaks. Pure functions, no LLM calls."""

from datetime import date, timedelta

AGAIN, HARD, GOOD, EASY = range(4)
MIN_EASE = 1.3


def schedule(rating: int, ease: float, interval_days: int, reps: int) -> dict:
    """Next state of a card after a rating. interval_days 0 means 'show again today'."""
    if rating == AGAIN:
        return {"ease": max(MIN_EASE, ease - 0.2), "interval_days": 0, "reps": 0}
    if rating == HARD:
        return {"ease": max(MIN_EASE, ease - 0.15), "interval_days": max(1, round(interval_days * 1.2)), "reps": reps + 1}
    if reps == 0:
        interval = 1 if rating == GOOD else 4
    elif reps == 1:
        interval = 3 if rating == GOOD else 6
    else:
        interval = round(interval_days * ease * (1.3 if rating == EASY else 1))
    return {
        "ease": ease + (0.15 if rating == EASY else 0),
        "interval_days": max(interval, interval_days + 1),  # a correct recall never shortens the gap
        "reps": reps + 1,
    }


def preview(ease: float, interval_days: int, reps: int) -> list[int]:
    """Interval each rating button would give, shown on the buttons ('Good · 3d')."""
    return [schedule(r, ease, interval_days, reps)["interval_days"] for r in range(4)]


def streak(study_days: set[str], today: date) -> dict:
    """Consecutive study days ending today; a streak survives until the end of the day after the last study day."""
    days = {date.fromisoformat(d) for d in study_days}
    studied_today = today in days
    day = today if studied_today else today - timedelta(days=1)
    current = 0
    while day in days:
        current += 1
        day -= timedelta(days=1)
    return {"current": current, "studied_today": studied_today}
