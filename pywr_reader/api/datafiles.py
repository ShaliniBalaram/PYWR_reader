"""External data files: locate, preview and plot them."""

import contextlib
import json
import os
import subprocess
import tempfile
import uuid

from flask import Blueprint, jsonify, request

from pywr_reader import envsetup
from pywr_reader.api.util import APP_DIR, err
from pywr_reader.session import WORKSPACE

bp = Blueprint("datafiles", __name__)


@bp.get("/api/data")
def data_status():
    try:
        WORKSPACE.require_model()
    except ValueError as exc:
        return err(exc)
    return jsonify({"ok": True, **WORKSPACE.data_payload()})


class _ViewError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def _run_dataview(path, key, series=False, window=None):
    """Read a data file via dataview.py in the pywr environment. Reading
    h5/xlsx needs pandas and PyTables, which live there — so this shells out,
    the way a run does, rather than adding them to the app.

    window is an optional (start, stop) row range for the plot — a deep zoom
    re-requests just that slice so it comes back at full daily resolution.

    Restricted to files the open model actually references — never an
    arbitrary file reader."""
    allowed = {item["resolved"] for item in
               ((WORKSPACE.data or {}).get("report") or []) if item.get("resolved")}
    if path not in allowed:
        raise _ViewError("that file is not one of this model's data files", 403)
    info = envsetup.check_env()
    if not info["ready"]:
        raise _ViewError("viewing data files needs the pywr environment "
                         "(h5 and xlsx are read with pandas) — set it up first",
                         409)
    out_path = os.path.join(tempfile.gettempdir(),
                            f"pywr_reader_view_{uuid.uuid4().hex[:8]}.json")
    cmd = [info["python"], os.path.join(APP_DIR, "pywr_reader", "dataview.py"),
           path, out_path]
    if key:
        cmd.append(key)
    if series:
        cmd.append("--series")
    if window:
        cmd += ["--start", str(int(window[0])), "--stop", str(int(window[1]))]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if not os.path.isfile(out_path):
            raise _ViewError(proc.stderr[-800:] or "could not read the file")
        with open(out_path, encoding="utf-8") as fh:
            result = json.load(fh)
    except subprocess.TimeoutExpired:
        raise _ViewError("timed out reading that file") from None
    finally:
        if os.path.isfile(out_path):
            with contextlib.suppress(OSError):
                os.remove(out_path)
    if not result.get("ok"):
        raise _ViewError(result.get("error") or "could not read the file")
    result["path"] = path
    return result


@bp.get("/api/data/preview")
def data_preview():
    """Look inside one of the model's data files — keys/sheets and a preview."""
    try:
        result = _run_dataview(request.args.get("path") or "",
                               request.args.get("key") or None)
    except _ViewError as exc:
        return err(exc, exc.code)
    return jsonify(result)


@bp.get("/api/data/series")
def data_series():
    """A data-file column for a plot, downsampled. With ?start=&stop= it reads
    just that row window (a zoomed-in view, at full resolution)."""
    window = None
    start, stop = request.args.get("start"), request.args.get("stop")
    if start is not None and stop is not None:
        try:
            window = (int(start), int(stop))
        except ValueError:
            return err("start and stop must be integers")
    try:
        result = _run_dataview(request.args.get("path") or "",
                               request.args.get("key") or None,
                               series=True, window=window)
    except _ViewError as exc:
        return err(exc, exc.code)
    return jsonify(result)


@bp.post("/api/data/dirs")
def data_add_dir():
    body = request.get_json(force=True)
    directory = (body.get("directory") or "").strip()
    remove = body.get("remove")
    try:
        WORKSPACE.require_model()
    except ValueError as exc:
        return err(exc)
    with WORKSPACE.lock:
        if remove:
            WORKSPACE.data_dirs = [d for d in WORKSPACE.data_dirs if d != remove]
        elif directory:
            if not os.path.isdir(directory):
                return err(f"not a directory: {directory}")
            if directory not in WORKSPACE.data_dirs:
                WORKSPACE.data_dirs.append(directory)
        WORKSPACE.resolve_data()
    return jsonify({"ok": True, **WORKSPACE.data_payload()})


# ---------------------------------------------------------------------------
# Trace image sidecar — a real image file + tiny geometry JSON next to the
# model. The pywr JSON itself is never touched.
#   <model>.pywrtrace.png   the traced map/schematic (actual image bytes)
#   <model>.pywrtrace.json  {image, x, y, scale, opacity, natW, natH, locked}


