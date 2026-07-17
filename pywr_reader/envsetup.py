"""Bootstrap a dedicated Python environment with pywr installed.

The app itself only needs Flask, so it runs on whatever Python the user has.
Running models needs pywr, which ships wheels only for Linux/Windows — on
macOS the reliable source is conda-forge. Strategy (first success wins):

  - macOS:  micromamba (conda-forge) → pip on current Python → uv Python 3.11
  - others: pip on current Python → uv Python 3.11 → micromamba

micromamba is a single static binary downloaded into the project — no admin
rights, no conda install, nothing outside this folder (MAMBA_ROOT_PREFIX is
kept local too). The env lives in <project>/.pywr-env. Progress streams to a
log file the frontend polls.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import threading

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_DIR = os.path.join(PROJECT_DIR, ".pywr-env")
BOOT_DIR = os.path.join(PROJECT_DIR, ".uv-bootstrap")
MAMBA_DIR = os.path.join(PROJECT_DIR, ".micromamba")
LOG_PATH = os.path.join(PROJECT_DIR, ".pywr-env-setup.log")

_lock = threading.Lock()
_state = {"running": False}


def env_python():
    for candidate in (os.path.join(ENV_DIR, "bin", "python"),
                      os.path.join(ENV_DIR, "Scripts", "python.exe")):
        if os.path.isfile(candidate):
            return candidate
    return None


def check_env():
    """Return {'ready': bool, 'python': path, 'pywr_version': str|None,
    'setting_up': bool}."""
    python = env_python()
    info = {"ready": False, "python": python, "pywr_version": None,
            "setting_up": _state["running"]}
    if python:
        try:
            out = subprocess.run(
                [python, "-c", "import pywr, sys; print(pywr.__version__)"],
                capture_output=True, text=True, timeout=60)
            if out.returncode == 0:
                info["ready"] = True
                info["pywr_version"] = out.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return info


def read_log(tail=200):
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return lines[-tail:]
    except OSError:
        return []


def _log(fh, msg):
    fh.write(msg.rstrip() + "\n")
    fh.flush()


def _run_logged(fh, cmd, **kw):
    _log(fh, "$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, **kw)
    for line in proc.stdout:
        fh.write(line)
    fh.flush()
    proc.wait()
    return proc.returncode


def _attempt_current_python(fh):
    _log(fh, f"== Attempt 1: venv on current Python "
             f"({sys.version.split()[0]}) ==")
    shutil.rmtree(ENV_DIR, ignore_errors=True)
    if _run_logged(fh, [sys.executable, "-m", "venv", ENV_DIR]) != 0:
        return False
    python = env_python()
    if _run_logged(fh, [python, "-m", "pip", "install", "--upgrade", "pip"]) != 0:
        return False
    if _run_logged(fh, [python, "-m", "pip", "install", "pywr", "pandas"]) != 0:
        return False
    return check_env()["ready"]


def _attempt_uv(fh, python_version="3.11"):
    _log(fh, f"== Attempt 2: uv-managed CPython {python_version} ==")
    uv = (shutil.which("uv")
          or next((p for p in (os.path.join(BOOT_DIR, "bin", "uv"),
                               os.path.join(BOOT_DIR, "Scripts", "uv.exe"))
                   if os.path.isfile(p)), None))
    if uv is None:
        shutil.rmtree(BOOT_DIR, ignore_errors=True)
        if _run_logged(fh, [sys.executable, "-m", "venv", BOOT_DIR]) != 0:
            return False
        boot_python = (os.path.join(BOOT_DIR, "bin", "python")
                       if os.name != "nt"
                       else os.path.join(BOOT_DIR, "Scripts", "python.exe"))
        if _run_logged(fh, [boot_python, "-m", "pip", "install", "uv"]) != 0:
            return False
        uv = (os.path.join(BOOT_DIR, "bin", "uv") if os.name != "nt"
              else os.path.join(BOOT_DIR, "Scripts", "uv.exe"))

    shutil.rmtree(ENV_DIR, ignore_errors=True)
    if _run_logged(fh, [uv, "venv", "--python", python_version, ENV_DIR]) != 0:
        return False
    python = env_python()
    if _run_logged(fh, [uv, "pip", "install", "--python", python,
                        "pywr", "pandas"]) != 0:
        return False
    return check_env()["ready"]


def _mamba_platform():
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return "osx-arm64" if machine in ("arm64", "aarch64") else "osx-64"
    if sys.platform.startswith("linux"):
        return "linux-aarch64" if machine in ("arm64", "aarch64") else "linux-64"
    return "win-64"


def _micromamba_exe(fh):
    exe = os.path.join(MAMBA_DIR, "bin", "micromamba")
    if not os.path.isfile(exe):
        os.makedirs(MAMBA_DIR, exist_ok=True)
        url = f"https://micro.mamba.pm/api/micromamba/{_mamba_platform()}/latest"
        _log(fh, f"downloading micromamba from {url}")
        if _run_logged(fh, ["/bin/sh", "-c",
                            f"curl -Ls '{url}' | tar -xj -C '{MAMBA_DIR}' "
                            "bin/micromamba"]) != 0:
            return None
    return exe


def _mamba_env():
    return dict(os.environ, MAMBA_ROOT_PREFIX=os.path.join(MAMBA_DIR, "root"))


def _attempt_micromamba(fh, python_version="3.11", subdir=None):
    _log(fh, f"== Attempt: micromamba + conda-forge"
             f"{f' (platform {subdir})' if subdir else ''} ==")
    exe = _micromamba_exe(fh)
    if exe is None:
        return False
    shutil.rmtree(ENV_DIR, ignore_errors=True)
    cmd = [exe, "create", "-y", "-p", ENV_DIR, "-c", "conda-forge"]
    if subdir:
        cmd += ["--platform", subdir]
    cmd += [f"python={python_version}", "pywr", "pandas"]
    if _run_logged(fh, cmd, env=_mamba_env()) != 0:
        return False
    return check_env()["ready"]


def _attempt_micromamba_rosetta(fh):
    """Apple Silicon: conda-forge ships pywr only for Intel — run it under
    Rosetta 2 by building an osx-64 env."""
    if not (sys.platform == "darwin"
            and platform.machine().lower() in ("arm64", "aarch64")):
        return False
    if subprocess.run(["arch", "-x86_64", "/usr/bin/true"],
                      capture_output=True).returncode != 0:
        _log(fh, "Rosetta 2 not available — skipping osx-64 route")
        return False
    return _attempt_micromamba(fh, subdir="osx-64")


def _attempt_source_build_with_conda_glpk(fh, python_version="3.11"):
    """Native env: conda-forge supplies python + GLPK headers/libs for this
    architecture, pip builds pywr from source against them."""
    _log(fh, "== Attempt: build pywr from source against conda-forge GLPK ==")
    exe = _micromamba_exe(fh)
    if exe is None:
        return False
    shutil.rmtree(ENV_DIR, ignore_errors=True)
    if _run_logged(fh, [exe, "create", "-y", "-p", ENV_DIR, "-c", "conda-forge",
                        f"python={python_version}", "glpk", "pandas", "pip"],
                   env=_mamba_env()) != 0:
        return False
    python = env_python()
    build_env = dict(os.environ,
                     CFLAGS=f"-I{ENV_DIR}/include",
                     LDFLAGS=f"-L{ENV_DIR}/lib -Wl,-rpath,{ENV_DIR}/lib")
    if _run_logged(fh, [python, "-m", "pip", "install", "pywr"],
                   env=build_env) != 0:
        return False
    return check_env()["ready"]


def _setup_worker():
    if sys.platform == "darwin":
        attempts = [_attempt_micromamba_rosetta,
                    _attempt_source_build_with_conda_glpk,
                    _attempt_micromamba,
                    _attempt_current_python, _attempt_uv]
    else:
        attempts = [_attempt_current_python, _attempt_uv, _attempt_micromamba]
    try:
        with open(LOG_PATH, "w", encoding="utf-8") as fh:
            ok = False
            for attempt in attempts:
                try:
                    ok = attempt(fh)
                except Exception as exc:  # noqa: BLE001 — log and fall through
                    _log(fh, f"{attempt.__name__} raised: {exc!r}")
                if ok:
                    break
            if ok:
                info = check_env()
                _log(fh, f"SUCCESS: pywr {info['pywr_version']} ready "
                         f"at {info['python']}")
            else:
                _log(fh, "FAILED: could not build a pywr environment. "
                         "See errors above.")
    finally:
        _state["running"] = False


def start_setup():
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_setup_worker, daemon=True).start()
    return True


if __name__ == "__main__":
    _state["running"] = True
    _setup_worker()
    print(json.dumps(check_env(), indent=2))
