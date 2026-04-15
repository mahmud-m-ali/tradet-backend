"""Sharia compliance screening engine based on AAOIFI standards."""

from config import Config


def screen_asset(debt_ratio: float, investment_ratio: float, revenue_ratio: float) -> dict:
    """
    Screen an asset against AAOIFI Sharia compliance thresholds.

    Returns dict with compliance status and details.
    """
    issues = []
    is_compliant = True

    if debt_ratio > Config.SHARIA_DEBT_THRESHOLD:
        is_compliant = False
        issues.append(
            f"Debt ratio {debt_ratio:.1%} exceeds {Config.SHARIA_DEBT_THRESHOLD:.0%} threshold"
        )

    if investment_ratio > Config.SHARIA_INVESTMENT_THRESHOLD:
        is_compliant = False
        issues.append(
            f"Non-compliant investment ratio {investment_ratio:.1%} exceeds {Config.SHARIA_INVESTMENT_THRESHOLD:.0%} threshold"
        )

    if revenue_ratio > Config.SHARIA_REVENUE_THRESHOLD:
        is_compliant = False
        issues.append(
            f"Non-permissible revenue ratio {revenue_ratio:.1%} exceeds {Config.SHARIA_REVENUE_THRESHOLD:.0%} threshold"
        )

    return {
        "is_compliant": is_compliant,
        "debt_ratio": debt_ratio,
        "investment_ratio": investment_ratio,
        "revenue_ratio": revenue_ratio,
        "issues": issues,
        "thresholds": {
            "max_debt_ratio": Config.SHARIA_DEBT_THRESHOLD,
            "max_investment_ratio": Config.SHARIA_INVESTMENT_THRESHOLD,
            "max_revenue_ratio": Config.SHARIA_REVENUE_THRESHOLD,
        },
    }


# Haram industry sectors - assets in these sectors are automatically non-compliant
HARAM_SECTORS = frozenset([
    "alcohol",
    "tobacco",
    "pork",
    "gambling",
    "adult_entertainment",
    "conventional_banking",
    "conventional_insurance",
    "weapons",
])


def is_halal_sector(sector: str) -> bool:
    """Check if a business sector is halal."""
    return sector.lower() not in HARAM_SECTORS
