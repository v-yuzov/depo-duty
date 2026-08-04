from __future__ import annotations

from datetime import date, timedelta

from duty.models import DutySlot


def iter_weekdays(period_start: date, period_end: date):
    if period_end < period_start:
        raise ValueError("Дата окончания периода раньше даты начала")
    current = period_start
    while current <= period_end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def build_slots(period_start: date, period_end: date) -> list[DutySlot]:
    return [DutySlot(start_date=day) for day in iter_weekdays(period_start, period_end)]
