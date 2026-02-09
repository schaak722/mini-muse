from flask import render_template, request
from flask_login import login_required
from . import routes_bp
from ..utils.kpi_calculator import get_dashboard_kpis

@routes_bp.get("/")
@login_required
def dashboard():
    # Get filter parameters
    period = request.args.get('period', 'last_7_days')
    custom_from = request.args.get('date_from')
    custom_to = request.args.get('date_to')
    
    # Calculate all KPIs
    kpis = get_dashboard_kpis(period, custom_from, custom_to)
    
    return render_template(
        "dashboard.html",
        active_nav="dashboard",
        kpis=kpis,
        period=period,
        custom_from=custom_from or '',
        custom_to=custom_to or ''
    )
