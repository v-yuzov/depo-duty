from __future__ import annotations

from duty.models import DutyAssignment, Employee, EmployeeStats, ScheduleResult
from duty.team_parser import format_date, format_date_with_weekday


MONTHS_RU = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}


def _employee_label(employee: Employee) -> str:
    return f"{employee.name} ({employee.subteam.value})"


def _status(stats: EmployeeStats) -> str:
    diff = stats.total - stats.target_rounded
    if abs(diff) <= 1:
        return "✅"
    sign = "+" if diff > 0 else ""
    return f"⚠️ {sign}{diff}"


def _target_label(stats: EmployeeStats) -> str:
    raw = stats.target
    low = int(raw) if raw == int(raw) else int(raw)
    high = stats.target_rounded
    if low == high:
        return str(high)
    return f"{low}-{high}"


def render_schedule(result: ScheduleResult) -> str:
    month_name = MONTHS_RU[result.period_start.month]
    year = result.period_start.year
    lines: list[str] = [
        f"# Дежурства: {month_name} {year}",
        "",
        f"**Период:** {format_date(result.period_start)} — {format_date(result.period_end)}",
        "",
        "| Дата начала | Время начала | Дата окончания | Время окончания | Основной | Запасной |",
        "|-------------|--------------|----------------|-----------------|----------|----------|",
    ]

    for assignment in result.assignments:
        lines.append(_render_assignment_row(assignment))

    lines.extend(
        [
            "",
            "## Статистика",
            "",
            "| Имя | Подкоманда | Основной | Запасной | Всего | Цель | Статус |",
            "|-----|------------|----------|----------|-------|------|--------|",
        ]
    )

    ordered_stats = sorted(
        result.stats,
        key=lambda item: (item.employee.subteam.value, item.employee.name),
    )
    for stats in ordered_stats:
        lines.append(
            "| {name} | {team} | {primary} | {backup} | {total} | {target} | {status} |".format(
                name=stats.employee.name,
                team=stats.employee.subteam.value,
                primary=stats.primary_count,
                backup=stats.backup_count,
                total=stats.total,
                target=_target_label(stats),
                status=_status(stats),
            )
        )

    lines.extend(
        [
            "",
            f"**Базовая нагрузка:** {result.base_load:.2f} "
            f"(слотов: {result.total_slots}, участий: {result.total_slots * 2})",
            f"**Мин. отдых между дежурствами:** {result.min_rest_slots} слот(ов) "
            f"(формула: max(1, n//2 − 1), персонально может быть мягче при коротком окне)",
            "",
            "## Чек-лист",
            "",
        ]
    )
    lines.extend(_render_checklist(result))

    if result.warnings:
        lines.extend(["", "## Предупреждения", ""])
        for warning in result.warnings:
            lines.append(f"- ⚠️ {warning}")

    lines.append("")
    return "\n".join(lines)


def _render_assignment_row(assignment: DutyAssignment) -> str:
    slot = assignment.slot
    primary = _employee_label(assignment.primary) if assignment.primary else "⚠️ —"
    backup = _employee_label(assignment.backup) if assignment.backup else "⚠️ —"
    marker = " ⚠️" if assignment.needs_manual_fix else ""
    return (
        f"| {format_date_with_weekday(slot.start_date)} | {slot.start_time} | "
        f"{format_date_with_weekday(slot.end_date)} | {slot.end_time} | "
        f"{primary} | {backup}{marker} |"
    )


def _render_checklist(result: ScheduleResult) -> list[str]:
    checks: list[str] = []

    different_teams = all(
        a.is_complete and a.primary is not None and a.backup is not None
        and a.primary.subteam != a.backup.subteam
        for a in result.assignments
        if a.is_complete
    )
    checks.append(
        f"- [{'x' if different_teams else ' '}] Основной и запасной из разных подкоманд"
    )

    no_consecutive = True
    for index in range(1, len(result.assignments)):
        prev = {p.name for p in result.assignments[index - 1].participants()}
        curr = {p.name for p in result.assignments[index].participants()}
        if prev & curr:
            no_consecutive = False
            break
    checks.append(
        f"- [{'x' if no_consecutive else ' '}] Никто не дежурит два слота подряд"
    )

    rest_ok = True
    last_seen: dict[str, int] = {}
    for index, assignment in enumerate(result.assignments):
        for person in assignment.participants():
            prev = last_seen.get(person.name)
            if prev is not None and index - prev - 1 < 1:
                rest_ok = False
            last_seen[person.name] = index
    checks.append(
        f"- [{'x' if rest_ok else ' '}] Соблюдён запрет соседних слотов "
        f"(целевой отдых ≥ {result.min_rest_slots})"
    )

    no_vacation_duty = True
    for assignment in result.assignments:
        for person in assignment.participants():
            if not person.is_available_on(assignment.slot.start_date):
                no_vacation_duty = False
                break
    checks.append(
        f"- [{'x' if no_vacation_duty else ' '}] Никто не дежурит во время отпуска"
    )

    complete = all(a.is_complete for a in result.assignments)
    checks.append(
        f"- [{'x' if complete else ' '}] Все слоты заполнены"
    )

    totals = [s.total for s in result.stats if s.available_slots > 0]
    spread_ok = (max(totals) - min(totals) <= 2) if totals else True
    checks.append(
        f"- [{'x' if spread_ok else ' '}] Разница нагрузки (доступные) ≤ 2"
    )

    return checks
