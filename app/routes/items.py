from flask import render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from . import routes_bp
from ..extensions import db
from ..models import Item, AuditLog
from ..forms import ItemForm
from flask import request
from sqlalchemy import or_

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

@routes_bp.get("/items")
@login_required
def items_list():
    status = (request.args.get("status") or "").strip()
    q = (request.args.get("q") or "").strip()

    query = db.session.query(Item)

    if status in ("IN_STOCK", "SOLD"):
        query = query.filter(Item.status == status)

    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Item.sku.ilike(like),
            Item.user_item_id.ilike(like),
            Item.order_number.ilike(like),
            Item.item_description.ilike(like),
        ))

    items = query.order_by(Item.arrival_date.desc()).limit(500).all()

    return render_template(
        "items/list.html",
        active_nav="items",
        items=items,
        status=status,
        q=q,
    )
@routes_bp.get("/items/new")
@login_required
def items_new():
    form = ItemForm()
    return render_template("items/new.html", active_nav="items", form=form)

@routes_bp.post("/items/new")
@login_required
def items_create():
    form = ItemForm()
    if not form.validate_on_submit():
        flash("Please fix the highlighted fields.", "error")
        return render_template("items/new.html", active_nav="items", form=form), 400

    i = Item(
        user_item_id=form.user_item_id.data.strip(),
        status="IN_STOCK",
        order_number=form.order_number.data.strip(),
        order_date=form.order_date.data,
        arrival_date=form.arrival_date.data,
        company_name=form.company_name.data.strip(),
        brand=form.brand.data.strip(),
        item_description=form.item_description.data.strip(),
        sku=form.sku.data.strip(),
        net_unit_cost=form.net_unit_cost.data,
        freight_net=form.freight_net.data,
        vat_rate=form.vat_rate.data,
        created_by=current_user.pk_id,
    )
    db.session.add(i)
    db.session.flush()
    audit("ITEM", i.pk_id, "CREATE")
    db.session.commit()
    flash("Item created.", "ok")
    return redirect(url_for("routes.items_list"))

@routes_bp.get("/items/<pk_id>/edit")
@login_required
def items_edit(pk_id):
    i = db.session.get(Item, pk_id)
    if not i:
        flash("Item not found.", "error")
        return redirect(url_for("routes.items_list"))
    form = ItemForm(obj=i)
    return render_template("items/edit.html", active_nav="items", form=form, item=i)

@routes_bp.post("/items/<pk_id>/edit")
@login_required
def items_update(pk_id):
    i = db.session.get(Item, pk_id)
    if not i:
        flash("Item not found.", "error")
        return redirect(url_for("routes.items_list"))

    form = ItemForm()
    if not form.validate_on_submit():
        flash("Please fix the highlighted fields.", "error")
        return render_template("items/edit.html", active_nav="items", form=form, item=i), 400

    # audit diffs
    for f in ["user_item_id","order_number","order_date","arrival_date","company_name","brand","item_description","sku","net_unit_cost","freight_net","vat_rate"]:
        old = getattr(i, f)
        new = getattr(form, f).data
        if old != new:
            setattr(i, f, new)
            audit("ITEM", i.pk_id, "UPDATE", field=f, old=old, new=new)

    db.session.commit()
    flash("Item updated.", "ok")
    return redirect(url_for("routes.items_list"))
