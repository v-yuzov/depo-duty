from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum


class Subteam(str, Enum):
    T = "Т"
    D = "Д"


WEEKDAY_NAMES_RU = {
    0: "пн",
    1: "вт",
    2: "ср",
    3: "чт",
    4: "пт",
    5: "сб",
    6: "вс",
}


@dataclass(frozen=True)
class VacationPeriod:
    start: date
    end: date

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


@dataclass(frozen=True)
class Employee:
    name: str
    subteam: Subteam
    vacation: VacationPeriod | None = None
    priority: bool = False

    def is_available_on(self, day: date) -> bool:
        if self.vacation is None:
            return True
        return not self.vacation.contains(day)

    @property
    def has_vacation_in_period(self) -> bool:
        return self.vacation is not None

    @property
    def subteam_label(self) -> str:
        """Подкоманда для вывода: Т / Т+ / Д / Д+."""
        return f"{self.subteam.value}+" if self.priority else self.subteam.value


@dataclass(frozen=True)
class DutySlot:
    start_date: date

    @property
    def end_date(self) -> date:
        if self.start_date.weekday() == 4:  # Friday
            return self.start_date + timedelta(days=3)
        return self.start_date + timedelta(days=1)

    @property
    def start_time(self) -> str:
        return "09:10"

    @property
    def end_time(self) -> str:
        return "09:10"


@dataclass
class DutyAssignment:
    slot: DutySlot
    primary: Employee | None = None
    backup: Employee | None = None
    needs_manual_fix: bool = False

    @property
    def is_complete(self) -> bool:
        return self.primary is not None and self.backup is not None

    def participants(self) -> list[Employee]:
        result: list[Employee] = []
        if self.primary is not None:
            result.append(self.primary)
        if self.backup is not None:
            result.append(self.backup)
        return result


@dataclass
class EmployeeStats:
    employee: Employee
    primary_count: int = 0
    backup_count: int = 0
    target: float = 0.0
    target_rounded: int = 0
    available_slots: int = 0

    @property
    def total(self) -> int:
        return self.primary_count + self.backup_count


@dataclass
class ScheduleResult:
    period_start: date
    period_end: date
    assignments: list[DutyAssignment]
    stats: list[EmployeeStats]
    base_load: float
    total_slots: int
    min_rest_slots: int = 1
    warnings: list[str] = field(default_factory=list)
