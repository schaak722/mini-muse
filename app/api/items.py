from datetime import date
from decimal import Decimal
from flask import request, jsonify
from flask_login import login_required, current_user

from . import api_bp
from ..extensions import db
from ..models import Item, AuditLog

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

@api_bp.get("/items")
@login_required
def list_items():
    status = request.args.get("status")
    q = db.session.query(Item)
    if status:
        q = q.filter(Item.status == status)
    items = q.order_by(Item.arrival_date.desc()).limit(500).all()
    return jsonify([{
        "pk_id": i.pk_id,
        "user_item_id": i.user_item_id,
        "status": i.status,
        "order_number": i.order_number,
        "order_date": i.order_date.isoformat(),
        "arrival_date": i.arrival_date.isoformat(),
        "company_name": i.company_name,
        "brand": i.brand,
        "item_description": i.item_description,
        "sku": i.sku,
        "net_unit_cost": str(i.net_unit_cost),
        "freight_net": str(i.freight_net),
        "vat_rate": str(i.vat_rate),
    } for i in items])

@api_bp.get("/items/<pk_id>")
@login_required
def get_item(pk_id):
    i = db.session.get(Item, pk_id)
    if not i:
        return jsonify({"error": "not_found"}), 404
    return jsonify({
        "pk_id": i.pk_id,
        "user_item_id": i.user_item_id,
        "status": i.status,
        "order_number": i.order_number,
        "order_date": i.order_date.isoformat(),
        "arrival_date": i.arrival_date.isoformat(),
        "company_name": i.company_name,
        "brand": i.brand,
        "item_description": i.item_description,
        "sku": i.sku,
        "net_unit_cost": str(i.net_unit_cost),
        "freight_net": str(i.freight_net),
        "vat_rate": str(i.vat_rate),
    })

@api_bp.post("/items")
@login_required
def create_item():
    d = request.get_json(force=True) or {}

    i = Item(
        user_item_id=str(d["user_item_id"]).strip(),
        status="IN_STOCK",
        order_number=str(d["order_number"]).strip(),
        order_date=date.fromisoformat(d["order_date"]),
        arrival_date=date.fromisoformat(d["arrival_date"]),
        company_name=str(d["company_name"]).strip(),
        brand=str(d["brand"]).strip(),
        item_description=str(d["item_description"]).strip(),
        sku=str(d["sku"]).strip(),
        net_unit_cost=Decimal(str(d["net_unit_cost"])),
        freight_net=Decimal(str(d["freight_net"])),
        vat_rate=Decimal(str(d.get("vat_rate", "0.18"))),
        created_by=current_user.pk_id,
    )
    db.session.add(i)
    _audit("ITEM", i.pk_id, "CREATE")
    db.session.commit()
    return jsonify({"ok": True, "pk_id": i.pk_id}), 201

@api_bp.patch("/items/<pk_id>")
@login_required
def update_item(pk_id):
    i = db.session.get(Item, pk_id)
    if not i:
        return jsonify({"error": "not_found"}), 404

    d = request.get_json(force=True) or {}
    editable = [
        "user_item_id","order_number","order_date","arrival_date","company_name",
        "brand","item_description","sku","net_unit_cost","freight_net","vat_rate"
    ]

    for f in editable:
        if f not in d:
            continue
        old = getattr(i, f)
        new = d[f]
        if f in ("order_date","arrival_date"):
            new = date.fromisoformat(new)
        if f in ("net_unit_cost","freight_net","vat_rate"):
            new = Decimal(str(new))
        if old != new:
            setattr(i, f, new)
            _audit("ITEM", i.pk_id, "UPDATE", field=f, old=old, new=new)

    db.session.commit()
    return jsonify({"ok": True})

@api_bp.delete("/items/<pk_id>")
@login_required
def delete_item(pk_id):
    i = db.session.get(Item, pk_id)
    if not i:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(i)
    _audit("ITEM", pk_id, "DELETE")
    db.session.commit()
    return jsonify({"ok": True})

