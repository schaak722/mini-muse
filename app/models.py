from datetime import datetime
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import db

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_VIEWER = "viewer"

ROLE_CHOICES = [ROLE_ADMIN, ROLE_USER, ROLE_VIEWER]


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    name = db.Column(db.String(120), nullable=False, default="User")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_USER)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_viewer(self) -> bool:
        return self.role == ROLE_VIEWER

    # Flask-Login uses this to decide if user account is active
    def is_active_user(self) -> bool:
        return bool(self.is_active)

    def get_id(self):
        return str(self.id)


class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)

    sku = db.Column(db.String(80), unique=True, index=True, nullable=False)
    description = db.Column(db.String(255), nullable=False)

    brand = db.Column(db.String(80), nullable=True)
    supplier = db.Column(db.String(120), nullable=True)

    colour = db.Column(db.String(80), nullable=True)
    size = db.Column(db.String(40), nullable=True)

    weight = db.Column(db.Numeric(10, 3), nullable=True)  # kg
    vat_rate = db.Column(db.Numeric(5, 2), nullable=False, default=Decimal("18.00"))

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"

    id = db.Column(db.Integer, primary_key=True)

    supplier_name = db.Column(db.String(120), nullable=True)
    brand = db.Column(db.String(80), nullable=True)

    order_number = db.Column(db.String(80), index=True, nullable=False)
    order_date = db.Column(db.Date, nullable=True)
    arrival_date = db.Column(db.Date, nullable=True)

    currency = db.Column(db.String(10), nullable=False, default="EUR")

    freight_total = db.Column(db.Numeric(12, 2), nullable=True)  # total inbound freight for this PO
    allocation_method = db.Column(db.String(20), nullable=False, default="value")  # value|qty

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    lines = db.relationship(
        "PurchaseLine",
        backref="purchase_order",
        lazy=True,
        cascade="all, delete-orphan",
    )


class PurchaseLine(db.Model):
    __tablename__ = "purchase_lines"

    id = db.Column(db.Integer, primary_key=True)

    purchase_order_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)

    sku = db.Column(db.String(80), index=True, nullable=False)  # denormalized for convenience
    description = db.Column(db.String(255), nullable=True)
    colour = db.Column(db.String(80), nullable=True)
    size = db.Column(db.String(40), nullable=True)

    qty = db.Column(db.Integer, nullable=False, default=0)
    unit_cost_net = db.Column(db.Numeric(12, 4), nullable=False, default=Decimal("0.0000"))

    packaging_per_unit = db.Column(db.Numeric(12, 4), nullable=True)

    freight_allocated_total = db.Column(db.Numeric(12, 4), nullable=True)
    freight_allocated_per_unit = db.Column(db.Numeric(12, 4), nullable=True)

    landed_unit_cost = db.Column(db.Numeric(12, 4), nullable=True)  # unit_cost + freight/unit + packaging/unit

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    item = db.relationship("Item", lazy=True)


class ImportBatch(db.Model):
    """
    Temporary storage for preview/resolve workflow.
    """
    __tablename__ = "import_batches"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(40), nullable=False, default="purchase_import")  # future-proof

    filename = db.Column(db.String(255), nullable=True)
    payload = db.Column(db.JSON, nullable=False)  # stores parsed orders + lines
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# -------------------------
# Phase 3: Sales + Profit
# -------------------------

class SalesOrder(db.Model):
    __tablename__ = "sales_orders"

    id = db.Column(db.Integer, primary_key=True)

    # An order number is often unique per channel (Shopify, PayPal, etc.).
    order_number = db.Column(db.String(80), nullable=False)
    order_date = db.Column(db.Date, nullable=False)

    channel = db.Column(db.String(40), nullable=False, default="unknown")  # shopify|paypal|pos|manual|...
    currency = db.Column(db.String(10), nullable=False, default="EUR")

    customer_name = db.Column(db.String(120), nullable=True)
    customer_email = db.Column(db.String(255), nullable=True)

    # VAT-inclusive values for Malta (gross)
    shipping_charged_gross = db.Column(db.Numeric(12, 2), nullable=True)     # what customer paid for shipping
    order_discount_gross = db.Column(db.Numeric(12, 2), nullable=True)       # order-level discount (if provided)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    lines = db.relationship(
        "SalesLine",
        backref="sales_order",
        lazy=True,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.UniqueConstraint("channel", "order_number", name="uq_sales_orders_channel_order_number"),
        db.Index("ix_sales_orders_order_date", "order_date"),
        db.Index("ix_sales_orders_channel", "channel"),
    )


class SalesLine(db.Model):
    __tablename__ = "sales_lines"

    id = db.Column(db.Integer, primary_key=True)

    sales_order_id = db.Column(db.Integer, db.ForeignKey("sales_orders.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)

    sku = db.Column(db.String(80), index=True, nullable=False)  # snapshot / denormalized for reporting
    description = db.Column(db.String(255), nullable=True)

    qty = db.Column(db.Integer, nullable=False, default=0)

    # VAT-inclusive inputs (gross)
    unit_price_gross = db.Column(db.Numeric(12, 4), nullable=False, default=Decimal("0.0000"))
    line_discount_gross = db.Column(db.Numeric(12, 4), nullable=True)  # discount applied to this line (gross)
    order_discount_alloc_gross = db.Column(db.Numeric(12, 4), nullable=True)  # allocated portion of order-level discount

    # VAT rate used for net calculations (snapshot at time of sale)
    vat_rate = db.Column(db.Numeric(5, 2), nullable=False, default=Decimal("18.00"))

    # Derived revenue values (net of VAT) stored for stable reporting
    unit_price_net = db.Column(db.Numeric(12, 4), nullable=True)
    revenue_net = db.Column(db.Numeric(12, 4), nullable=True)            # total net revenue for this line after discounts

    # Cost/profit snapshot (so historical reports remain stable)
    cost_method = db.Column(db.String(20), nullable=False, default="weighted_avg")  # weighted_avg|last
    unit_cost_basis = db.Column(db.Numeric(12, 4), nullable=True)                  # landed unit cost used for this sale line
    cost_total = db.Column(db.Numeric(12, 4), nullable=True)                       # unit_cost_basis * qty
    profit = db.Column(db.Numeric(12, 4), nullable=True)                           # revenue_net - cost_total

    # Optional: traceability
    cost_source_po_id = db.Column(db.Integer, nullable=True)  # purchase_orders.id (not FK to keep it flexible)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    item = db.relationship("Item", lazy=True)

    __table_args__ = (
        db.Index("ix_sales_lines_sales_order_id", "sales_order_id"),
        db.Index("ix_sales_lines_item_id", "item_id"),
    )


# -------------------------
# Phase 4: Daily aggregates
# -------------------------

class DailyMetric(db.Model):
    """
    One row per calendar date across all channels.
    Used for fast dashboard/reports.
    """
    __tablename__ = "daily_metrics"

    id = db.Column(db.Integer, primary_key=True)
    metric_date = db.Column(db.Date, nullable=False, unique=True, index=True)

    orders_count = db.Column(db.Integer, nullable=False, default=0)
    units = db.Column(db.Integer, nullable=False, default=0)

    revenue_net = db.Column(db.Numeric(14, 4), nullable=False, default=Decimal("0.0000"))
    cogs = db.Column(db.Numeric(14, 4), nullable=False, default=Decimal("0.0000"))
    profit = db.Column(db.Numeric(14, 4), nullable=False, default=Decimal("0.0000"))

    discount_gross = db.Column(db.Numeric(14, 4), nullable=False, default=Decimal("0.0000"))
    discount_net = db.Column(db.Numeric(14, 4), nullable=False, default=Decimal("0.0000"))

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SkuMetricDaily(db.Model):
    """
    One row per (date, sku).
    Used for fast SKU widgets / reports.
    """
    __tablename__ = "sku_metrics_daily"

    id = db.Column(db.Integer, primary_key=True)

    metric_date = db.Column(db.Date, nullable=False, index=True)
    sku = db.Column(db.String(80), nullable=False, index=True)

    units = db.Column(db.Integer, nullable=False, default=0)
    revenue_net = db.Column(db.Numeric(14, 4), nullable=False, default=Decimal("0.0000"))
    profit = db.Column(db.Numeric(14, 4), nullable=False, default=Decimal("0.0000"))

    discount_gross = db.Column(db.Numeric(14, 4), nullable=False, default=Decimal("0.0000"))
    discount_net = db.Column(db.Numeric(14, 4), nullable=False, default=Decimal("0.0000"))

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("metric_date", "sku", name="uq_sku_metrics_daily_date_sku"),
        db.Index("ix_sku_metrics_daily_date", "metric_date"),
        db.Index("ix_sku_metrics_daily_sku", "sku"),
    )
