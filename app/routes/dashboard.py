from flask import render_template
from flask_login import login_required
from sqlalchemy import func
from . import routes_bp
from ..extensions import db
from ..models import Item, Sale

@routes_bp.get("/")
@login_required
def dashboard():
    in_stock = db.session.query(func.count(Item.pk_id)).filter(Item.status == "IN_STOCK").scalar() or 0
    sold = db.session.query(func.count(Item.pk_id)).filter(Item.status == "SOLD").scalar() or 0
    total_profit = db.session.query(func.coalesce(func.sum(Sale.item_profit), 0)).scalar()

    return render_template(
        "dashboard.html",
        active_nav="dashboard",
        in_stock=int(in_stock),
        sold=int(sold),
        total_profit=str(total_profit),
    )
