#!/usr/bin/env bash
set -euo pipefail

IMPORTER_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
CORE_SOURCE_ROOT="${1:?usage: check-core-contract.sh /path/to/core /path/to/gui}"
GUI_SOURCE_ROOT="${2:?usage: check-core-contract.sh /path/to/core /path/to/gui}"

export PYTHONPATH="$IMPORTER_ROOT/src"
export OGV_CORE_SOURCE_ROOT="$CORE_SOURCE_ROOT"
export OGV_GUI_SOURCE_ROOT="$GUI_SOURCE_ROOT"

python3 -m offline_game_vault_importer check-core \
  --source-root "$CORE_SOURCE_ROOT"
python3 -m unittest discover \
  -s "$IMPORTER_ROOT/tests" \
  -p 'test_current_core_contract.py' \
  -v
