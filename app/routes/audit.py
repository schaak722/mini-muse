from flask import render_template, request
from flask_login import login_required
from . import routes_bp
from ..extensions import db
from ..models import AuditLog

@routes_bp.get("/audit")
@login_required
def audit_list():
    entity_type = request.args.get("entity_type") or ""
    entity_pk_id = request.args.get("entity_pk_id") or ""

    q = db.session.query(AuditLog)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if entity_pk_id:
        q = q.filter(AuditLog.entity_pk_id == entity_pk_id)

    logs = q.order_by(AuditLog.created_at.desc()).limit(500).all()
    return render_template("audit/list.html", active_nav="audit", logs=logs, entity_type=entity_type, entity_pk_id=entity_pk_id)

