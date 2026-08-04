from __future__ import annotations

import argparse
import sys
from pathlib import Path

from duty.renderer import render_schedule
from duty.scheduler import build_schedule
from duty.team_parser import load_team, parse_date, period_filename


def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _supports_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def status(step: int, total: int, message: str) -> None:
    prefix = _c("36", f"[{step}/{total}]")
    print(f"  {prefix} {message}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Генерация графика дежурств по team.md",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        required=True,
        help="Начало периода (ДД.ММ.ГГГГ), первый слот",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        required=True,
        help="Конец периода (ДД.ММ.ГГГГ), старт последнего слота",
    )
    parser.add_argument(
        "--team",
        dest="team_path",
        default="team.md",
        help="Путь к team.md (по умолчанию ./team.md)",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        default=None,
        help="Путь к файлу результата (по умолчанию duty-<from>-<to>.md)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    total_steps = 5

    status(1, total_steps, "Разбор периода…")
    period_start = parse_date(args.date_from)
    period_end = parse_date(args.date_to)
    if period_end < period_start:
        print(_c("31", "✗ Дата окончания раньше даты начала"), file=sys.stderr)
        return 1

    team_path = Path(args.team_path)
    if not team_path.exists():
        print(_c("31", f"✗ Файл команды не найден: {team_path}"), file=sys.stderr)
        return 1

    status(2, total_steps, f"Чтение команды из {team_path.name}…")
    employees = load_team(team_path)
    vacationers = sum(1 for e in employees if e.vacation is not None)
    print(
        f"         сотрудники: {len(employees)}, "
        f"с отпуском в данных: {vacationers}",
        flush=True,
    )

    status(3, total_steps, "Очереди: отпускники → основные → запасные…")
    result = build_schedule(
        employees=employees,
        period_start=period_start,
        period_end=period_end,
    )
    filled = sum(1 for a in result.assignments if a.is_complete)
    print(
        f"         слотов: {result.total_slots}, "
        f"заполнено: {filled}, "
        f"база нагрузки: {result.base_load:.2f}, "
        f"мин. отдых: {result.min_rest_slots}",
        flush=True,
    )

    status(4, total_steps, "Формирование markdown…")
    content = render_schedule(result)

    output_path = (
        Path(args.output_path)
        if args.output_path
        else Path(period_filename(period_start, period_end))
    )
    status(5, total_steps, f"Запись в {output_path}…")
    output_path.write_text(content, encoding="utf-8")

    print()
    print(_c("32", f"✓ График сохранён: {output_path}"))
    if result.warnings:
        print(_c("33", f"! Предупреждений: {len(result.warnings)}"))
        for warning in result.warnings:
            print(f"    — {warning}")
    manual = sum(1 for a in result.assignments if a.needs_manual_fix)
    if manual:
        print(_c("33", f"! Слотов для ручной правки: {manual}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
