"""Trading routes - order placement and management (Sharia & ECX compliant)."""

import time
import sqlite3
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from database import get_db, return_db
from utils.trading_session import is_trading_open

trading_bp = Blueprint("trading", __name__)

# Flat fee rate (no interest — Sharia compliant)
TRADE_FEE_RATE = 0.015  # 1.5% flat commission


@trading_bp.route("/orders", methods=["POST"])
@jwt_required()
def place_order():
    """Place a buy or sell order (spot trading only — no futures/margin)."""
    user_id = int(get_jwt_identity())
    data = request.get_json()

    required = ["asset_id", "order_type", "quantity", "price"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400

    if data["order_type"] not in ("buy", "sell"):
        return jsonify({"error": "order_type must be 'buy' or 'sell'"}), 400

    execution_type = data.get("execution_type", "market")  # market or limit
    if execution_type not in ("market", "limit"):
        return jsonify({"error": "execution_type must be 'market' or 'limit'"}), 400

    conn = get_db()

    # Verify KYC
    user = conn.execute("SELECT kyc_status FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user or user["kyc_status"] != "verified":
        return_db(conn)
        return jsonify({"error": "KYC verification required before trading"}), 403

    # Verify asset exists and is Sharia compliant
    asset = conn.execute("SELECT * FROM assets WHERE id = ? AND is_active = 1", (data["asset_id"],)).fetchone()
    if not asset:
        return_db(conn)
        return jsonify({"error": "Asset not found or inactive"}), 404

    if not asset["is_sharia_compliant"]:
        return_db(conn)
        return jsonify({"error": "Asset is not Sharia compliant and cannot be traded"}), 403

    # Check ECX trading session
    session = is_trading_open(
        asset["trading_session_days"], asset["trading_session_start"], asset["trading_session_end"]
    )
    if not session["is_open"] and asset["is_ecx_listed"]:
        return_db(conn)
        return jsonify({"error": "Trading session closed", "details": session}), 403

    # Validate quantity
    qty = float(data["quantity"])
    price = float(data["price"])

    if qty < asset["min_trade_qty"] or qty > asset["max_trade_qty"]:
        return_db(conn)
        return jsonify({
            "error": f"Quantity must be between {asset['min_trade_qty']} and {asset['max_trade_qty']} {asset['unit']}"
        }), 400

    total_amount = qty * price
    fee_amount = total_amount * TRADE_FEE_RATE

    if data["order_type"] == "buy":
        # Check wallet balance
        wallet = conn.execute("SELECT balance FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
        if not wallet or wallet["balance"] < (total_amount + fee_amount):
            return_db(conn)
            return jsonify({
                "error": "Insufficient balance",
                "required": total_amount + fee_amount,
                "available": wallet["balance"] if wallet else 0,
            }), 400
    else:
        # Check portfolio holdings for sell
        holding = conn.execute(
            "SELECT quantity FROM portfolios WHERE user_id = ? AND asset_id = ?",
            (user_id, data["asset_id"]),
        ).fetchone()
        if not holding or holding["quantity"] < qty:
            return_db(conn)
            return jsonify({
                "error": "Insufficient holdings to sell",
                "available": holding["quantity"] if holding else 0,
            }), 400

    max_retries = 3
    for attempt in range(max_retries):
      try:
        # Create order
        conn.execute(
            """INSERT INTO orders (user_id, asset_id, order_type, quantity, price, total_amount, fee_amount, fee_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'flat')""",
            (user_id, data["asset_id"], data["order_type"], qty, price, total_amount, fee_amount),
        )
        order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Limit orders: reserve funds immediately then stay pending
        if execution_type == "limit":
            if data["order_type"] == "buy":
                # Deduct (reserve) funds from wallet immediately
                reserved = total_amount + fee_amount
                new_balance = wallet["balance"] - reserved
                conn.execute(
                    "UPDATE wallets SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (new_balance, user_id),
                )
                conn.execute(
                    """INSERT INTO transactions (user_id, transaction_type, amount, balance_after, reference_id, description)
                       VALUES (?, 'trade_buy', ?, ?, ?, ?)""",
                    (user_id, reserved, new_balance, str(order_id),
                     f"Reserved for limit buy: {qty} {asset['symbol']} @ {price} ETB"),
                )
            # Log placed event
            conn.execute(
                """INSERT INTO order_events (order_id, user_id, event_type, quantity, price, amount, details)
                   VALUES (?, ?, 'placed', ?, ?, ?, ?)""",
                (order_id, user_id, qty, price, total_amount,
                 f"Limit {data['order_type']} order placed: {qty} {asset['symbol']} @ {price} ETB"),
            )
            conn.commit()
            return_db(conn)
            return jsonify({
                "message": "Limit order placed successfully (pending fill)",
                "order_id": order_id,
                "execution_type": "limit",
                "order_status": "pending",
                "total_amount": total_amount,
                "fee_amount": fee_amount,
                "fee_type": "flat (Sharia compliant — no interest)",
            }), 201

        # For demo: auto-fill market orders immediately (in production, use matching engine)
        conn.execute(
            "UPDATE orders SET order_status = 'filled', filled_quantity = quantity, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (order_id,),
        )

        if data["order_type"] == "buy":
            # Deduct from wallet
            new_balance = wallet["balance"] - total_amount - fee_amount
            conn.execute(
                "UPDATE wallets SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (new_balance, user_id),
            )
            # Add to portfolio
            existing = conn.execute(
                "SELECT * FROM portfolios WHERE user_id = ? AND asset_id = ?",
                (user_id, data["asset_id"]),
            ).fetchone()
            if existing:
                new_qty = existing["quantity"] + qty
                new_avg = ((existing["avg_buy_price"] * existing["quantity"]) + (price * qty)) / new_qty
                conn.execute(
                    """UPDATE portfolios SET quantity = ?, avg_buy_price = ?,
                       total_invested = total_invested + ?, updated_at = CURRENT_TIMESTAMP
                       WHERE user_id = ? AND asset_id = ?""",
                    (new_qty, new_avg, total_amount, user_id, data["asset_id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO portfolios (user_id, asset_id, quantity, avg_buy_price, total_invested) VALUES (?,?,?,?,?)",
                    (user_id, data["asset_id"], qty, price, total_amount),
                )
            # Transaction record
            conn.execute(
                """INSERT INTO transactions (user_id, transaction_type, amount, balance_after, reference_id, description)
                   VALUES (?, 'trade_buy', ?, ?, ?, ?)""",
                (user_id, total_amount + fee_amount, new_balance, str(order_id),
                 f"Buy {qty} {asset['symbol']} @ {price} ETB"),
            )
        else:
            # Sell: add to wallet, reduce portfolio
            wallet = conn.execute("SELECT balance FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
            new_balance = wallet["balance"] + total_amount - fee_amount
            conn.execute(
                "UPDATE wallets SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (new_balance, user_id),
            )
            holding = conn.execute(
                "SELECT * FROM portfolios WHERE user_id = ? AND asset_id = ?",
                (user_id, data["asset_id"]),
            ).fetchone()
            new_qty = holding["quantity"] - qty
            if new_qty <= 0:
                conn.execute(
                    "DELETE FROM portfolios WHERE user_id = ? AND asset_id = ?",
                    (user_id, data["asset_id"]),
                )
            else:
                conn.execute(
                    "UPDATE portfolios SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND asset_id = ?",
                    (new_qty, user_id, data["asset_id"]),
                )
            conn.execute(
                """INSERT INTO transactions (user_id, transaction_type, amount, balance_after, reference_id, description)
                   VALUES (?, 'trade_sell', ?, ?, ?, ?)""",
                (user_id, total_amount - fee_amount, new_balance, str(order_id),
                 f"Sell {qty} {asset['symbol']} @ {price} ETB"),
            )

        # Log placed + filled events (market orders are instantly filled)
        conn.execute(
            """INSERT INTO order_events (order_id, user_id, event_type, quantity, price, amount, details)
               VALUES (?, ?, 'placed', ?, ?, ?, ?)""",
            (order_id, user_id, qty, price, total_amount,
             f"Market {data['order_type']} order placed: {qty} {asset['symbol']} @ {price} ETB"),
        )
        conn.execute(
            """INSERT INTO order_events (order_id, user_id, event_type, quantity, price, amount, details)
               VALUES (?, ?, 'filled', ?, ?, ?, ?)""",
            (order_id, user_id, qty, price, total_amount,
             f"Market {data['order_type']} order filled: {qty} {asset['symbol']} @ {price} ETB | Fee: {fee_amount} ETB"),
        )
        # Audit
        conn.execute(
            "INSERT INTO audit_log (user_id, action, entity_type, entity_id, details) VALUES (?,?,?,?,?)",
            (user_id, f"order_{data['order_type']}", "order", order_id,
             f"{data['order_type']} {qty} {asset['symbol']} @ {price} ETB | Fee: {fee_amount} ETB"),
        )

        conn.commit()
        return_db(conn)
        return jsonify({
            "message": "Order placed and filled successfully",
            "order_id": order_id,
            "total_amount": total_amount,
            "fee_amount": fee_amount,
            "fee_type": "flat (Sharia compliant — no interest)",
        }), 201

      except sqlite3.OperationalError as e:
        conn.rollback()
        if "disk I/O error" in str(e) and attempt < max_retries - 1:
            return_db(conn)
            time.sleep(0.5 * (attempt + 1))
            conn = get_db()
            continue
        return_db(conn)
        return jsonify({"error": str(e)}), 500
      except Exception as e:
        conn.rollback()
        return_db(conn)
        return jsonify({"error": str(e)}), 500


@trading_bp.route("/orders", methods=["GET"])
@jwt_required()
def get_orders():
    user_id = int(get_jwt_identity())
    status = request.args.get("status")

    conn = get_db()
    query = """
        SELECT o.*, a.symbol, a.name as asset_name
        FROM orders o JOIN assets a ON o.asset_id = a.id
        WHERE o.user_id = ?
    """
    params = [user_id]
    if status:
        query += " AND o.order_status = ?"
        params.append(status)
    query += " ORDER BY o.created_at DESC LIMIT 50"

    orders = conn.execute(query, params).fetchall()
    return_db(conn)
    return jsonify([dict(o) for o in orders])


@trading_bp.route("/orders/<int:order_id>/cancel", methods=["POST"])
@jwt_required()
def cancel_order(order_id):
    user_id = int(get_jwt_identity())
    conn = get_db()
    order = conn.execute(
        """SELECT o.*, a.symbol FROM orders o
           JOIN assets a ON o.asset_id = a.id
           WHERE o.id = ? AND o.user_id = ?""",
        (order_id, user_id),
    ).fetchone()

    if not order:
        return_db(conn)
        return jsonify({"error": "Order not found"}), 404
    if order["order_status"] != "pending":
        return_db(conn)
        return jsonify({"error": "Only pending orders can be cancelled"}), 400

    conn.execute(
        "UPDATE orders SET order_status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (order_id,),
    )

    # Refund reserved funds for limit buy orders
    if order["order_type"] == "buy":
        refund = order["total_amount"] + order["fee_amount"]
        wallet = conn.execute("SELECT balance FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
        new_balance = (wallet["balance"] if wallet else 0) + refund
        conn.execute(
            "UPDATE wallets SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (new_balance, user_id),
        )
        conn.execute(
            """INSERT INTO transactions (user_id, transaction_type, amount, balance_after, reference_id, description)
               VALUES (?, 'refund', ?, ?, ?, ?)""",
            (user_id, refund, new_balance, str(order_id),
             f"Refund for cancelled buy order: {order['quantity']} {order['symbol']} @ {order['price']} ETB"),
        )

    # Log cancellation event (immutable audit trail)
    conn.execute(
        """INSERT INTO order_events (order_id, user_id, event_type, quantity, price, amount, details)
           VALUES (?, ?, 'cancelled', ?, ?, ?, ?)""",
        (order_id, user_id, order["quantity"], order["price"], order["total_amount"],
         f"Order cancelled: {order['order_type']} {order['quantity']} {order['symbol']} @ {order['price']} ETB"),
    )
    conn.commit()
    return_db(conn)
    return jsonify({"message": "Order cancelled"})


@trading_bp.route("/order-events", methods=["GET"])
@jwt_required()
def get_order_events():
    """Return all order lifecycle events for the user — regulatory audit trail."""
    user_id = int(get_jwt_identity())
    conn = get_db()
    events = conn.execute(
        """SELECT oe.*, o.order_type, a.symbol, a.name as asset_name
           FROM order_events oe
           JOIN orders o ON oe.order_id = o.id
           JOIN assets a ON o.asset_id = a.id
           WHERE oe.user_id = ?
           ORDER BY oe.created_at DESC LIMIT 100""",
        (user_id,),
    ).fetchall()
    return_db(conn)
    return jsonify([dict(e) for e in events])
