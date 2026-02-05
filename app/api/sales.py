from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from flask import request, jsonify
from flask_login import login_required, current_user

from . import api_bp
from ..extensions import db
from ..models import Item, Sale, AuditLog

def _q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def compute_snapshots(gross_price: Decimal, vat_rate: Decimal, total_cost_net: Decimal):
    net_rev = _q2(gross_price / (Decimal("1.0") + vat_rate))
    vat_amt = _q2(gross_price - net_rev)
    profit = _q2(net_rev - total_cost_net)
    return net_rev, vat_amt, profit

def _audit(entity_type, entity_pk_id, action, field=None, old=None, new=None, reason=None):
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

@api_bp.get("/sales")
@login_required
def list_sales():
    sales = db.session.query(Sale).order_by(Sale.sale_date.desc()).limit(500).all()
    return jsonify([{
        "pk_id": s.pk_id,
        "item_pk_id": s.item_pk_id,
        "sale_date": s.sale_date.isoformat(),
        "item_selling_price_gross": str(s.item_selling_price_gross),
        "item_net_revenue": str(s.item_net_revenue),
        "item_vat_amount": str(s.item_vat_amount),
        "item_profit": str(s.item_profit),
    } for s in sales])

@api_bp.post("/items/<item_pk_id>/sale")
@login_required
def create_sale(item_pk_id):
    item = db.session.get(Item, item_pk_id)
    if not item:
        return jsonify({"error": "item_not_found"}), 404
    if item.status != "IN_STOCK":
        return jsonify({"error": "item_not_in_stock"}), 400

    d = request.get_json(force=True) or {}

    gross = Decimal(str(d["item_selling_price_gross"]))
    vat_rate = Decimal(str(item.vat_rate))
    packaging = Decimal(str(d.get("packaging_net", "0")))
    delivery_cost = Decimal(str(d.get("delivery_cost_net", "0")))
    other = Decimal(str(d.get("other_cost_net", "0")))

    total_cost = item.net_unit_cost + item.freight_net + packaging + delivery_cost + other
    net_rev, vat_amt, profit = compute_snapshots(gross, vat_rate, total_cost)

    s = Sale(
        item_pk_id=item.pk_id,
        sale_date=date.fromisoformat(d.get("sale_date", date.today().isoformat())),
        item_selling_price_gross=gross,
        discount_type=d.get("discount_type"),
        discount_value=Decimal(str(d["discount_value"])) if d.get("discount_value") is not None else None,
        discount_amount_gross=Decimal(str(d["discount_amount_gross"])) if d.get("discount_amount_gross") is not None else None,
        delivery_fee_charged_gross=Decimal(str(d.get("delivery_fee_charged_gross", "0"))),
        packaging_net=packaging,
        delivery_cost_net=delivery_cost,
        other_cost_net=other,
        item_net_revenue=net_rev,
        item_vat_amount=vat_amt,
        item_profit=profit,
        notes=d.get("notes"),
        created_by=current_user.pk_id,
    )

    item.status = "SOLD"

    db.session.add(s)
    _audit("SALE", s.pk_id, "CREATE")
    _audit("ITEM", item.pk_id, "UPDATE", field="status", old="IN_STOCK", new="SOLD")
    db.session.commit()

    return jsonify({"ok": True, "sale_pk_id": s.pk_id}), 201

@api_bp.patch("/sales/<sale_pk_id>")
@login_required
def update_sale(sale_pk_id):
    s = db.session.get(Sale, sale_pk_id)
    if not s:
        return jsonify({"error": "not_found"}), 404
    item = db.session.get(Item, s.item_pk_id)
    if not item:
        return jsonify({"error": "item_missing"}), 500

    d = request.get_json(force=True) or {}

    # track changes + update allowed fields
    fields = [
        "sale_date","item_selling_price_gross","discount_type","discount_value","discount_amount_gross",
        "delivery_fee_charged_gross","packaging_net","delivery_cost_net","other_cost_net","notes"
    ]

    for f in fields:
        if f not in d:
            continue
        old = getattr(s, f)
        new = d[f]
        if f == "sale_date":
            new = date.fromisoformat(new)
        if f in ("item_selling_price_gross","discount_value","discount_amount_gross","delivery_fee_charged_gross","packaging_net","delivery_cost_net","other_cost_net"):
            new = Decimal(str(new))
        if old != new:
            setattr(s, f, new)
            _audit("SALE", s.pk_id, "UPDATE", field=f, old=old, new=new, reason=d.get("reason"))

    # recompute snapshots every edit (per spec)
    gross = Decimal(str(s.item_selling_price_gross))
    vat_rate = Decimal(str(item.vat_rate))
    total_cost = item.net_unit_cost + item.freight_net + s.packaging_net + s.delivery_cost_net + s.other_cost_net
    net_rev, vat_amt, profit = compute_snapshots(gross, vat_rate, total_cost)

    # audit computed changes (optional but useful)
    if s.item_net_revenue != net_rev:
        _audit("SALE", s.pk_id, "UPDATE", field="item_net_revenue", old=s.item_net_revenue, new=net_rev, reason="recalc")
    if s.item_vat_amount != vat_amt:
        _audit("SALE", s.pk_id, "UPDATE", field="item_vat_amount", old=s.item_vat_amount, new=vat_amt, reason="recalc")
    if s.item_profit != profit:
        _audit("SALE", s.pk_id, "UPDATE", field="item_profit", old=s.item_profit, new=profit, reason="recalc")

    s.item_net_revenue = net_rev
    s.item_vat_amount = vat_amt
    s.item_profit = profit

    db.session.commit()
    return jsonify({"ok": True})

@api_bp.post("/sales/<sale_pk_id>/reverse")
@login_required
def reverse_sale(sale_pk_id):
    s = db.session.get(Sale, sale_pk_id)
    if not s:
        return jsonify({"error": "not_found"}), 404

    d = request.get_json(force=True) or {}
    reason = (d.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "reason_required"}), 400

    item = db.session.get(Item, s.item_pk_id)
    if not item:
        return jsonify({"error": "item_missing"}), 500

    # transactional reverse
    item.status = "IN_STOCK"
    _audit("ITEM", item.pk_id, "UPDATE", field="status", old="SOLD", new="IN_STOCK", reason=reason)
    _audit("SALE", s.pk_id, "REVERSE_SALE", reason=reason)

    db.session.delete(s)
    db.session.commit()
    return jsonify({"ok": True})

