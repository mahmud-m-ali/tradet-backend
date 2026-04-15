"""
cPanel Passenger WSGI entry point for TradEt backend.

cPanel setup:
  - Python version: 3.13
  - Application root: tradet-backend
  - Application URL: tradet.amber.et
  - Application startup file: passenger_wsgi.py
  - Application Entry point: application
"""
import sys
import os

# Add app directory to path
sys.path.insert(0, os.path.dirname(__file__))

# Production environment variables
# Set SECRET_KEY and JWT_SECRET_KEY in cPanel → Python App → Environment Variables
os.environ.setdefault("FLASK_ENV", "production")
os.environ.setdefault("SECRET_KEY", "CHANGE_ME_IN_CPANEL_ENV_VARS")
os.environ.setdefault("JWT_SECRET_KEY", "CHANGE_ME_IN_CPANEL_ENV_VARS")

# Database lives in home directory (persistent)
home = os.path.expanduser("~")
os.environ.setdefault("DATABASE_PATH", os.path.join(home, "tradet.db"))

from app import create_app
application = create_app()
