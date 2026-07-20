"""The one-click pywr environment: status and setup."""


from flask import Blueprint, jsonify

from pywr_reader import envsetup

bp = Blueprint("env", __name__)


@bp.get("/api/env")
def env_status():
    info = envsetup.check_env()
    info["ok"] = True
    info["log"] = (envsetup.read_log(60)
                   if info["setting_up"] or not info["ready"] else [])
    return jsonify(info)


@bp.post("/api/env/setup")
def env_setup():
    started = envsetup.start_setup()
    return jsonify({"ok": True, "started": started})


# ---------------------------------------------------------------------------
# External data files
# ---------------------------------------------------------------------------
