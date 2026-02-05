from decimal import Decimal, ROUND_HALF_UP
from flask import render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import or_
from . import routes_bp
from ..extensions import db
from ..models import Item, Sale, AuditLog
from ..forms import SaleForm, ReverseSaleForm
from flask import request

def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def compute_snapshots(gross_price: Decimal, vat_rate: Decimal, total_cost_net: Decimal):
    net_rev = q2(gross_price / (Decimal("1.0") + vat_rate))
    vat_amt = q2(gross_price - net_rev)
    profit = q2(net_rev - total_cost_net)
    return net_rev, vat_amt, profit

def audit(entity_type, entity_pk_id, action, field=None, old=None, new=None, reason=None):
    db.session.add(AuditLog(
        entity_type=entity_type,
        entity_pk_id=entity_pk_id,
        action=action,
        field_name=field,
        old_value=None if old is None else str(old),
        new_value=None if new is None else str(new),
        reason=reason,
        actor_user_id=getattr(current_user, "pk_id", None),
    ))

@routes_bp.get("/sales")
@login_required
def sales_list():
    sales = db.session.query(Sale).order_by(Sale.sale_date.desc()).limit(500).all()
    reverse_form = ReverseSaleForm()
    return render_template("sales/list.html", active_nav="sales", sales=sales, reverse_form=reverse_form)

@routes_bp.get("/sales/new")
@login_required
def sales_new():
    form = SaleForm()

    # Optional prefill from item view page
    prefill = (request.args.get("item_pk_id") or "").strip()
    if prefill:
        form.item_pk_id.data = prefill

    # Dropdown list of IN_STOCK items
    items = (
        db.session.query(Item)
        .filter(Item.status == "IN_STOCK")
        .order_by(Item.arrival_date.desc())
        .limit(500)
        .all()
    )

    return render_template("sales/new.html", active_nav="sales", form=form, items=items)

@routes_bp.post("/sales/new")
@login_required
def sales_create():
    form = SaleForm()
    if not form.validate_on_submit():
        flash("Please fix the highlighted fields.", "error")
        return render_template("sales/new.html", active_nav="sales", form=form), 400

    item = db.session.get(Item, form.item_pk_id.data.strip())
    if not item:
        flash("Item not found. Check the Item PK ID.", "error")
        return render_template("sales/new.html", active_nav="sales", form=form), 400
    if item.status != "IN_STOCK":
        flash("That item is not IN_STOCK.", "error")
        return render_template("sales/new.html", active_nav="sales", form=form), 400

    gross = Decimal(str(form.item_selling_price_gross.data))
    vat_rate = Decimal(str(item.vat_rate))

    packaging = Decimal(str(form.packaging_net.data or 0))
    delivery_cost = Decimal(str(form.delivery_cost_net.data or 0))
    other = Decimal(str(form.other_cost_net.data or 0))

    total_cost = Decimal(str(item.net_unit_cost)) + Decimal(str(item.freight_net)) + packaging + delivery_cost + other
    net_rev, vat_amt, profit = compute_snapshots(gross, vat_rate, total_cost)

    sale = Sale(
        item_pk_id=item.pk_id,
        sale_date=form.sale_date.data,
        item_selling_price_gross=gross,
        discount_type=form.discount_type.data or None,
        discount_value=form.discount_value.data,
        discount_amount_gross=form.discount_amount_gross.data,
        delivery_fee_charged_gross=form.delivery_fee_charged_gross.data or 0,
        packaging_net=packaging,
        delivery_cost_net=delivery_cost,
        other_cost_net=other,
        item_net_revenue=net_rev,
        item_vat_amount=vat_amt,
        item_profit=profit,
        notes=form.notes.data,
        created_by=current_user.pk_id,
    )

    item.status = "SOLD"

    db.session.add(sale)
    audit("SALE", sale.pk_id, "CREATE")
    audit("ITEM", item.pk_id, "UPDATE", field="status", old="IN_STOCK", new="SOLD")
    db.session.commit()

    flash("Sale recorded (item marked SOLD).", "ok")
    return redirect(url_for("routes.sales_list"))

@routes_bp.post("/sales/<sale_pk_id>/reverse")
@login_required
def sales_reverse(sale_pk_id):
    form = ReverseSaleForm()
    sale = db.session.get(Sale, sale_pk_id)
    if not sale:
        flash("Sale not found.", "error")
        return redirect(url_for("routes.sales_list"))

    if not form.validate_on_submit():
        flash("Reverse requires a reason.", "error")
        return redirect(url_for("routes.sales_list"))

    item = db.session.get(Item, sale.item_pk_id)
    if not item:
        flash("Linked item missing (data issue).", "error")
        return redirect(url_for("routes.sales_list"))

    reason = form.reason.data.strip()
    item.status = "IN_STOCK"
    audit("ITEM", item.pk_id, "UPDATE", field="status", old="SOLD", new="IN_STOCK", reason=reason)
    audit("SALE", sale.pk_id, "REVERSE_SALE", reason=reason)

    db.session.delete(sale)
    db.session.commit()

    flash("Sale reversed (item back to IN_STOCK).", "ok")
    return redirect(url_for("routes.sales_list"))

