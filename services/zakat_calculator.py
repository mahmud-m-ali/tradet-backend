"""Zakat calculator for investment portfolios — based on Islamic jurisprudence."""

from datetime import datetime, timedelta

# Zakat rate: 2.5% on qualifying wealth held for one lunar year (Hawl)
ZAKAT_RATE = 0.025

# Nisab threshold (minimum wealth for Zakat obligation)
# Based on 85 grams of gold or 595 grams of silver
GOLD_GRAMS_NISAB = 85
SILVER_GRAMS_NISAB = 595

# Approximate gold/silver prices in ETB (updated periodically)
GOLD_PRICE_PER_GRAM_ETB = 5200.0   # ~$91 USD * 57 ETB
SILVER_PRICE_PER_GRAM_ETB = 62.0    # ~$1.09 USD * 57 ETB


def get_nisab_threshold(method="gold"):
    """
    Get Nisab threshold in ETB.
    method: 'gold' (more common) or 'silver' (more conservative)
    """
    if method == "silver":
        return SILVER_GRAMS_NISAB * SILVER_PRICE_PER_GRAM_ETB
    return GOLD_GRAMS_NISAB * GOLD_PRICE_PER_GRAM_ETB


def calculate_zakat(
    portfolio_value: float,
    cash_balance: float,
    other_savings: float = 0,
    gold_value: float = 0,
    silver_value: float = 0,
    debts: float = 0,
    expenses: float = 0,
    nisab_method: str = "gold",
):
    """
    Calculate Zakat on investment portfolio and other wealth.

    Parameters:
    - portfolio_value: Current market value of stock/sukuk/commodity holdings
    - cash_balance: Wallet balance (ETB)
    - other_savings: Bank savings, cash at hand (ETB)
    - gold_value: Value of gold holdings (ETB)
    - silver_value: Value of silver holdings (ETB)
    - debts: Outstanding debts to deduct (ETB)
    - expenses: Essential expenses to deduct (ETB)
    - nisab_method: 'gold' or 'silver' for Nisab threshold

    Returns dict with Zakat calculation details.
    """
    # Total zakatable wealth
    total_wealth = (
        portfolio_value
        + cash_balance
        + other_savings
        + gold_value
        + silver_value
    )

    # Deductions
    total_deductions = debts + expenses
    net_wealth = max(total_wealth - total_deductions, 0)

    # Nisab threshold
    nisab = get_nisab_threshold(nisab_method)

    # Is Zakat obligatory?
    is_obligatory = net_wealth >= nisab

    # Zakat amount
    zakat_amount = round(net_wealth * ZAKAT_RATE, 2) if is_obligatory else 0

    # Breakdown by asset type
    breakdown = []
    if portfolio_value > 0:
        breakdown.append({
            "category": "Investment Portfolio",
            "category_am": "የኢንቨስትመንት ፖርትፎሊዮ",
            "value": portfolio_value,
            "zakat": round(portfolio_value * ZAKAT_RATE, 2) if is_obligatory else 0,
        })
    if cash_balance > 0:
        breakdown.append({
            "category": "Wallet Balance",
            "category_am": "የዋሌት ቀሪ ሂሳብ",
            "value": cash_balance,
            "zakat": round(cash_balance * ZAKAT_RATE, 2) if is_obligatory else 0,
        })
    if other_savings > 0:
        breakdown.append({
            "category": "Other Savings",
            "category_am": "ሌሎች ቁጠባዎች",
            "value": other_savings,
            "zakat": round(other_savings * ZAKAT_RATE, 2) if is_obligatory else 0,
        })
    if gold_value > 0:
        breakdown.append({
            "category": "Gold",
            "category_am": "ወርቅ",
            "value": gold_value,
            "zakat": round(gold_value * ZAKAT_RATE, 2) if is_obligatory else 0,
        })
    if silver_value > 0:
        breakdown.append({
            "category": "Silver",
            "category_am": "ብር (ብረት)",
            "value": silver_value,
            "zakat": round(silver_value * ZAKAT_RATE, 2) if is_obligatory else 0,
        })

    return {
        "total_wealth": round(total_wealth, 2),
        "total_deductions": round(total_deductions, 2),
        "net_wealth": round(net_wealth, 2),
        "nisab_threshold": round(nisab, 2),
        "nisab_method": nisab_method,
        "nisab_gold_grams": GOLD_GRAMS_NISAB,
        "nisab_silver_grams": SILVER_GRAMS_NISAB,
        "is_obligatory": is_obligatory,
        "zakat_rate": ZAKAT_RATE,
        "zakat_amount": zakat_amount,
        "zakat_amount_monthly": round(zakat_amount / 12, 2),
        "breakdown": breakdown,
        "currency": "ETB",
        "calculated_at": datetime.utcnow().isoformat(),
        "note": "Zakat is due after holding wealth above Nisab for one lunar year (Hawl). "
                "Consult a qualified Islamic scholar for personal rulings.",
        "note_am": "ዘካት ከኒሳብ በላይ ሀብት ለአንድ የጨረቃ ዓመት (ሐውል) ከያዙ በኋላ ይከፈላል። "
                   "ለግል ፍርድ ብቁ የሆነ የእስልምና ሊቅ ያማክሩ።",
    }
