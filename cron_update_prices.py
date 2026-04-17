#!/usr/bin/env python3
"""
Scheduled task — update market prices and exchange rates.

Run via cron or system scheduler:
  Command: /path/to/.venv/bin/python /path/to/backend/cron_update_prices.py
  Schedule: Hourly or every 5 minutes

This script runs outside Flask, directly updating the SQLite database.
"""

import os
import sys
import logging
import random
from datetime import datetime

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DATABASE_PATH', os.path.join(os.path.dirname(__file__), 'tradet.db'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def get_connection():
    """Get a direct SQLite connection (no Flask context needed)."""
    import sqlite3
    db_path = os.environ.get('DATABASE_PATH', 'tradet.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def update_exchange_rates(conn):
    """Fetch live exchange rates from open.er-api.com and cache in DB."""
    try:
        import requests
    except ImportError:
        logger.error("requests not installed")
        return

    try:
        resp = requests.get("https://open.er-api.com/v6/latest/USD", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("result") == "success":
                etb = data["rates"].get("ETB", 155.5)
                logger.info(f"Exchange rates fetched: USD/ETB = {etb:.2f}")

                # Store in a simple key-value table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS app_cache (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                import json
                conn.execute(
                    "INSERT OR REPLACE INTO app_cache (key, value, updated_at) VALUES (?, ?, ?)",
                    ("exchange_rates", json.dumps(data["rates"]), datetime.utcnow().isoformat())
                )
                conn.execute(
                    "INSERT OR REPLACE INTO app_cache (key, value, updated_at) VALUES (?, ?, ?)",
                    ("etb_usd_rate", str(etb), datetime.utcnow().isoformat())
                )
                conn.commit()
                return etb
    except Exception as e:
        logger.error(f"Exchange rate fetch failed: {e}")
    return 155.5


def update_live_prices(conn, etb_rate):
    """Fetch live prices from yfinance and update the database."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed, skipping live prices")
        return 0

    from services.live_prices import SYMBOL_TO_YAHOO, COMMODITY_PROXIES

    all_tickers = {}
    for symbol, ticker in SYMBOL_TO_YAHOO.items():
        if ticker:
            all_tickers[symbol] = ticker
    for symbol, ticker in COMMODITY_PROXIES.items():
        if ticker:
            all_tickers[symbol] = ticker

    if not all_tickers:
        return 0

    ticker_str = " ".join(set(all_tickers.values()))
    updated = 0

    try:
        data = yf.Tickers(ticker_str)
        for halalet_symbol, yahoo_ticker in all_tickers.items():
            try:
                info = data.tickers[yahoo_ticker].fast_info
                price_usd = float(info.last_price) if info.last_price else None
                if price_usd is None:
                    continue

                is_sar = yahoo_ticker.endswith(".SR")
                if is_sar:
                    price_etb = price_usd * (etb_rate / 3.75)
                elif yahoo_ticker.endswith(".NR") or yahoo_ticker.endswith(".LA"):
                    price_etb = price_usd * etb_rate * 0.008
                else:
                    price_etb = price_usd * etb_rate

                prev_close = float(info.previous_close) if info.previous_close else price_usd
                change_pct = ((price_usd - prev_close) / prev_close * 100) if prev_close else 0

                asset = conn.execute(
                    "SELECT id FROM assets WHERE symbol = ?", (halalet_symbol,)
                ).fetchone()
                if asset:
                    conn.execute(
                        """INSERT INTO market_prices
                           (asset_id, price, bid_price, ask_price, high_24h, low_24h, volume_24h, change_24h)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (asset["id"], round(price_etb, 2),
                         round(price_etb * 0.999, 2), round(price_etb * 1.001, 2),
                         round(price_etb * 1.01, 2), round(price_etb * 0.99, 2),
                         int(info.last_volume) if info.last_volume else 0,
                         round(change_pct, 2)),
                    )
                    updated += 1
            except Exception as e:
                logger.debug(f"Failed {halalet_symbol}: {e}")
                continue

        conn.commit()
    except Exception as e:
        logger.error(f"yfinance batch fetch failed: {e}")

    return updated


def simulate_local_prices(conn):
    """Simulate price movements for assets without live feeds (ECX local commodities)."""
    from services.live_prices import SYMBOL_TO_YAHOO, COMMODITY_PROXIES

    live_symbols = set(SYMBOL_TO_YAHOO.keys()) | {k for k, v in COMMODITY_PROXIES.items() if v}

    assets = conn.execute(
        """SELECT a.id, a.symbol, mp.price, mp.high_24h, mp.low_24h, mp.volume_24h
           FROM assets a
           LEFT JOIN market_prices mp ON mp.asset_id = a.id
               AND mp.id = (SELECT MAX(id) FROM market_prices WHERE asset_id = a.id)
           WHERE a.is_active = 1"""
    ).fetchall()

    updated = 0
    for asset in assets:
        if asset["symbol"] in live_symbols:
            continue

        current_price = asset["price"] or 100.0
        change = random.gauss(0, 0.02)
        change = max(-0.05, min(0.05, change))
        new_price = round(max(current_price * (1 + change), 0.01), 2)
        change_pct = round((new_price - current_price) / current_price * 100, 2)
        high = round(max(new_price, asset["high_24h"] or new_price), 2)
        low = round(min(new_price, asset["low_24h"] or new_price), 2)
        volume = (asset["volume_24h"] or 0) + random.randint(10, 500)

        conn.execute(
            """INSERT INTO market_prices
               (asset_id, price, bid_price, ask_price, high_24h, low_24h, volume_24h, change_24h)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset["id"], new_price, round(new_price * 0.999, 2),
             round(new_price * 1.001, 2), high, low, volume, change_pct),
        )
        updated += 1

    conn.commit()
    return updated


def main():
    logger.info("=== TradEt Price Update Cron Starting ===")
    conn = get_connection()

    # 1. Update exchange rates
    etb_rate = update_exchange_rates(conn)
    logger.info(f"ETB/USD rate: {etb_rate:.2f}")

    # 2. Fetch live prices (yfinance)
    live_count = update_live_prices(conn, etb_rate)
    logger.info(f"Updated {live_count} live prices")

    # 3. Simulate local prices
    sim_count = simulate_local_prices(conn)
    logger.info(f"Simulated {sim_count} local prices")

    conn.close()
    logger.info(f"=== Done: {live_count} live + {sim_count} simulated prices updated ===")


if __name__ == "__main__":
    main()
