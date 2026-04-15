"""
cPanel Passenger WSGI entry point for TradEt backend.

cPanel setup used:
  - Python version: 3.13.11
  - Application root: tradet-backend
  - Application URL: https://tradet.amber.et/api
  - Application startup file: passenger_wsgi.py
  - Application Entry point: application

IMPORTANT — Why the prefix middleware is needed:
  When Application URL ends with /api, cPanel's Passenger strips the /api
  prefix from PATH_INFO before handing the request to Flask. But all Flask
  routes are defined as /api/..., so without the fix they would all 404.
  The _PrefixMiddleware below re-adds /api so Flask can match its routes.
"""
import sys
import os
import glob

# ── Activate the cPanel-created virtualenv ─────────────────────────────────
# cPanel stores virtualenvs at ~/virtualenv/<app_root>/<python_version>/
home = os.path.expanduser("~")
_pattern = os.path.join(home, "virtualenv", "tradet-backend", "3.13*", "bin", "python3")
_matches = glob.glob(_pattern)
INTERP = _matches[0] if _matches else sys.executable

if sys.executable != INTERP:
    os.execl(INTERP, INTERP, *sys.argv)

# ── Production environment variables ───────────────────────────────────────
# Set SECRET_KEY and JWT_SECRET_KEY in cPanel → Python App → Environment Variables
os.environ.setdefault("FLASK_ENV", "production")
os.environ.setdefault("SECRET_KEY", "CHANGE_ME_IN_CPANEL_ENV_VARS")
os.environ.setdefault("JWT_SECRET_KEY", "CHANGE_ME_IN_CPANEL_ENV_VARS")

# Database lives in the home directory (persistent across restarts)
os.environ.setdefault("DATABASE_PATH", os.path.join(home, "tradet.db"))

# ── Flask app ───────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from app import create_app
_flask_app = create_app()


# ── Prefix middleware ───────────────────────────────────────────────────────
# Passenger strips the /api mount prefix from PATH_INFO.
# This middleware puts it back so Flask routes (/api/health, etc.) match.
class _PrefixMiddleware:
    """Re-adds the /api prefix that Passenger strips when mounted at /api."""
    def __init__(self, app, prefix="/api"):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if not path.startswith(self.prefix):
            environ["PATH_INFO"] = self.prefix + path
        # Clear SCRIPT_NAME so url_for() generates correct URLs
        environ["SCRIPT_NAME"] = ""
        return self.app(environ, start_response)


application = _PrefixMiddleware(_flask_app)
