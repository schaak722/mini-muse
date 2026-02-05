from flask import Blueprint, request, jsonify
from flask_login import login_user, logout_user, login_required
from .models import User
from .extensions import db, login_manager

auth_bp = Blueprint("auth", __name__)

@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, user_id)

@auth_bp.post("/login")
def login():
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = db.session.query(User).filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "invalid_credentials"}), 401

    login_user(user)
    return jsonify({"ok": True, "user_id": user.pk_id})

@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    return jsonify({"ok": True})

