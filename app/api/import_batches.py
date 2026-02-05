from flask import request, jsonify
from flask_login import login_required, current_user
from . import api_bp
from ..extensions import db
from ..models import ImportBatch, AuditLog

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

@api_bp.get("/import-batches")
@login_required
def list_batches():
    batches = db.session.query(ImportBatch).order_by(ImportBatch.uploaded_at.desc()).limit(200).all()
    return jsonify([{
        "pk_id": b.pk_id,
        "filename": b.filename,
        "uploaded_at": b.uploaded_at.isoformat(),
        "total_rows": b.total_rows,
        "success_rows": b.success_rows,
        "failed_rows": b.failed_rows,
    } for b in batches])

@api_bp.post("/import-batches")
@login_required
def create_batch():
    d = request.get_json(force=True) or {}
    b = ImportBatch(
        filename=str(d.get("filename") or "").strip(),
        uploaded_by=current_user.pk_id,
        total_rows=int(d.get("total_rows") or 0),
        success_rows=int(d.get("success_rows") or 0),
        failed_rows=int(d.get("failed_rows") or 0),
        error_report=d.get("error_report"),
    )
    db.session.add(b)
    _audit("IMPORT_BATCH", b.pk_id, "CREATE")
    db.session.commit()
    return jsonify({"ok": True, "pk_id": b.pk_id}), 201

@api_bp.get("/import-batches/<pk_id>")
@login_required
def get_batch(pk_id):
    b = db.session.get(ImportBatch, pk_id)
    if not b:
        return jsonify({"error": "not_found"}), 404
    return jsonify({
        "pk_id": b.pk_id,
        "filename": b.filename,
        "uploaded_at": b.uploaded_at.isoformat(),
        "total_rows": b.total_rows,
        "success_rows": b.success_rows,
        "failed_rows": b.failed_rows,
        "error_report": b.error_report,
    })

