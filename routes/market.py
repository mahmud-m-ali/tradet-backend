"""Market data and asset routes."""

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required
from database import get_db, return_db
from utils.sharia_screening import screen_asset
from utils.trading_session import is_trading_open
from services.live_prices import SYMBOL_TO_YAHOO, COMMODITY_PROXIES

market_bp = Blueprint("market", __name__)

# Symbols that have real live data feeds
_LIVE_SYMBOLS = set(SYMBOL_TO_YAHOO.keys()) | {k for k, v in COMMODITY_PROXIES.items() if v}


@market_bp.route("/categories", methods=["GET"])
def get_categories():
    conn = get_db()
    categories = conn.execute(
        "SELECT * FROM asset_categories ORDER BY name"
    ).fetchall()
    return_db(conn)

    return jsonify([dict(c) for c in categories])


@market_bp.route("/assets", methods=["GET"])
def get_assets():
    """Get all tradeable assets with latest prices and Sharia status."""
    category_id = request.args.get("category_id")
    sharia_only = request.args.get("sharia_only", "false").lower() == "true"
    ecx_only = request.args.get("ecx_only", "false").lower() == "true"
    force_refresh = request.args.get("refresh", "false").lower() == "true"

    if force_refresh:
        from services.price_updater import update_prices_from_live

        try:
            update_prices_from_live(current_app._get_current_object())
        except Exception:
            current_app.logger.exception("Manual live price refresh failed")

    assets = _load_assets(category_id, sharia_only, ecx_only)
    sparklines = _load_sparklines(assets)

    from services.price_updater import get_recent_live_symbols
    live_updated = get_recent_live_symbols()

    result = []
    for a in assets:
        asset_dict = dict(a)
        # Add trading session status
        session_status = is_trading_open(
            a["trading_session_days"], a["trading_session_start"], a["trading_session_end"]
        )
        asset_dict["trading_session"] = session_status
        asset_dict["sparkline"] = sparklines.get(a["id"], [])

        # Data source indicator
        sym = a["symbol"]
        if sym in live_updated:
            asset_dict["data_source"] = "live"
            asset_dict["data_source_label"] = "Live (yfinance)"
        elif sym in _LIVE_SYMBOLS:
            asset_dict["data_source"] = "live_pending"
            asset_dict["data_source_label"] = "Live feed available (waiting for update)"
        else:
            asset_dict["data_source"] = "simulated"
            asset_dict["data_source_label"] = "Simulated / Manual entry"

        # Compute compliance_level: halal > permissible > non_compliant
        if asset_dict.get("is_haram", 0):
            asset_dict["compliance_level"] = "non_compliant"
        elif asset_dict.get("is_sharia_compliant", 1):
            asset_dict["compliance_level"] = "halal"
        else:
            asset_dict["compliance_level"] = "permissible"

        # Add Sharia screening details
        if a["category_type"] == "equity":
            screening = screen_asset(
                a["debt_ratio"], a["non_compliant_investment_ratio"], a["non_permissible_revenue_ratio"]
            )
            asset_dict["sharia_screening"] = screening

        result.append(asset_dict)

    return jsonify(result)


def _load_assets(category_id, sharia_only, ecx_only):
    conn = get_db()
    query = """
        SELECT a.*, ac.name as category_name, ac.category_type,
               mp.price, mp.bid_price, mp.ask_price, mp.high_24h, mp.low_24h,
               mp.volume_24h, mp.change_24h, mp.recorded_at as price_updated_at
        FROM assets a
        JOIN asset_categories ac ON a.category_id = ac.id
        LEFT JOIN market_prices mp ON mp.asset_id = a.id
            AND mp.id = (SELECT MAX(id) FROM market_prices WHERE asset_id = a.id)
        WHERE a.is_active = 1
    """
    params = []

    if category_id:
        query += " AND a.category_id = ?"
        params.append(int(category_id))
    if sharia_only:
        query += " AND a.is_sharia_compliant = 1"
    if ecx_only:
        query += " AND a.is_ecx_listed = 1"

    query += " ORDER BY a.symbol"
    assets = conn.execute(query, params).fetchall()
    return_db(conn)
    return assets


def _load_sparklines(assets):
    sparkline_conn = get_db()
    sparklines = {}
    for a in assets:
        prices_rows = sparkline_conn.execute(
            "SELECT price FROM market_prices WHERE asset_id = ? ORDER BY id DESC LIMIT 15",
            (a["id"],),
        ).fetchall()
        sparklines[a["id"]] = [r["price"] for r in reversed(prices_rows)]
    return_db(sparkline_conn)
    return sparklines


@market_bp.route("/assets/<int:asset_id>", methods=["GET"])
def get_asset_detail(asset_id):
    conn = get_db()
    asset = conn.execute(
        """SELECT a.*, ac.name as category_name, ac.category_type
           FROM assets a JOIN asset_categories ac ON a.category_id = ac.id
           WHERE a.id = ?""",
        (asset_id,),
    ).fetchone()

    if not asset:
        return_db(conn)
        return jsonify({"error": "Asset not found"}), 404

    # Get price history (last 30 entries)
    prices = conn.execute(
        """SELECT price, bid_price, ask_price, high_24h, low_24h, volume_24h, change_24h, recorded_at
           FROM market_prices WHERE asset_id = ? ORDER BY id DESC LIMIT 30""",
        (asset_id,),
    ).fetchall()
    return_db(conn)

    result = dict(asset)
    result["price_history"] = [dict(p) for p in prices]
    result["trading_session"] = is_trading_open(
        asset["trading_session_days"], asset["trading_session_start"], asset["trading_session_end"]
    )

    if asset["category_type"] == "equity":
        result["sharia_screening"] = screen_asset(
            asset["debt_ratio"], asset["non_compliant_investment_ratio"], asset["non_permissible_revenue_ratio"]
        )

    return jsonify(result)


@market_bp.route("/assets/<int:asset_id>/prices", methods=["GET"])
def get_price_history(asset_id):
    limit = request.args.get("limit", 100, type=int)
    conn = get_db()
    prices = conn.execute(
        "SELECT * FROM market_prices WHERE asset_id = ? ORDER BY id DESC LIMIT ?",
        (asset_id, limit),
    ).fetchall()
    return_db(conn)
    return jsonify([dict(p) for p in prices])
