#!/bin/sh
# Run the PyWR Reader test suite with the project venv.
#   ./run_tests.sh
# Unit + API tests need only Flask (requirements.txt). The integration tests
# that actually run pywr are skipped automatically until the pywr environment
# has been set up (from the app: the "Set up PyWR" button).
set -e
cd "$(dirname "$0")"

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "Using: $($PY --version)"
exec "$PY" -m unittest discover -s tests -v
