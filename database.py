import sqlite3
import os
import threading
import queue
import logging
from config import Config

logger = logging.getLogger(__name__)


class ConnectionPool:
    """Thread-safe SQLite connection pool."""

    def __init__(self, database_path, pool_size=5):
        self._database_path = database_path
        self._pool = queue.Queue(maxsize=pool_size)
        self._pool_size = pool_size
        self._lock = threading.Lock()
        self._created = 0

    def _create_connection(self):
        conn = sqlite3.connect(
            self._database_path,
            check_same_thread=False,
            timeout=30,
        )
        conn.row_factory = sqlite3.Row
        # Use DELETE journal mode (WAL can cause disk I/O errors on NFS mounts)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def get_connection(self):
        try:
            conn = self._pool.get_nowait()
            # Test if connection is still alive
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.Error:
                # Connection is stale, create new one
                with self._lock:
                    self._created -= 1
        except queue.Empty:
            pass

        with self._lock:
            if self._created < self._pool_size:
                self._created += 1
                return self._create_connection()

        # Pool exhausted, wait for one to become available
        return self._pool.get(timeout=10)

    def return_connection(self, conn):
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            conn.close()
            with self._lock:
                self._created -= 1

    def close_all(self):
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except queue.Empty:
                break
        with self._lock:
            self._created = 0


# Global pool instance
_pool = None


def init_pool():
    """Initialize the global connection pool."""
    global _pool
    _pool = ConnectionPool(Config.DATABASE_PATH, pool_size=5)


def close_pool():
    """Close and reset the global connection pool."""
    global _pool
    if _pool is not None:
        _pool.close_all()
        _pool = None


def get_db():
    """Get a database connection from the pool."""
    global _pool
    if _pool is None:
        init_pool()
    return _pool.get_connection()


def return_db(conn):
    """Return a connection to the pool instead of closing it."""
    global _pool
    if _pool is not None:
        _pool.return_connection(conn)
    else:
        conn.close()


def init_db():
    """Initialize the database with all required tables."""
    conn = get_db()
    cursor = conn.cursor()

    # Users table with KYC fields
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            kyc_status TEXT DEFAULT 'pending' CHECK(kyc_status IN ('pending','verified','rejected')),
            kyc_id_type TEXT,
            kyc_id_number TEXT,
            trade_license_number TEXT,
            account_type TEXT DEFAULT 'individual' CHECK(account_type IN ('individual','business')),
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Asset categories (Sharia-compliant classification)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS asset_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            name_am TEXT,
            description TEXT,
            is_sharia_compliant INTEGER DEFAULT 1,
            category_type TEXT CHECK(category_type IN ('commodity','equity','sukuk','currency')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tradeable assets
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            name_am TEXT,
            category_id INTEGER NOT NULL,
            description TEXT,
            unit TEXT NOT NULL DEFAULT 'KG',
            min_trade_qty REAL DEFAULT 1.0,
            max_trade_qty REAL DEFAULT 10000.0,
            is_ecx_listed INTEGER DEFAULT 0,
            is_sharia_compliant INTEGER DEFAULT 1,
            sharia_screening_date TEXT,
            sharia_notes TEXT,
            debt_ratio REAL DEFAULT 0.0,
            non_compliant_investment_ratio REAL DEFAULT 0.0,
            non_permissible_revenue_ratio REAL DEFAULT 0.0,
            is_active INTEGER DEFAULT 1,
            is_haram INTEGER DEFAULT 0,
            trading_session_days TEXT,
            trading_session_start TEXT,
            trading_session_end TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES asset_categories(id)
        )
    """)

    # Market prices
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            price REAL NOT NULL,
            bid_price REAL,
            ask_price REAL,
            high_24h REAL,
            low_24h REAL,
            volume_24h REAL DEFAULT 0,
            change_24h REAL DEFAULT 0,
            currency TEXT DEFAULT 'ETB',
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        )
    """)

    # Persist successful live-feed refreshes so status survives process restarts
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS live_update_status (
            symbol TEXT PRIMARY KEY,
            updated_at TIMESTAMP NOT NULL
        )
    """)

    # Orders
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            asset_id INTEGER NOT NULL,
            order_type TEXT NOT NULL CHECK(order_type IN ('buy','sell')),
            order_status TEXT DEFAULT 'pending' CHECK(order_status IN ('pending','filled','partially_filled','cancelled','expired')),
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            filled_quantity REAL DEFAULT 0,
            total_amount REAL NOT NULL,
            fee_amount REAL DEFAULT 0,
            fee_type TEXT DEFAULT 'flat',
            currency TEXT DEFAULT 'ETB',
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        )
    """)

    # Trades (executed orders)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buy_order_id INTEGER NOT NULL,
            sell_order_id INTEGER NOT NULL,
            asset_id INTEGER NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            total_amount REAL NOT NULL,
            buyer_fee REAL DEFAULT 0,
            seller_fee REAL DEFAULT 0,
            currency TEXT DEFAULT 'ETB',
            executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (buy_order_id) REFERENCES orders(id),
            FOREIGN KEY (sell_order_id) REFERENCES orders(id),
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        )
    """)

    # User portfolio / holdings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            asset_id INTEGER NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            avg_buy_price REAL NOT NULL DEFAULT 0,
            total_invested REAL NOT NULL DEFAULT 0,
            currency TEXT DEFAULT 'ETB',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, asset_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        )
    """)

    # Wallets (ETB balance)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            balance REAL NOT NULL DEFAULT 0,
            currency TEXT DEFAULT 'ETB',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # Transaction ledger (audit trail)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            transaction_type TEXT NOT NULL CHECK(transaction_type IN ('deposit','withdrawal','trade_buy','trade_sell','fee','refund')),
            amount REAL NOT NULL,
            balance_after REAL NOT NULL,
            reference_id TEXT,
            description TEXT,
            currency TEXT DEFAULT 'ETB',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # Watchlist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            asset_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, asset_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        )
    """)

    # Price alerts
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            asset_id INTEGER NOT NULL,
            target_price REAL NOT NULL,
            condition TEXT NOT NULL CHECK(condition IN ('above','below')),
            is_active INTEGER DEFAULT 1,
            is_triggered INTEGER DEFAULT 0,
            triggered_at TIMESTAMP,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        )
    """)

    # Exchange rate cache
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exchange_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            currency_code TEXT NOT NULL,
            buying_rate REAL NOT NULL,
            selling_rate REAL NOT NULL,
            mid_rate REAL NOT NULL,
            source TEXT DEFAULT 'NBE',
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Order event log (regulatory audit trail — each state change is a separate immutable row)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('placed','filled','cancelled','expired','partial_fill')),
            quantity REAL,
            price REAL,
            amount REAL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_events_user ON order_events(user_id, created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id)")

    # Payment methods (user-registered bank accounts)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bank_name TEXT NOT NULL,
            account_number TEXT NOT NULL,
            account_name TEXT NOT NULL,
            is_primary INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_methods_user ON payment_methods(user_id)")

    # Audit log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id INTEGER,
            details TEXT,
            ip_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Schema migrations tracker
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT UNIQUE NOT NULL,
            description TEXT,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Performance indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_prices_asset ON market_prices(asset_id, id DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_portfolios_user ON portfolios(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id, created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_user ON price_alerts(user_id, is_active)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_assets_symbol ON assets(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id, created_at DESC)")

    # Schema migrations for existing databases
    try:
        cursor.execute("ALTER TABLE assets ADD COLUMN is_haram INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass  # Column already exists

    # Migration: add Permissible Equities category if not present
    existing_cat = cursor.execute(
        "SELECT id FROM asset_categories WHERE name = 'Permissible Equities'"
    ).fetchone()
    if not existing_cat:
        cursor.execute(
            "INSERT INTO asset_categories (name, name_am, description, is_sharia_compliant, category_type) VALUES (?,?,?,?,?)",
            ("Permissible Equities", "ተቀባይነት ያለው አክሲዮን", "Conventional companies in non-haram sectors — permissible for trade", 0, "equity"),
        )
        cat_id = cursor.execute("SELECT last_insert_rowid()").fetchone()[0]
        permissible_migration = [
            ("AWASH", "Awash Bank", "አዋሽ ባንክ", cat_id, "Largest private bank in Ethiopia — conventional banking with IFB window", "Share", 1, 500, 0, 0, 0),
            ("DASHEN", "Dashen Bank", "ዳሽን ባንክ", cat_id, "Major Ethiopian private commercial bank — ESX listed", "Share", 1, 500, 0, 0, 0),
            ("CBE", "Commercial Bank of Ethiopia", "የኢትዮጵያ ንግድ ባንክ", cat_id, "State-owned commercial bank — largest bank in Ethiopia", "Share", 1, 1000, 0, 0, 0),
            ("ETHA", "Ethiopian Airlines", "ኢትዮጵያ አየር መንገድ", cat_id, "Africa's largest airline — aviation and logistics", "Share", 1, 200, 0, 0, 0),
            ("EEP", "Ethiopian Electric Power", "የኢትዮጵያ ኤሌክትሪክ ኃይል", cat_id, "State power utility — hydro and renewable energy generation", "Share", 1, 500, 0, 0, 0),
            ("DERBA", "Derba Midroc Cement", "ደርባ ሚድሮክ ሲሚንቶ", cat_id, "Ethiopia's largest cement producer — construction materials", "Share", 1, 300, 0, 0, 0),
            ("COOP", "Cooperative Bank of Oromia", "የኦሮሚያ ህ/ሥራ ባንክ", cat_id, "Cooperative banking model serving agricultural communities", "Share", 1, 500, 0, 0, 0),
            ("BUNNA", "Bunna Bank", "ቡና ባንክ", cat_id, "Mid-sized private commercial bank — conventional with IFB window", "Share", 1, 300, 0, 0, 0),
        ]
        for asset in permissible_migration:
            try:
                cursor.execute(
                    """INSERT OR IGNORE INTO assets (symbol, name, name_am, category_id, description, unit,
                       min_trade_qty, max_trade_qty, is_ecx_listed, is_sharia_compliant, is_haram)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    asset,
                )
                asset_id = cursor.execute("SELECT last_insert_rowid()").fetchone()[0]
                # Add an initial price for each permissible asset
                prices_map = {
                    "AWASH": (980.00, 978.00, 982.00, 995.00, 970.00, 12500, 1.4),
                    "DASHEN": (850.00, 848.00, 852.00, 865.00, 840.00, 9800, 0.9),
                    "CBE": (560.00, 558.00, 562.00, 572.00, 552.00, 18200, 2.1),
                    "ETHA": (1200.00, 1198.00, 1202.00, 1220.00, 1185.00, 4500, 1.8),
                    "EEP": (320.00, 318.00, 322.00, 330.00, 315.00, 8900, -0.6),
                    "DERBA": (740.00, 738.00, 742.00, 755.00, 730.00, 3200, 1.2),
                    "COOP": (620.00, 618.00, 622.00, 632.00, 612.00, 5600, 0.5),
                    "BUNNA": (490.00, 488.00, 492.00, 500.00, 483.00, 4100, -0.3),
                }
                sym = asset[0]
                if sym in prices_map:
                    cursor.execute(
                        """INSERT INTO market_prices (asset_id, price, bid_price, ask_price, high_24h, low_24h, volume_24h, change_24h)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (asset_id,) + prices_map[sym],
                    )
            except Exception:
                pass
        conn.commit()

    conn.commit()
    return_db(conn)


def seed_data():
    """Seed initial data: categories, assets, and sample prices."""
    conn = get_db()
    cursor = conn.cursor()

    # Check if data already exists
    count = cursor.execute("SELECT COUNT(*) FROM asset_categories").fetchone()[0]
    if count > 0:
        return_db(conn)
        return

    # Asset categories — tailored for Islamic Banks in Ethiopia
    categories = [
        ("ECX Commodities", "የኢሲኤክስ ሸቀጦች", "ECX-listed agricultural commodities", 1, "commodity"),
        ("Grains & Pulses", "እህል እና ጥራጥሬ", "Grain and pulse commodities on ECX", 1, "commodity"),
        ("Sukuk", "ሱኩክ", "Islamic bonds — asset-backed profit-sharing certificates", 1, "sukuk"),
        ("Ethiopian Equities", "የኢትዮጵያ አክሲዮን", "ESX-listed and pre-IPO Ethiopian shares", 1, "equity"),
        ("Islamic Banks", "እስላማዊ ባንኮች", "Interest-free bank shares (Sharia native)", 1, "equity"),
        ("Halal Global Equities", "ዓለም አቀፍ ሀላል", "AAOIFI-screened global equities", 1, "equity"),
        ("Takaful & Insurance", "ተካፉል", "Sharia-compliant cooperative insurance", 1, "equity"),
        ("Permissible Equities", "ተቀባይነት ያለው አክሲዮን", "Conventional companies in non-haram sectors — permissible for trade", 0, "equity"),
    ]
    cursor.executemany(
        "INSERT INTO asset_categories (name, name_am, description, is_sharia_compliant, category_type) VALUES (?,?,?,?,?)",
        categories,
    )

    # Comprehensive asset catalog for Islamic Banks
    assets = [
        # === ECX COMMODITIES (cat 1) ===
        ("COFFEE-EXP", "Export Coffee", "ኤክስፖርት ቡና", 1, "Premium Ethiopian export coffee — Yirgacheffe, Sidamo, Harrar grades", "Quintal", 1, 500, 1, 1, "Mon,Tue,Wed,Thu,Fri", "14:00", "18:00"),
        ("COFFEE-LOC", "Local Coffee", "የሀገር ውስጥ ቡና", 1, "Domestic market coffee beans", "Quintal", 1, 200, 1, 1, "Tue,Wed,Thu", "11:30", "12:30"),
        ("SESAME-W", "White Sesame", "ነጭ ሰሊጥ", 1, "Humera/Gondar white sesame for export", "Quintal", 5, 1000, 1, 1, "Mon,Tue,Wed,Thu,Fri", "10:00", "11:00"),
        ("SESAME-R", "Red Sesame", "ቀይ ሰሊጥ", 1, "Wollega red/mixed sesame seeds", "Quintal", 5, 1000, 1, 1, "Mon,Tue,Wed,Thu,Fri", "10:00", "11:00"),
        ("SESAME-HM", "Humera Sesame", "ሁመራ ሰሊጥ", 1, "Premium Humera origin sesame — top export grade", "Quintal", 5, 800, 1, 1, "Mon,Tue,Wed,Thu,Fri", "10:00", "11:00"),
        ("NOOG", "Niger Seed (Noog)", "ኑግ", 1, "Ethiopian niger seed — oil crop export", "Quintal", 5, 500, 1, 1, "Mon,Tue,Wed,Thu,Fri", "10:00", "11:00"),

        # === GRAINS & PULSES (cat 2) ===
        ("BEAN-WP", "White Pea Beans", "ነጭ ባቄላ", 2, "White pea beans for export", "Quintal", 5, 500, 1, 1, "Wed", "09:00", "09:30"),
        ("BEAN-RK", "Red Kidney Beans", "ቀይ ባቄላ", 2, "Red kidney beans", "Quintal", 5, 500, 1, 1, "Wed", "09:00", "09:30"),
        ("MUNG-GR", "Green Mung Bean", "መሸላ", 2, "Green mung beans for export", "Quintal", 5, 500, 1, 1, "Wed", "09:00", "09:30"),
        ("CHICKPEA", "Chickpeas (Desi)", "ሽምብራ", 2, "Ethiopian desi chickpeas", "Quintal", 5, 500, 1, 1, "Wed", "09:00", "09:30"),
        ("LENTIL", "Red Lentils", "ምስር", 2, "Ethiopian red lentils", "Quintal", 5, 500, 1, 1, "Wed", "09:00", "09:30"),
        ("MAIZE", "Maize", "በቆሎ", 2, "Maize grain", "Quintal", 10, 2000, 1, 1, "Wed", "09:00", "09:30"),
        ("WHEAT", "Wheat", "ስንዴ", 2, "Wheat grain", "Quintal", 10, 2000, 1, 1, "Wed", "09:00", "09:30"),
        ("TEFF", "Teff", "ጤፍ", 2, "Ethiopian teff grain — staple crop", "Quintal", 5, 1000, 1, 1, "Wed", "09:00", "09:30"),
        ("SORGHUM", "Sorghum", "ማሽላ", 2, "Sorghum grain", "Quintal", 10, 2000, 1, 1, "Wed", "09:00", "09:30"),

        # === SUKUK (cat 3) ===
        ("SUKUK-GOV", "Government Sukuk", "የመንግስት ሱኩክ", 3, "Ethiopian government Sharia-compliant Ijara sukuk — infrastructure backed", "Unit", 1, 10000, 0, 1, None, None, None),
        ("SUKUK-INF", "Infrastructure Sukuk", "የመሠረተ ልማት ሱኩክ", 3, "Asset-backed sukuk for Ethiopian infrastructure projects (roads, energy)", "Unit", 1, 5000, 0, 1, None, None, None),
        ("SUKUK-CRP", "Corporate Sukuk", "የኩባንያ ሱኩክ", 3, "Corporate Mudarabah sukuk — profit-sharing with Ethiopian enterprises", "Unit", 1, 5000, 0, 1, None, None, None),

        # === ETHIOPIAN EQUITIES — ESX listed (cat 4) ===
        ("WGBX", "Wegagen Bank", "ወጋገን ባንክ", 4, "First ESX-listed bank — 6.2M shares at 1,000 ETB par. Conventional + IFB window", "Share", 1, 100, 0, 1, None, None, None),
        ("ETLC", "Ethio Telecom", "ኢትዮ ቴሌኮም", 4, "State telecom — 10% public share sale. 70M+ subscribers, telebirr platform", "Share", 1, 500, 0, 1, None, None, None),
        ("GDAX", "Gadaa Bank", "ጋዳ ባንክ", 4, "ESX-listed bank — community-focused banking", "Share", 1, 200, 0, 1, None, None, None),
        ("HALAL-FD", "Halal Food Industries", "ሐላል ምግብ ኢንዱስትሪ", 4, "Modern halal export slaughterhouse at Mojo — meat processing & export", "Share", 1, 1000, 0, 1, None, None, None),
        ("EIC", "Ethiopian Insurance Corp", "የኢትዮጵያ ኢንሹራንስ", 4, "State-owned insurer — pre-IPO. Takaful window available", "Share", 1, 500, 0, 1, None, None, None),

        # === ISLAMIC BANKS — Sharia-native (cat 5) ===
        ("ZAMZAM", "ZamZam Bank", "ዘምዘም ባንክ", 5, "Ethiopia's first full-fledged interest-free bank. Sharia-native operations", "Share", 1, 500, 0, 1, None, None, None),
        ("HIJRA", "Hijra Bank", "ሂጅራ ባንክ", 5, "Full-fledged Islamic bank — digital Sharia financing pioneer", "Share", 1, 500, 0, 1, None, None, None),
        ("RAMMIS", "Rammis Bank", "ራሚስ ባንክ", 5, "Full-fledged interest-free bank — licensed Oct 2022", "Share", 1, 500, 0, 1, None, None, None),
        ("SHABELLE", "Shabelle Bank", "ሻበሌ ባንክ", 5, "Full-fledged Islamic bank — operates entirely under Sharia principles", "Share", 1, 500, 0, 1, None, None, None),

        # === GLOBAL HALAL EQUITIES — AAOIFI screened (cat 6) ===
        ("AAPL", "Apple Inc.", None, 6, "Consumer tech — AAOIFI compliant. Debt ratio 8.2%, non-permissible revenue <1%", "Share", 1, 100, 0, 1, None, None, None),
        ("MSFT", "Microsoft Corp.", None, 6, "Enterprise tech — AAOIFI compliant. Cloud & productivity software", "Share", 1, 100, 0, 1, None, None, None),
        ("TSLA", "Tesla Inc.", None, 6, "EV & clean energy — AAOIFI compliant. Zero-debt operations", "Share", 1, 50, 0, 1, None, None, None),
        ("NVDA", "NVIDIA Corp.", None, 6, "AI & semiconductor — AAOIFI compliant. Leading AI chip maker", "Share", 1, 50, 0, 1, None, None, None),
        ("AMZN", "Amazon.com", None, 6, "E-commerce & cloud — AAOIFI compliant. AWS dominance", "Share", 1, 50, 0, 1, None, None, None),
        ("GOOGL", "Alphabet (Google)", None, 6, "Digital advertising & AI — AAOIFI compliant", "Share", 1, 50, 0, 1, None, None, None),
        ("2222.SR", "Saudi Aramco", None, 6, "World's largest oil company — Tadawul listed, Sharia native", "Share", 1, 1000, 0, 1, None, None, None),
        ("SAFCOM", "Safaricom PLC", None, 6, "East Africa's largest telco — M-Pesa platform. NSE listed", "Share", 1, 500, 0, 1, None, None, None),
        ("DANGCEM", "Dangote Cement", None, 6, "Africa's largest cement producer — NGX listed, halal screened", "Share", 1, 200, 0, 1, None, None, None),

        # === TAKAFUL & INSURANCE (cat 7) ===
        ("TAKAFUL-ET", "Ethiopian Takaful", "የኢትዮጵያ ተካፉል", 7, "Cooperative Islamic insurance — property & family takaful", "Unit", 1, 5000, 0, 1, None, None, None),
        ("NYALA-TK", "Nyala Takaful Window", "ንያላ ተካፉል", 7, "Nyala Insurance takaful window — Sharia-compliant coverage", "Unit", 1, 5000, 0, 1, None, None, None),
    ]
    cursor.executemany(
        """INSERT INTO assets (symbol, name, name_am, category_id, description, unit, min_trade_qty, max_trade_qty, is_ecx_listed, is_sharia_compliant,
           trading_session_days, trading_session_start, trading_session_end) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        assets,
    )

    # Permissible assets — conventional companies NOT in haram sectors (cat 8)
    # is_sharia_compliant=0 (not AAOIFI-screened), is_haram=0 (not forbidden)
    permissible_assets = [
        ("AWASH", "Awash Bank", "አዋሽ ባንክ", 8, "Largest private bank in Ethiopia — conventional banking with IFB window", "Share", 1, 500, 0, 0, 0, None, None, None),
        ("DASHEN", "Dashen Bank", "ዳሽን ባንክ", 8, "Major Ethiopian private commercial bank — ESX listed", "Share", 1, 500, 0, 0, 0, None, None, None),
        ("CBE", "Commercial Bank of Ethiopia", "የኢትዮጵያ ንግድ ባንክ", 8, "State-owned commercial bank — largest bank in Ethiopia", "Share", 1, 1000, 0, 0, 0, None, None, None),
        ("ETHA", "Ethiopian Airlines", "ኢትዮጵያ አየር መንገድ", 8, "Africa's largest airline — aviation and logistics", "Share", 1, 200, 0, 0, 0, None, None, None),
        ("EEP", "Ethiopian Electric Power", "የኢትዮጵያ ኤሌክትሪክ ኃይል", 8, "State power utility — hydro and renewable energy generation", "Share", 1, 500, 0, 0, 0, None, None, None),
        ("DERBA", "Derba Midroc Cement", "ደርባ ሚድሮክ ሲሚንቶ", 8, "Ethiopia's largest cement producer — construction materials", "Share", 1, 300, 0, 0, 0, None, None, None),
        ("COOP", "Cooperative Bank of Oromia", "የኦሮሚያ ህ/ሥራ ባንክ", 8, "Cooperative banking model serving agricultural communities", "Share", 1, 500, 0, 0, 0, None, None, None),
        ("BUNNA", "Bunna Bank", "ቡና ባንክ", 8, "Mid-sized private commercial bank — conventional with IFB window", "Share", 1, 300, 0, 0, 0, None, None, None),
    ]
    cursor.executemany(
        """INSERT INTO assets (symbol, name, name_am, category_id, description, unit, min_trade_qty, max_trade_qty,
           is_ecx_listed, is_sharia_compliant, is_haram, trading_session_days, trading_session_start, trading_session_end)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        permissible_assets,
    )

    # Market prices (in ETB) — realistic sample data
    prices = [
        # ECX Commodities
        (1, 42500.00, 42400.00, 42600.00, 43000.00, 41800.00, 15420, 2.3),
        (2, 38000.00, 37900.00, 38100.00, 38500.00, 37200.00, 8300, 1.8),
        (3, 28500.00, 28400.00, 28600.00, 29000.00, 27800.00, 12500, -0.5),
        (4, 26000.00, 25900.00, 26100.00, 26500.00, 25500.00, 9800, 0.7),
        (5, 31200.00, 31100.00, 31300.00, 31800.00, 30500.00, 7800, 1.4),
        (6, 18500.00, 18400.00, 18600.00, 19000.00, 18000.00, 4200, -0.8),
        # Grains & Pulses
        (7, 8500.00, 8450.00, 8550.00, 8700.00, 8300.00, 5600, 1.2),
        (8, 9200.00, 9150.00, 9250.00, 9400.00, 9000.00, 4200, -0.3),
        (9, 7800.00, 7750.00, 7850.00, 8000.00, 7600.00, 3100, 0.9),
        (10, 12500.00, 12400.00, 12600.00, 12800.00, 12200.00, 2800, 1.1),
        (11, 14200.00, 14100.00, 14300.00, 14500.00, 13800.00, 2400, -0.6),
        (12, 4200.00, 4150.00, 4250.00, 4350.00, 4100.00, 18900, 1.5),
        (13, 5600.00, 5550.00, 5650.00, 5750.00, 5450.00, 22100, -1.1),
        (14, 7800.00, 7750.00, 7850.00, 8000.00, 7500.00, 15600, 2.8),
        (15, 3800.00, 3750.00, 3850.00, 3900.00, 3700.00, 9200, 0.4),
        # Sukuk
        (16, 1000.00, 1000.00, 1000.00, 1000.00, 1000.00, 500, 0.0),
        (17, 1000.00, 998.00, 1002.00, 1005.00, 995.00, 320, 0.2),
        (18, 1050.00, 1045.00, 1055.00, 1060.00, 1040.00, 180, 0.5),
        # Ethiopian Equities
        (19, 1050.31, 1048.00, 1052.00, 1065.00, 1040.00, 6200, 1.2),
        (20, 320.00, 318.00, 322.00, 328.00, 315.00, 10700, 3.5),
        (21, 890.00, 885.00, 895.00, 905.00, 878.00, 3200, 0.8),
        (22, 450.00, 445.00, 455.00, 460.00, 440.00, 1800, 1.5),
        (23, 780.00, 775.00, 785.00, 795.00, 770.00, 2100, -0.4),
        # Islamic Banks
        (24, 285.00, 283.00, 287.00, 292.00, 280.00, 8500, 2.8),
        (25, 245.00, 243.00, 247.00, 250.00, 240.00, 6200, 1.9),
        (26, 198.00, 196.00, 200.00, 203.00, 195.00, 4100, 0.6),
        (27, 175.00, 173.00, 177.00, 180.00, 172.00, 3800, -0.2),
        # Global Halal
        (28, 12450.00, 12430.00, 12470.00, 12600.00, 12300.00, 890, 1.6),
        (29, 25800.00, 25780.00, 25820.00, 26000.00, 25500.00, 720, 0.9),
        (30, 18200.00, 18180.00, 18220.00, 18500.00, 17900.00, 540, -2.1),
        (31, 7850.00, 7830.00, 7870.00, 8000.00, 7700.00, 680, 3.2),
        (32, 11200.00, 11180.00, 11220.00, 11400.00, 11000.00, 420, 1.1),
        (33, 10500.00, 10480.00, 10520.00, 10700.00, 10300.00, 380, 0.7),
        (34, 2150.00, 2145.00, 2155.00, 2180.00, 2120.00, 1200, 0.3),
        (35, 185.00, 183.00, 187.00, 190.00, 182.00, 2800, 1.8),
        (36, 1680.00, 1675.00, 1685.00, 1700.00, 1660.00, 950, -0.5),
        # Takaful
        (37, 500.00, 498.00, 502.00, 510.00, 495.00, 600, 0.4),
        (38, 380.00, 378.00, 382.00, 388.00, 375.00, 450, 0.1),
        # Permissible Equities
        (39, 980.00, 978.00, 982.00, 995.00, 970.00, 12500, 1.4),   # AWASH
        (40, 850.00, 848.00, 852.00, 865.00, 840.00, 9800, 0.9),    # DASHEN
        (41, 560.00, 558.00, 562.00, 572.00, 552.00, 18200, 2.1),   # CBE
        (42, 1200.00, 1198.00, 1202.00, 1220.00, 1185.00, 4500, 1.8), # ETHA
        (43, 320.00, 318.00, 322.00, 330.00, 315.00, 8900, -0.6),   # EEP
        (44, 740.00, 738.00, 742.00, 755.00, 730.00, 3200, 1.2),    # DERBA
        (45, 620.00, 618.00, 622.00, 632.00, 612.00, 5600, 0.5),    # COOP
        (46, 490.00, 488.00, 492.00, 500.00, 483.00, 4100, -0.3),   # BUNNA
    ]
    cursor.executemany(
        """INSERT INTO market_prices (asset_id, price, bid_price, ask_price, high_24h, low_24h, volume_24h, change_24h) VALUES (?,?,?,?,?,?,?,?)""",
        prices,
    )

    # Add multiple price history entries for chart data
    import random
    random.seed(42)
    for asset_id in range(1, 47):
        base_price = prices[asset_id - 1][1]
        for i in range(30):
            variation = random.uniform(-0.03, 0.03)
            p = base_price * (1 + variation)
            cursor.execute(
                """INSERT INTO market_prices (asset_id, price, bid_price, ask_price, high_24h, low_24h, volume_24h, change_24h)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (asset_id, round(p, 2), round(p * 0.999, 2), round(p * 1.001, 2),
                 round(p * 1.01, 2), round(p * 0.99, 2),
                 random.randint(100, 20000), round(random.uniform(-3, 3), 2)),
            )

    conn.commit()
    return_db(conn)
    print("Database seeded successfully.")


if __name__ == "__main__":
    init_db()
    seed_data()
    print("Database initialized and seeded.")
