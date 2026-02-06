#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv"

if [[ ! -d "${VENV}" ]]; then
  python3 -m venv "${VENV}"
fi

"${VENV}/bin/python" -m pip install -U pip
"${VENV}/bin/python" -m pip install -U setuptools
"${VENV}/bin/python" -m pip install -U "tree_sitter==0.21.3"

echo "Done. tree-sitter deps installed into ${VENV}"
