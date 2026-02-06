import uuid
from datetime import datetime, date
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Date, DateTime, Numeric, Text

from .extensions import db

def _uuid() -> str:
    return str(uuid.uuid4())

class User(db.Model, UserMixin):
    __tablename__ = "users"

    pk_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    first_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[str] = mapped_column(String(20), nullable=False, default="ADMIN")  # ADMIN/MANAGER/USER
    is_active: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return self.pk_id


class Item(db.Model):
    __tablename__ = "items"

    pk_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_item_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="IN_STOCK")  # IN_STOCK / SOLD

    order_number: Mapped[str] = mapped_column(String(80), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    arrival_date: Mapped[date] = mapped_column(Date, nullable=False)

    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(255), nullable=False)
    item_description: Mapped[str] = mapped_column(String(500), nullable=False)
    sku: Mapped[str] = mapped_column(String(120), nullable=False)

    # Optional product attributes
    colour: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size: Mapped[str | None] = mapped_column(String(120), nullable=True)
    dimension: Mapped[str | None] = mapped_column(String(120), nullable=True)
    weight: Mapped[str | None] = mapped_column(String(120), nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)

    net_unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    freight_net: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0.18"))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.pk_id"), nullable=True)

    sale: Mapped["Sale"] = relationship("Sale", back_populates="item", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_items_status", "status"),
        Index("ix_items_sku", "sku"),
        Index("ix_items_order_number", "order_number"),
        Index("ix_items_arrival_date", "arrival_date"),
        Index("ix_items_order_date", "order_date"),
    )


class Sale(db.Model):
    __tablename__ = "sales"

    pk_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    item_pk_id: Mapped[str] = mapped_column(String, ForeignKey("items.pk_id"), nullable=False, unique=True)

    sale_date: Mapped[date] = mapped_column(Date, nullable=False)

    item_selling_price_gross: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    discount_type: Mapped[str | None] = mapped_column(String(10), nullable=True)  # PERCENT/AMOUNT
    discount_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    discount_amount_gross: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    delivery_fee_charged_gross: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))

    packaging_net: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    delivery_cost_net: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    other_cost_net: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))

    item_net_revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    item_vat_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    item_profit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.pk_id"), nullable=True)

    item: Mapped[Item] = relationship("Item", back_populates="sale")

    __table_args__ = (
        Index("ix_sales_sale_date", "sale_date"),
    )


class ImportBatch(db.Model):
    __tablename__ = "import_batches"

    pk_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    uploaded_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.pk_id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    total_rows: Mapped[int] = mapped_column(db.Integer, nullable=False, default=0)
    success_rows: Mapped[int] = mapped_column(db.Integer, nullable=False, default=0)
    failed_rows: Mapped[int] = mapped_column(db.Integer, nullable=False, default=0)

    # Store errors JSON as text for MVP; later can move to JSONB or file
    error_report: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    pk_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)

    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # ITEM/SALE/IMPORT_BATCH
    entity_pk_id: Mapped[str] = mapped_column(String(80), nullable=False)

    action: Mapped[str] = mapped_column(String(30), nullable=False)  # CREATE/UPDATE/REVERSE_SALE/IMPORT
    field_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.pk_id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_pk_id"),
        Index("ix_audit_created_at", "created_at"),
    )
