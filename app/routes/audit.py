from flask import render_template, request, url_for
from flask_login import login_required
from sqlalchemy import or_
from . import routes_bp
from ..extensions import db
from ..models import AuditLog, Item, User
import math
from urllib.parse import urlencode

@routes_bp.get("/audit")
@login_required
def audit_list():
    from datetime import datetime
    
    q = (request.args.get("q") or "").strip()
    
    # Pagination
    per_page = int(request.args.get("per_page", 25))
    if per_page not in [25, 50, 100]:
        per_page = 25
    
    page = int(request.args.get("page", 1))
    if page < 1:
        page = 1
    
    # Base query with joins for item description and user email
    query = db.session.query(
        AuditLog,
        Item.user_item_id,
        Item.item_description,
        User.email.label('user_email')
    ).outerjoin(
        Item, AuditLog.entity_pk_id == Item.pk_id
    ).outerjoin(
        User, AuditLog.actor_user_id == User.pk_id
    )
    
    # Search across multiple fields
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Item.user_item_id.ilike(like),  # Internal ID
                Item.item_description.ilike(like),  # Item description
                AuditLog.entity_pk_id.ilike(like),  # Batch number (when entity is IMPORT_BATCH)
                User.email.ilike(like),  # User email
            )
        )
    
    # Count total
    total_count = query.count()
    total_pages = math.ceil(total_count / per_page) if total_count > 0 else 1
    
    # Ensure page doesn't exceed total pages
    if page > total_pages:
        page = total_pages
    
    # Get logs for current page
    offset = (page - 1) * per_page
    results = query.order_by(AuditLog.created_at.desc()).limit(per_page).offset(offset).all()
    
    # Flatten results into a list of enriched audit logs
    logs = []
    for audit_log, user_item_id, item_description, user_email in results:
        # Create a copy of the audit log with additional attributes
        enriched_log = audit_log
        enriched_log.user_item_id = user_item_id or '-'
        enriched_log.item_description = item_description or '-'
        enriched_log.user_email = user_email or '-'
        logs.append(enriched_log)
    
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
        if per_page != 25:
            params['per_page'] = per_page
        params['page'] = target_page
        return url_for('routes.audit_list') + '?' + urlencode(params)
    
    return render_template(
        "audit/list.html",
        active_nav="audit",
        logs=logs,
        q=q,
        per_page=per_page,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        start_item=start_item,
        end_item=end_item,
        page_range=page_range,
        build_url=build_url
    )
