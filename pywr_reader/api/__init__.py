"""API blueprints for PyWR Reader."""

from pywr_reader.api import datafiles, edit, env, files, runs, traceimg

_BLUEPRINTS = (files.bp, edit.bp, env.bp, datafiles.bp, traceimg.bp, runs.bp)


def register_blueprints(app):
    for bp in _BLUEPRINTS:
        app.register_blueprint(bp)
