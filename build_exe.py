"""Package PyWR Reader as a standalone executable — no Python needed to run it.

    python build_exe.py

Produces `dist/PyWR Reader.exe` on Windows, `dist/PyWR Reader` on macOS and
Linux. PyInstaller is not a cross-compiler: **you get an executable for the
machine you build on**, so a Windows .exe has to be built on Windows. The
GitHub Actions workflow in .github/workflows/build.yml does exactly that on a
Windows runner, which is how the .exe on the Releases page is made.

What goes in: the app, Flask, the frontend under static/, and the example
model. What stays out: pywr. The packaged app still sets up .pywr-env beside
the executable on demand, exactly as the source version does — bundling a
scientific stack with compiled solvers would multiply the download for
everyone, including the people who only ever read models.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "PyWR Reader"
# runner.py and dataview.py are executed as scripts by a separate interpreter
# (the pywr environment), so they have to ship as real files, not just as
# importable modules baked into the binary.
DATA = [("static", "static"),
        ("examples", "examples"),
        (os.path.join("pywr_reader", "runner.py"), "pywr_reader"),
        (os.path.join("pywr_reader", "dataview.py"), "pywr_reader")]


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Get it with:\n"
              "  pip install -r requirements-dev.txt", file=sys.stderr)
        return 1
    try:
        # PyInstaller bundles what it can import from the interpreter running
        # it. Without Flask here you get a binary that builds cleanly and then
        # dies with ModuleNotFoundError the first time anyone runs it — so
        # refuse now, while the reason is still obvious.
        import flask  # noqa: F401
    except ImportError:
        print(f"Flask is not installed for {sys.executable}, so it would not "
              "be bundled.\nInstall the app's requirements into the same "
              "environment as PyInstaller:\n"
              "  pip install -r requirements.txt -r requirements-dev.txt",
              file=sys.stderr)
        return 1

    separator = ";" if os.name == "nt" else ":"
    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm", "--clean",
           "--onefile",           # one file to hand someone
           "--name", NAME,
           "--console",           # the window is where the app says it started
           "--distpath", os.path.join(HERE, "dist"),
           "--workpath", os.path.join(HERE, "build"),
           "--specpath", os.path.join(HERE, "build")]
    for src, dest in DATA:
        cmd += ["--add-data", f"{os.path.join(HERE, src)}{separator}{dest}"]
    cmd.append(os.path.join(HERE, "app.py"))

    print("Building — this takes a minute…", flush=True)
    if subprocess.run(cmd, cwd=HERE).returncode != 0:
        return 1

    built = os.path.join(HERE, "dist", NAME + (".exe" if os.name == "nt" else ""))
    if not os.path.isfile(built):
        print(f"PyInstaller finished but {built} is missing.", file=sys.stderr)
        return 1
    size_mb = os.path.getsize(built) / (1024 * 1024)
    print(f"\nBuilt {built}  ({size_mb:.0f} MB)")
    print("Double-click it, or run it from a terminal. Nothing else needed —\n"
          "it carries its own Python and Flask.")
    shutil.rmtree(os.path.join(HERE, "build"), ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
