import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, redirect, url_for, flash, request, Response
from flask_login import login_required

from ..extensions import db
from ..decorators import require_role, require_edit_permission
from ..models import (
    Item,
    ImportBatch,
    SalesOrder,
    SalesLine,
    PurchaseOrder,
    PurchaseLine,
)

sales_bp = Blueprint("sales", __name__, url_prefix="/sales")


# -------------------------
# Helpers (CSV + parsing)
# -------------------------

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
    Supports: YYYY-MM-DD, DD/MM/YYYY, DD/MM/YY, YYYY-MM-DD HH:MM:SS
    """
    s = (val or "").strip()
    if not s:
        return None

    # Some exports include timestamps; try a few common formats
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _gross_to_net(gross: Decimal, vat_rate: Decimal) -> Decimal:
    """
    Malta: prices are VAT-inclusive. net = gross / (1 + vat/100)
    """
    vr = vat_rate if vat_rate is not None else Decimal("18.00")
    factor = Decimal("1.0") + (Decimal(str(vr)) / Decimal("100.0"))
    if factor <= 0:
        return gross
    return (gross / factor)


def _effective_po_date(po: PurchaseOrder):
    """
    For cost selection: use arrival_date, else order_date, else created_at.date()
    """
    if po.arrival_date:
        return po.arrival_date
    if po.order_date:
        return po.order_date
    if po.created_at:
        return po.created_at.date()
    return None


def _line_landed_cost(pl: PurchaseLine) -> Decimal:
    """
    Prefer landed_unit_cost. If missing, fallback to unit_cost_net + packaging_per_unit.
    """
    if pl.landed_unit_cost is not None:
        return Decimal(str(pl.landed_unit_cost))
    unit = Decimal(str(pl.unit_cost_net or 0))
    pkg = Decimal(str(pl.packaging_per_unit or 0))
    return unit + pkg


def _compute_unit_cost_basis(sku: str, sale_date, method: str):
    """
    method:
      - 'weighted_avg': weighted average landed unit cost for purchases with effective_date <= sale_date
      - 'last': last available purchase landed unit cost with effective_date <= sale_date
    Fallbacks:
      - if no purchases before sale_date, use latest purchase overall
      - if no purchases at all, return (0, None)
    Returns: (unit_cost_basis: Decimal, cost_source_po_id: int|None)
    """
    sku = (sku or "").strip()
    if not sku:
        return (Decimal("0"), None)

    rows = (
        db.session.query(PurchaseLine, PurchaseOrder)
        .join(PurchaseOrder, PurchaseLine.purchase_order_id == PurchaseOrder.id)
        .filter(PurchaseLine.sku == sku)
        .all()
    )

    if not rows:
        return (Decimal("0"), None)

    enriched = []
    for pl, po in rows:
        eff = _effective_po_date(po)
        qty = int(pl.qty or 0)
        if qty <= 0:
            continue
        cost = _line_landed_cost(pl)
        enriched.append((eff, po.id, qty, cost))

    if not enriched:
        return (Decimal("0"), None)

    # Filter by sale_date if possible
    before = [r for r in enriched if r[0] is not None and sale_date is not None and r[0] <= sale_date]

    # If none match (or dates missing), fallback to all
    candidates = before if before else enriched

    # Sort newest first by effective date (None last)
    candidates_sorted = sorted(
        candidates,
        key=lambda x: (x[0] is None, x[0]),
        reverse=True,
    )

    if (method or "weighted_avg") == "last":
        eff, po_id, qty, cost = candidates_sorted[0]
        return (Decimal(str(cost)), int(po_id) if po_id else None)

    # weighted average
    total_qty = sum(Decimal(qty) for _, _, qty, _ in candidates)
    if total_qty <= 0:
        return (Decimal("0"), None)

    total_cost = sum(Decimal(qty) * Decimal(str(cost)) for _, _, qty, cost in candidates)
    avg = total_cost / total_qty
    return (avg, None)


# -------------------------
# Views
# -------------------------

@sales_bp.get("")
@login_required
@require_role("viewer")
def list_sales_orders():
    q = (request.args.get("q") or "").strip().lower()
    channel = (request.args.get("channel") or "").strip().lower()
    date_from = _safe_date(request.args.get("from") or "")
    date_to = _safe_date(request.args.get("to") or "")

    query = SalesOrder.query

    if q:
        query = query.filter(
            db.or_(
                db.func.lower(SalesOrder.order_number).contains(q),
                db.func.lower(SalesOrder.customer_name).contains(q),
                db.func.lower(SalesOrder.customer_email).contains(q),
            )
        )

    if channel:
        query = query.filter(db.func.lower(SalesOrder.channel) == channel)

    if date_from:
        query = query.filter(SalesOrder.order_date >= date_from)
    if date_to:
        query = query.filter(SalesOrder.order_date <= date_to)

    orders = query.order_by(SalesOrder.order_date.desc(), SalesOrder.id.desc()).limit(200).all()

    # Distinct channels for dropdown
    channels = [r[0] for r in db.session.query(SalesOrder.channel).distinct().order_by(SalesOrder.channel.asc()).all()]

    # Totals per order (single grouped query)
    order_ids = [o.id for o in orders]
    
    totals_map = {}
    if order_ids:
        totals = (
            db.session.query(
                SalesLine.sales_order_id,
                db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0),
                db.func.coalesce(db.func.sum(SalesLine.cost_total), 0),
                db.func.coalesce(db.func.sum(SalesLine.profit), 0),
                db.func.coalesce(db.func.sum(SalesLine.qty), 0),
            )
            .filter(SalesLine.sales_order_id.in_(order_ids))
            .group_by(SalesLine.sales_order_id)
            .all()
        )
        totals_map = {oid: {"rev": rev, "cost": cost, "profit": prof, "units": units} for oid, rev, cost, prof, units in totals}

    return render_template(
        "sales/orders_list.html",
        orders=orders,
        q=q,
        channel=channel,
        date_from=(date_from.isoformat() if date_from else ""),
        date_to=(date_to.isoformat() if date_to else ""),
        channels=channels,
        totals_map=totals_map,
    )


@sales_bp.get("/<int:order_id>")
@login_required
@require_role("viewer")
def sales_order_detail(order_id: int):
    so = db.session.get(SalesOrder, order_id)
    if not so:
        flash("Sales order not found.", "danger")
        return redirect(url_for("sales.list_sales_orders"))

    lines = SalesLine.query.filter_by(sales_order_id=so.id).order_by(SalesLine.sku.asc()).all()

    total_units = sum(l.qty or 0 for l in lines)
    total_rev_net = sum(Decimal(str(l.revenue_net or 0)) for l in lines)
    total_cost = sum(Decimal(str(l.cost_total or 0)) for l in lines)
    total_profit = sum(Decimal(str(l.profit or 0)) for l in lines)

    # Discount analytics
    total_discount_gross = sum(
        Decimal(str((l.line_discount_gross or 0))) + Decimal(str((l.order_discount_alloc_gross or 0)))
        for l in lines
    )

    # Convert discounts from gross to net (by line VAT rate) so we can compare profit on net basis.
    total_discount_net = sum(
        _gross_to_net(
            Decimal(str((l.line_discount_gross or 0))) + Decimal(str((l.order_discount_alloc_gross or 0))),
            Decimal(str(l.vat_rate or Decimal("18.00"))),
        )
        for l in lines
    )

    # Profit if there were no discounts (net revenue + net discounts - cost)
    profit_no_discount = (total_rev_net + total_discount_net) - total_cost
    profit_lost_to_discounts = profit_no_discount - total_profit

    margin = Decimal("0")
    if total_rev_net > 0:
        margin = (total_profit / total_rev_net) * Decimal("100")

    return render_template(
        "sales/order_detail.html",
        so=so,
        lines=lines,
        total_units=total_units,
        total_rev_net=total_rev_net,
        total_cost=total_cost,
        total_profit=total_profit,
        margin=margin,
        total_discount_gross=total_discount_gross,
        total_discount_net=total_discount_net,
        profit_no_discount=profit_no_discount,
        profit_lost_to_discounts=profit_lost_to_discounts,
    )

# -------------------------
# Import (Upload -> Preview -> Commit)
# -------------------------

@sales_bp.get("/import")
@login_required
@require_edit_permission
def import_upload():
    return render_template("sales/import_upload.html")


@sales_bp.post("/import")
@login_required
@require_edit_permission
def import_parse():
    f = request.files.get("file")
    if not f or f.filename == "":
        flash("Please choose a CSV file.", "danger")
        return redirect(url_for("sales.import_upload"))

    raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("CSV must be UTF-8 encoded.", "danger")
        return redirect(url_for("sales.import_upload"))

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        flash("CSV appears empty or invalid.", "danger")
        return redirect(url_for("sales.import_upload"))

    header_map = {_norm_header(h): h for h in reader.fieldnames}

    rows = list(reader)

    groups = {}
    skipped_no_order = 0
    skipped_no_sku = 0

    for r in rows:
        order_number = _pick(
            r,
            header_map,
            "Order Number",
            "order_number",
            "Order",
            "Name",
            "Order ID",
            "order_id",
            "Invoice Number",
        )
        if not order_number:
            skipped_no_order += 1
            continue

        order_date = _safe_date(
            _pick(
                r,
                header_map,
                "Order Date",
                "Created at",
                "Created At",
                "Date",
                "Processed at",
                "processed_at",
            )
        )

        channel = _pick(r, header_map, "Channel", "Source", "Sales Channel", "sales_channel") or "unknown"
        currency = _pick(r, header_map, "Currency", "Presentment currency", "presentment_currency") or "EUR"

        customer_name = _pick(r, header_map, "Customer", "Customer Name", "customer_name")
        customer_email = _pick(r, header_map, "Email", "Customer Email", "customer_email")

        shipping = _safe_decimal(_pick(r, header_map, "Shipping", "Shipping amount", "Total shipping", "shipping"), default=Decimal("0"))
        order_discount = _safe_decimal(_pick(r, header_map, "Total discounts", "Order Discount", "Discount", "discount_total"), default=Decimal("0"))

        sku = _pick(r, header_map, "SKU", "sku", "Variant SKU", "Lineitem sku", "Lineitem SKU")
        if not sku:
            skipped_no_sku += 1
            continue

        desc = _pick(r, header_map, "Item", "Title", "Lineitem name", "Lineitem Name", "Description", "Item Description")

        qty = _safe_int(_pick(r, header_map, "Qty", "Quantity", "Lineitem quantity", "Lineitem Quantity"), default=0)
        if qty <= 0:
            # Allow 0? Usually meaningless; skip
            continue

        unit_price_gross = _safe_decimal(_pick(r, header_map, "Unit Price", "Price", "Lineitem price", "Lineitem Price"), default=Decimal("0"))
        line_total_gross = _safe_decimal(_pick(r, header_map, "Line Total", "Line total", "Lineitem total", "Lineitem Total"), default=Decimal("0"))

        if unit_price_gross <= 0 and line_total_gross > 0 and qty > 0:
            unit_price_gross = (line_total_gross / Decimal(qty))

        line_discount_gross = _safe_decimal(
            _pick(r, header_map, "Line Discount", "Discount amount", "Lineitem discount", "Lineitem Discount"),
            default=Decimal("0"),
        )

        # Some exports store discounts as negative numbers
        if line_discount_gross < 0:
            line_discount_gross = abs(line_discount_gross)

        if order_number not in groups:
            groups[order_number] = {
                "order_number": order_number,
                "order_date": order_date.isoformat() if order_date else None,
                "channel": channel.strip().lower(),
                "currency": currency.strip().upper(),
                "customer_name": customer_name or None,
                "customer_email": customer_email or None,
                # these may appear repeated on each line in exports; use max to avoid double counting
                "shipping_charged_gross": str(shipping) if shipping else None,
                "order_discount_gross": str(order_discount) if order_discount else None,
                "lines": [],
            }
        else:
            # de-dupe / consolidate per order
            if shipping and (Decimal(groups[order_number].get("shipping_charged_gross") or "0") < shipping):
                groups[order_number]["shipping_charged_gross"] = str(shipping)
            if order_discount and (Decimal(groups[order_number].get("order_discount_gross") or "0") < order_discount):
                groups[order_number]["order_discount_gross"] = str(order_discount)

        groups[order_number]["lines"].append(
            {
                "sku": sku.strip(),
                "description": desc,
                "qty": str(qty),
                "unit_price_gross": str(unit_price_gross),
                "line_discount_gross": str(line_discount_gross),
            }
        )

    if not groups:
        flash("No orders detected. Ensure your CSV includes an order number and SKU columns.", "danger")
        return redirect(url_for("sales.import_upload"))

    # Missing SKUs
    missing_skus = set()
    for g in groups.values():
        for ln in g["lines"]:
            s = (ln.get("sku") or "").strip()
            if s and not Item.query.filter_by(sku=s).first():
                missing_skus.add(s)

    payload = {
        "orders": list(groups.values()),
        "missing_skus": sorted(list(missing_skus)),
        "missing_skus_count": len(missing_skus),
        "stats": {
            "orders_count": len(groups),
            "lines_count": sum(len(g["lines"]) for g in groups.values()),
            "skipped_no_order": skipped_no_order,
            "skipped_no_sku": skipped_no_sku,
        },
    }

    batch = ImportBatch(kind="sales_import", filename=f.filename, payload=payload)
    db.session.add(batch)
    db.session.commit()

    return redirect(url_for("sales.import_preview", batch_id=batch.id))


@sales_bp.get("/import/<int:batch_id>/preview")
@login_required
@require_edit_permission
def import_preview(batch_id: int):
    batch = db.session.get(ImportBatch, batch_id)
    if not batch:
        flash("Import batch not found.", "danger")
        return redirect(url_for("sales.import_upload"))

    payload = batch.payload
    return render_template("sales/import_preview.html", batch=batch, payload=payload)


@sales_bp.post("/import/<int:batch_id>/commit")
@login_required
@require_edit_permission
def import_commit(batch_id: int):
    batch = db.session.get(ImportBatch, batch_id)
    if not batch:
        flash("Import batch not found.", "danger")
        return redirect(url_for("sales.import_upload"))

    payload = batch.payload

    create_missing = (request.form.get("create_missing") == "1")
    cost_method = (request.form.get("cost_method") or "weighted_avg").strip()

    created_orders = 0
    created_lines = 0
    created_items = 0
    skipped_existing = 0
    skipped_missing_sku = 0

    for o in payload.get("orders", []):
        order_number = (o.get("order_number") or "").strip()
        if not order_number:
            continue

        channel = (o.get("channel") or "unknown").strip().lower()
        currency = (o.get("currency") or "EUR").strip().upper()
        order_date = _safe_date(o.get("order_date") or "") or datetime.utcnow().date()

        # skip duplicates (unique by channel + order_number)
        existing = SalesOrder.query.filter_by(channel=channel, order_number=order_number).first()
        if existing:
            skipped_existing += 1
            continue

        shipping_gross = _safe_decimal(o.get("shipping_charged_gross"), default=Decimal("0"))
        order_disc_gross = _safe_decimal(o.get("order_discount_gross"), default=Decimal("0"))

        so = SalesOrder(
            order_number=order_number,
            order_date=order_date,
            channel=channel,
            currency=currency,
            customer_name=o.get("customer_name"),
            customer_email=o.get("customer_email"),
            shipping_charged_gross=shipping_gross if shipping_gross != 0 else None,
            order_discount_gross=order_disc_gross if order_disc_gross != 0 else None,
        )
        db.session.add(so)
        db.session.flush()
        created_orders += 1

        # Prepare allocation of order-level discount across lines
        prepared = []
        for ln in o.get("lines", []):
            sku = (ln.get("sku") or "").strip()
            if not sku:
                continue

            item = Item.query.filter_by(sku=sku).first()
            if not item and create_missing:
                item = Item(
                    sku=sku,
                    description=(ln.get("description") or sku)[:255],
                    vat_rate=Decimal("18.00"),
                    is_active=True,
                )
                db.session.add(item)
                db.session.flush()
                created_items += 1

            if not item:
                skipped_missing_sku += 1
                continue

            qty = _safe_int(ln.get("qty"), default=0)
            if qty <= 0:
                continue

            unit_price_gross = _safe_decimal(ln.get("unit_price_gross"), default=Decimal("0"))
            line_discount_gross = _safe_decimal(ln.get("line_discount_gross"), default=Decimal("0"))

            gross_line = unit_price_gross * Decimal(qty)
            base_after_line_discount = gross_line - line_discount_gross
            if base_after_line_discount < 0:
                base_after_line_discount = Decimal("0")

            prepared.append(
                {
                    "item": item,
                    "sku": sku,
                    "description": (ln.get("description") or item.description),
                    "qty": qty,
                    "unit_price_gross": unit_price_gross,
                    "line_discount_gross": line_discount_gross,
                    "base_after_line_discount": base_after_line_discount,
                }
            )

        total_base = sum(p["base_after_line_discount"] for p in prepared) or Decimal("0")
        order_discount_total = order_disc_gross if order_disc_gross is not None else Decimal("0")

        for p in prepared:
            # Allocate order-level discount proportionally
            alloc = Decimal("0")
            if order_discount_total > 0 and total_base > 0:
                alloc = (order_discount_total * (p["base_after_line_discount"] / total_base))

            gross_after_all_discounts = p["base_after_line_discount"] - alloc
            if gross_after_all_discounts < 0:
                gross_after_all_discounts = Decimal("0")

            vat_rate = Decimal(str(p["item"].vat_rate or Decimal("18.00")))

            unit_price_net = _gross_to_net(p["unit_price_gross"], vat_rate)
            revenue_net = _gross_to_net(gross_after_all_discounts, vat_rate)

            unit_cost_basis, cost_source_po_id = _compute_unit_cost_basis(p["sku"], order_date, cost_method)
            cost_total = unit_cost_basis * Decimal(p["qty"])
            profit = revenue_net - cost_total

            sl = SalesLine(
                sales_order_id=so.id,
                item_id=p["item"].id,
                sku=p["sku"],
                description=(p["description"] or "")[:255],
                qty=p["qty"],
                unit_price_gross=p["unit_price_gross"],
                line_discount_gross=p["line_discount_gross"] if p["line_discount_gross"] != 0 else None,
                order_discount_alloc_gross=alloc if alloc != 0 else None,
                vat_rate=vat_rate,
                unit_price_net=unit_price_net,
                revenue_net=revenue_net,
                cost_method=cost_method,
                unit_cost_basis=unit_cost_basis,
                cost_total=cost_total,
                profit=profit,
                cost_source_po_id=cost_source_po_id,
            )
            db.session.add(sl)
            created_lines += 1

    db.session.commit()

    flash(
        f"Sales import complete. Created orders: {created_orders}, lines: {created_lines}, "
        f"new SKUs: {created_items}, skipped existing orders: {skipped_existing}, "
        f"skipped lines (missing SKUs): {skipped_missing_sku}.",
        "success",
    )
    return redirect(url_for("sales.list_sales_orders"))

# -------------------------
# Item-level report (SKU)
# -------------------------

@sales_bp.get("/items-report")
@login_required
@require_role("viewer")
def items_report():
    q = (request.args.get("q") or "").strip().lower()
    channel = (request.args.get("channel") or "").strip().lower()
    date_from = _safe_date(request.args.get("from") or "")
    date_to = _safe_date(request.args.get("to") or "")

    query = (
        db.session.query(
            SalesLine.sku.label("sku"),
            db.func.max(SalesLine.description).label("description"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("qty_sold"),
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("revenue_net"),
            db.func.coalesce(db.func.sum(SalesLine.cost_total), 0).label("cost_total"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
    )

    if q:
        query = query.filter(
            db.or_(
                db.func.lower(SalesLine.sku).contains(q),
                db.func.lower(SalesLine.description).contains(q),
            )
        )

    if channel:
        query = query.filter(db.func.lower(SalesOrder.channel) == channel)

    if date_from:
        query = query.filter(SalesOrder.order_date >= date_from)
    if date_to:
        query = query.filter(SalesOrder.order_date <= date_to)

    rows = (
        query.group_by(SalesLine.sku)
        .order_by(db.desc(db.func.coalesce(db.func.sum(SalesLine.profit), 0)))
        .limit(500)
        .all()
    )

    channels = [r[0] for r in db.session.query(SalesOrder.channel).distinct().order_by(SalesOrder.channel.asc()).all()]

    # Compute grand totals for the footer/KPIs
    total_qty = sum(int(r.qty_sold or 0) for r in rows)
    total_rev = sum(Decimal(str(r.revenue_net or 0)) for r in rows)
    total_cost = sum(Decimal(str(r.cost_total or 0)) for r in rows)
    total_profit = sum(Decimal(str(r.profit or 0)) for r in rows)
    total_margin = Decimal("0")
    if total_rev > 0:
        total_margin = (total_profit / total_rev) * Decimal("100")

    return render_template(
        "sales/items_report.html",
        rows=rows,
        q=q,
        channel=channel,
        date_from=(date_from.isoformat() if date_from else ""),
        date_to=(date_to.isoformat() if date_to else ""),
        channels=channels,
        total_qty=total_qty,
        total_rev=total_rev,
        total_cost=total_cost,
        total_profit=total_profit,
        total_margin=total_margin,
    )

@sales_bp.get("/items-report.csv")
@login_required
@require_role("viewer")
def items_report_csv():
    q = (request.args.get("q") or "").strip().lower()
    channel = (request.args.get("channel") or "").strip().lower()
    date_from = _safe_date(request.args.get("from") or "")
    date_to = _safe_date(request.args.get("to") or "")

    query = (
        db.session.query(
            SalesLine.sku.label("sku"),
            db.func.max(SalesLine.description).label("description"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("qty_sold"),
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("revenue_net"),
            db.func.coalesce(db.func.sum(SalesLine.cost_total), 0).label("cost_total"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
    )

    if q:
        query = query.filter(
            db.or_(
                db.func.lower(SalesLine.sku).contains(q),
                db.func.lower(SalesLine.description).contains(q),
            )
        )

    if channel:
        query = query.filter(db.func.lower(SalesOrder.channel) == channel)

    if date_from:
        query = query.filter(SalesOrder.order_date >= date_from)
    if date_to:
        query = query.filter(SalesOrder.order_date <= date_to)

    rows = (
        query.group_by(SalesLine.sku)
        .order_by(db.desc(db.func.coalesce(db.func.sum(SalesLine.profit), 0)))
        .limit(5000)
        .all()
    )

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["SKU", "Description", "Qty Sold", "Revenue Net", "Cost Total", "Profit", "Margin %"])

    for r in rows:
        rev = Decimal(str(r.revenue_net or 0))
        prof = Decimal(str(r.profit or 0))
        margin = Decimal("0")
        if rev > 0:
            margin = (prof / rev) * Decimal("100")

        w.writerow([
            r.sku,
            r.description or "",
            int(r.qty_sold or 0),
            f"{rev:.2f}",
            f"{Decimal(str(r.cost_total or 0)):.2f}",
            f"{prof:.2f}",
            f"{margin:.2f}",
        ])

    csv_data = out.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sales_items_report.csv"},
    )

# -------------------------
# Discount report
# -------------------------

@sales_bp.get("/discount-report")
@login_required
@require_role("viewer")
def discount_report():
    q = (request.args.get("q") or "").strip().lower()
    channel = (request.args.get("channel") or "").strip().lower()
    date_from = _safe_date(request.args.get("from") or "")
    date_to = _safe_date(request.args.get("to") or "")

    query = (
        db.session.query(
            SalesLine.sku.label("sku"),
            db.func.max(SalesLine.description).label("description"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("qty_sold"),
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("revenue_net"),
            db.func.coalesce(db.func.sum(SalesLine.cost_total), 0).label("cost_total"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
            db.func.coalesce(db.func.sum(SalesLine.line_discount_gross), 0).label("line_discount_gross"),
            db.func.coalesce(db.func.sum(SalesLine.order_discount_alloc_gross), 0).label("order_discount_alloc_gross"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
    )

    if q:
        query = query.filter(
            db.or_(
                db.func.lower(SalesLine.sku).contains(q),
                db.func.lower(SalesLine.description).contains(q),
            )
        )
    if channel:
        query = query.filter(db.func.lower(SalesOrder.channel) == channel)
    if date_from:
        query = query.filter(SalesOrder.order_date >= date_from)
    if date_to:
        query = query.filter(SalesOrder.order_date <= date_to)

    rows = (
        query.group_by(SalesLine.sku)
        .order_by(db.desc(db.func.coalesce(db.func.sum(SalesLine.line_discount_gross), 0) +
                          db.func.coalesce(db.func.sum(SalesLine.order_discount_alloc_gross), 0)))
        .limit(500)
        .all()
    )

    channels = [r[0] for r in db.session.query(SalesOrder.channel).distinct().order_by(SalesOrder.channel.asc()).all()]

    # Totals
    total_qty = sum(int(r.qty_sold or 0) for r in rows)
    total_rev = sum(Decimal(str(r.revenue_net or 0)) for r in rows)
    total_cost = sum(Decimal(str(r.cost_total or 0)) for r in rows)
    total_profit = sum(Decimal(str(r.profit or 0)) for r in rows)
    total_disc_gross = sum(Decimal(str(r.line_discount_gross or 0)) + Decimal(str(r.order_discount_alloc_gross or 0)) for r in rows)

    total_margin = Decimal("0")
    if total_rev > 0:
        total_margin = (total_profit / total_rev) * Decimal("100")

    # Discount ratio (gross discounts vs gross sales estimate)
    # We don't store gross sales total directly, so we approximate gross sales as:
    # gross_after_discounts + discounts.
    # gross_after_discounts is not stored per line, so we compute an approximate:
    # net revenue -> gross using VAT 18%. This is an approximation because items can have different VAT rates.
    approx_gross_sales = total_rev * Decimal("1.18")
    discount_pct = Decimal("0")
    if approx_gross_sales > 0:
        discount_pct = (total_disc_gross / approx_gross_sales) * Decimal("100")

    return render_template(
        "sales/discount_report.html",
        rows=rows,
        q=q,
        channel=channel,
        date_from=(date_from.isoformat() if date_from else ""),
        date_to=(date_to.isoformat() if date_to else ""),
        channels=channels,
        total_qty=total_qty,
        total_rev=total_rev,
        total_cost=total_cost,
        total_profit=total_profit,
        total_margin=total_margin,
        total_disc_gross=total_disc_gross,
        approx_gross_sales=approx_gross_sales,
        discount_pct=discount_pct,
    )


@sales_bp.get("/discount-report.csv")
@login_required
@require_role("viewer")
def discount_report_csv():
    q = (request.args.get("q") or "").strip().lower()
    channel = (request.args.get("channel") or "").strip().lower()
    date_from = _safe_date(request.args.get("from") or "")
    date_to = _safe_date(request.args.get("to") or "")

    query = (
        db.session.query(
            SalesLine.sku.label("sku"),
            db.func.max(SalesLine.description).label("description"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("qty_sold"),
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("revenue_net"),
            db.func.coalesce(db.func.sum(SalesLine.cost_total), 0).label("cost_total"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
            db.func.coalesce(db.func.sum(SalesLine.line_discount_gross), 0).label("line_discount_gross"),
            db.func.coalesce(db.func.sum(SalesLine.order_discount_alloc_gross), 0).label("order_discount_alloc_gross"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
    )

    if q:
        query = query.filter(
            db.or_(
                db.func.lower(SalesLine.sku).contains(q),
                db.func.lower(SalesLine.description).contains(q),
            )
        )
    if channel:
        query = query.filter(db.func.lower(SalesOrder.channel) == channel)
    if date_from:
        query = query.filter(SalesOrder.order_date >= date_from)
    if date_to:
        query = query.filter(SalesOrder.order_date <= date_to)

    rows = (
        query.group_by(SalesLine.sku)
        .order_by(db.desc(db.func.coalesce(db.func.sum(SalesLine.line_discount_gross), 0) +
                          db.func.coalesce(db.func.sum(SalesLine.order_discount_alloc_gross), 0)))
        .limit(5000)
        .all()
    )

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["SKU", "Description", "Qty Sold", "Revenue Net", "Cost Total", "Profit", "Discount Gross", "Discount % (approx)"])

    for r in rows:
        disc_gross = Decimal(str(r.line_discount_gross or 0)) + Decimal(str(r.order_discount_alloc_gross or 0))
        rev_net = Decimal(str(r.revenue_net or 0))
        approx_gross = rev_net * Decimal("1.18")
        disc_pct = Decimal("0")
        if approx_gross > 0:
            disc_pct = (disc_gross / approx_gross) * Decimal("100")

        w.writerow([
            r.sku,
            r.description or "",
            int(r.qty_sold or 0),
            f"{rev_net:.2f}",
            f"{Decimal(str(r.cost_total or 0)):.2f}",
            f"{Decimal(str(r.profit or 0)):.2f}",
            f"{disc_gross:.2f}",
            f"{disc_pct:.2f}",
        ])

    csv_data = out.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sales_discount_report.csv"},
    )

# -------------------------
# Alerts: negative margin / low margin / high discount
# -------------------------

@sales_bp.get("/alerts")
@login_required
@require_role("viewer")
def alerts():
    q = (request.args.get("q") or "").strip().lower()
    channel = (request.args.get("channel") or "").strip().lower()
    date_from = _safe_date(request.args.get("from") or "")
    date_to = _safe_date(request.args.get("to") or "")

    # Thresholds (defaults)
    margin_threshold = _safe_decimal(request.args.get("margin") or "20", default=Decimal("20"))
    discount_threshold = _safe_decimal(request.args.get("discount") or "15", default=Decimal("15"))

    # Aggregate per SKU
    query = (
        db.session.query(
            SalesLine.sku.label("sku"),
            db.func.max(SalesLine.description).label("description"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("qty_sold"),
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("revenue_net"),
            db.func.coalesce(db.func.sum(SalesLine.cost_total), 0).label("cost_total"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
            db.func.coalesce(db.func.sum(SalesLine.line_discount_gross), 0).label("line_discount_gross"),
            db.func.coalesce(db.func.sum(SalesLine.order_discount_alloc_gross), 0).label("order_discount_alloc_gross"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
    )

    if q:
        query = query.filter(
            db.or_(
                db.func.lower(SalesLine.sku).contains(q),
                db.func.lower(SalesLine.description).contains(q),
            )
        )

    if channel:
        query = query.filter(db.func.lower(SalesOrder.channel) == channel)

    if date_from:
        query = query.filter(SalesOrder.order_date >= date_from)
    if date_to:
        query = query.filter(SalesOrder.order_date <= date_to)

    rows = query.group_by(SalesLine.sku).all()

    # Build alert rows in Python for reliable type math
    alert_rows = []
    counts = {"negative_profit": 0, "low_margin": 0, "high_discount": 0}

    for r in rows:
        rev_net = Decimal(str(r.revenue_net or 0))
        profit = Decimal(str(r.profit or 0))
        cost_total = Decimal(str(r.cost_total or 0))
        qty_sold = int(r.qty_sold or 0)

        disc_gross = Decimal(str(r.line_discount_gross or 0)) + Decimal(str(r.order_discount_alloc_gross or 0))

        margin_pct = Decimal("0")
        if rev_net > 0:
            margin_pct = (profit / rev_net) * Decimal("100")

        # Approx: net -> gross with 18% VAT (good enough for KPI/alerts; can refine later with VAT-rate weighting)
        approx_gross_sales = rev_net * Decimal("1.18")
        discount_pct = Decimal("0")
        if approx_gross_sales > 0:
            discount_pct = (disc_gross / approx_gross_sales) * Decimal("100")

        is_negative = profit < 0
        is_low_margin = (rev_net > 0) and (margin_pct < margin_threshold)
        is_high_discount = (disc_gross > 0) and (discount_pct > discount_threshold)

        if is_negative:
            counts["negative_profit"] += 1
        if is_low_margin:
            counts["low_margin"] += 1
        if is_high_discount:
            counts["high_discount"] += 1

        # Only show rows that trigger at least one alert
        if is_negative or is_low_margin or is_high_discount:
            alert_rows.append(
                {
                    "sku": r.sku,
                    "description": r.description or "",
                    "qty_sold": qty_sold,
                    "revenue_net": rev_net,
                    "cost_total": cost_total,
                    "profit": profit,
                    "margin_pct": margin_pct,
                    "discount_gross": disc_gross,
                    "discount_pct": discount_pct,
                    "flag_negative": is_negative,
                    "flag_low_margin": is_low_margin,
                    "flag_high_discount": is_high_discount,
                }
            )

    # Sort: worst first (negative profit at top, then lowest margin, then highest discount)
    def _sort_key(x):
        return (
            0 if x["flag_negative"] else 1,
            float(x["margin_pct"]),
            -float(x["discount_pct"]),
            -float(x["profit"]),
        )

    alert_rows.sort(key=_sort_key)

    channels = [r[0] for r in db.session.query(SalesOrder.channel).distinct().order_by(SalesOrder.channel.asc()).all()]

    return render_template(
        "sales/alerts.html",
        rows=alert_rows,
        counts=counts,
        q=q,
        channel=channel,
        date_from=(date_from.isoformat() if date_from else ""),
        date_to=(date_to.isoformat() if date_to else ""),
        channels=channels,
        margin_threshold=margin_threshold,
        discount_threshold=discount_threshold,
    )


@sales_bp.get("/alerts.csv")
@login_required
@require_role("viewer")
def alerts_csv():
    q = (request.args.get("q") or "").strip().lower()
    channel = (request.args.get("channel") or "").strip().lower()
    date_from = _safe_date(request.args.get("from") or "")
    date_to = _safe_date(request.args.get("to") or "")

    margin_threshold = _safe_decimal(request.args.get("margin") or "20", default=Decimal("20"))
    discount_threshold = _safe_decimal(request.args.get("discount") or "15", default=Decimal("15"))

    # Reuse logic by calling alerts() would be messy; repeat the aggregation safely.
    query = (
        db.session.query(
            SalesLine.sku.label("sku"),
            db.func.max(SalesLine.description).label("description"),
            db.func.coalesce(db.func.sum(SalesLine.qty), 0).label("qty_sold"),
            db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0).label("revenue_net"),
            db.func.coalesce(db.func.sum(SalesLine.cost_total), 0).label("cost_total"),
            db.func.coalesce(db.func.sum(SalesLine.profit), 0).label("profit"),
            db.func.coalesce(db.func.sum(SalesLine.line_discount_gross), 0).label("line_discount_gross"),
            db.func.coalesce(db.func.sum(SalesLine.order_discount_alloc_gross), 0).label("order_discount_alloc_gross"),
        )
        .join(SalesOrder, SalesLine.sales_order_id == SalesOrder.id)
    )

    if q:
        query = query.filter(
            db.or_(
                db.func.lower(SalesLine.sku).contains(q),
                db.func.lower(SalesLine.description).contains(q),
            )
        )
    if channel:
        query = query.filter(db.func.lower(SalesOrder.channel) == channel)
    if date_from:
        query = query.filter(SalesOrder.order_date >= date_from)
    if date_to:
        query = query.filter(SalesOrder.order_date <= date_to)

    rows = query.group_by(SalesLine.sku).all()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "SKU", "Description", "Qty Sold", "Revenue Net", "Cost Total", "Profit", "Margin %",
        "Discount Gross", "Discount % (approx)", "NEGATIVE_PROFIT", "LOW_MARGIN", "HIGH_DISCOUNT"
    ])

    for r in rows:
        rev_net = Decimal(str(r.revenue_net or 0))
        profit = Decimal(str(r.profit or 0))
        cost_total = Decimal(str(r.cost_total or 0))
        qty_sold = int(r.qty_sold or 0)

        disc_gross = Decimal(str(r.line_discount_gross or 0)) + Decimal(str(r.order_discount_alloc_gross or 0))

        margin_pct = Decimal("0")
        if rev_net > 0:
            margin_pct = (profit / rev_net) * Decimal("100")

        approx_gross_sales = rev_net * Decimal("1.18")
        discount_pct = Decimal("0")
        if approx_gross_sales > 0:
            discount_pct = (disc_gross / approx_gross_sales) * Decimal("100")

        flag_negative = profit < 0
        flag_low_margin = (rev_net > 0) and (margin_pct < margin_threshold)
        flag_high_discount = (disc_gross > 0) and (discount_pct > discount_threshold)

        if flag_negative or flag_low_margin or flag_high_discount:
            w.writerow([
                r.sku,
                r.description or "",
                qty_sold,
                f"{rev_net:.2f}",
                f"{cost_total:.2f}",
                f"{profit:.2f}",
                f"{margin_pct:.2f}",
                f"{disc_gross:.2f}",
                f"{discount_pct:.2f}",
                "YES" if flag_negative else "",
                "YES" if flag_low_margin else "",
                "YES" if flag_high_discount else "",
            ])

    csv_data = out.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sales_alerts.csv"},
    )

# -------------------------
# Export CSV (orders list)
# -------------------------

@sales_bp.get("/export.csv")
@login_required
@require_role("viewer")
def export_orders_csv():
    # Uses same filters as list
    q = (request.args.get("q") or "").strip().lower()
    channel = (request.args.get("channel") or "").strip().lower()
    date_from = _safe_date(request.args.get("from") or "")
    date_to = _safe_date(request.args.get("to") or "")

    query = SalesOrder.query
    if q:
        query = query.filter(
            db.or_(
                db.func.lower(SalesOrder.order_number).contains(q),
                db.func.lower(SalesOrder.customer_name).contains(q),
                db.func.lower(SalesOrder.customer_email).contains(q),
            )
        )
    if channel:
        query = query.filter(db.func.lower(SalesOrder.channel) == channel)
    if date_from:
        query = query.filter(SalesOrder.order_date >= date_from)
    if date_to:
        query = query.filter(SalesOrder.order_date <= date_to)

    orders = query.order_by(SalesOrder.order_date.desc(), SalesOrder.id.desc()).limit(2000).all()
    order_ids = [o.id for o in orders]
    
    totals_map = {}
    if order_ids:
        totals = (
            db.session.query(
                SalesLine.sales_order_id,
                db.func.coalesce(db.func.sum(SalesLine.revenue_net), 0),
                db.func.coalesce(db.func.sum(SalesLine.cost_total), 0),
                db.func.coalesce(db.func.sum(SalesLine.profit), 0),
                db.func.coalesce(db.func.sum(SalesLine.qty), 0),
            )
            .filter(SalesLine.sales_order_id.in_(order_ids))
            .group_by(SalesLine.sales_order_id)
            .all()
        )
        totals_map = {oid: {"rev": rev, "cost": cost, "profit": prof, "units": units} for oid, rev, cost, prof, units in totals}

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Order Date", "Order Number", "Channel", "Currency", "Units", "Revenue Net", "Cost", "Profit", "Margin %"])

    for o in orders:
        t = totals_map.get(o.id, {"rev": 0, "cost": 0, "profit": 0, "units": 0})
        rev = Decimal(str(t["rev"] or 0))
        prof = Decimal(str(t["profit"] or 0))
        margin = Decimal("0")
        if rev > 0:
            margin = (prof / rev) * Decimal("100")

        w.writerow([
            o.order_date.isoformat() if o.order_date else "",
            o.order_number,
            o.channel,
            o.currency,
            int(t["units"] or 0),
            f"{rev:.2f}",
            f"{Decimal(str(t['cost'] or 0)):.2f}",
            f"{prof:.2f}",
            f"{margin:.2f}",
        ])

    csv_data = out.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sales_orders_export.csv"},
    )

