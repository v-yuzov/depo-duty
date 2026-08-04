from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from duty.models import Employee, Subteam, VacationPeriod


DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
VACATION_RE = re.compile(
    r"(\d{2}\.\d{2}\.\d{4})\s*[–\-—]\s*(\d{2}\.\d{2}\.\d{4})"
)


def parse_date(value: str) -> date:
    match = DATE_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Некорректная дата: {value!r}. Ожидается ДД.ММ.ГГГГ")
    day, month, year = map(int, match.groups())
    return date(year, month, day)


def format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def format_date_with_weekday(value: date) -> str:
    from duty.models import WEEKDAY_NAMES_RU

    return f"{format_date(value)} ({WEEKDAY_NAMES_RU[value.weekday()]})"


def parse_vacation(raw: str) -> VacationPeriod | None:
    text = raw.strip()
    if not text:
        return None
    match = VACATION_RE.fullmatch(text)
    if not match:
        raise ValueError(f"Некорректный период отпуска: {raw!r}")
    return VacationPeriod(start=parse_date(match.group(1)), end=parse_date(match.group(2)))


def parse_subteam(raw: str) -> Subteam:
    value = raw.strip()
    for item in Subteam:
        if item.value == value:
            return item
    raise ValueError(f"Неизвестная подкоманда: {raw!r}")


def parse_team_markdown(content: str) -> list[Employee]:
    employees: list[Employee] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        name, subteam_raw, vacation_raw = cells[0], cells[1], cells[2]
        if name in {"Имя", "-----"} or set(name) <= {"-"}:
            continue
        if name.startswith("-"):
            continue
        employees.append(
            Employee(
                name=name,
                subteam=parse_subteam(subteam_raw),
                vacation=parse_vacation(vacation_raw),
            )
        )
    if not employees:
        raise ValueError("В team.md не найдено ни одного сотрудника")
    return employees


def load_team(path: Path) -> list[Employee]:
    return parse_team_markdown(path.read_text(encoding="utf-8"))


def period_filename(period_start: date, period_end: date) -> str:
    return f"duty-{format_date(period_start)}-{format_date(period_end)}.md"
