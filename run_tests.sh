#!/bin/sh
# Run the PyWR Reader test suite with the project venv.
#   ./run_tests.sh
# Unit, API and frontend-contract tests need only Flask (requirements.txt).
# Two groups skip themselves unless their extra is present, so this always
# runs green on a bare checkout:
#   * pywr integration — until the pywr environment is set up (from the app:
#     the "Set up PyWR" button)
#   * browser smoke    — until playwright is installed:
#       ./.venv/bin/pip install -r requirements-dev.txt
#       ./.venv/bin/playwright install chromium
set -e
cd "$(dirname "$0")"

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "Using: $($PY --version)"

# lint first if ruff is installed (dev extra); a bare checkout just skips it
if [ -x .venv/bin/ruff ]; then
    echo "Linting with ruff…"
    .venv/bin/ruff check .
fi

exec "$PY" -m unittest discover -s tests -v
