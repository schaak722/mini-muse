from flask import request, jsonify
from flask_login import login_required
from . import api_bp
from ..extensions import db
from ..models import AuditLog

@api_bp.get("/audit-logs")
@login_required
def list_audit_logs():
    entity_type = request.args.get("entity_type")
    entity_pk_id = request.args.get("entity_pk_id")

    q = db.session.query(AuditLog)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if entity_pk_id:
        q = q.filter(AuditLog.entity_pk_id == entity_pk_id)

    logs = q.order_by(AuditLog.created_at.desc()).limit(500).all()
    return jsonify([{
        "pk_id": a.pk_id,
        "entity_type": a.entity_type,
        "entity_pk_id": a.entity_pk_id,
        "action": a.action,
        "field_name": a.field_name,
        "old_value": a.old_value,
        "new_value": a.new_value,
        "reason": a.reason,
        "actor_user_id": a.actor_user_id,
        "created_at": a.created_at.isoformat(),
    } for a in logs])

