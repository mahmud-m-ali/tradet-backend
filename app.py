"""TradEt Backend API — Sharia & Ethiopian Trade Compliant."""

import os
import logging
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import Config
from database import init_db, seed_data, get_db, return_db
from routes.auth import auth_bp
from routes.market import market_bp
from routes.trading import trading_bp
from routes.portfolio import portfolio_bp
from routes.alerts import alerts_bp
from routes.extras import extras_bp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# Path to Flutter web build — served from ~/tradet-backend-app/web/ on the server
WEB_BUILD_DIR = os.path.join(os.path.dirname(__file__), 'web')
_static_folder = WEB_BUILD_DIR if os.path.isdir(WEB_BUILD_DIR) else None


def create_app():
    app = Flask(__name__, static_folder=_static_folder, static_url_path='')
    app.config.from_object(Config)

    # Enable CORS for Flutter web/mobile
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # JWT for authentication
    JWTManager(app)

    # Rate limiting
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per hour", "50 per minute"],
        storage_uri="memory://",
        enabled=os.getenv('RATELIMIT_ENABLED', 'true').lower() != 'false',
    )
    # Stricter limits for auth endpoints
    limiter.limit("10 per minute")(auth_bp)
    # Relaxed limits for market data (read-heavy)
    limiter.limit("300 per hour")(market_bp)

    # Request logging
    @app.before_request
    def log_request():
        if request.path.startswith('/api/'):
            app.logger.debug(f"{request.method} {request.path} from {request.remote_addr}")

    # Global error handlers
    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429

    @app.errorhandler(500)
    def internal_error(e):
        app.logger.error(f"Internal server error: {e}")
        return jsonify({"error": "Internal server error. Please try again."}), 500

    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(market_bp, url_prefix="/api/market")
    app.register_blueprint(trading_bp, url_prefix="/api/trading")
    app.register_blueprint(portfolio_bp, url_prefix="/api")
    app.register_blueprint(alerts_bp, url_prefix="/api")
    app.register_blueprint(extras_bp, url_prefix="/api")

    # Health check
    @app.route("/api/health")
    def health():
        db_ok = False
        try:
            conn = get_db()
            conn.execute("SELECT 1")
            return_db(conn)
            db_ok = True
        except Exception:
            pass
        return jsonify({
            "status": "healthy" if db_ok else "degraded",
            "app": "TradEt API",
            "version": "3.1.0",
            "database": "ok" if db_ok else "error",
            "compliance": ["sharia", "ethiopian_trade_law", "ecx", "nbe"],
        }), 200 if db_ok else 503

    # Sharia compliance info endpoint
    @app.route("/api/compliance")
    def compliance_info():
        return jsonify({
            "sharia": {
                "standard": "AAOIFI",
                "max_debt_ratio": Config.SHARIA_DEBT_THRESHOLD,
                "max_investment_ratio": Config.SHARIA_INVESTMENT_THRESHOLD,
                "max_revenue_ratio": Config.SHARIA_REVENUE_THRESHOLD,
                "prohibited": [
                    "Interest (Riba)",
                    "Excessive uncertainty (Gharar)",
                    "Gambling (Maisir)",
                    "Short selling",
                    "Futures/Options",
                    "Haram products (alcohol, tobacco, pork, weapons)",
                ],
                "fee_model": "Flat commission (no interest)",
            },
            "ethiopian": {
                "exchange": "Ethiopia Commodity Exchange (ECX)",
                "regulator": "Ethiopia Commodity Exchange Authority (ECEA)",
                "central_bank": "National Bank of Ethiopia (NBE)",
                "currency": "Ethiopian Birr (ETB)",
                "kyc_required": True,
                "data_residency": "Ethiopia",
            },
        })

    # Serve Flutter web app
    @app.route('/')
    def serve_web():
        return send_from_directory(WEB_BUILD_DIR, 'index.html')

    @app.errorhandler(404)
    def not_found(e):
        # For SPA routing: serve index.html for non-API routes
        if not e.description or '/api/' not in str(e):
            try:
                return send_from_directory(WEB_BUILD_DIR, 'index.html')
            except Exception:
                pass
        return jsonify({"error": "Not found"}), 404

    # Initialize database
    with app.app_context():
        init_db()
        seed_data()

    # Start background price updater (disabled on PythonAnywhere via env var)
    if os.getenv('DISABLE_PRICE_UPDATER', '').lower() != 'true':
        try:
            from services.price_updater import start_price_updater
            start_price_updater(app)
        except Exception as e:
            logging.warning(f"Price updater not started: {e}")
    else:
        logging.info("Price updater disabled via DISABLE_PRICE_UPDATER env var")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=8000)
