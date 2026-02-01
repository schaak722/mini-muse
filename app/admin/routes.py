from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from ..decorators import require_admin
from ..extensions import db
from ..models import (
    User,
    SalesOrder,
    SalesLine,
    DailyMetric,
    SkuMetricDaily,
)
from .forms import UserCreateForm, UserEditForm

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _safe_date(val):
    s = (val or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


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


# -------------------------
# Phase 4: Metrics recompute
# -------------------------

@admin_bp.get("/metrics")
@login_required
@require_admin
def metrics_home():
    # Default range: month to date
    today = datetime.utcnow().date()
    start_month = today.replace(day=1)
    return render_template(
        "admin/metrics_recompute.html",
        d_from=start_month.isoformat(),
        d_to=today.isoformat(),
    )


@admin_bp.post("/metrics/recompute")
@login_required
@require_admin
def metrics_recompute():
    d_from = _safe_date(request.form.get("from") or "")
    d_to = _safe_date(request.form.get("to") or "")

    if not d_from or not d_to:
        flash("Please provide both From and To dates.", "danger")
        return redirect(url_for("admin.metrics_home"))

    if d_from > d_to:
        flash("From date cannot be after To date.", "danger")
        return redirect(url_for("admin.metrics_home"))

    # Delete existing rows in range
    DailyMetric.query.filter(DailyMetric.metric_date >= d_from, DailyMetric.metric_date <= d_to).delete(synchronize_session=False)
    SkuMetricDaily.query.filter(SkuMetricDaily.metric_date >= d_from, SkuMetricDaily.metric_date <= d_to).delete(synchronize_session=False)

    # Expressions for discount calculations
    disc_gross = (
        db.func.coalesce(SalesLine.line_discount_gross, 0) +
        db.func.coalesce(SalesLine.order_discount_alloc_gross, 0)
    )
    vat_factor = db.literal(1) + (SalesLine.vat_rate / db.literal(100))
    disc_net = disc_gross / vat_factor

    # Daily totals
    daily_rows = (
        db.session.query(
            SalesOrder.order_date.label("d"),
            db.func.count(db.func.distinct(SalesOrder.id)).label("orders"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("units"),
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("rev_net"),
            db.func.coalesce(db.func.sum(SalesLine.cost_total), 0).label("cogs"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
            db.func.coalesce(db.func.sum(disc_gross), 0).label("disc_gross"),
            db.func.coalesce(db.func.sum(disc_net), 0).label("disc_net"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
        .filter(SalesOrder.order_date >= d_from)
        .filter(SalesOrder.order_date <= d_to)
        .group_by(SalesOrder.order_date)
        .order_by(SalesOrder.order_date.asc())
        .all()
    )

    for r in daily_rows:
        db.session.add(
            DailyMetric(
                metric_date=r.d,
                orders_count=int(r.orders or 0),
                units=int(r.units or 0),
                revenue_net=Decimal(str(r.rev_net or 0)),
                cogs=Decimal(str(r.cogs or 0)),
                profit=Decimal(str(r.profit or 0)),
                discount_gross=Decimal(str(r.disc_gross or 0)),
                discount_net=Decimal(str(r.disc_net or 0)),
            )
        )

    # SKU daily rollups
    sku_rows = (
        db.session.query(
            SalesOrder.order_date.label("d"),
            SalesLine.sku.label("sku"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("units"),
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("rev_net"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
            db.func.coalesce(db.func.sum(disc_gross), 0).label("disc_gross"),
            db.func.coalesce(db.func.sum(disc_net), 0).label("disc_net"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
        .filter(SalesOrder.order_date >= d_from)
        .filter(SalesOrder.order_date <= d_to)
        .group_by(SalesOrder.order_date, SalesLine.sku)
        .order_by(SalesOrder.order_date.asc())
        .all()
    )

    for r in sku_rows:
        db.session.add(
            SkuMetricDaily(
                metric_date=r.d,
                sku=r.sku,
                units=int(r.units or 0),
                revenue_net=Decimal(str(r.rev_net or 0)),
                profit=Decimal(str(r.profit or 0)),
                discount_gross=Decimal(str(r.disc_gross or 0)),
                discount_net=Decimal(str(r.disc_net or 0)),
            )
        )

    db.session.commit()

    flash(f"Metrics recomputed for {d_from.isoformat()} → {d_to.isoformat()}.", "success")
    return redirect(url_for("admin.metrics_home"))
