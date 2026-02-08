from flask import render_template, request, jsonify, url_for, redirect
from flask_login import login_required, current_user
from sqlalchemy import or_
from functools import wraps
from datetime import datetime
import math
from urllib.parse import urlencode

from . import routes_bp
from ..extensions import db
from ..models import User


def admin_required(f):
    """Decorator to require admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if current_user.role != 'admin':
            return redirect(url_for('routes.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


@routes_bp.get("/users")
@login_required
@admin_required
def users_list():
    """List all users with search and pagination"""
    q = (request.args.get("q") or "").strip()
    
    # Pagination
    per_page = int(request.args.get("per_page", 25))
    if per_page not in [25, 50, 100]:
        per_page = 25
    
    page = int(request.args.get("page", 1))
    if page < 1:
        page = 1
    
    # Base query
    query = db.session.query(User)
    
    # Search
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                User.email.ilike(like),
                User.full_name.ilike(like),
                User.role.ilike(like)
            )
        )
    
    # Count total
    total_count = query.count()
    total_pages = math.ceil(total_count / per_page) if total_count > 0 else 1
    
    # Ensure page doesn't exceed total pages
    if page > total_pages:
        page = total_pages
    
    # Get users for current page
    offset = (page - 1) * per_page
    users = query.order_by(User.created_at.desc()).limit(per_page).offset(offset).all()
    
    # Calculate display range
    start_item = offset + 1 if total_count > 0 else 0
    end_item = min(offset + per_page, total_count)
    
    # Generate smart page range
    page_range = []
    if total_pages <= 7:
        page_range = list(range(1, total_pages + 1))
    else:
        if page <= 4:
            page_range = list(range(1, 6)) + ['...', total_pages]
        elif page >= total_pages - 3:
            page_range = [1, '...'] + list(range(total_pages - 4, total_pages + 1))
        else:
            page_range = [1, '...'] + list(range(page - 1, page + 2)) + ['...', total_pages]
    
    # Build URL helper
    def build_url(target_page):
        params = {}
        if q:
            params['q'] = q
        if per_page != 25:
            params['per_page'] = per_page
        params['page'] = target_page
        return url_for('routes.users_list') + '?' + urlencode(params)
    
    # Count active users
    active_count = db.session.query(User).filter(User.is_active == True).count()
    
    return render_template(
        "users/list.html",
        active_nav="users",
        users=users,
        q=q,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        start_item=start_item,
        end_item=end_item,
        page_range=page_range,
        build_url=build_url,
        active_count=active_count
    )


@routes_bp.post("/users/add")
@login_required
@admin_required
def users_add():
    """Create a new user"""
    try:
        email = request.form.get('email', '').strip()
        full_name = request.form.get('full_name', '').strip()
        role = request.form.get('role', 'user').strip()
        password = request.form.get('password', '').strip()
        
        # Validation
        if not email or not full_name or not password:
            return jsonify({"error": "Email, full name, and password are required"}), 400
        
        if len(password) < 8:
            return jsonify({"error": "Password must be at least 8 characters"}), 400
        
        if role not in ['admin', 'user']:
            return jsonify({"error": "Invalid role"}), 400
        
        # Check if email already exists
        if db.session.query(User).filter(User.email == email).first():
            return jsonify({"error": "Email already exists"}), 400
        
        # Create user
        user = User(
            email=email,
            full_name=full_name,
            role=role,
            is_active=True
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        return jsonify({"success": True, "message": "User created successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@routes_bp.post("/users/<pk_id>/edit")
@login_required
@admin_required
def users_edit(pk_id):
    """Edit an existing user"""
    try:
        user = db.session.query(User).filter(User.pk_id == pk_id).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        email = request.form.get('email', '').strip()
        full_name = request.form.get('full_name', '').strip()
        role = request.form.get('role', '').strip()
        password = request.form.get('password', '').strip()
        
        # Validation
        if not email or not full_name:
            return jsonify({"error": "Email and full name are required"}), 400
        
        if role not in ['admin', 'user']:
            return jsonify({"error": "Invalid role"}), 400
        
        # Check if email already exists (excluding current user)
        existing_user = db.session.query(User).filter(User.email == email, User.pk_id != pk_id).first()
        if existing_user:
            return jsonify({"error": "Email already exists"}), 400
        
        # Update user
        user.email = email
        user.full_name = full_name
        user.role = role
        
        # Update password only if provided
        if password:
            if len(password) < 8:
                return jsonify({"error": "Password must be at least 8 characters"}), 400
            user.set_password(password)
        
        db.session.commit()
        
        return jsonify({"success": True, "message": "User updated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@routes_bp.post("/users/<pk_id>/deactivate")
@login_required
@admin_required
def users_deactivate(pk_id):
    """Deactivate a user"""
    try:
        user = db.session.query(User).filter(User.pk_id == pk_id).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        # Prevent deactivating yourself
        if user.pk_id == current_user.pk_id:
            return jsonify({"error": "Cannot deactivate your own account"}), 400
        
        user.is_active = False
        db.session.commit()
        
        return jsonify({"success": True, "message": "User deactivated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@routes_bp.post("/users/<pk_id>/reactivate")
@login_required
@admin_required
def users_reactivate(pk_id):
    """Reactivate a user"""
    try:
        user = db.session.query(User).filter(User.pk_id == pk_id).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        user.is_active = True
        db.session.commit()
        
        return jsonify({"success": True, "message": "User reactivated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
