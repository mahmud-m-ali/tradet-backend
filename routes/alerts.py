"""Price alerts routes — notify users when assets hit target prices."""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from database import get_db, return_db

alerts_bp = Blueprint("alerts", __name__)


@alerts_bp.route("/alerts", methods=["GET"])
@jwt_required()
def get_alerts():
    """Get user's price alerts."""
    user_id = int(get_jwt_identity())
    conn = get_db()
    alerts = conn.execute(
        """SELECT pa.*, a.symbol, a.name as asset_name,
                  mp.price as current_price
           FROM price_alerts pa
           JOIN assets a ON pa.asset_id = a.id
           LEFT JOIN market_prices mp ON mp.asset_id = a.id
               AND mp.id = (SELECT MAX(id) FROM market_prices WHERE asset_id = a.id)
           WHERE pa.user_id = ? AND pa.is_active = 1
           ORDER BY pa.created_at DESC""",
        (user_id,),
    ).fetchall()
    return_db(conn)
    return jsonify([dict(a) for a in alerts])


@alerts_bp.route("/alerts", methods=["POST"])
@jwt_required()
def create_alert():
    """Create a new price alert."""
    user_id = int(get_jwt_identity())
    data = request.get_json()

    required = ["asset_id", "target_price", "condition"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400

    if data["condition"] not in ("above", "below"):
        return jsonify({"error": "condition must be 'above' or 'below'"}), 400

    conn = get_db()
    # Verify asset exists
    asset = conn.execute("SELECT id FROM assets WHERE id = ?", (data["asset_id"],)).fetchone()
    if not asset:
        return_db(conn)
        return jsonify({"error": "Asset not found"}), 404

    # Limit alerts per user
    count = conn.execute(
        "SELECT COUNT(*) FROM price_alerts WHERE user_id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()[0]
    if count >= 50:
        return_db(conn)
        return jsonify({"error": "Maximum 50 active alerts allowed"}), 400

    conn.execute(
        """INSERT INTO price_alerts (user_id, asset_id, target_price, condition, note)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, data["asset_id"], float(data["target_price"]),
         data["condition"], data.get("note", "")),
    )
    alert_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return_db(conn)

    return jsonify({"message": "Alert created", "alert_id": alert_id}), 201


@alerts_bp.route("/alerts/<int:alert_id>", methods=["DELETE"])
@jwt_required()
def delete_alert(alert_id):
    """Delete a price alert."""
    user_id = int(get_jwt_identity())
    conn = get_db()
    conn.execute(
        "DELETE FROM price_alerts WHERE id = ? AND user_id = ?",
        (alert_id, user_id),
    )
    conn.commit()
    return_db(conn)
    return jsonify({"message": "Alert deleted"})


@alerts_bp.route("/alerts/triggered", methods=["GET"])
@jwt_required()
def get_triggered_alerts():
    """Get alerts that have been triggered."""
    user_id = int(get_jwt_identity())
    conn = get_db()
    alerts = conn.execute(
        """SELECT pa.*, a.symbol, a.name as asset_name
           FROM price_alerts pa
           JOIN assets a ON pa.asset_id = a.id
           WHERE pa.user_id = ? AND pa.is_triggered = 1
           ORDER BY pa.triggered_at DESC LIMIT 50""",
        (user_id,),
    ).fetchall()
    return_db(conn)
    return jsonify([dict(a) for a in alerts])


def check_and_trigger_alerts(app):
    """Check all active alerts against current prices and trigger matches."""
    with app.app_context():
        conn = get_db()
        alerts = conn.execute(
            """SELECT pa.*, mp.price as current_price
               FROM price_alerts pa
               JOIN market_prices mp ON mp.asset_id = pa.asset_id
                   AND mp.id = (SELECT MAX(id) FROM market_prices WHERE asset_id = pa.asset_id)
               WHERE pa.is_active = 1 AND pa.is_triggered = 0"""
        ).fetchall()

        triggered = 0
        for alert in alerts:
            should_trigger = False
            if alert["condition"] == "above" and alert["current_price"] >= alert["target_price"]:
                should_trigger = True
            elif alert["condition"] == "below" and alert["current_price"] <= alert["target_price"]:
                should_trigger = True

            if should_trigger:
                conn.execute(
                    """UPDATE price_alerts SET is_triggered = 1, is_active = 0,
                       triggered_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (alert["id"],),
                )
                triggered += 1

        if triggered:
            conn.commit()
        return_db(conn)
        return triggered
