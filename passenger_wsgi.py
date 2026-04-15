"""
cPanel Passenger WSGI entry point for TradEt backend.

Setup in cPanel:
  1. Go to "Setup Python App"
  2. Python version: 3.13 (or latest available)
  3. Application root: tradet-backend   (the folder you uploaded)
  4. Application URL: your domain or subdomain (e.g. api.yourdomain.com)
  5. Application startup file: passenger_wsgi.py
  6. Application Entry point: application
  7. Click "Create" → then "Run Pip Install" to install requirements.txt
"""
import sys
import os

# Add the app directory to sys.path
INTERP = os.path.join(os.environ.get("HOME", ""), "virtualenv", "tradet-backend", "3.13", "bin", "python3")
if sys.executable != INTERP:
    os.execl(INTERP, INTERP, *sys.argv)

# Set environment variables for production
os.environ.setdefault("FLASK_ENV", "production")
os.environ.setdefault("SECRET_KEY", os.environ.get("SECRET_KEY", "change-me-in-cpanel-env-vars"))
os.environ.setdefault("JWT_SECRET_KEY", os.environ.get("JWT_SECRET_KEY", "change-me-in-cpanel-env-vars"))

# Point DB to a writable location in the home directory
home = os.environ.get("HOME", "")
os.environ.setdefault("DATABASE_PATH", os.path.join(home, "tradet.db"))

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
application = create_app()
