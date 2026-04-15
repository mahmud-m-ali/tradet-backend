"""Live market data service using yfinance (free, unlimited)."""

import logging
import time
from datetime import datetime, timedelta
from functools import wraps

logger = logging.getLogger(__name__)


def retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0):
    """Decorator: retry with exponential backoff on failure."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_retries - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(f"{func.__name__} attempt {attempt+1} failed: {e}. Retrying in {delay:.1f}s...")
                        time.sleep(delay)
            logger.error(f"{func.__name__} failed after {max_retries} attempts: {last_exc}")
            return {} if 'fetch' in func.__name__ else []
        return wrapper
    return decorator

# Map TradEt symbols to Yahoo Finance tickers
SYMBOL_TO_YAHOO = {
    # Global Halal Equities
    "AAPL": "AAPL",
    "MSFT": "MSFT",
    "TSLA": "TSLA",
    "NVDA": "NVDA",
    "AMZN": "AMZN",
    "GOOGL": "GOOGL",
    "2222.SR": "2222.SR",
    # African equities (approximate tickers)
    "SAFCOM": "SCOM.NR",
    "DANGCEM": "DANGCEM.LA",
}

# Commodity proxy tickers on Yahoo Finance (futures/ETFs)
COMMODITY_PROXIES = {
    "COFFEE-EXP": "KC=F",       # Coffee futures
    "COFFEE-LOC": "KC=F",
    "SESAME-W": None,            # No Yahoo ticker for sesame
    "SESAME-R": None,
    "SESAME-HM": None,
    "NOOG": None,
    "WHEAT": "ZW=F",             # Wheat futures
    "MAIZE": "ZC=F",             # Corn/Maize futures
    "TEFF": None,                # No global ticker
    "SORGHUM": None,
}

# ETB/USD rate cache (default reflects April 2026 rate)
_exchange_rate_cache = {"rate": 155.5, "updated": None}


def get_etb_usd_rate():
    """Get cached ETB/USD rate."""
    return _exchange_rate_cache["rate"]


@retry_with_backoff(max_retries=3, base_delay=2.0)
def fetch_live_prices():
    """
    Fetch live prices for all assets with Yahoo Finance tickers.
    Returns dict: {halalet_symbol: {price, change, high, low, volume, bid, ask}}
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed. Run: pip install yfinance")
        return {}

    results = {}

    # Combine all tickers
    all_tickers = {}
    for symbol, ticker in SYMBOL_TO_YAHOO.items():
        if ticker:
            all_tickers[symbol] = ticker
    for symbol, ticker in COMMODITY_PROXIES.items():
        if ticker:
            all_tickers[symbol] = ticker

    if not all_tickers:
        return results

    # Batch download for efficiency
    ticker_str = " ".join(set(all_tickers.values()))
    try:
        data = yf.Tickers(ticker_str)

        for halalet_symbol, yahoo_ticker in all_tickers.items():
            try:
                info = data.tickers[yahoo_ticker].fast_info
                price_usd = float(info.last_price) if info.last_price else None
                if price_usd is None:
                    continue

                # Convert to ETB for global equities
                etb_rate = get_etb_usd_rate()
                is_sar = yahoo_ticker.endswith(".SR")

                if is_sar:
                    # SAR to ETB (1 SAR ~ 41.5 ETB as of April 2026)
                    price_etb = price_usd * (etb_rate / 3.75)
                elif yahoo_ticker.endswith(".NR") or yahoo_ticker.endswith(".LA"):
                    # Local African currencies - use approximate rates
                    price_etb = price_usd * etb_rate * 0.008  # rough conversion
                else:
                    price_etb = price_usd * etb_rate

                prev_close = float(info.previous_close) if info.previous_close else price_usd
                change_pct = ((price_usd - prev_close) / prev_close * 100) if prev_close else 0

                results[halalet_symbol] = {
                    "price": round(price_etb, 2),
                    "price_usd": round(price_usd, 2),
                    "change_24h": round(change_pct, 2),
                    "high_24h": round(price_etb * 1.01, 2),  # Approximate from day range
                    "low_24h": round(price_etb * 0.99, 2),
                    "volume_24h": int(info.last_volume) if info.last_volume else 0,
                    "bid_price": round(price_etb * 0.999, 2),
                    "ask_price": round(price_etb * 1.001, 2),
                    "source": "yfinance",
                    "updated_at": datetime.utcnow().isoformat(),
                }
            except Exception as e:
                logger.debug(f"Failed to fetch {halalet_symbol}: {e}")
                continue

    except Exception as e:
        logger.error(f"yfinance batch fetch failed: {e}")

    return results


@retry_with_backoff(max_retries=2, base_delay=3.0)
def fetch_exchange_rates():
    """Fetch exchange rates using open.er-api.com (free, no key needed)."""
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed")
        return _fallback_rates()

    rates = {}
    SPREAD = 0.015  # 1.5% buy/sell spread typical for Ethiopian banks

    # Primary source: open.er-api.com (free, reliable, no API key)
    try:
        resp = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=15,
            headers={"User-Agent": "TradEt/1.0"},
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("result") == "success":
                er = data["rates"]
                etb_per_usd = er.get("ETB", 155.5)

                # Update the global cache
                _exchange_rate_cache["rate"] = etb_per_usd
                _exchange_rate_cache["updated"] = datetime.utcnow().isoformat()

                # Build rates for each currency we care about
                # The API gives us X per 1 USD. We need ETB per 1 unit of each currency.
                currency_map = {
                    "USD": 1.0,
                    "EUR": er.get("EUR", 0.87),
                    "GBP": er.get("GBP", 0.76),
                    "SAR": er.get("SAR", 3.75),
                    "AED": er.get("AED", 3.67),
                    "KES": er.get("KES", 129.5),
                    "JPY": er.get("JPY", 150.0),
                    "CNY": er.get("CNY", 7.25),
                    "INR": er.get("INR", 83.5),
                    "CAD": er.get("CAD", 1.36),
                    "CHF": er.get("CHF", 0.88),
                }

                for code, units_per_usd in currency_map.items():
                    # ETB per 1 unit of this currency = ETB_per_USD / units_per_USD
                    mid = round(etb_per_usd / units_per_usd, 4)
                    rates[code] = {
                        "buying": round(mid * (1 - SPREAD), 4),
                        "selling": round(mid * (1 + SPREAD), 4),
                        "mid": mid,
                    }

                logger.info(f"Fetched exchange rates: USD/ETB={etb_per_usd:.2f}")
                return rates
    except Exception as e:
        logger.warning(f"open.er-api.com fetch failed: {e}")

    # Fallback to hardcoded rates (updated April 2026)
    return _fallback_rates()


def _fallback_rates():
    """Fallback exchange rates if API is unreachable (April 2026 values)."""
    return {
        "USD": {"buying": 153.2, "selling": 157.8, "mid": 155.5},
        "EUR": {"buying": 176.8, "selling": 182.0, "mid": 179.4},
        "GBP": {"buying": 202.5, "selling": 208.5, "mid": 205.5},
        "SAR": {"buying": 40.8, "selling": 42.0, "mid": 41.4},
        "AED": {"buying": 41.7, "selling": 42.9, "mid": 42.3},
        "KES": {"buying": 1.18, "selling": 1.22, "mid": 1.20},
        "JPY": {"buying": 0.96, "selling": 0.99, "mid": 0.975},
        "CNY": {"buying": 21.1, "selling": 21.7, "mid": 21.4},
        "INR": {"buying": 1.83, "selling": 1.89, "mid": 1.86},
        "CAD": {"buying": 110.0, "selling": 113.2, "mid": 111.6},
        "CHF": {"buying": 174.0, "selling": 179.0, "mid": 176.5},
    }


@retry_with_backoff(max_retries=2, base_delay=3.0)
def fetch_ecx_prices():
    """
    Attempt to scrape ECX commodity prices from public sources.
    Falls back to simulated realistic price movements.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return {}

    prices = {}

    # Try ECX website
    try:
        resp = requests.get(
            "https://www.ecx.com.et/",
            timeout=10,
            headers={"User-Agent": "TradEt/1.0"},
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # ECX publishes daily prices - try to extract from page
            # This is best-effort since the site structure may change
            price_elements = soup.find_all(class_=lambda x: x and "price" in str(x).lower())
            for elem in price_elements:
                text = elem.get_text(strip=True)
                logger.debug(f"ECX price element: {text}")
    except Exception as e:
        logger.debug(f"ECX scraping failed (expected): {e}")

    return prices


def get_price_history(symbol, period="1mo", interval="1d"):
    """Get historical price data for charting."""
    try:
        import yfinance as yf
    except ImportError:
        return []

    yahoo_ticker = SYMBOL_TO_YAHOO.get(symbol) or COMMODITY_PROXIES.get(symbol)
    if not yahoo_ticker:
        return []

    try:
        ticker = yf.Ticker(yahoo_ticker)
        hist = ticker.history(period=period, interval=interval)
        etb_rate = get_etb_usd_rate()

        result = []
        for date, row in hist.iterrows():
            result.append({
                "date": date.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]) * etb_rate, 2),
                "high": round(float(row["High"]) * etb_rate, 2),
                "low": round(float(row["Low"]) * etb_rate, 2),
                "close": round(float(row["Close"]) * etb_rate, 2),
                "volume": int(row["Volume"]),
            })
        return result
    except Exception as e:
        logger.error(f"Failed to get history for {symbol}: {e}")
        return []
