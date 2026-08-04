from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from duty.models import (
    DutyAssignment,
    DutySlot,
    Employee,
    EmployeeStats,
    ScheduleResult,
    Subteam,
)
from duty.slots import build_slots
from duty.targets import (
    compute_min_rest_slots,
    compute_personal_min_rest,
    compute_stats,
    is_vacationer_in_period,
)


DEFAULT_BACKTRACK_LIMIT = 48


@dataclass
class _Counters:
    primary: dict[str, int]
    backup: dict[str, int]
    target: dict[str, int]
    last_index: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_stats(cls, stats: list[EmployeeStats]) -> "_Counters":
        return cls(
            primary={s.employee.name: 0 for s in stats},
            backup={s.employee.name: 0 for s in stats},
            target={s.employee.name: s.target_rounded for s in stats},
            last_index={},
        )

    def total(self, name: str) -> int:
        return self.primary[name] + self.backup[name]

    def deficit(self, name: str) -> int:
        return self.target[name] - self.total(name)

    def primary_target(self, name: str) -> int:
        return (self.target[name] + 1) // 2

    def backup_target(self, name: str) -> int:
        return self.target[name] // 2

    def add(self, employee: Employee, role: str, slot_index: int) -> None:
        if role == "primary":
            self.primary[employee.name] += 1
        else:
            self.backup[employee.name] += 1
        self.last_index[employee.name] = slot_index

    def slots_since_last(self, name: str, slot_index: int) -> int | None:
        if name not in self.last_index:
            return None
        return slot_index - self.last_index[name] - 1


def even_indices(count: int, picks: int) -> list[int]:
    if picks <= 0 or count <= 0:
        return []
    if picks >= count:
        return list(range(count))
    if picks == 1:
        return [count // 2]
    return [round(i * (count - 1) / (picks - 1)) for i in range(picks)]


def planned_slot_dates(
    employee: Employee,
    slots: list[DutySlot],
    target: int,
) -> set[date]:
    available = [slot for slot in slots if employee.is_available_on(slot.start_date)]
    indices = even_indices(len(available), target)
    return {available[i].start_date for i in indices}


def other_vacationers_on_leave(
    slot_day: date,
    candidate: Employee,
    vacationers: list[Employee],
) -> int:
    return sum(
        1
        for other in vacationers
        if other.name != candidate.name and not other.is_available_on(slot_day)
    )


class DutyScheduler:
    def __init__(
        self,
        employees: list[Employee],
        period_start: date,
        period_end: date,
        backtrack_limit: int = DEFAULT_BACKTRACK_LIMIT,
    ) -> None:
        self.employees = employees
        self.period_start = period_start
        self.period_end = period_end
        self.backtrack_limit = backtrack_limit
        self.slots = build_slots(period_start, period_end)
        self.base_load, self.stats = compute_stats(employees, self.slots, ceil_all=True)
        self.stats_by_name = {s.employee.name: s for s in self.stats}
        self.vacationers = [
            employee for employee in employees if is_vacationer_in_period(employee, self.slots)
        ]
        self.vacationer_names = {employee.name for employee in self.vacationers}
        self.global_min_rest = compute_min_rest_slots(len(employees))
        self.personal_min_rest = {
            s.employee.name: compute_personal_min_rest(
                s.available_slots,
                s.target_rounded,
                self.global_min_rest,
            )
            for s in self.stats
        }
        self.planned: dict[str, set[date]] = {
            employee.name: planned_slot_dates(
                employee,
                self.slots,
                self.stats_by_name[employee.name].target_rounded,
            )
            for employee in self.vacationers
        }

    def schedule(self) -> ScheduleResult:
        assignments = [DutyAssignment(slot=slot) for slot in self.slots]
        counters = _Counters.from_stats(self.stats)
        warnings: list[str] = []

        self._phase_vacationers(assignments, counters, warnings)
        self._phase_fill_all(assignments, counters, warnings)
        self._sync_stats(counters)

        return ScheduleResult(
            period_start=self.period_start,
            period_end=self.period_end,
            assignments=assignments,
            stats=self.stats,
            base_load=self.base_load,
            total_slots=len(self.slots),
            min_rest_slots=self.global_min_rest,
            warnings=warnings,
        )

    def _sync_stats(self, counters: _Counters) -> None:
        for item in self.stats:
            item.primary_count = counters.primary[item.employee.name]
            item.backup_count = counters.backup[item.employee.name]

    def _previous_participants(
        self,
        assignments: list[DutyAssignment],
        index: int,
    ) -> set[str]:
        if index <= 0:
            return set()
        return {person.name for person in assignments[index - 1].participants()}

    def _phase_vacationers(
        self,
        assignments: list[DutyAssignment],
        counters: _Counters,
        warnings: list[str],
    ) -> None:
        self._vacationer_pass(
            assignments,
            counters,
            warnings,
            require_planned_or_cover=True,
        )
        self._vacationer_pass(
            assignments,
            counters,
            warnings,
            require_planned_or_cover=False,
        )

    def _vacationer_pass(
        self,
        assignments: list[DutyAssignment],
        counters: _Counters,
        warnings: list[str],
        *,
        require_planned_or_cover: bool,
    ) -> None:
        for index, assignment in enumerate(assignments):
            if assignment.is_complete:
                continue
            slot = assignment.slot
            previous = self._previous_participants(assignments, index)
            needy = [
                employee
                for employee in self.vacationers
                if counters.deficit(employee.name) > 0
                and employee.is_available_on(slot.start_date)
                and employee.name not in previous
            ]
            if not needy:
                continue
            if require_planned_or_cover and not self._is_attractive_vacationer_slot(slot, needy):
                continue
            assigned = self._assign_slot(
                assignment=assignment,
                slot_index=index,
                pool=self.vacationers,
                counters=counters,
                previous_names=previous,
                vacationer_mode=True,
            )
            if (
                not assigned
                and require_planned_or_cover
                and self._can_form_vacationer_pair(slot, previous)
            ):
                warnings.append(
                    f"{slot.start_date}: не удалось собрать пару из отпускников"
                )

    def _is_attractive_vacationer_slot(
        self,
        slot: DutySlot,
        needy: list[Employee],
    ) -> bool:
        for employee in needy:
            if slot.start_date in self.planned.get(employee.name, set()):
                return True
            if other_vacationers_on_leave(slot.start_date, employee, self.vacationers) > 0:
                return True
        return False

    def _can_form_vacationer_pair(self, slot: DutySlot, previous_names: set[str]) -> bool:
        available = [
            employee
            for employee in self.vacationers
            if employee.is_available_on(slot.start_date) and employee.name not in previous_names
        ]
        teams = {employee.subteam for employee in available}
        return len(available) >= 2 and Subteam.T in teams and Subteam.D in teams

    def _phase_fill_all(
        self,
        assignments: list[DutyAssignment],
        counters: _Counters,
        warnings: list[str],
    ) -> None:
        for index, assignment in enumerate(assignments):
            if assignment.is_complete:
                continue
            previous = self._previous_participants(assignments, index)
            assigned = self._assign_slot(
                assignment=assignment,
                slot_index=index,
                pool=self.employees,
                counters=counters,
                previous_names=previous,
                vacationer_mode=False,
            )
            if not assigned:
                assignment.needs_manual_fix = True
                warnings.append(
                    f"{assignment.slot.start_date}: слот требует ручной правки"
                )

    def _rest_ok(
        self,
        name: str,
        slot_index: int,
        counters: _Counters,
        *,
        strict: bool,
    ) -> bool:
        since = counters.slots_since_last(name, slot_index)
        if since is None:
            return True
        if since < 1:
            return False
        needed = self.personal_min_rest[name]
        if strict:
            return since >= needed
        return True

    def _assign_slot(
        self,
        assignment: DutyAssignment,
        slot_index: int,
        pool: list[Employee],
        counters: _Counters,
        previous_names: set[str],
        vacationer_mode: bool,
    ) -> bool:
        # Сначала пробуем с соблюдением мин. отдыха, затем с ослаблением
        for strict_rest in (True, False):
            pair = self._find_best_pair(
                slot=assignment.slot,
                slot_index=slot_index,
                pool=pool,
                counters=counters,
                previous_names=previous_names,
                vacationer_mode=vacationer_mode,
                strict_rest=strict_rest,
            )
            if pair is not None:
                primary, backup = pair
                assignment.primary = primary
                assignment.backup = backup
                counters.add(primary, "primary", slot_index)
                counters.add(backup, "backup", slot_index)
                return True
        return False

    def _find_best_pair(
        self,
        slot: DutySlot,
        slot_index: int,
        pool: list[Employee],
        counters: _Counters,
        previous_names: set[str],
        vacationer_mode: bool,
        *,
        strict_rest: bool,
    ) -> tuple[Employee, Employee] | None:
        primary_candidates = self._rank_candidates(
            pool=pool,
            slot=slot,
            slot_index=slot_index,
            counters=counters,
            previous_names=previous_names,
            role="primary",
            other=None,
            vacationer_mode=vacationer_mode,
            strict_rest=strict_rest,
        )
        if not primary_candidates:
            return None

        attempts = 0
        best_pair: tuple[Employee, Employee] | None = None
        best_score = float("-inf")

        for primary in primary_candidates:
            if attempts >= self.backtrack_limit:
                break
            backup_candidates = self._rank_candidates(
                pool=pool,
                slot=slot,
                slot_index=slot_index,
                counters=counters,
                previous_names=previous_names | {primary.name},
                role="backup",
                other=primary,
                vacationer_mode=vacationer_mode,
                strict_rest=strict_rest,
            )
            if not backup_candidates:
                attempts += 1
                continue
            for backup in backup_candidates:
                if attempts >= self.backtrack_limit:
                    break
                attempts += 1
                if (
                    vacationer_mode
                    and counters.deficit(primary.name) <= 0
                    and counters.deficit(backup.name) <= 0
                ):
                    continue
                # В фазе отпускников не раздуваем сверх цели партнёра без нужды
                if vacationer_mode and counters.deficit(backup.name) < 0 and counters.deficit(primary.name) <= 0:
                    continue
                score = self._pair_score(
                    primary,
                    backup,
                    slot,
                    slot_index,
                    counters,
                    vacationer_mode,
                )
                if score > best_score:
                    best_score = score
                    best_pair = (primary, backup)
            if best_pair is not None and vacationer_mode and best_score >= 50:
                break

        if best_pair is None:
            return None
        primary, backup = best_pair
        if vacationer_mode and counters.deficit(primary.name) <= 0 and counters.deficit(backup.name) <= 0:
            return None
        return best_pair

    def _pair_score(
        self,
        primary: Employee,
        backup: Employee,
        slot: DutySlot,
        slot_index: int,
        counters: _Counters,
        vacationer_mode: bool,
    ) -> float:
        score = 0.0
        score += counters.deficit(primary.name) * 12
        score += counters.deficit(backup.name) * 12

        # Баланс ролей: целимся в ~половину основных / запасных
        score += (counters.primary_target(primary.name) - counters.primary[primary.name]) * 18
        score += (counters.backup_target(backup.name) - counters.backup[backup.name]) * 18
        score -= abs(counters.primary[primary.name] - counters.backup[primary.name]) * 4
        score -= abs(counters.primary[backup.name] - counters.backup[backup.name]) * 4

        # Штраф за перегруз относительно цели
        score -= max(0, counters.total(primary.name) + 1 - counters.target[primary.name]) * 25
        score -= max(0, counters.total(backup.name) + 1 - counters.target[backup.name]) * 25

        # Штраф за недостаточный отдых
        score -= self._rest_penalty(primary.name, slot_index, counters) * 40
        score -= self._rest_penalty(backup.name, slot_index, counters) * 40

        # Баланс подкоманд по роли primary (чтобы Т не уезжали только в запасные)
        t_primary = sum(counters.primary[e.name] for e in self.employees if e.subteam == Subteam.T)
        d_primary = sum(counters.primary[e.name] for e in self.employees if e.subteam == Subteam.D)
        if primary.subteam == Subteam.T and t_primary <= d_primary:
            score += 8
        if primary.subteam == Subteam.D and d_primary <= t_primary:
            score += 8

        if vacationer_mode:
            if slot.start_date in self.planned.get(primary.name, set()):
                score += 20
            if slot.start_date in self.planned.get(backup.name, set()):
                score += 20
            score += other_vacationers_on_leave(slot.start_date, primary, self.vacationers) * 5
            score += other_vacationers_on_leave(slot.start_date, backup, self.vacationers) * 5
        return score

    def _rest_penalty(self, name: str, slot_index: int, counters: _Counters) -> float:
        since = counters.slots_since_last(name, slot_index)
        if since is None:
            return 0.0
        needed = self.personal_min_rest[name]
        if since < 1:
            return 100.0
        if since >= needed:
            return 0.0
        return float(needed - since)

    def _rank_candidates(
        self,
        pool: list[Employee],
        slot: DutySlot,
        slot_index: int,
        counters: _Counters,
        previous_names: set[str],
        role: str,
        other: Employee | None,
        vacationer_mode: bool,
        *,
        strict_rest: bool,
    ) -> list[Employee]:
        candidates: list[Employee] = []
        for employee in pool:
            if not employee.is_available_on(slot.start_date):
                continue
            if employee.name in previous_names:
                continue
            if other is not None and employee.name == other.name:
                continue
            if other is not None and employee.subteam == other.subteam:
                continue
            if not self._rest_ok(employee.name, slot_index, counters, strict=strict_rest):
                continue
            if vacationer_mode and counters.deficit(employee.name) < 0:
                continue
            # Не даём уезжать далеко за цель
            if counters.total(employee.name) >= counters.target[employee.name] + 1:
                if not vacationer_mode:
                    continue
            candidates.append(employee)

        # Если отфильтровали всех из-за +1 к цели — ослабляем
        if not candidates and not vacationer_mode:
            for employee in pool:
                if not employee.is_available_on(slot.start_date):
                    continue
                if employee.name in previous_names:
                    continue
                if other is not None and employee.name == other.name:
                    continue
                if other is not None and employee.subteam == other.subteam:
                    continue
                if not self._rest_ok(employee.name, slot_index, counters, strict=strict_rest):
                    continue
                candidates.append(employee)

        def sort_key(employee: Employee) -> tuple:
            planned_bonus = 0
            cover_bonus = 0
            if vacationer_mode:
                planned_bonus = (
                    1 if slot.start_date in self.planned.get(employee.name, set()) else 0
                )
                cover_bonus = other_vacationers_on_leave(
                    slot.start_date,
                    employee,
                    self.vacationers,
                )
            role_count = (
                counters.primary[employee.name]
                if role == "primary"
                else counters.backup[employee.name]
            )
            role_target = (
                counters.primary_target(employee.name)
                if role == "primary"
                else counters.backup_target(employee.name)
            )
            role_deficit = role_target - role_count
            other_role = (
                counters.backup[employee.name]
                if role == "primary"
                else counters.primary[employee.name]
            )
            balance_penalty = abs(role_count - other_role)
            over_target = max(0, counters.total(employee.name) - counters.target[employee.name])
            rest_gap = counters.slots_since_last(employee.name, slot_index)
            rest_needed = self.personal_min_rest[employee.name]
            rest_shortfall = 0 if rest_gap is None else max(0, rest_needed - rest_gap)
            return (
                -planned_bonus,
                -cover_bonus,
                -role_deficit,
                -counters.deficit(employee.name),
                over_target,
                rest_shortfall,
                counters.total(employee.name),
                balance_penalty,
                role_count,
                employee.name,
            )

        candidates.sort(key=sort_key)
        return candidates


def build_schedule(
    employees: list[Employee],
    period_start: date,
    period_end: date,
    backtrack_limit: int = DEFAULT_BACKTRACK_LIMIT,
) -> ScheduleResult:
    return DutyScheduler(
        employees=employees,
        period_start=period_start,
        period_end=period_end,
        backtrack_limit=backtrack_limit,
    ).schedule()
