#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Цвета (если stdout — терминал)
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'
  DIM=$'\033[2m'
  CYAN=$'\033[36m'
  GREEN=$'\033[32m'
  YELLOW=$'\033[33m'
  RED=$'\033[31m'
  RESET=$'\033[0m'
else
  BOLD=""; DIM=""; CYAN=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

DATE_RE='^[0-9]{2}\.[0-9]{2}\.[0-9]{4}$'

info()  { printf '%s›%s %s\n' "$CYAN" "$RESET" "$*" >&2; }
ok()    { printf '%s✓%s %s\n' "$GREEN" "$RESET" "$*" >&2; }
warn()  { printf '%s!%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
fail()  { printf '%s✗%s %s\n' "$RED" "$RESET" "$*" >&2; }

header() {
  printf '\n%s%s%s\n' "$BOLD" "══════════════════════════════════════" "$RESET" >&2
  printf '%s  Генератор графика дежурств%s\n' "$BOLD" "$RESET" >&2
  printf '%s%s%s\n\n' "$BOLD" "══════════════════════════════════════" "$RESET" >&2
}

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

ask_date() {
  local prompt="$1"
  local hint="$2"
  local value=""
  while true; do
    printf '%s?%s %s %s(%s)%s: ' "$CYAN" "$RESET" "$prompt" "$DIM" "$hint" "$RESET" >&2
    read -r value
    value="$(trim "$value")"
    if [[ -z "$value" ]]; then
      warn "Нужна дата в формате ДД.ММ.ГГГГ"
      continue
    fi
    if [[ ! "$value" =~ $DATE_RE ]]; then
      warn "Неверный формат: «$value». Пример: $hint"
      continue
    fi
    printf '%s\n' "$value"
    return 0
  done
}

header

DATE_FROM=""
DATE_TO=""
EXTRA_ARGS=()

if [[ $# -ge 2 ]]; then
  DATE_FROM="$1"
  DATE_TO="$2"
  shift 2
  EXTRA_ARGS=("$@")
  info "Период из аргументов: ${BOLD}${DATE_FROM}${RESET} — ${BOLD}${DATE_TO}${RESET}"
else
  info "Укажите период дежурств"
  info "Последний день = старт последнего слота"
  printf '\n' >&2
  DATE_FROM="$(ask_date "Дата начала" "10.08.2026")"
  printf '\n' >&2
  DATE_TO="$(ask_date "Дата окончания" "31.08.2026")"
  printf '\n' >&2
fi

if [[ ! "$DATE_FROM" =~ $DATE_RE ]]; then
  fail "Некорректная дата начала: $DATE_FROM"
  exit 1
fi
if [[ ! "$DATE_TO" =~ $DATE_RE ]]; then
  fail "Некорректная дата окончания: $DATE_TO"
  exit 1
fi

TEAM_FILE="${ROOT}/team.md"
if [[ ! -f "$TEAM_FILE" ]]; then
  fail "Не найден файл команды: $TEAM_FILE"
  exit 1
fi

OUTPUT_NAME="duty-${DATE_FROM}-${DATE_TO}.md"

info "Период:  ${BOLD}${DATE_FROM} — ${DATE_TO}${RESET}"
info "Команда: ${BOLD}team.md${RESET}"
info "Выход:   ${BOLD}${OUTPUT_NAME}${RESET}"
printf '\n' >&2
info "Запуск планировщика…"
printf '\n' >&2

set +e
# Пустой массив + set -u ломается на bash macOS — раскрываем безопасно
python3 "$ROOT/schedule_duty.py" --from "$DATE_FROM" --to "$DATE_TO" \
  ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
status=$?
set -e

printf '\n' >&2
if [[ $status -eq 0 ]]; then
  ok "Готово"
  exit 0
fi

fail "Скрипт завершился с ошибкой (код $status)"
exit "$status"
