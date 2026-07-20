"""Shared helpers for the API blueprints."""

import os

from flask import jsonify

# The project root (where app.py, static/ and the pywr_reader package live).
# api/util.py → api → pywr_reader → project root.
APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def err(msg, code=400):
    """A JSON error response the frontend can show verbatim."""
    return jsonify({"ok": False, "error": str(msg)}), code
