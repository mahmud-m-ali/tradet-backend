"""
PythonAnywhere WSGI Configuration for TradEt.

Copy this file's contents to your PythonAnywhere WSGI config file:
/var/www/atomahmud_pythonanywhere_com_wsgi.py
"""
import sys
import os

# Add project to path
project_home = '/home/atomahmud/TradEt/backend'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Set environment variables
os.environ['DATABASE_PATH'] = '/home/atomahmud/TradEt/backend/tradet.db'
os.environ['SECRET_KEY'] = 'tradet-prod-secret-key-2026'
os.environ['JWT_SECRET_KEY'] = 'tradet-jwt-secret-key-2026'
os.environ['RATELIMIT_ENABLED'] = 'false'
os.environ['DISABLE_PRICE_UPDATER'] = 'true'  # Avoid network timeouts on PythonAnywhere

# Import Flask app
from app import create_app
application = create_app()
