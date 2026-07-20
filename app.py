"""PyWR Reader — local web app.

Run:  python app.py   →  http://127.0.0.1:5321

This is the thin entry point: it builds the Flask app and registers the API
blueprints (pywr_reader/api/*). The open model and the runs live in a single
session object (pywr_reader/session); each blueprint reads and mutates it.
"""

import os

from flask import Flask

from pywr_reader.api import register_blueprints
from pywr_reader.api.util import APP_DIR

# re-exported so the tests (and any script) have one import for the session
from pywr_reader.session import RUNS, WORKSPACE  # noqa: F401

app = Flask(__name__, static_folder=os.path.join(APP_DIR, "static"),
            static_url_path="/static")
app.json.sort_keys = False  # keep pywr model key order in API responses
register_blueprints(app)


if __name__ == "__main__":
    port = int(os.environ.get("PYWR_READER_PORT", "5321"))
    print(f"PyWR Reader → http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
