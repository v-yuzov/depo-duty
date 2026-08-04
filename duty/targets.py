from __future__ import annotations

import math

from duty.models import DutySlot, Employee, EmployeeStats


def count_available_slots(employee: Employee, slots: list[DutySlot]) -> int:
    return sum(1 for slot in slots if employee.is_available_on(slot.start_date))


def compute_base_load(slot_count: int, employee_count: int) -> float:
    if employee_count <= 0:
        raise ValueError("Список сотрудников пуст")
    if slot_count <= 0:
        raise ValueError("Нет слотов дежурства в периоде")
    return (slot_count * 2) / employee_count


def compute_raw_target(base_load: float, available_slots: int, total_slots: int) -> float:
    if total_slots <= 0:
        return 0.0
    return base_load * (available_slots / total_slots)


def compute_min_rest_slots(participant_count: int) -> int:
    """Минимальное число свободных слотов между дежурствами одного человека.

    Каждый слот занимает 2 участников → полный круг ≈ n/2.
    Отдых = круг − 1 (не считая сам день дежурства).

    Примеры:
    - 10 человек → max(1, 5 − 1) = 4
    - 6 человек → max(1, 3 − 1) = 2
    - 4 человека → max(1, 2 − 1) = 1
    """
    if participant_count <= 2:
        return 1
    return max(1, participant_count // 2 - 1)


def compute_personal_min_rest(available_slots: int, target: int, global_min: int) -> int:
    """Персональный минимум отдыха: не жёстче, чем позволяет окно доступности."""
    if target <= 1 or available_slots <= 1:
        return min(global_min, max(1, available_slots - 1)) if available_slots > 1 else 1
    # Чтобы target участий уместились равномерно: шаг ≈ available/target
    ideal = max(1, available_slots // target - 1)
    return max(1, min(global_min, ideal))


def compute_stats(
    employees: list[Employee],
    slots: list[DutySlot],
    *,
    ceil_all: bool = True,
) -> tuple[float, list[EmployeeStats]]:
    total_slots = len(slots)
    base_load = compute_base_load(total_slots, len(employees))
    stats: list[EmployeeStats] = []
    for employee in employees:
        available = count_available_slots(employee, slots)
        raw = compute_raw_target(base_load, available, total_slots)
        rounded = math.ceil(raw) if ceil_all else round(raw)
        if available > 0 and rounded < 1 and raw > 0:
            rounded = 1
        stats.append(
            EmployeeStats(
                employee=employee,
                target=raw,
                target_rounded=int(rounded),
                available_slots=available,
            )
        )
    return base_load, stats


def is_vacationer_in_period(employee: Employee, slots: list[DutySlot]) -> bool:
    if employee.vacation is None:
        return False
    period_start = slots[0].start_date
    period_end = slots[-1].start_date
    return not (employee.vacation.end < period_start or employee.vacation.start > period_end)
