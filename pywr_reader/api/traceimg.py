"""The trace-over image sidecar (draw a network over a map)."""

import base64
import binascii
import json
import os

from flask import Blueprint, jsonify, request

from pywr_reader.api.util import err
from pywr_reader.session import WORKSPACE

bp = Blueprint("traceimg", __name__)

_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
             "image/gif": "gif", "image/webp": "webp", "image/bmp": "bmp",
             "image/svg+xml": "svg"}
_EXT_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
             "svg": "image/svg+xml"}


def _trace_geom_path():
    p = WORKSPACE.path
    return os.path.splitext(p)[0] + ".pywrtrace.json" if p else None


def _trace_image_glob():
    """All possible sidecar image paths (any extension) for this model."""
    stem = os.path.splitext(WORKSPACE.path)[0] if WORKSPACE.path else None
    return [f"{stem}.pywrtrace.{ext}" for ext in _EXT_MIME] if stem else []


def _decode_data_url(src):
    """('image/png', b'...') from a data URL, or (None, None)."""
    if not isinstance(src, str) or not src.startswith("data:"):
        return None, None
    try:
        header, b64 = src.split(",", 1)
        mime = header[5:].split(";")[0].lower()
        return mime, base64.b64decode(b64)
    except (ValueError, binascii.Error):
        return None, None


@bp.get("/api/traceimage")
def trace_get():
    try:
        WORKSPACE.require_model()
    except ValueError as exc:
        return err(exc)
    gp = _trace_geom_path()
    if gp and os.path.isfile(gp):
        try:
            with open(gp, encoding="utf-8") as fh:
                geom = json.load(fh)
            # reconstruct a data URL from the real image file for the browser
            img_path = os.path.join(os.path.dirname(gp), geom.get("image", ""))
            if geom.get("image") and os.path.isfile(img_path):
                ext = os.path.splitext(img_path)[1].lstrip(".").lower()
                with open(img_path, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("ascii")
                geom["src"] = f"data:{_EXT_MIME.get(ext, 'image/png')};base64,{b64}"
                return jsonify({"ok": True, "trace": geom,
                                "path": gp, "image": img_path})
        except (OSError, ValueError) as exc:
            return err(f"could not read trace file: {exc}")
    return jsonify({"ok": True, "trace": None, "path": gp})


@bp.post("/api/traceimage")
def trace_save():
    body = request.get_json(force=True)
    try:
        WORKSPACE.require_model()
    except ValueError as exc:
        return err(exc)
    gp = _trace_geom_path()
    if not gp:
        return err("save the model first — the trace file is stored beside it",
                    409)
    trace = body.get("trace")
    try:
        if trace is None:                       # remove image + geometry
            for path in [gp] + _trace_image_glob():
                if os.path.isfile(path):
                    os.remove(path)
            return jsonify({"ok": True, "path": None})

        geom = {k: v for k, v in trace.items() if k != "src"}
        mime, data = _decode_data_url(trace.get("src"))
        if data is not None:                    # new/updated image → write it
            ext = _MIME_EXT.get(mime, "png")
            img_path = os.path.splitext(WORKSPACE.path)[0] + f".pywrtrace.{ext}"
            for stale in _trace_image_glob():   # drop an old image of another type
                if stale != img_path and os.path.isfile(stale):
                    os.remove(stale)
            with open(img_path, "wb") as fh:
                fh.write(data)
            geom["image"] = os.path.basename(img_path)
        elif os.path.isfile(gp):                # geometry-only update
            with open(gp, encoding="utf-8") as fh:
                geom["image"] = json.load(fh).get("image")
        else:
            return err("no image data provided for the trace")

        with open(gp, "w", encoding="utf-8") as fh:
            json.dump(geom, fh, indent=1)
        img = os.path.join(os.path.dirname(gp), geom.get("image") or "")
        return jsonify({"ok": True, "path": gp,
                        "image": img if geom.get("image") else None})
    except OSError as exc:
        return err(exc)


# ---------------------------------------------------------------------------
# Running models
# ---------------------------------------------------------------------------
