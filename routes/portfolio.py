"""Portfolio, wallet, and watchlist routes."""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from database import get_db, return_db

portfolio_bp = Blueprint("portfolio", __name__)


@portfolio_bp.route("/portfolio", methods=["GET"])
@jwt_required()
def get_portfolio():
    user_id = int(get_jwt_identity())
    conn = get_db()
    holdings = conn.execute(
        """SELECT p.*, a.symbol, a.name as asset_name, a.name_am, a.unit,
                  a.is_sharia_compliant,
                  mp.price as current_price, mp.change_24h
           FROM portfolios p
           JOIN assets a ON p.asset_id = a.id
           LEFT JOIN market_prices mp ON mp.asset_id = a.id
               AND mp.id = (SELECT MAX(id) FROM market_prices WHERE asset_id = a.id)
           WHERE p.user_id = ?
           ORDER BY p.total_invested DESC""",
        (user_id,),
    ).fetchall()

    wallet = conn.execute("SELECT balance FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    return_db(conn)

    portfolio_items = []
    total_value = 0
    total_invested = 0

    for h in holdings:
        current_value = h["quantity"] * (h["current_price"] or 0)
        pnl = current_value - h["total_invested"]
        pnl_pct = (pnl / h["total_invested"] * 100) if h["total_invested"] > 0 else 0
        item = dict(h)
        item["current_value"] = current_value
        item["pnl"] = pnl
        item["pnl_percentage"] = round(pnl_pct, 2)
        portfolio_items.append(item)
        total_value += current_value
        total_invested += h["total_invested"]

    cash_balance = wallet["balance"] if wallet else 0

    return jsonify({
        "holdings": portfolio_items,
        "summary": {
            "total_holdings_value": round(total_value, 2),
            "total_invested": round(total_invested, 2),
            "total_pnl": round(total_value - total_invested, 2),
            "cash_balance": cash_balance,
            "total_portfolio_value": round(total_value + cash_balance, 2),
            "currency": "ETB",
        },
    })


@portfolio_bp.route("/wallet", methods=["GET"])
@jwt_required()
def get_wallet():
    user_id = int(get_jwt_identity())
    conn = get_db()
    wallet = conn.execute("SELECT * FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    transactions = conn.execute(
        "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
        (user_id,),
    ).fetchall()
    return_db(conn)

    return jsonify({
        "balance": wallet["balance"] if wallet else 0,
        "currency": "ETB",
        "transactions": [dict(t) for t in transactions],
    })


@portfolio_bp.route("/wallet/deposit", methods=["POST"])
@jwt_required()
def deposit():
    """Deposit ETB into wallet (simulated for demo)."""
    user_id = int(get_jwt_identity())
    data = request.get_json()
    amount = float(data.get("amount", 0))

    if amount <= 0 or amount > 1000000:
        return jsonify({"error": "Amount must be between 1 and 1,000,000 ETB"}), 400

    conn = get_db()
    wallet = conn.execute("SELECT balance FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    new_balance = (wallet["balance"] if wallet else 0) + amount

    conn.execute(
        "UPDATE wallets SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (new_balance, user_id),
    )
    conn.execute(
        """INSERT INTO transactions (user_id, transaction_type, amount, balance_after, description)
           VALUES (?, 'deposit', ?, ?, ?)""",
        (user_id, amount, new_balance, f"Deposit {amount} ETB"),
    )
    conn.commit()
    return_db(conn)

    return jsonify({"message": f"Deposited {amount} ETB", "new_balance": new_balance})


@portfolio_bp.route("/wallet/withdraw", methods=["POST"])
@jwt_required()
def withdraw():
    """Withdraw ETB from wallet to a bank account (simulated for demo)."""
    user_id = int(get_jwt_identity())
    data = request.get_json()
    amount = float(data.get("amount", 0))
    bank_name = data.get("bank_name", "")
    account_number = data.get("account_number", "")

    if amount <= 0 or amount > 1000000:
        return jsonify({"error": "Amount must be between 1 and 1,000,000 ETB"}), 400
    if not bank_name:
        return jsonify({"error": "Bank name is required"}), 400
    if not account_number:
        return jsonify({"error": "Account number is required"}), 400

    conn = get_db()
    wallet = conn.execute("SELECT balance FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    current_balance = wallet["balance"] if wallet else 0

    if amount > current_balance:
        return_db(conn)
        return jsonify({
            "error": "Insufficient balance",
            "available": current_balance,
            "requested": amount,
        }), 400

    new_balance = current_balance - amount

    conn.execute(
        "UPDATE wallets SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (new_balance, user_id),
    )
    conn.execute(
        """INSERT INTO transactions (user_id, transaction_type, amount, balance_after, description)
           VALUES (?, 'withdrawal', ?, ?, ?)""",
        (user_id, amount, new_balance, f"Withdraw {amount} ETB to {bank_name} ({account_number})"),
    )
    conn.commit()
    return_db(conn)

    return jsonify({
        "message": f"Withdrew {amount} ETB to {bank_name}",
        "new_balance": new_balance,
        "bank_name": bank_name,
    })


@portfolio_bp.route("/watchlist", methods=["GET"])
@jwt_required()
def get_watchlist():
    user_id = int(get_jwt_identity())
    conn = get_db()
    items = conn.execute(
        """SELECT w.*, a.symbol, a.name, a.name_am, a.is_sharia_compliant,
                  mp.price, mp.change_24h, mp.volume_24h
           FROM watchlists w
           JOIN assets a ON w.asset_id = a.id
           LEFT JOIN market_prices mp ON mp.asset_id = a.id
               AND mp.id = (SELECT MAX(id) FROM market_prices WHERE asset_id = a.id)
           WHERE w.user_id = ?
           ORDER BY w.created_at DESC""",
        (user_id,),
    ).fetchall()
    return_db(conn)
    return jsonify([dict(i) for i in items])


@portfolio_bp.route("/watchlist", methods=["POST"])
@jwt_required()
def add_to_watchlist():
    user_id = int(get_jwt_identity())
    data = request.get_json()
    asset_id = data.get("asset_id")

    if not asset_id:
        return jsonify({"error": "asset_id required"}), 400

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO watchlists (user_id, asset_id) VALUES (?, ?)",
            (user_id, asset_id),
        )
        conn.commit()
        return jsonify({"message": "Added to watchlist"}), 201
    except Exception:
        return jsonify({"error": "Already in watchlist or asset not found"}), 409
    finally:
        return_db(conn)


@portfolio_bp.route("/watchlist/<int:asset_id>", methods=["DELETE"])
@jwt_required()
def remove_from_watchlist(asset_id):
    user_id = int(get_jwt_identity())
    conn = get_db()
    conn.execute(
        "DELETE FROM watchlists WHERE user_id = ? AND asset_id = ?",
        (user_id, asset_id),
    )
    conn.commit()
    return_db(conn)
    return jsonify({"message": "Removed from watchlist"})


# ─── Payment Methods ───

@portfolio_bp.route("/payment-methods", methods=["GET"])
@jwt_required()
def get_payment_methods():
    user_id = int(get_jwt_identity())
    conn = get_db()
    methods = conn.execute(
        "SELECT * FROM payment_methods WHERE user_id = ? ORDER BY is_primary DESC, created_at DESC",
        (user_id,),
    ).fetchall()
    return_db(conn)
    return jsonify([dict(m) for m in methods])


@portfolio_bp.route("/payment-methods", methods=["POST"])
@jwt_required()
def add_payment_method():
    user_id = int(get_jwt_identity())
    data = request.get_json()
    bank_name = data.get("bank_name", "").strip()
    account_number = data.get("account_number", "").strip()
    account_name = data.get("account_name", "").strip()

    if not bank_name or not account_number or not account_name:
        return jsonify({"error": "bank_name, account_number, and account_name are required"}), 400

    conn = get_db()
    # Limit to 5 payment methods
    count = conn.execute(
        "SELECT COUNT(*) FROM payment_methods WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    if count >= 5:
        return_db(conn)
        return jsonify({"error": "Maximum 5 payment methods allowed"}), 400

    is_primary = 1 if count == 0 else int(data.get("is_primary", 0))
    if is_primary:
        conn.execute(
            "UPDATE payment_methods SET is_primary = 0 WHERE user_id = ?", (user_id,)
        )

    conn.execute(
        """INSERT INTO payment_methods (user_id, bank_name, account_number, account_name, is_primary)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, bank_name, account_number, account_name, is_primary),
    )
    conn.commit()
    return_db(conn)
    return jsonify({"message": "Payment method added"}), 201


@portfolio_bp.route("/payment-methods/<int:method_id>", methods=["DELETE"])
@jwt_required()
def delete_payment_method(method_id):
    user_id = int(get_jwt_identity())
    conn = get_db()
    method = conn.execute(
        "SELECT * FROM payment_methods WHERE id = ? AND user_id = ?", (method_id, user_id)
    ).fetchone()
    if not method:
        return_db(conn)
        return jsonify({"error": "Payment method not found"}), 404

    conn.execute("DELETE FROM payment_methods WHERE id = ?", (method_id,))
    # Promote oldest remaining as primary if deleted was primary
    if method["is_primary"]:
        conn.execute(
            """UPDATE payment_methods SET is_primary = 1
               WHERE user_id = ? AND id = (SELECT MIN(id) FROM payment_methods WHERE user_id = ?)""",
            (user_id, user_id),
        )
    conn.commit()
    return_db(conn)
    return jsonify({"message": "Payment method removed"})


@portfolio_bp.route("/payment-methods/<int:method_id>/set-primary", methods=["POST"])
@jwt_required()
def set_primary_payment_method(method_id):
    user_id = int(get_jwt_identity())
    conn = get_db()
    method = conn.execute(
        "SELECT id FROM payment_methods WHERE id = ? AND user_id = ?", (method_id, user_id)
    ).fetchone()
    if not method:
        return_db(conn)
        return jsonify({"error": "Payment method not found"}), 404
    conn.execute("UPDATE payment_methods SET is_primary = 0 WHERE user_id = ?", (user_id,))
    conn.execute("UPDATE payment_methods SET is_primary = 1 WHERE id = ?", (method_id,))
    conn.commit()
    return_db(conn)
    return jsonify({"message": "Primary payment method updated"})


@portfolio_bp.route("/wallet/transactions", methods=["GET"])
@jwt_required()
def get_transactions():
    """Full transaction ledger for the user."""
    user_id = int(get_jwt_identity())
    conn = get_db()
    wallet = conn.execute("SELECT balance FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    transactions = conn.execute(
        "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
        (user_id,),
    ).fetchall()
    return_db(conn)
    return jsonify({
        "balance": wallet["balance"] if wallet else 0,
        "transactions": [dict(t) for t in transactions],
    })
