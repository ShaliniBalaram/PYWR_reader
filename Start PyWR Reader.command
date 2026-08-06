#!/bin/sh
# Double-click to start PyWR Reader and open it in your browser.
#
# macOS: opens a Terminal window and runs the app in it.
# Linux: run it from a terminal (./"Start PyWR Reader.command"), or open it
#        with your file manager if that is set up to run executables.
#
# Closing the window — or pressing Ctrl+C — stops the app.
# On the very first run it sets up a private .venv, which needs internet once.

cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "Python 3 is not installed (or not on your PATH)."
  echo "Install it from https://www.python.org/downloads/ and try again."
  printf 'Press Return to close… '
  read _dummy
  exit 1
fi

"$PY" app.py --open
status=$?

# Hold the window open on a real failure, so a double-clicker can read the
# reason. 130 is Ctrl+C, which is how you are meant to stop it.
if [ "$status" -ne 0 ] && [ "$status" -ne 130 ]; then
  echo
  printf 'PyWR Reader stopped with an error. Press Return to close… '
  read _dummy
fi
exit "$status"
