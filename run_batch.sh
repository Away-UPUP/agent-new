#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${INSPECTION_PYTHON:-python}" "$task_root/scripts/batch_runner.py" start "$@"
