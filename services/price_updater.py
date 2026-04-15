"""Background price updater — runs periodically to refresh market data."""

import threading
import time
import random
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_updater_thread = None
_running = False

# Track which symbols got live data successfully (symbol -> timestamp)
_last_live_success = {}

# Update intervals (seconds)
LIVE_UPDATE_INTERVAL = 300     # 5 min for yfinance data
ECX_UPDATE_INTERVAL = 900      # 15 min for ECX scraping
EXCHANGE_RATE_INTERVAL = 3600  # 1 hour for NBE rates


def simulate_price_movement(current_price, volatility=0.02):
    """Simulate realistic price movement for assets without live feeds."""
    change = random.gauss(0, volatility)
    change = max(-0.05, min(0.05, change))  # Cap at +/-5%
    new_price = current_price * (1 + change)
    return round(max(new_price, 0.01), 2)


def update_prices_from_live(app):
    """Fetch live prices and update database."""
    from services.live_prices import fetch_live_prices
    from database import get_db, return_db

    try:
        live_data = fetch_live_prices()
        if not live_data:
            return 0

        with app.app_context():
            conn = get_db()
            updated = 0
            for symbol, price_data in live_data.items():
                asset = conn.execute(
                    "SELECT id FROM assets WHERE symbol = ?", (symbol,)
                ).fetchone()
                if asset:
                    conn.execute(
                        """INSERT INTO market_prices
                           (asset_id, price, bid_price, ask_price, high_24h, low_24h, volume_24h, change_24h)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (asset["id"], price_data["price"], price_data["bid_price"],
                         price_data["ask_price"], price_data["high_24h"], price_data["low_24h"],
                         price_data["volume_24h"], price_data["change_24h"]),
                    )
                    updated_at = price_data.get("updated_at") or datetime.utcnow().isoformat()
                    _last_live_success[symbol] = updated_at
                    conn.execute(
                        """INSERT INTO live_update_status (symbol, updated_at)
                           VALUES (?, ?)
                           ON CONFLICT(symbol) DO UPDATE SET updated_at = excluded.updated_at""",
                        (symbol, updated_at),
                    )
                    updated += 1
            conn.commit()
            return_db(conn)
            logger.info(f"Updated {updated} live prices")
            return updated
    except Exception as e:
        logger.error(f"Live price update failed: {e}")
        return 0


def update_simulated_prices(app):
    """Simulate price movements for assets without live feeds (ECX commodities, local equities)."""
    from database import get_db, return_db
    from services.live_prices import SYMBOL_TO_YAHOO, COMMODITY_PROXIES

    live_symbols = set(SYMBOL_TO_YAHOO.keys()) | {k for k, v in COMMODITY_PROXIES.items() if v}

    try:
        with app.app_context():
            conn = get_db()
            assets = conn.execute(
                """SELECT a.id, a.symbol, mp.price, mp.bid_price, mp.ask_price,
                          mp.high_24h, mp.low_24h, mp.volume_24h
                   FROM assets a
                   LEFT JOIN market_prices mp ON mp.asset_id = a.id
                       AND mp.id = (SELECT MAX(id) FROM market_prices WHERE asset_id = a.id)
                   WHERE a.is_active = 1"""
            ).fetchall()

            updated = 0
            for asset in assets:
                if asset["symbol"] in live_symbols:
                    continue  # Skip live-feed assets

                current_price = asset["price"] or 100.0
                new_price = simulate_price_movement(current_price)
                change = round((new_price - current_price) / current_price * 100, 2)
                high = round(max(new_price, asset["high_24h"] or new_price), 2)
                low = round(min(new_price, asset["low_24h"] or new_price), 2)
                volume = (asset["volume_24h"] or 0) + random.randint(10, 500)

                conn.execute(
                    """INSERT INTO market_prices
                       (asset_id, price, bid_price, ask_price, high_24h, low_24h, volume_24h, change_24h)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (asset["id"], new_price, round(new_price * 0.999, 2),
                     round(new_price * 1.001, 2), high, low, volume, change),
                )
                updated += 1

            conn.commit()
            return_db(conn)
            logger.info(f"Simulated {updated} price updates")
    except Exception as e:
        logger.error(f"Simulated price update failed: {e}")


def update_exchange_rates(app):
    """Fetch and cache exchange rates."""
    from services.live_prices import fetch_exchange_rates
    try:
        rates = fetch_exchange_rates()
        logger.info(f"Updated exchange rates: {list(rates.keys())}")
        return rates
    except Exception as e:
        logger.error(f"Exchange rate update failed: {e}")
        return {}


def get_recent_live_symbols(max_age_seconds=None):
    """Return symbols refreshed from a live feed within the freshness window."""
    from database import get_db, return_db

    freshness = max_age_seconds or (LIVE_UPDATE_INTERVAL * 3)
    cutoff = datetime.utcnow() - timedelta(seconds=freshness)
    recent = {
        symbol
        for symbol, updated_at in _last_live_success.items()
        if _parse_timestamp(updated_at) >= cutoff
    }

    conn = None
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT symbol, updated_at FROM live_update_status"
        ).fetchall()
        for row in rows:
            if _parse_timestamp(row["updated_at"]) >= cutoff:
                recent.add(row["symbol"])
    except Exception as e:
        logger.debug(f"Unable to load persisted live status: {e}")
    finally:
        if conn is not None:
            return_db(conn)

    return recent


def _parse_timestamp(value):
    """Parse timestamps emitted by SQLite or Python code."""
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.min

    normalized = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return datetime.min


def _background_updater(app):
    """Main background loop."""
    global _running
    last_live = 0
    last_exchange = 0

    while _running:
        now = time.time()

        # Update live prices
        if now - last_live >= LIVE_UPDATE_INTERVAL:
            update_prices_from_live(app)
            update_simulated_prices(app)
            last_live = now

        # Update exchange rates
        if now - last_exchange >= EXCHANGE_RATE_INTERVAL:
            update_exchange_rates(app)
            last_exchange = now

        time.sleep(60)  # Check every minute


def start_price_updater(app):
    """Start the background price updater thread."""
    global _updater_thread, _running

    if _updater_thread and _updater_thread.is_alive():
        return

    _running = True
    _updater_thread = threading.Thread(target=_background_updater, args=(app,), daemon=True)
    _updater_thread.start()
    logger.info("Background price updater started")

    # Do an initial update
    threading.Thread(target=update_prices_from_live, args=(app,), daemon=True).start()
    threading.Thread(target=update_exchange_rates, args=(app,), daemon=True).start()


def stop_price_updater():
    """Stop the background updater."""
    global _running
    _running = False
