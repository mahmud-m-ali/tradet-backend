import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "tradet-dev-secret-key-change-in-production")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "tradet-jwt-secret-change-in-production")
    DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "tradet.db"))
    JWT_ACCESS_TOKEN_EXPIRES = 3600  # 1 hour
    JWT_REFRESH_TOKEN_EXPIRES = 86400 * 30  # 30 days
    RATE_LIMIT_DEFAULT = "100/hour"
    ETB_CURRENCY_CODE = "ETB"
    # AAOIFI Sharia screening thresholds
    SHARIA_DEBT_THRESHOLD = 0.30  # 30% max debt-to-market-cap
    SHARIA_INVESTMENT_THRESHOLD = 0.30  # 30% max non-compliant investments
    SHARIA_REVENUE_THRESHOLD = 0.05  # 5% max non-permissible revenue
