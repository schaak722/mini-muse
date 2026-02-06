import csv
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from . import routes_bp
from ..extensions import db
from ..models import ImportBatch, AuditLog, Item


def audit(entity_type, entity_pk_id, action, field=None, old=None, new=None, reason=None):
    """Helper to create audit log entries"""
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


def parse_date(date_str):
    """Parse DD/MM/YYYY format to date object"""
    if not date_str or not date_str.strip():
        return None
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_decimal(value_str):
    """Parse string to Decimal, handling empty values"""
    if not value_str or not str(value_str).strip():
        return Decimal("0.00")
    try:
        return Decimal(str(value_str).strip())
    except (ValueError, InvalidOperation):
        return None


@routes_bp.get("/imports")
@login_required
def imports_list():
    """List all import batches"""
    batches = db.session.query(ImportBatch).order_by(ImportBatch.uploaded_at.desc()).limit(200).all()
    return render_template("imports/list.html", active_nav="imports", batches=batches)


@routes_bp.get("/imports/new")
@login_required
def imports_new():
    """Show CSV upload form"""
    return render_template("imports/new.html", active_nav="imports")


@routes_bp.post("/imports/new")
@login_required
def imports_create():
    """Process uploaded CSV and create items"""
    
    # Check if file was uploaded
    if 'file' not in request.files:
        flash("No file uploaded", "error")
        return redirect(url_for("routes.imports_new"))
    
    file = request.files['file']
    
    if file.filename == '':
        flash("No file selected", "error")
        return redirect(url_for("routes.imports_new"))
    
    if not file.filename.endswith('.csv'):
        flash("File must be a CSV", "error")
        return redirect(url_for("routes.imports_new"))
    
    # Read CSV content
    try:
        content = file.read().decode('utf-8-sig')  # Handle BOM
        csv_file = StringIO(content)
        reader = csv.DictReader(csv_file)
    except Exception as e:
        flash(f"Error reading CSV file: {str(e)}", "error")
        return redirect(url_for("routes.imports_new"))
    
    # Column mapping (CSV header -> database field)
    COLUMN_MAP = {
        'Unique ID': 'user_item_id',
        'Order Number': 'order_number',
        'Order Date': 'order_date',
        'Arrival Date': 'arrival_date',
        'Company Name': 'company_name',
        'Brand': 'brand',
        'Item Description': 'item_description',
        'SKU': 'sku',
        'Net Unit Cost': 'net_unit_cost',
        'Freight': 'freight_net',
        'Colour': 'colour',
        'Size': 'size',
        'Dimension': 'dimension',
        'Weight': 'weight',
        'Comments': 'comments',
    }
    
    # Required fields that must be present and non-empty
    REQUIRED_FIELDS = [
        'Unique ID', 'Order Number', 'Order Date', 'Arrival Date',
        'Company Name', 'Brand', 'Item Description', 'SKU',
        'Net Unit Cost', 'Freight'
    ]
    
    # Process rows
    total_rows = 0
    success_rows = 0
    failed_rows = 0
    errors = []
    
    for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
        total_rows += 1
        
        # Skip completely empty rows
        if all(not v or not str(v).strip() for v in row.values()):
            total_rows -= 1  # Don't count empty rows
            continue
        
        # Validate required fields
        missing_fields = []
        for field in REQUIRED_FIELDS:
            if field not in row or not row[field] or not str(row[field]).strip():
                missing_fields.append(field)
        
        if missing_fields:
            failed_rows += 1
            errors.append({
                'row': row_num,
                'unique_id': row.get('Unique ID', 'N/A'),
                'error': f"Missing required fields: {', '.join(missing_fields)}"
            })
            continue
        
        # Check for duplicate Unique ID
        existing_item = db.session.query(Item).filter_by(
            user_item_id=row['Unique ID'].strip()
        ).first()
        
        if existing_item:
            failed_rows += 1
            errors.append({
                'row': row_num,
                'unique_id': row['Unique ID'],
                'error': f"Duplicate ID - already exists in database"
            })
            continue
        
        # Parse dates
        order_date = parse_date(row['Order Date'])
        arrival_date = parse_date(row['Arrival Date'])
        
        if not order_date:
            failed_rows += 1
            errors.append({
                'row': row_num,
                'unique_id': row['Unique ID'],
                'error': f"Invalid Order Date format (expected DD/MM/YYYY): {row['Order Date']}"
            })
            continue
        
        if not arrival_date:
            failed_rows += 1
            errors.append({
                'row': row_num,
                'unique_id': row['Unique ID'],
                'error': f"Invalid Arrival Date format (expected DD/MM/YYYY): {row['Arrival Date']}"
            })
            continue
        
        # Parse numeric fields
        net_unit_cost = parse_decimal(row['Net Unit Cost'])
        freight_net = parse_decimal(row['Freight'])
        
        if net_unit_cost is None:
            failed_rows += 1
            errors.append({
                'row': row_num,
                'unique_id': row['Unique ID'],
                'error': f"Invalid Net Unit Cost: {row['Net Unit Cost']}"
            })
            continue
        
        if freight_net is None:
            failed_rows += 1
            errors.append({
                'row': row_num,
                'unique_id': row['Unique ID'],
                'error': f"Invalid Freight: {row['Freight']}"
            })
            continue
        
        # Create Item
        try:
            item = Item(
                user_item_id=row['Unique ID'].strip(),
                status='IN_STOCK',
                order_number=row['Order Number'].strip(),
                order_date=order_date,
                arrival_date=arrival_date,
                company_name=row['Company Name'].strip(),
                brand=row['Brand'].strip(),
                item_description=row['Item Description'].strip(),
                sku=row['SKU'].strip(),
                net_unit_cost=net_unit_cost,
                freight_net=freight_net,
                vat_rate=Decimal("0.18"),  # Default 18%
                colour=row.get('Colour', '').strip() or None,
                size=row.get('Size', '').strip() or None,
                dimension=row.get('Dimension', '').strip() or None,
                weight=row.get('Weight', '').strip() or None,
                comments=row.get('Comments', '').strip() or None,
                created_by=current_user.pk_id
            )
            
            db.session.add(item)
            success_rows += 1
            
        except Exception as e:
            failed_rows += 1
            errors.append({
                'row': row_num,
                'unique_id': row.get('Unique ID', 'N/A'),
                'error': f"Database error: {str(e)}"
            })
            continue
    
    # Create ImportBatch record
    batch = ImportBatch(
        filename=secure_filename(file.filename),
        uploaded_by=current_user.pk_id,
        total_rows=total_rows,
        success_rows=success_rows,
        failed_rows=failed_rows,
        error_report=json.dumps(errors, indent=2) if errors else None
    )
    db.session.add(batch)
    db.session.flush()  # ADD THIS LINE - generates the pk_id
    
    # Create audit log
    audit("IMPORT_BATCH", batch.pk_id, "CREATE", reason=f"Imported {success_rows}/{total_rows} items")
    
    # Commit everything
    
    try:
        db.session.commit()
        
        if failed_rows > 0:
            flash(f"Import completed: {success_rows} succeeded, {failed_rows} failed. Check error report below.", "ok")
        else:
            flash(f"Import successful! {success_rows} items added to inventory.", "ok")
        
        return redirect(url_for("routes.imports_list"))
        
    except Exception as e:
        db.session.rollback()
        flash(f"Import failed: {str(e)}", "error")
        return redirect(url_for("routes.imports_new"))
