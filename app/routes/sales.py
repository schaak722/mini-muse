from decimal import Decimal
from datetime import date
from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_

from . import routes_bp
from ..extensions import db
from ..models import Item, Sale, AuditLog


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


def calculate_sale_metrics(selling_price_gross, vat_rate, net_unit_cost, freight_net, 
                          packaging_net, delivery_cost_net, other_cost_net):
    """Calculate net revenue, VAT amount, and profit for a sale"""
    vat_multiplier = Decimal("1") + vat_rate
    
    # Net revenue = gross selling price / (1 + VAT rate)
    net_revenue = selling_price_gross / vat_multiplier
    
    # VAT amount = gross - net
    vat_amount = selling_price_gross - net_revenue
    
    # Profit = net revenue - all costs
    total_costs = net_unit_cost + freight_net + packaging_net + delivery_cost_net + other_cost_net
    profit = net_revenue - total_costs
    
    return {
        'net_revenue': net_revenue.quantize(Decimal('0.01')),
        'vat_amount': vat_amount.quantize(Decimal('0.01')),
        'profit': profit.quantize(Decimal('0.01'))
    }


@routes_bp.get("/sales")
@login_required
def sales_list():
    """List all sold items with their sale details"""
    from datetime import datetime
    import math
    from urllib.parse import urlencode
    
    q = (request.args.get("q") or "").strip()
    
    order_from = (request.args.get("order_from") or "").strip()
    order_to = (request.args.get("order_to") or "").strip()
    sale_from = (request.args.get("sale_from") or "").strip()
    sale_to = (request.args.get("sale_to") or "").strip()
    
    # Pagination
    per_page = int(request.args.get("per_page", 25))
    if per_page not in [25, 50, 100]:
        per_page = 25
    
    page = int(request.args.get("page", 1))
    if page < 1:
        page = 1
    
    # Join items with sales
    query = db.session.query(Item).join(Sale).filter(Item.status == "SOLD")
    
    # Search
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
    
    # Date filtering with proper conversion
    try:
        # Order Date range
        if order_from:
            order_from_obj = datetime.strptime(order_from, '%Y-%m-%d').date()
            query = query.filter(Item.order_date >= order_from_obj)
        if order_to:
            order_to_obj = datetime.strptime(order_to, '%Y-%m-%d').date()
            query = query.filter(Item.order_date <= order_to_obj)
        
        # Sale Date range
        if sale_from:
            sale_from_obj = datetime.strptime(sale_from, '%Y-%m-%d').date()
            query = query.filter(Sale.sale_date >= sale_from_obj)
        if sale_to:
            sale_to_obj = datetime.strptime(sale_to, '%Y-%m-%d').date()
            query = query.filter(Sale.sale_date <= sale_to_obj)
    except ValueError:
        flash("Invalid date format. Please use the date picker.", "error")
    
    # Count total
    total_count = query.count()
    total_pages = math.ceil(total_count / per_page) if total_count > 0 else 1
    
    # Ensure page doesn't exceed total pages
    if page > total_pages:
        page = total_pages
    
    # Get items for current page
    offset = (page - 1) * per_page
    items = query.order_by(Sale.sale_date.desc()).limit(per_page).offset(offset).all()
    
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
        if order_from:
            params['order_from'] = order_from
        if order_to:
            params['order_to'] = order_to
        if sale_from:
            params['sale_from'] = sale_from
        if sale_to:
            params['sale_to'] = sale_to
        if per_page != 25:
            params['per_page'] = per_page
        params['page'] = target_page
        return url_for('routes.sales_list') + '?' + urlencode(params)
    
    return render_template(
        "sales/list.html",
        active_nav="sales",
        items=items,
        q=q,
        order_from=order_from,
        order_to=order_to,
        sale_from=sale_from,
        sale_to=sale_to,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        start_item=start_item,
        end_item=end_item,
        page_range=page_range,
        build_url=build_url,
    )


@routes_bp.post("/items/<pk_id>/sell")
@login_required
def items_sell(pk_id):
    """Create a sale for an item (mark as sold)"""
    item = db.session.get(Item, pk_id)
    if not item:
        return jsonify({"error": "Item not found"}), 404
    
    if item.status == "SOLD":
        return jsonify({"error": "Item is already sold"}), 400
    
    # Validate required fields exist on item
    required_fields = [
        'user_item_id', 'order_number', 'order_date', 'arrival_date',
        'company_name', 'brand', 'item_description', 'sku',
        'net_unit_cost', 'freight_net', 'vat_rate'
    ]
    
    missing = [f for f in required_fields if not getattr(item, f)]
    if missing:
        return jsonify({"error": f"Item is missing required fields: {', '.join(missing)}"}), 400
    
    # Get form data
    try:
        sale_date_str = request.form.get('sale_date')
        if not sale_date_str:
            return jsonify({"error": "Sale date is required"}), 400
        
        sale_date = date.fromisoformat(sale_date_str)
        
        selling_price = Decimal(request.form.get('selling_price', '0'))
        if selling_price <= 0:
            return jsonify({"error": "Selling price must be greater than 0"}), 400
        
        # Optional fields
        packaging = Decimal(request.form.get('packaging', '0') or '0')
        delivery_cost = Decimal(request.form.get('delivery_cost', '0') or '0')
        other_cost = Decimal(request.form.get('other_cost', '0') or '0')
        delivery_fee = Decimal(request.form.get('delivery_fee', '0') or '0')
        
        discount_type = request.form.get('discount_type') or None
        discount_value = request.form.get('discount_value')
        discount_value = Decimal(discount_value) if discount_value else None
        
        notes = request.form.get('notes', '').strip() or None
        
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid input: {str(e)}"}), 400
    
    # Calculate discount amount if provided
    discount_amount = None
    if discount_type and discount_value:
        if discount_type == 'PERCENT':
            discount_amount = (selling_price * discount_value / Decimal('100')).quantize(Decimal('0.01'))
        elif discount_type == 'AMOUNT':
            discount_amount = discount_value
    
    # Calculate metrics
    metrics = calculate_sale_metrics(
        selling_price,
        item.vat_rate,
        item.net_unit_cost,
        item.freight_net,
        packaging,
        delivery_cost,
        other_cost
    )
    
    # Create sale record
    sale = Sale(
        item_pk_id=item.pk_id,
        sale_date=sale_date,
        item_selling_price_gross=selling_price,
        discount_type=discount_type,
        discount_value=discount_value,
        discount_amount_gross=discount_amount,
        delivery_fee_charged_gross=delivery_fee,
        packaging_net=packaging,
        delivery_cost_net=delivery_cost,
        other_cost_net=other_cost,
        item_net_revenue=metrics['net_revenue'],
        item_vat_amount=metrics['vat_amount'],
        item_profit=metrics['profit'],
        notes=notes,
        created_by=current_user.pk_id
    )
    
    # Update item status
    item.status = "SOLD"
    
    db.session.add(sale)
    db.session.flush()
    
    # Audit
    audit("SALE", sale.pk_id, "CREATE", reason=f"Item sold for €{selling_price}")
    
    db.session.commit()
    
    return jsonify({"success": True, "message": "Item marked as sold"})


@routes_bp.post("/sales/<pk_id>/edit")
@login_required
def sales_edit(pk_id):
    """Edit an existing sale"""
    sale = db.session.get(Sale, pk_id)
    if not sale:
        return jsonify({"error": "Sale not found"}), 404
    
    item = db.session.get(Item, sale.item_pk_id)
    if not item:
        return jsonify({"error": "Item not found"}), 404
    
    # Get form data
    try:
        sale_date_str = request.form.get('sale_date')
        if not sale_date_str:
            return jsonify({"error": "Sale date is required"}), 400
        
        new_sale_date = date.fromisoformat(sale_date_str)
        
        selling_price = Decimal(request.form.get('selling_price', '0'))
        if selling_price <= 0:
            return jsonify({"error": "Selling price must be greater than 0"}), 400
        
        # Optional fields
        packaging = Decimal(request.form.get('packaging', '0') or '0')
        delivery_cost = Decimal(request.form.get('delivery_cost', '0') or '0')
        other_cost = Decimal(request.form.get('other_cost', '0') or '0')
        delivery_fee = Decimal(request.form.get('delivery_fee', '0') or '0')
        
        discount_type = request.form.get('discount_type') or None
        discount_value = request.form.get('discount_value')
        discount_value = Decimal(discount_value) if discount_value else None
        
        notes = request.form.get('notes', '').strip() or None
        
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid input: {str(e)}"}), 400
    
    # Calculate discount amount if provided
    discount_amount = None
    if discount_type and discount_value:
        if discount_type == 'PERCENT':
            discount_amount = (selling_price * discount_value / Decimal('100')).quantize(Decimal('0.01'))
        elif discount_type == 'AMOUNT':
            discount_amount = discount_value
    
    # Calculate new metrics
    metrics = calculate_sale_metrics(
        selling_price,
        item.vat_rate,
        item.net_unit_cost,
        item.freight_net,
        packaging,
        delivery_cost,
        other_cost
    )
    
    # Audit changes
    if sale.sale_date != new_sale_date:
        audit("SALE", sale.pk_id, "UPDATE", field="sale_date", old=sale.sale_date, new=new_sale_date)
    if sale.item_selling_price_gross != selling_price:
        audit("SALE", sale.pk_id, "UPDATE", field="item_selling_price_gross", old=sale.item_selling_price_gross, new=selling_price)
    if sale.packaging_net != packaging:
        audit("SALE", sale.pk_id, "UPDATE", field="packaging_net", old=sale.packaging_net, new=packaging)
    if sale.delivery_cost_net != delivery_cost:
        audit("SALE", sale.pk_id, "UPDATE", field="delivery_cost_net", old=sale.delivery_cost_net, new=delivery_cost)
    if sale.other_cost_net != other_cost:
        audit("SALE", sale.pk_id, "UPDATE", field="other_cost_net", old=sale.other_cost_net, new=other_cost)
    if sale.delivery_fee_charged_gross != delivery_fee:
        audit("SALE", sale.pk_id, "UPDATE", field="delivery_fee_charged_gross", old=sale.delivery_fee_charged_gross, new=delivery_fee)
    if sale.discount_type != discount_type:
        audit("SALE", sale.pk_id, "UPDATE", field="discount_type", old=sale.discount_type, new=discount_type)
    if sale.discount_value != discount_value:
        audit("SALE", sale.pk_id, "UPDATE", field="discount_value", old=sale.discount_value, new=discount_value)
    if sale.notes != notes:
        audit("SALE", sale.pk_id, "UPDATE", field="notes", old=sale.notes, new=notes)
    
    # Update sale record
    sale.sale_date = new_sale_date
    sale.item_selling_price_gross = selling_price
    sale.discount_type = discount_type
    sale.discount_value = discount_value
    sale.discount_amount_gross = discount_amount
    sale.delivery_fee_charged_gross = delivery_fee
    sale.packaging_net = packaging
    sale.delivery_cost_net = delivery_cost
    sale.other_cost_net = other_cost
    sale.item_net_revenue = metrics['net_revenue']
    sale.item_vat_amount = metrics['vat_amount']
    sale.item_profit = metrics['profit']
    sale.notes = notes
    
    db.session.commit()
    
    return jsonify({"success": True, "message": "Sale updated successfully"})


@routes_bp.post("/sales/<pk_id>/reverse")
@login_required
def sales_reverse(pk_id):
    """Reverse a sale - return item to inventory"""
    sale = db.session.get(Sale, pk_id)
    if not sale:
        return jsonify({"error": "Sale not found"}), 404
    
    item = db.session.get(Item, sale.item_pk_id)
    if not item:
        return jsonify({"error": "Item not found"}), 404
    
    reason = request.form.get('reason', '').strip()
    if not reason:
        return jsonify({"error": "Reason is required"}), 400
    
    # Audit the reversal
    audit("SALE", sale.pk_id, "REVERSE_SALE", reason=reason)
    
    # Delete sale record
    db.session.delete(sale)
    
    # Return item to inventory
    item.status = "IN_STOCK"
    
    db.session.commit()
    
    return jsonify({"success": True, "message": "Sale reversed successfully"})
