import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from ..extensions import db
from ..decorators import require_role, require_edit_permission
from ..models import Item, PurchaseOrder, PurchaseLine, ImportBatch, SavedSearch
from ..utils.csv_stream import stream_csv
from .forms import PurchaseCostsForm

purchases_bp = Blueprint("purchases", __name__, url_prefix="/purchases")


def _norm_header(s: str) -> str:
    return "".join(ch.lower() for ch in (s or "").strip() if ch.isalnum())


def _pick(row: dict, header_map: dict, *keys: str) -> str:
    for k in keys:
        h = header_map.get(_norm_header(k))
        if h and h in row:
            return (row.get(h) or "").strip()
    return ""


def _safe_decimal(val, default=Decimal("0")):
    if val is None:
        return default
    s = str(val).strip()
    if s == "":
        return default
    s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return default


def _safe_int(val, default=0):
    try:
        s = str(val).strip()
        if s == "":
            return default
        return int(Decimal(s.replace(",", ".")))
    except Exception:
        return default


def _safe_date(val):
    """
    Supports: YYYY-MM-DD, DD/MM/YYYY, DD/MM/YY
    """
    s = (val or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _recalc_allocations(po: PurchaseOrder):
    """
    Recalculate freight allocation + landed costs for a purchase order.
    """
    lines = PurchaseLine.query.filter_by(purchase_order_id=po.id).all()

    freight_total = Decimal(str(po.freight_total)) if po.freight_total is not None else Decimal("0")
    if freight_total <= 0:
        for ln in lines:
            ln.freight_allocated_total = Decimal("0")
            ln.freight_allocated_per_unit = Decimal("0")
            pkg = Decimal(str(ln.packaging_per_unit)) if ln.packaging_per_unit is not None else Decimal("0")
            ln.landed_unit_cost = Decimal(str(ln.unit_cost_net)) + pkg
        db.session.commit()
        return

    method = po.allocation_method or "value"

    if method == "qty":
        base_total = sum(Decimal(ln.qty or 0) for ln in lines) or Decimal("0")
        for ln in lines:
            base = Decimal(ln.qty or 0)
            alloc = (freight_total * base / base_total) if base_total > 0 else Decimal("0")
            ln.freight_allocated_total = alloc
            qty = Decimal(ln.qty or 0) or Decimal("1")
            ln.freight_allocated_per_unit = (alloc / qty) if qty > 0 else Decimal("0")
            pkg = Decimal(str(ln.packaging_per_unit)) if ln.packaging_per_unit is not None else Decimal("0")
            ln.landed_unit_cost = Decimal(str(ln.unit_cost_net)) + ln.freight_allocated_per_unit + pkg
        db.session.commit()
        return

    # default: value allocation
    base_total = sum((Decimal(str(ln.unit_cost_net)) * Decimal(ln.qty or 0)) for ln in lines) or Decimal("0")
    for ln in lines:
        base = (Decimal(str(ln.unit_cost_net)) * Decimal(ln.qty or 0))
        alloc = (freight_total * base / base_total) if base_total > 0 else Decimal("0")
        ln.freight_allocated_total = alloc
        qty = Decimal(ln.qty or 0) or Decimal("1")
        ln.freight_allocated_per_unit = (alloc / qty) if qty > 0 else Decimal("0")
        pkg = Decimal(str(ln.packaging_per_unit)) if ln.packaging_per_unit is not None else Decimal("0")
        ln.landed_unit_cost = Decimal(str(ln.unit_cost_net)) + ln.freight_allocated_per_unit + pkg

    db.session.commit()


@purchases_bp.get("")
@login_required
@require_role("viewer")
def list_purchase_orders():
    """
    Purchases list with:
    - q search
    - date range (order date or arrival date)
    - pagination
    - saved searches dropdown
    """
    q = (request.args.get("q") or "").strip().lower()

    date_field = (request.args.get("date_field") or "order").strip().lower()  # order|arrival
    date_from = _safe_date(request.args.get("from") or "")
    date_to = _safe_date(request.args.get("to") or "")

    page = int(request.args.get("page") or 1)
    per_page = 50
    if page < 1:
        page = 1

    query = PurchaseOrder.query
    if q:
        query = query.filter(
            db.or_(
                db.func.lower(PurchaseOrder.order_number).contains(q),
                db.func.lower(PurchaseOrder.supplier_name).contains(q),
                db.func.lower(PurchaseOrder.brand).contains(q),
            )
        )

    col = PurchaseOrder.arrival_date if date_field == "arrival" else PurchaseOrder.order_date
    if date_from:
        query = query.filter(col >= date_from)
    if date_to:
        query = query.filter(col <= date_to)

    total = query.count()
    orders = (
        query.order_by(PurchaseOrder.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    saved = (
        SavedSearch.query
        .filter_by(user_id=current_user.id, context="purchases")
        .order_by(SavedSearch.created_at.desc())
        .limit(20)
        .all()
    )

    params = {
        "q": q or "",
        "date_field": date_field or "order",
        "from": (date_from.isoformat() if date_from else ""),
        "to": (date_to.isoformat() if date_to else ""),
    }
    qs_no_page = urlencode({k: v for k, v in params.items() if v != ""})

    has_prev = page > 1
    has_next = (page * per_page) < total

    return render_template(
        "purchases/orders_list.html",
        orders=orders,
        q=q,
        date_field=date_field,
        date_from=(date_from.isoformat() if date_from else ""),
        date_to=(date_to.isoformat() if date_to else ""),
        page=page,
        per_page=per_page,
        total=total,
        has_prev=has_prev,
        has_next=has_next,
        qs_no_page=qs_no_page,
        saved_searches=saved,
    )


@purchases_bp.get("/export.csv")
@login_required
@require_role("viewer")
def export_purchase_orders_csv():
    """
    Streaming export of purchase orders with current filters.
    """
    q = (request.args.get("q") or "").strip().lower()
    date_field = (request.args.get("date_field") or "order").strip().lower()
    date_from = _safe_date(request.args.get("from") or "")
    date_to = _safe_date(request.args.get("to") or "")

    query = PurchaseOrder.query
    if q:
        query = query.filter(
            db.or_(
                db.func.lower(PurchaseOrder.order_number).contains(q),
                db.func.lower(PurchaseOrder.supplier_name).contains(q),
                db.func.lower(PurchaseOrder.brand).contains(q),
            )
        )

    col = PurchaseOrder.arrival_date if date_field == "arrival" else PurchaseOrder.order_date
    if date_from:
        query = query.filter(col >= date_from)
    if date_to:
        query = query.filter(col <= date_to)

    rows = query.order_by(PurchaseOrder.created_at.desc()).yield_per(500)

    headers = [
        "Order Number",
        "Supplier",
        "Brand",
        "Order Date",
        "Arrival Date",
        "Currency",
        "Freight Total",
        "Allocation Method",
    ]

    def row_fn(po: PurchaseOrder):
        return [
            po.order_number or "",
            po.supplier_name or "",
            po.brand or "",
            po.order_date.isoformat() if po.order_date else "",
            po.arrival_date.isoformat() if po.arrival_date else "",
            po.currency or "",
            str(po.freight_total or ""),
            po.allocation_method or "",
        ]

    return stream_csv(rows, headers, row_fn, filename="purchase_orders_export.csv")


@purchases_bp.get("/<int:po_id>/export-lines.csv")
@login_required
@require_role("viewer")
def export_purchase_lines_csv(po_id: int):
    """
    Streaming export of purchase lines (with landed costs) for a single PO.
    """
    po = db.session.get(PurchaseOrder, po_id)
    if not po:
        flash("Purchase order not found.", "danger")
        return redirect(url_for("purchases.list_purchase_orders"))

    rows = (
        PurchaseLine.query
        .filter_by(purchase_order_id=po.id)
        .order_by(PurchaseLine.sku.asc())
        .yield_per(500)
    )

    headers = [
        "PO Number",
        "SKU",
        "Description",
        "Qty",
        "Unit Cost Net",
        "Packaging/Unit",
        "Freight/Unit",
        "Landed/Unit",
        "Freight Alloc Total",
    ]

    def row_fn(ln: PurchaseLine):
        return [
            po.order_number or "",
            ln.sku or "",
            ln.description or "",
            int(ln.qty or 0),
            str(ln.unit_cost_net or 0),
            str(ln.packaging_per_unit or 0),
            str(ln.freight_allocated_per_unit or 0),
            str(ln.landed_unit_cost or 0),
            str(ln.freight_allocated_total or 0),
        ]

    safe_po = (po.order_number or "po").replace(" ", "_")
    return stream_csv(rows, headers, row_fn, filename=f"purchase_lines_{safe_po}.csv")


@purchases_bp.get("/<int:po_id>")
@login_required
@require_role("viewer")
def purchase_detail(po_id: int):
    po = db.session.get(PurchaseOrder, po_id)
    if not po:
        flash("Purchase order not found.", "danger")
        return redirect(url_for("purchases.list_purchase_orders"))

    lines = PurchaseLine.query.filter_by(purchase_order_id=po.id).order_by(PurchaseLine.sku.asc()).all()

    # Simple totals
    total_qty = sum(l.qty or 0 for l in lines)
    total_goods = sum(Decimal(str(l.unit_cost_net)) * Decimal(l.qty or 0) for l in lines)
    total_freight_alloc = sum(Decimal(str(l.freight_allocated_total or 0)) for l in lines)
    total_packaging = sum(Decimal(str(l.packaging_per_unit or 0)) * Decimal(l.qty or 0) for l in lines)

    return render_template(
        "purchases/order_detail.html",
        po=po,
        lines=lines,
        total_qty=total_qty,
        total_goods=total_goods,
        total_freight_alloc=total_freight_alloc,
        total_packaging=total_packaging,
    )


@purchases_bp.get("/<int:po_id>/costs")
@purchases_bp.post("/<int:po_id>/costs")
@login_required
@require_edit_permission
def edit_costs(po_id: int):
    po = db.session.get(PurchaseOrder, po_id)
    if not po:
        flash("Purchase order not found.", "danger")
        return redirect(url_for("purchases.list_purchase_orders"))

    form = PurchaseCostsForm(obj=po)
    if form.validate_on_submit():
        po.freight_total = form.freight_total.data
        po.allocation_method = form.allocation_method.data
        db.session.commit()
        _recalc_allocations(po)
        flash("Costs saved and allocations recalculated.", "success")
        return redirect(url_for("purchases.purchase_detail", po_id=po.id))

    return render_template("purchases/order_costs.html", po=po, form=form)


@purchases_bp.get("/import")
@login_required
@require_edit_permission
def import_upload():
    return render_template("purchases/import_upload.html")


@purchases_bp.post("/import")
@login_required
@require_edit_permission
def import_parse():
    f = request.files.get("file")
    if not f or f.filename == "":
        flash("Please choose a CSV file.", "danger")
        return redirect(url_for("purchases.import_upload"))

    raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("CSV must be UTF-8 encoded.", "danger")
        return redirect(url_for("purchases.import_upload"))

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        flash("CSV appears empty or invalid.", "danger")
        return redirect(url_for("purchases.import_upload"))

    header_map = {_norm_header(h): h for h in reader.fieldnames}

    rows = []
    for r in reader:
        rows.append(r)

    # Group by Order Number (supports multiple POs in one CSV)
    groups = {}
    for r in rows:
        order_number = _pick(r, header_map, "Order Number", "order_number", "OrderNumber", "PO", "po")
        if not order_number:
            # skip blank rows
            continue

        supplier = _pick(r, header_map, "Company Name", "Supplier", "supplier", "company")
        brand = _pick(r, header_map, "Brand", "brand")
        order_date = _safe_date(_pick(r, header_map, "Order Date", "order_date"))
        arrival_date = _safe_date(_pick(r, header_map, "Arrival Date", "arrival_date"))

        sku = _pick(r, header_map, "SKU", "sku")
        desc = _pick(r, header_map, "Item Description", "Description", "item_description", "description")
        colour = _pick(r, header_map, "Colour", "color")
        size = _pick(r, header_map, "Size", "size")
        weight = _pick(r, header_map, "Weight", "weight", "Weight (kg)")
        qty = _pick(r, header_map, "Qty", "Quantity", "qty")
        unit_cost = _pick(r, header_map, "Net Unit Cost", "Unit Cost", "unit_cost_net", "net_unit_cost")

        packaging = _pick(r, header_map, "Packaging", "packaging", "Packaging per unit", "packaging_per_unit")
        freight_total = _pick(r, header_map, "Freight Total", "freight_total", "FreightTotal")

        if order_number not in groups:
            groups[order_number] = {
                "supplier": supplier or None,
                "brand": brand or None,
                "order_number": order_number,
                "order_date": order_date.isoformat() if order_date else None,
                "arrival_date": arrival_date.isoformat() if arrival_date else None,
                "freight_total": str(_safe_decimal(freight_total, default=Decimal("0"))) if freight_total else None,
                "allocation_method": "value",
                "lines": []
            }

        groups[order_number]["lines"].append({
            "sku": sku,
            "description": desc,
            "colour": colour,
            "size": size,
            "weight": weight,
            "qty": qty,
            "unit_cost_net": unit_cost,
            "packaging_per_unit": packaging,
        })

    if not groups:
        flash("No purchase orders detected. Ensure your CSV has an 'Order Number' column.", "danger")
        return redirect(url_for("purchases.import_upload"))

    # Identify missing SKUs
    missing_skus = set()
    for g in groups.values():
        for ln in g["lines"]:
            sku = (ln.get("sku") or "").strip()
            if sku and not Item.query.filter_by(sku=sku).first():
                missing_skus.add(sku)

    payload = {
        "orders": list(groups.values()),
        "missing_skus": sorted(list(missing_skus)),
        "stats": {
            "orders_count": len(groups),
            "lines_count": sum(len(g["lines"]) for g in groups.values()),
            "missing_skus_count": len(missing_skus),
        }
    }

    batch = ImportBatch(filename=f.filename, payload=payload)
    db.session.add(batch)
    db.session.commit()

    return redirect(url_for("purchases.import_preview", batch_id=batch.id))


@purchases_bp.get("/import/<int:batch_id>/preview")
@login_required
@require_edit_permission
def import_preview(batch_id: int):
    batch = db.session.get(ImportBatch, batch_id)
    if not batch:
        flash("Import batch not found.", "danger")
        return redirect(url_for("purchases.import_upload"))

    payload = batch.payload
    return render_template("purchases/import_preview.html", batch=batch, payload=payload)


@purchases_bp.post("/import/<int:batch_id>/commit")
@login_required
@require_edit_permission
def import_commit(batch_id: int):
    batch = db.session.get(ImportBatch, batch_id)
    if not batch:
        flash("Import batch not found.", "danger")
        return redirect(url_for("purchases.import_upload"))

    payload = batch.payload
    create_missing = (request.form.get("create_missing") == "1")

    created_items = 0
    created_pos = 0
    created_lines = 0

    for po_data in payload.get("orders", []):
        po = PurchaseOrder(
            supplier_name=po_data.get("supplier"),
            brand=po_data.get("brand"),
            order_number=po_data.get("order_number"),
            order_date=_safe_date(po_data.get("order_date") or ""),
            arrival_date=_safe_date(po_data.get("arrival_date") or ""),
            currency="EUR",
            freight_total=_safe_decimal(po_data.get("freight_total"), default=None) if po_data.get("freight_total") else None,
            allocation_method=po_data.get("allocation_method") or "value",
        )
        db.session.add(po)
        db.session.flush()  # get po.id
        created_pos += 1

        for ln in po_data.get("lines", []):
            sku = (ln.get("sku") or "").strip()
            if not sku:
                continue

            item = Item.query.filter_by(sku=sku).first()
            if not item and create_missing:
                item = Item(
                    sku=sku,
                    description=(ln.get("description") or sku)[:255],
                    brand=po.brand,
                    supplier=po.supplier_name,
                    colour=(ln.get("colour") or "").strip() or None,
                    size=(ln.get("size") or "").strip() or None,
                    vat_rate=Decimal("18.00"),
                    is_active=True,
                )
                # weight optional
                w = (ln.get("weight") or "").strip()
                if w:
                    item.weight = _safe_decimal(w, default=None)
                db.session.add(item)
                db.session.flush()
                created_items += 1

            if not item:
                # Skip line if missing SKU and user chose not to create
                continue

            qty = _safe_int(ln.get("qty"), default=0)
            unit_cost = _safe_decimal(ln.get("unit_cost_net"), default=Decimal("0"))
            pkg = _safe_decimal(ln.get("packaging_per_unit"), default=Decimal("0"))

            pl = PurchaseLine(
                purchase_order_id=po.id,
                item_id=item.id,
                sku=item.sku,
                description=(ln.get("description") or item.description),
                colour=(ln.get("colour") or item.colour),
                size=(ln.get("size") or item.size),
                qty=qty,
                unit_cost_net=unit_cost,
                packaging_per_unit=pkg,
            )
            db.session.add(pl)
            created_lines += 1

    db.session.commit()

    # Recalc allocations for all newly created POs
    new_pos = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).limit(created_pos).all()
    for po in new_pos:
        _recalc_allocations(po)

    flash(
        f"Import complete. Created POs: {created_pos}, lines: {created_lines}, new SKUs: {created_items}.",
        "success",
    )
    return redirect(url_for("purchases.list_purchase_orders"))
