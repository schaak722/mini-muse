from flask import Blueprint, request, jsonify, render_template, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from .models import User
from .extensions import db, login_manager
from .forms import LoginForm

auth_bp = Blueprint("auth", __name__)

@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, user_id)

# HTML login page (primary)
@auth_bp.get("/login")
def login_page():
    form = LoginForm()
    return render_template("auth_login.html", form=form)

@auth_bp.post("/login")
def login_post():
    from datetime import datetime
    
    form = LoginForm()
    if not form.validate_on_submit():
        flash("Please enter email + password.", "error")
        return render_template("auth_login.html", form=form), 400

    email = form.email.data.strip().lower()
    user = db.session.query(User).filter_by(email=email).first()
    if not user or not user.check_password(form.password.data):
        flash("Invalid credentials.", "error")
        return render_template("auth_login.html", form=form), 401
    
    # Check if user is active
    if not user.is_active:
        flash("Your account has been deactivated. Please contact an administrator.", "error")
        return render_template("auth_login.html", form=form), 401

    # Update last login time
    user.last_login_at = datetime.utcnow()
    db.session.commit()
    
    login_user(user)
    return redirect(url_for("routes.dashboard"))

@auth_bp.get("/logout")
@login_required
def logout_page():
    logout_user()
    return redirect(url_for("auth.login_page"))

# JSON login endpoint (optional / kept)
@auth_bp.post("/api/login")
def login_json():
    from datetime import datetime
    
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = db.session.query(User).filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "invalid_credentials"}), 401
    
    # Check if user is active
    if not user.is_active:
        return jsonify({"error": "account_deactivated"}), 401

    # Update last login time
    user.last_login_at = datetime.utcnow()
    db.session.commit()
    
    login_user(user)
    return jsonify({"ok": True, "user_id": user.pk_id})

@auth_bp.post("/api/logout")
@login_required
def logout_json():
    logout_user()
    return jsonify({"ok": True})
