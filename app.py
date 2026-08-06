"""PyWR Reader — local web app.

Run:  python app.py   →  http://127.0.0.1:5321

The thin entry point. It first makes sure the one dependency (Flask) is
available — creating a private .venv beside the app on first run, the same way
the pywr environment bootstraps itself — then builds the Flask app and
registers the API blueprints (pywr_reader/api/*). The open model and the runs
live in a single session object (pywr_reader/session); each blueprint reads and
mutates it.
"""

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

MIN_PYTHON = (3, 9)
# The project root. Computed here rather than imported from pywr_reader.api,
# because the bootstrap below has to run before Flask can be imported.
_HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(_HERE, ".venv")
# Set on the hand-over, so a bootstrap that didn't work cannot re-exec forever.
BOOTSTRAP_ENV = "PYWR_READER_BOOTSTRAPPED"


def _die(*lines):
    print("\n".join(lines), file=sys.stderr)
    raise SystemExit(1)


def venv_python(venv_dir=VENV_DIR):
    """The interpreter inside a venv — Scripts\\python.exe on Windows."""
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def manual_steps():
    """The by-hand install in this platform's spelling, so a message that has
    to give up is still actionable."""
    if os.name == "nt":
        return ["  py -m venv .venv",
                "  .venv\\Scripts\\pip install -r requirements.txt",
                "  .venv\\Scripts\\python app.py"]
    return ["  python3 -m venv .venv",
            "  ./.venv/bin/pip install -r requirements.txt",
            "  ./.venv/bin/python app.py"]


def has_flask(python=None):
    """Is Flask importable — here, or by another interpreter?"""
    if python is None:
        try:
            import flask  # noqa: F401
            return True
        except ImportError:
            return False
    try:
        return subprocess.run([python, "-c", "import flask"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False


def bootstrap():
    """Make `python app.py` the only command anyone has to remember.

    Returns immediately when Flask is already importable, so running from
    inside .venv costs one cheap import and behaves exactly as before.
    Otherwise it creates .venv beside the app, installs the requirements into
    it, and hands over to that interpreter.
    """
    if has_flask():
        return
    if os.environ.get(BOOTSTRAP_ENV):        # already tried once — don't loop
        _die("PyWR Reader still cannot import Flask after setting up .venv.",
             "", "Set it up by hand:", *manual_steps())

    python = venv_python()
    if not os.path.isfile(python):
        print(f"PyWR Reader: creating a private environment in {VENV_DIR} "
              "(one time)…", flush=True)
        try:
            subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            _die(f"Could not create {VENV_DIR}: {exc}", "",
                 "On Debian/Ubuntu the venv module is a separate package:",
                 "  sudo apt install python3-venv", "",
                 "Or set it up by hand:", *manual_steps())

    if not has_flask(python):
        print("PyWR Reader: installing Flask (needs internet, one time)…",
              flush=True)
        try:
            subprocess.run([python, "-m", "pip", "install", "--quiet",
                            "--disable-pip-version-check",
                            "-r", os.path.join(_HERE, "requirements.txt")],
                           check=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            _die(f"Could not install the requirements: {exc}", "",
                 "This step needs internet access the first time.", "",
                 "Or set it up by hand:", *manual_steps())

    # Hand over to the venv's interpreter, keeping any arguments. subprocess
    # rather than os.execv, which is unreliable on Windows.
    env = dict(os.environ)
    env[BOOTSTRAP_ENV] = "1"
    raise SystemExit(subprocess.run(
        [python, os.path.abspath(__file__), *sys.argv[1:]], env=env).returncode)


if sys.version_info < MIN_PYTHON:
    _die(f"PyWR Reader needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer — "
         f"this is Python {sys.version.split()[0]}.", "",
         "Install a newer one from https://www.python.org/downloads/ and run "
         "this again.")
bootstrap()

from flask import Flask  # noqa: E402

from pywr_reader.api import register_blueprints  # noqa: E402
from pywr_reader.api.util import APP_DIR  # noqa: E402

# re-exported so the tests (and any script) have one import for the session
from pywr_reader.session import RUNS, WORKSPACE  # noqa: E402, F401

app = Flask(__name__, static_folder=os.path.join(APP_DIR, "static"),
            static_url_path="/static")
app.json.sort_keys = False  # keep pywr model key order in API responses
register_blueprints(app)


def port_in_use(port, host="127.0.0.1", timeout=0.4):
    """Is something already listening there?"""
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


def is_pywr_reader(url, timeout=1.5):
    """Does that address answer with this app's page? Separates "already
    running" from "that port belongs to something else", so the message can say
    which without guessing."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return b"PyWR Reader" in response.read(4096)
    except OSError:
        return False


def open_when_ready(url, port, tries=100, delay=0.1):
    """Open the browser once the server answers. Waiting matters: opening it
    immediately lands on a connection-refused page, which looks like a broken
    app to anyone who started it by double-clicking."""
    for _ in range(tries):
        if port_in_use(port):
            break
        time.sleep(delay)
    webbrowser.open(url)


if __name__ == "__main__":
    port = int(os.environ.get("PYWR_READER_PORT", "5321"))
    url = f"http://127.0.0.1:{port}"
    # the launchers pass --open; a bare `python app.py` stays quiet as before
    want_browser = "--open" in sys.argv or bool(os.environ.get("PYWR_READER_OPEN"))

    if port_in_use(port):
        # double-clicking the launcher twice shouldn't be a traceback
        if is_pywr_reader(url):
            print(f"PyWR Reader is already running → {url}")
            if want_browser:
                webbrowser.open(url)
            raise SystemExit(0)
        _die(f"Port {port} is in use by something that isn't PyWR Reader.", "",
             "Close whatever is using it, or run on a different port:",
             "  set PYWR_READER_PORT=5322 && py app.py" if os.name == "nt"
             else "  PYWR_READER_PORT=5322 python3 app.py")

    if want_browser:
        threading.Thread(target=open_when_ready, args=(url, port),
                         daemon=True).start()
    print(f"PyWR Reader → {url}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
