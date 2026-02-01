from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, render_template
from flask_login import login_required, current_user

from ..decorators import require_role
from ..extensions import db
from ..models import SalesOrder, SalesLine, DailyMetric

main_bp = Blueprint("main", __name__, url_prefix="")


@main_bp.get("/")
@login_required
def root():
    return dashboard()


def _to_decimal(x) -> Decimal:
    try:
        return Decimal(str(x or 0))
    except Exception:
        return Decimal("0")


def _sum_period_live(date_from, date_to):
    row = (
        db.session.query(
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("rev"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("units"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
        .filter(SalesOrder.order_date >= date_from)
        .filter(SalesOrder.order_date <= date_to)
        .one()
    )
    return (_to_decimal(row.rev), _to_decimal(row.profit), int(row.units or 0))


def _sum_period_agg(date_from, date_to):
    row = (
        db.session.query(
            db.func.coalesce(db.func.sum(DailyMetric.revenue_net), 0).label("rev"),
            db.func.coalesce(db.func.sum(DailyMetric.profit), 0).label("profit"),
            db.func.coalesce(db.func.sum(DailyMetric.units), 0).label("units"),
        )
        .filter(DailyMetric.metric_date >= date_from)
        .filter(DailyMetric.metric_date <= date_to)
        .one()
    )
    return (_to_decimal(row.rev), _to_decimal(row.profit), int(row.units or 0))


def _sum_period(date_from, date_to):
    # If aggregates exist for the range, use them; otherwise fallback.
    any_agg = (
        db.session.query(db.func.count(DailyMetric.id))
        .filter(DailyMetric.metric_date >= date_from)
        .filter(DailyMetric.metric_date <= date_to)
        .scalar()
    ) or 0
    if any_agg > 0:
        return _sum_period_agg(date_from, date_to)
    return _sum_period_live(date_from, date_to)


@main_bp.get("/dashboard")
@login_required
@require_role("viewer")
def dashboard():
    today = datetime.utcnow().date()
    start_7d = today - timedelta(days=6)
    start_month = today.replace(day=1)

    rev_7d, profit_7d, units_7d = _sum_period(start_7d, today)
    rev_mtd, profit_mtd, units_mtd = _sum_period(start_month, today)

    margin_7d = (profit_7d / rev_7d * Decimal("100")) if rev_7d > 0 else Decimal("0")
    margin_mtd = (profit_mtd / rev_mtd * Decimal("100")) if rev_mtd > 0 else Decimal("0")

    missing_cost_lines_mtd = (
        db.session.query(db.func.count(SalesLine.id))
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
        .filter(SalesOrder.order_date >= start_month)
        .filter(SalesOrder.order_date <= today)
        .filter(db.or_(SalesLine.unit_cost_basis.is_(None), SalesLine.unit_cost_basis == 0))
        .scalar()
    ) or 0

    top_by_units = (
        db.session.query(
            SalesLine.sku.label("sku"),
            db.func.max(SalesLine.description).label("description"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("units"),
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("rev"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
        .filter(SalesOrder.order_date >= start_month)
        .filter(SalesOrder.order_date <= today)
        .group_by(SalesLine.sku)
        .order_by(db.desc(db.func.coalesce(db.func.sum(SalesLine.qty), 0)))
        .limit(10)
        .all()
    )

    top_by_profit = (
        db.session.query(
            SalesLine.sku.label("sku"),
            db.func.max(SalesLine.description).label("description"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("units"),
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("rev"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
        .filter(SalesOrder.order_date >= start_month)
        .filter(SalesOrder.order_date <= today)
        .group_by(SalesLine.sku)
        .order_by(db.desc(db.func.coalesce(db.func.sum(SalesLine.profit), 0)))
        .limit(10)
        .all()
    )

    recent_orders = SalesOrder.query.order_by(SalesOrder.order_date.desc(), SalesOrder.id.desc()).limit(10).all()
    recent_ids = [o.id for o in recent_orders]

    recent_totals_map = {}
    if recent_ids:
        recent_totals = (
            db.session.query(
                SalesLine.sales_order_id,
                db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("rev"),
                db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
                db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("units"),
            )
            .filter(SalesLine.sales_order_id.in_(recent_ids))
            .group_by(SalesLine.sales_order_id)
            .all()
        )
        recent_totals_map = {
            oid: {"rev": _to_decimal(rev), "profit": _to_decimal(profit), "units": int(units or 0)}
            for oid, rev, profit, units in recent_totals
        }

    kpis = {
        "today": today.isoformat(),
        "rev_7d": rev_7d,
        "profit_7d": profit_7d,
        "margin_7d": margin_7d,
        "units_7d": units_7d,
        "rev_mtd": rev_mtd,
        "profit_mtd": profit_mtd,
        "margin_mtd": margin_mtd,
        "units_mtd": units_mtd,
        "missing_cost_lines_mtd": missing_cost_lines_mtd,
    }

    return render_template(
        "main/dashboard.html",
        kpis=kpis,
        user=current_user,
        top_by_units=top_by_units,
        top_by_profit=top_by_profit,
        recent_orders=recent_orders,
        recent_totals_map=recent_totals_map,
    )
