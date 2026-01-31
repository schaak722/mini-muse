from flask import Blueprint, render_template
from flask_login import login_required, current_user
from ..decorators import require_role

main_bp = Blueprint("main", __name__, url_prefix="")

@main_bp.get("/")
@login_required
def root():
    return dashboard()

@main_bp.get("/dashboard")
@login_required
@require_role("viewer")
def dashboard():
    # Phase 0: placeholder KPIs
    kpis = {
        "revenue_week": 0,
        "revenue_month": 0,
        "top_sku": "-",
        "top_units": 0,
    }
    return render_template("main/dashboard.html", kpis=kpis, user=current_user)

