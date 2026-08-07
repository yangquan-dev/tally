#!/usr/bin/env bash
# 一键签发授权（非交互）
# 用法：
#   ./scripts/issue.sh "某某物业"
#   ./scripts/issue.sh "某某物业" 365
#   ./scripts/issue.sh "某某物业" 2027-12-31
#   ./scripts/issue.sh "某某物业" 365 ~/Desktop/某某物业.lic
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  PYTHON="$ROOT/venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

CUSTOMER="${1:-}"
if [[ -z "$CUSTOMER" ]]; then
  echo "用法: $0 \"客户名称\" [天数|到期日YYYY-MM-DD] [输出路径]"
  echo "示例: $0 \"某某物业\" 365"
  echo "示例: $0 \"某某物业\" 2027-12-31"
  exit 1
fi

EXTRA=()
ARG2="${2:-365}"
if [[ "$ARG2" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  EXTRA+=(--expires "$ARG2")
else
  EXTRA+=(--days "$ARG2")
fi

if [[ -n "${3:-}" ]]; then
  EXTRA+=(--out "$3")
fi

exec "$PYTHON" "$ROOT/scripts/issue_license.py" issue --customer "$CUSTOMER" "${EXTRA[@]}"
