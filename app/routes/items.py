from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import or_

from . import routes_bp
from ..extensions import db
from ..models import Item, AuditLog
from ..forms import ItemForm


def audit(entity_type, entity_pk_id, action, field=None, old=None, new=None, reason=None):
    db.session.add(
        AuditLog(
            entity_type=entity_type,
            entity_pk_id=entity_pk_id,
            action=action,
            field_name=field,
            old_value=None if old is None else str(old),
            new_value=None if new is None else str(new),
            reason=reason,
            actor_user_id=getattr(current_user, "pk_id", None),
        )
    )


@routes_bp.get("/items")
@login_required
def items_list():
    from datetime import datetime
    import math
    from urllib.parse import urlencode
    
    q = (request.args.get("q") or "").strip()

    date_type = request.args.get("date_type", "arrival")
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    
    # Pagination
    per_page = int(request.args.get("per_page", 25))
    if per_page not in [25, 50, 100]:
        per_page = 25
    
    page = int(request.args.get("page", 1))
    if page < 1:
        page = 1

    # Inventory page = IN_STOCK only (per spec)
    query = db.session.query(Item).filter(Item.status == "IN_STOCK")

    # Search: SKU, Order #, Supplier (Company), Brand, Description
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Item.sku.ilike(like),
                Item.order_number.ilike(like),
                Item.company_name.ilike(like),
                Item.brand.ilike(like),
                Item.item_description.ilike(like),
            )
        )

    # Date range based on selected type - convert strings to date objects
    try:
        if date_type == "order":
            if date_from:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
                query = query.filter(Item.order_date >= date_from_obj)
            if date_to:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
                query = query.filter(Item.order_date <= date_to_obj)
        else:  # arrival
            if date_from:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
                query = query.filter(Item.arrival_date >= date_from_obj)
            if date_to:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
                query = query.filter(Item.arrival_date <= date_to_obj)
    except ValueError as e:
        flash(f"Invalid date format. Please use the date picker.", "error")

    # Count total
    total_count = query.count()
    total_pages = math.ceil(total_count / per_page) if total_count > 0 else 1
    
    # Ensure page doesn't exceed total pages
    if page > total_pages:
        page = total_pages
    
    # Get items for current page
    offset = (page - 1) * per_page
    items = query.order_by(Item.arrival_date.desc()).limit(per_page).offset(offset).all()
    
    # Calculate display range
    start_item = offset + 1 if total_count > 0 else 0
    end_item = min(offset + per_page, total_count)
    
    # Generate smart page range
    page_range = []
    if total_pages <= 7:
        # Show all pages
        page_range = list(range(1, total_pages + 1))
    else:
        # Smart pagination with ellipsis
        if page <= 4:
            # Near start: 1 2 3 4 5 ... last
            page_range = list(range(1, 6)) + ['...', total_pages]
        elif page >= total_pages - 3:
            # Near end: 1 ... -4 -3 -2 -1 last
            page_range = [1, '...'] + list(range(total_pages - 4, total_pages + 1))
        else:
            # Middle: 1 ... page-1 page page+1 ... last
            page_range = [1, '...'] + list(range(page - 1, page + 2)) + ['...', total_pages]
    
    # Build URL helper
    def build_url(target_page):
        params = {}
        if q:
            params['q'] = q
        if date_type and date_type != 'arrival':
            params['date_type'] = date_type
        if date_from:
            params['date_from'] = date_from
        if date_to:
            params['date_to'] = date_to
        if per_page != 25:
            params['per_page'] = per_page
        params['page'] = target_page
        return url_for('routes.items_list') + '?' + urlencode(params)

    return render_template(
        "items/list.html",
        active_nav="items",
        items=items,
        q=q,
        date_type=date_type,
        date_from=date_from,
        date_to=date_to,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        start_item=start_item,
        end_item=end_item,
        page_range=page_range,
        build_url=build_url,
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
    db.session.flush()  # ensure pk exists for audit
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

    for f in [
        "user_item_id",
        "order_number",
        "order_date",
        "arrival_date",
        "company_name",
        "brand",
        "item_description",
        "sku",
        "net_unit_cost",
        "freight_net",
        "vat_rate",
    ]:
        old = getattr(i, f)
        new = getattr(form, f).data
        if old != new:
            setattr(i, f, new)
            audit("ITEM", i.pk_id, "UPDATE", field=f, old=old, new=new)

    db.session.commit()
    flash("Item updated.", "ok")
    return redirect(url_for("routes.items_list"))


@routes_bp.post("/items/<pk_id>/update-comments")
@login_required
def items_update_comments(pk_id):
    """Update item comments via AJAX"""
    i = db.session.get(Item, pk_id)
    if not i:
        return {"error": "Item not found"}, 404
    
    comments = request.form.get("comments", "").strip()
    old_comments = i.comments
    i.comments = comments if comments else None
    
    if old_comments != i.comments:
        audit("ITEM", i.pk_id, "UPDATE", field="comments", old=old_comments, new=i.comments)
    
    db.session.commit()
    return {"success": True, "comments": i.comments or ""}

