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


@dataclass
class CircularQueue:
    """Круговая очередь с указателем."""

    items: list[Employee]
    pos: int = 0

    def __bool__(self) -> bool:
        return bool(self.items)

    def take(self, predicate) -> Employee | None:
        if not self.items:
            return None
        n = len(self.items)
        for _ in range(n):
            person = self.items[self.pos]
            self.pos = (self.pos + 1) % n
            if predicate(person):
                return person
        return None


def sort_team_members(employees: list[Employee]) -> list[Employee]:
    """Внутри подкоманды: сначала +, затем по алфавиту."""
    return sorted(employees, key=lambda e: (0 if e.priority else 1, e.name))


def build_interleaved_queue(employees: list[Employee]) -> list[Employee]:
    """Чередование Д/Т: Д+,Т+,Д,Т… (+ внутри своей подкоманды первые)."""
    d_list = sort_team_members([e for e in employees if e.subteam == Subteam.D])
    t_list = sort_team_members([e for e in employees if e.subteam == Subteam.T])
    result: list[Employee] = []
    i = j = 0
    while i < len(d_list) or j < len(t_list):
        if i < len(d_list):
            result.append(d_list[i])
            i += 1
        if j < len(t_list):
            result.append(t_list[j])
            j += 1
    return result


@dataclass
class _State:
    primary: dict[str, int] = field(default_factory=dict)
    backup: dict[str, int] = field(default_factory=dict)
    duty_indices: dict[str, list[int]] = field(default_factory=dict)
    target: dict[str, int] = field(default_factory=dict)
    min_rest: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_stats(
        cls,
        stats: list[EmployeeStats],
        min_rest: dict[str, int],
    ) -> "_State":
        return cls(
            primary={s.employee.name: 0 for s in stats},
            backup={s.employee.name: 0 for s in stats},
            duty_indices={s.employee.name: [] for s in stats},
            target={s.employee.name: s.target_rounded for s in stats},
            min_rest=min_rest,
        )

    def total(self, name: str) -> int:
        return self.primary[name] + self.backup[name]

    def deficit(self, name: str) -> int:
        return self.target[name] - self.total(name)

    def prefer_primary(self, name: str) -> bool:
        return self.primary[name] <= self.backup[name]

    def prefer_backup(self, name: str) -> bool:
        return self.backup[name] <= self.primary[name]

    def rest_ok(self, name: str, slot_index: int, *, strict: bool) -> bool:
        """Отдых относительно всех занятых слотов (прошлых и будущих)."""
        needed = self.min_rest[name] if strict else 1
        for other in self.duty_indices[name]:
            free_between = abs(slot_index - other) - 1
            if free_between < needed:
                return False
        return True

    def assign(self, employee: Employee, role: str, slot_index: int) -> None:
        if role == "primary":
            self.primary[employee.name] += 1
        else:
            self.backup[employee.name] += 1
        self.duty_indices[employee.name].append(slot_index)


class DutyScheduler:
    def __init__(
        self,
        employees: list[Employee],
        period_start: date,
        period_end: date,
        backtrack_limit: int = 0,
    ) -> None:
        self.employees = employees
        self.period_start = period_start
        self.period_end = period_end
        self.slots = build_slots(period_start, period_end)
        self.base_load, self.stats = compute_stats(employees, self.slots, ceil_all=True)
        self.global_min_rest = compute_min_rest_slots(len(employees))
        self.personal_min_rest = {
            s.employee.name: compute_personal_min_rest(
                s.available_slots,
                s.target_rounded,
                self.global_min_rest,
            )
            for s in self.stats
        }
        self.vacationers = [
            e for e in employees if is_vacationer_in_period(e, self.slots)
        ]
        self.vacationer_names = {e.name for e in self.vacationers}
        self.regulars = [e for e in employees if e.name not in self.vacationer_names]

    def schedule(self) -> ScheduleResult:
        assignments = [DutyAssignment(slot=slot) for slot in self.slots]
        state = _State.from_stats(self.stats, self.personal_min_rest)
        warnings: list[str] = []

        self._phase_vacationers(assignments, state)
        self._phase_primary_queue(assignments, state)
        self._phase_backup_queues(assignments, state, warnings)
        self._sync_stats(state)

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

    def _sync_stats(self, state: _State) -> None:
        for item in self.stats:
            item.primary_count = state.primary[item.employee.name]
            item.backup_count = state.backup[item.employee.name]

    def _can_duty(
        self,
        employee: Employee,
        slot: DutySlot,
        slot_index: int,
        state: _State,
        *,
        strict_rest: bool,
        require_deficit: bool,
        occupied: set[str],
    ) -> bool:
        if employee.name in occupied:
            return False
        if not employee.is_available_on(slot.start_date):
            return False
        if not state.rest_ok(employee.name, slot_index, strict=strict_rest):
            return False
        if require_deficit and state.deficit(employee.name) <= 0:
            return False
        return True

    def _phase_vacationers(
        self,
        assignments: list[DutyAssignment],
        state: _State,
    ) -> None:
        """Фаза 1: отпускники по одному в слот, чередуя основной/запасной.

        Не обязаны закрывать пару целиком — вторую роль добьют фазы 2/3
        из очередей без отпускников.
        """
        if not self.vacationers:
            return

        queue = CircularQueue(build_interleaved_queue(self.vacationers))

        for strict_rest in (True, False):
            progress = True
            while progress:
                progress = False
                if not any(state.deficit(e.name) > 0 for e in self.vacationers):
                    break
                for index, assignment in enumerate(assignments):
                    if not any(state.deficit(e.name) > 0 for e in self.vacationers):
                        break
                    if self._try_place_vacationer(
                        assignment=assignment,
                        slot_index=index,
                        state=state,
                        queue=queue,
                        strict_rest=strict_rest,
                    ):
                        progress = True

    def _try_place_vacationer(
        self,
        assignment: DutyAssignment,
        slot_index: int,
        state: _State,
        queue: CircularQueue,
        *,
        strict_rest: bool,
    ) -> bool:
        """Ставит одного отпускника в роль, которой ему не хватает (осн ↔ зап)."""
        slot = assignment.slot
        occupied = {p.name for p in assignment.participants()}
        if assignment.is_complete:
            return False

        def base_ok(employee: Employee) -> bool:
            return self._can_duty(
                employee,
                slot,
                slot_index,
                state,
                strict_rest=strict_rest,
                require_deficit=True,
                occupied=occupied,
            )

        # Сначала те, кому сейчас нужен primary; затем кому нужен backup
        person = queue.take(
            lambda e: base_ok(e) and state.prefer_primary(e.name)
        ) or queue.take(
            lambda e: base_ok(e) and state.prefer_backup(e.name)
        ) or queue.take(base_ok)
        if person is None:
            return False

        preferred = "primary" if state.prefer_primary(person.name) else "backup"
        other = "backup" if preferred == "primary" else "primary"

        for role in (preferred, other):
            if role == "primary" and assignment.primary is not None:
                continue
            if role == "backup" and assignment.backup is not None:
                continue
            partner = assignment.backup if role == "primary" else assignment.primary
            if partner is not None and person.subteam == partner.subteam:
                continue

            if role == "primary":
                assignment.primary = person
            else:
                assignment.backup = person
            state.assign(person, role, slot_index)
            return True

        return False

    def _phase_primary_queue(
        self,
        assignments: list[DutyAssignment],
        state: _State,
    ) -> None:
        """Фаза 2: очередь Д/Т без отпускников → пустые основные."""
        queue = CircularQueue(build_interleaved_queue(self.regulars))
        if not queue:
            return

        for strict_rest in (True, False):
            for index, assignment in enumerate(assignments):
                if assignment.primary is not None:
                    continue
                slot = assignment.slot
                occupied = {p.name for p in assignment.participants()}
                # Если запасной уже из отпускников — основной только из другой подкоманды
                required_team: Subteam | None = None
                if assignment.backup is not None:
                    required_team = (
                        Subteam.T
                        if assignment.backup.subteam == Subteam.D
                        else Subteam.D
                    )

                def pred(employee: Employee) -> bool:
                    if required_team is not None and employee.subteam != required_team:
                        return False
                    return self._can_duty(
                        employee,
                        slot,
                        index,
                        state,
                        strict_rest=strict_rest,
                        require_deficit=False,
                        occupied=occupied,
                    )

                primary = queue.take(pred)
                if primary is not None:
                    assignment.primary = primary
                    state.assign(primary, "primary", index)

    def _phase_backup_queues(
        self,
        assignments: list[DutyAssignment],
        state: _State,
        warnings: list[str],
    ) -> None:
        """Фаза 3: очереди Д и Т без отпускников (+ первые) → запасные."""
        queue_t = CircularQueue(
            sort_team_members([e for e in self.regulars if e.subteam == Subteam.T])
        )
        queue_d = CircularQueue(
            sort_team_members([e for e in self.regulars if e.subteam == Subteam.D])
        )

        for strict_rest in (True, False):
            for index, assignment in enumerate(assignments):
                if assignment.backup is not None:
                    continue
                if assignment.primary is None:
                    continue
                slot = assignment.slot
                occupied = {assignment.primary.name}
                opposite = (
                    Subteam.T if assignment.primary.subteam == Subteam.D else Subteam.D
                )
                queue = queue_t if opposite == Subteam.T else queue_d

                def pred(employee: Employee) -> bool:
                    return self._can_duty(
                        employee,
                        slot,
                        index,
                        state,
                        strict_rest=strict_rest,
                        require_deficit=False,
                        occupied=occupied,
                    )

                backup = queue.take(pred)
                if backup is not None:
                    assignment.backup = backup
                    state.assign(backup, "backup", index)

        for assignment in assignments:
            if not assignment.is_complete:
                assignment.needs_manual_fix = True
                warnings.append(
                    f"{assignment.slot.start_date}: слот требует ручной правки"
                )


def build_schedule(
    employees: list[Employee],
    period_start: date,
    period_end: date,
    backtrack_limit: int = 0,
) -> ScheduleResult:
    return DutyScheduler(
        employees=employees,
        period_start=period_start,
        period_end=period_end,
        backtrack_limit=backtrack_limit,
    ).schedule()
