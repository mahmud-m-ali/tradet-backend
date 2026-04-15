"""Extra routes — news, Zakat, exchange rates, currency converter, admin data entry."""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from database import get_db, return_db

extras_bp = Blueprint("extras", __name__)


# ═══════════════════════════════════════════
# NEWS FEED
# ═══════════════════════════════════════════

@extras_bp.route("/news", methods=["GET"])
def get_news():
    """Get financial news from free RSS feeds."""
    from services.news_service import fetch_news
    category = request.args.get("category")  # global, ethiopia, islamic
    limit = request.args.get("limit", 30, type=int)
    articles = fetch_news(category=category, limit=limit)
    return jsonify({"articles": articles, "count": len(articles)})


# ═══════════════════════════════════════════
# ZAKAT CALCULATOR
# ═══════════════════════════════════════════

@extras_bp.route("/zakat/calculate", methods=["POST"])
@jwt_required()
def calculate_zakat():
    """Calculate Zakat on portfolio + additional wealth."""
    from services.zakat_calculator import calculate_zakat as calc

    user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    # Auto-fetch portfolio value and wallet balance
    conn = get_db()
    holdings = conn.execute(
        """SELECT p.quantity, mp.price
           FROM portfolios p
           LEFT JOIN market_prices mp ON mp.asset_id = p.asset_id
               AND mp.id = (SELECT MAX(id) FROM market_prices WHERE asset_id = p.asset_id)
           WHERE p.user_id = ?""",
        (user_id,),
    ).fetchall()
    portfolio_value = sum(h["quantity"] * (h["price"] or 0) for h in holdings)

    wallet = conn.execute("SELECT balance FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    cash_balance = wallet["balance"] if wallet else 0
    return_db(conn)

    result = calc(
        portfolio_value=portfolio_value,
        cash_balance=cash_balance,
        other_savings=float(data.get("other_savings", 0)),
        gold_value=float(data.get("gold_value", 0)),
        silver_value=float(data.get("silver_value", 0)),
        debts=float(data.get("debts", 0)),
        expenses=float(data.get("expenses", 0)),
        nisab_method=data.get("nisab_method", "gold"),
    )
    return jsonify(result)


@extras_bp.route("/zakat/nisab", methods=["GET"])
def get_nisab():
    """Get current Nisab thresholds."""
    from services.zakat_calculator import get_nisab_threshold, GOLD_GRAMS_NISAB, SILVER_GRAMS_NISAB
    return jsonify({
        "gold": {
            "grams": GOLD_GRAMS_NISAB,
            "threshold_etb": get_nisab_threshold("gold"),
        },
        "silver": {
            "grams": SILVER_GRAMS_NISAB,
            "threshold_etb": get_nisab_threshold("silver"),
        },
        "currency": "ETB",
    })


# ═══════════════════════════════════════════
# EXCHANGE RATES
# ═══════════════════════════════════════════

@extras_bp.route("/exchange-rates", methods=["GET"])
def get_exchange_rates():
    """Get current exchange rates (ETB base)."""
    from services.live_prices import fetch_exchange_rates, get_etb_usd_rate
    rates = fetch_exchange_rates()
    return jsonify({
        "base_currency": "ETB",
        "rates": rates,
        "etb_usd": get_etb_usd_rate(),
    })


@extras_bp.route("/convert", methods=["GET"])
def convert_currency():
    """Convert between currencies. Params: amount, from, to."""
    from services.live_prices import fetch_exchange_rates
    amount = request.args.get("amount", 1.0, type=float)
    from_curr = request.args.get("from", "ETB").upper()
    to_curr = request.args.get("to", "USD").upper()

    rates = fetch_exchange_rates()

    # All rates are ETB-based (how much ETB per 1 unit of foreign currency)
    if from_curr == "ETB" and to_curr in rates:
        converted = amount / rates[to_curr]["mid"]
    elif to_curr == "ETB" and from_curr in rates:
        converted = amount * rates[from_curr]["mid"]
    elif from_curr in rates and to_curr in rates:
        # Cross rate via ETB
        etb_amount = amount * rates[from_curr]["mid"]
        converted = etb_amount / rates[to_curr]["mid"]
    elif from_curr == to_curr:
        converted = amount
    else:
        return jsonify({"error": f"Unsupported currency pair: {from_curr}/{to_curr}"}), 400

    return jsonify({
        "from": from_curr,
        "to": to_curr,
        "amount": amount,
        "converted": round(converted, 4),
        "rate": round(converted / amount, 6) if amount else 0,
    })


# ═══════════════════════════════════════════
# LIVE PRICES STATUS
# ═══════════════════════════════════════════

@extras_bp.route("/market/live-status", methods=["GET"])
def live_status():
    """Check which assets have live price feeds vs simulated."""
    from services.live_prices import SYMBOL_TO_YAHOO, COMMODITY_PROXIES
    from services.price_updater import _last_live_success
    live_symbols = list(SYMBOL_TO_YAHOO.keys())
    commodity_live = [k for k, v in COMMODITY_PROXIES.items() if v]
    commodity_simulated = [k for k, v in COMMODITY_PROXIES.items() if not v]

    return jsonify({
        "live_global_equities": live_symbols,
        "live_commodities": commodity_live,
        "simulated_commodities": commodity_simulated,
        "actually_live_now": _last_live_success,
        "live_count": len(_last_live_success),
        "total_live_capable": len(live_symbols) + len(commodity_live),
        "note": "Global equities use yfinance (live). ECX commodities without Yahoo tickers use simulated prices. "
                "'actually_live_now' shows symbols that have received at least one real price update this session.",
    })


@extras_bp.route("/market/history/<symbol>", methods=["GET"])
def get_chart_history(symbol):
    """Get OHLCV price history for charting (candlestick data)."""
    from services.live_prices import get_price_history
    period = request.args.get("period", "1mo")  # 1d, 5d, 1mo, 3mo, 6mo, 1y
    interval = request.args.get("interval", "1d")  # 1m, 5m, 15m, 1h, 1d

    history = get_price_history(symbol, period=period, interval=interval)
    if not history:
        return jsonify({"error": "No history available for this symbol", "data": []}), 200

    return jsonify({"symbol": symbol, "period": period, "interval": interval, "data": history})


# ═══════════════════════════════════════════
# ADMIN: MANUAL DATA ENTRY (ESX/ECX)
# ═══════════════════════════════════════════

@extras_bp.route("/admin/prices", methods=["POST"])
@jwt_required()
def admin_update_price():
    """
    Admin endpoint to manually enter ESX/ECX prices.
    Useful until official APIs are available.
    """
    user_id = int(get_jwt_identity())
    data = request.get_json()

    # Verify admin (for demo, check if user is first registered user)
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE id = ? AND id <= 3", (user_id,)).fetchone()
    if not user:
        return_db(conn)
        return jsonify({"error": "Admin access required"}), 403

    required = ["asset_id", "price"]
    for field in required:
        if field not in data:
            return_db(conn)
            return jsonify({"error": f"Missing field: {field}"}), 400

    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (data["asset_id"],)).fetchone()
    if not asset:
        return_db(conn)
        return jsonify({"error": "Asset not found"}), 404

    price = float(data["price"])
    bid = float(data.get("bid_price", price * 0.999))
    ask = float(data.get("ask_price", price * 1.001))
    high = float(data.get("high_24h", price * 1.01))
    low = float(data.get("low_24h", price * 0.99))
    volume = int(data.get("volume_24h", 0))

    # Calculate change from previous price
    prev = conn.execute(
        "SELECT price FROM market_prices WHERE asset_id = ? ORDER BY id DESC LIMIT 1",
        (data["asset_id"],),
    ).fetchone()
    prev_price = prev["price"] if prev else price
    change = round((price - prev_price) / prev_price * 100, 2) if prev_price else 0

    conn.execute(
        """INSERT INTO market_prices
           (asset_id, price, bid_price, ask_price, high_24h, low_24h, volume_24h, change_24h)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["asset_id"], price, bid, ask, high, low, volume, change),
    )
    conn.execute(
        "INSERT INTO audit_log (user_id, action, entity_type, entity_id, details) VALUES (?,?,?,?,?)",
        (user_id, "admin_price_update", "asset", data["asset_id"],
         f"Manual price update: {asset['symbol']} = {price} ETB"),
    )
    conn.commit()
    return_db(conn)

    return jsonify({
        "message": f"Price updated for {asset['symbol']}",
        "price": price,
        "change": change,
    })


@extras_bp.route("/admin/prices/bulk", methods=["POST"])
@jwt_required()
def admin_bulk_update():
    """Bulk update prices for multiple assets at once."""
    user_id = int(get_jwt_identity())

    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE id = ? AND id <= 3", (user_id,)).fetchone()
    if not user:
        return_db(conn)
        return jsonify({"error": "Admin access required"}), 403

    data = request.get_json()
    updates = data.get("prices", [])
    if not updates:
        return_db(conn)
        return jsonify({"error": "No price updates provided"}), 400

    updated = 0
    for item in updates:
        asset_id = item.get("asset_id")
        price = item.get("price")
        if not asset_id or not price:
            continue

        asset = conn.execute("SELECT id, symbol FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if not asset:
            continue

        price = float(price)
        conn.execute(
            """INSERT INTO market_prices
               (asset_id, price, bid_price, ask_price, high_24h, low_24h, volume_24h, change_24h)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset_id, price, price * 0.999, price * 1.001,
             price * 1.01, price * 0.99, item.get("volume", 0), item.get("change", 0)),
        )
        updated += 1

    conn.commit()
    return_db(conn)

    return jsonify({"message": f"Updated {updated} prices", "count": updated})
