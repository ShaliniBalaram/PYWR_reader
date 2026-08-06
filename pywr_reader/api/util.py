"""Shared helpers for the API blueprints."""

import os
import sys

from flask import jsonify

# Where static/ and the helper scripts live. Normally the project root
# (api/util.py → api → pywr_reader → root); in a packaged build it's the
# folder PyInstaller unpacks the bundled data into.
APP_DIR = (sys._MEIPASS if getattr(sys, "frozen", False)
           else os.path.dirname(os.path.dirname(os.path.dirname(
               os.path.abspath(__file__)))))


def err(msg, code=400):
    """A JSON error response the frontend can show verbatim."""
    return jsonify({"ok": False, "error": str(msg)}), code
