from flask import render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from . import routes_bp
from ..extensions import db
from ..models import ImportBatch, AuditLog

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

@routes_bp.get("/imports")
@login_required
def imports_list():
    batches = db.session.query(ImportBatch).order_by(ImportBatch.uploaded_at.desc()).limit(200).all()
    return render_template("imports/list.html", active_nav="imports", batches=batches)

@routes_bp.get("/imports/new")
@login_required
def imports_new():
    return render_template("imports/new.html", active_nav="imports")

@routes_bp.post("/imports/new")
@login_required
def imports_create_stub():
    # Placeholder until we implement real file upload + processing
    b = ImportBatch(
        filename="(upload not implemented yet)",
        uploaded_by=current_user.pk_id,
        total_rows=0,
        success_rows=0,
        failed_rows=0,
        error_report=None,
    )
    db.session.add(b)
    audit("IMPORT_BATCH", b.pk_id, "CREATE")
    db.session.commit()
    flash("Import batch created (upload to be implemented next).", "ok")
    return redirect(url_for("routes.imports_list"))

