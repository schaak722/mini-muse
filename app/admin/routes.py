from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from ..decorators import require_admin
from ..extensions import db
from ..models import User
from .forms import UserCreateForm, UserEditForm

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

@admin_bp.get("/users")
@login_required
@require_admin
def users_list():
    q = (request.args.get("q") or "").strip().lower()

    query = User.query
    if q:
        query = query.filter(
            db.or_(
                db.func.lower(User.email).contains(q),
                db.func.lower(User.name).contains(q),
                db.func.lower(User.role).contains(q),
            )
        )

    users = query.order_by(User.created_at.desc()).all()
    return render_template("admin/users_list.html", users=users, q=q)

@admin_bp.get("/users/new")
@admin_bp.post("/users/new")
@login_required
@require_admin
def users_new():
    form = UserCreateForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if User.query.filter_by(email=email).first():
            flash("A user with that email already exists.", "danger")
            return render_template("admin/user_form.html", form=form, mode="create")

        user = User(
            email=email,
            name=form.name.data.strip(),
            role=form.role.data,
            is_active=bool(form.is_active.data),
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        flash("User created.", "success")
        return redirect(url_for("admin.users_list"))

    return render_template("admin/user_form.html", form=form, mode="create")

@admin_bp.get("/users/<int:user_id>/edit")
@admin_bp.post("/users/<int:user_id>/edit")
@login_required
@require_admin
def users_edit(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin.users_list"))

    form = UserEditForm(obj=user)

    if form.validate_on_submit():
        user.name = form.name.data.strip()
        user.role = form.role.data
        user.is_active = bool(form.is_active.data)

        if form.new_password.data:
            user.set_password(form.new_password.data)

        # prevent admin from locking themselves out accidentally
        if user.id == current_user.id and user.is_active is False:
            flash("You cannot deactivate your own account.", "danger")
            db.session.rollback()
            return render_template("admin/user_form.html", form=form, mode="edit", user=user)

        db.session.commit()
        flash("User updated.", "success")
        return redirect(url_for("admin.users_list"))

    return render_template("admin/user_form.html", form=form, mode="edit", user=user)

