"""Authentication and KYC routes."""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity
import bcrypt
from database import get_db, return_db
from utils.encryption import encrypt_field, decrypt_field

auth_bp = Blueprint("auth", __name__)


def _validate_password(password):
    """Validate password meets minimum security requirements."""
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not any(c.isupper() for c in password):
        return "Password must contain at least one uppercase letter"
    if not any(c.islower() for c in password):
        return "Password must contain at least one lowercase letter"
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one digit"
    return None


def _validate_email(email):
    """Basic email validation."""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    required = ["email", "phone", "password", "full_name"]
    for field in required:
        if field not in data or not str(data[field]).strip():
            return jsonify({"error": f"Missing field: {field}"}), 400

    # Validate email format
    if not _validate_email(data["email"]):
        return jsonify({"error": "Invalid email format"}), 400

    # Validate password strength
    pwd_error = _validate_password(data["password"])
    if pwd_error:
        return jsonify({"error": pwd_error}), 400

    # Sanitize inputs
    data["email"] = data["email"].strip().lower()
    data["full_name"] = data["full_name"].strip()
    data["phone"] = data["phone"].strip()

    password_hash = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()

    # Encrypt PII fields at rest (INSA CSMS — Technology pillar)
    enc_full_name = encrypt_field(data["full_name"])
    enc_phone = encrypt_field(data["phone"])

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO users (email, phone, password_hash, full_name, account_type)
               VALUES (?, ?, ?, ?, ?)""",
            (data["email"], enc_phone, password_hash, enc_full_name,
             data.get("account_type", "individual")),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Create wallet for the user
        conn.execute("INSERT INTO wallets (user_id, balance) VALUES (?, 0)", (user_id,))

        # Audit log
        conn.execute(
            "INSERT INTO audit_log (user_id, action, entity_type, details) VALUES (?, ?, ?, ?)",
            (user_id, "register", "user", f"New user registered: {data['email']}"),
        )
        conn.commit()

        token = create_access_token(identity=str(user_id))
        refresh = create_refresh_token(identity=str(user_id))
        return jsonify({
            "message": "Registration successful. Please complete KYC verification.",
            "user_id": user_id,
            "token": token,
            "refresh_token": refresh,
        }), 201
    except Exception as e:
        conn.rollback()
        if "UNIQUE constraint" in str(e):
            return jsonify({"error": "Email or phone already registered"}), 409
        return jsonify({"error": str(e)}), 500
    finally:
        return_db(conn)


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data or "email" not in data or "password" not in data:
        return jsonify({"error": "Email and password required"}), 400

    email = data["email"].strip().lower()

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return_db(conn)

    if not user:
        return jsonify({"error": "Invalid credentials"}), 401

    if not bcrypt.checkpw(data["password"].encode(), user["password_hash"].encode()):
        return jsonify({"error": "Invalid credentials"}), 401

    if not user["is_active"]:
        return jsonify({"error": "Account is deactivated"}), 403

    token = create_access_token(identity=str(user["id"]))
    refresh = create_refresh_token(identity=str(user["id"]))
    return jsonify({
        "token": token,
        "refresh_token": refresh,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": decrypt_field(user["full_name"]),
            "kyc_status": user["kyc_status"],
            "account_type": user["account_type"],
        },
    })


@auth_bp.route("/kyc", methods=["POST"])
@jwt_required()
def submit_kyc():
    """Submit KYC documents for verification (Ethiopian & Sharia compliant)."""
    user_id = int(get_jwt_identity())
    data = request.get_json()

    required = ["id_type", "id_number"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400

    valid_id_types = ["national_id", "passport", "drivers_license", "kebele_id"]
    if data["id_type"] not in valid_id_types:
        return jsonify({"error": f"ID type must be one of: {valid_id_types}"}), 400

    conn = get_db()
    # Encrypt sensitive KYC fields at rest
    conn.execute(
        """UPDATE users SET kyc_id_type = ?, kyc_id_number = ?, kyc_status = 'verified',
           trade_license_number = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (data["id_type"], encrypt_field(data["id_number"]),
         encrypt_field(data.get("trade_license_number")), user_id),
    )
    conn.execute(
        "INSERT INTO audit_log (user_id, action, entity_type, details) VALUES (?, ?, ?, ?)",
        (user_id, "kyc_submit", "user", f"KYC submitted: {data['id_type']}"),
    )
    conn.commit()
    return_db(conn)

    return jsonify({"message": "KYC verification submitted successfully", "status": "verified"})


@auth_bp.route("/profile", methods=["GET"])
@jwt_required()
def get_profile():
    user_id = int(get_jwt_identity())
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    wallet = conn.execute("SELECT balance FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    return_db(conn)

    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify({
        "id": user["id"],
        "email": user["email"],
        "phone": decrypt_field(user["phone"]),
        "full_name": decrypt_field(user["full_name"]),
        "kyc_status": user["kyc_status"],
        "account_type": user["account_type"],
        "wallet_balance": wallet["balance"] if wallet else 0,
        "created_at": user["created_at"],
    })


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh_token():
    """Refresh an expired access token using a refresh token."""
    user_id = get_jwt_identity()
    new_token = create_access_token(identity=user_id)
    return jsonify({"token": new_token})
