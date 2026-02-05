"""init

Revision ID: 0001_init
Revises:
Create Date: 2026-02-05
"""
from alembic import op
import sqlalchemy as sa


revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("pk_id", sa.String(), primary_key=True),
        sa.Column("first_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="ADMIN"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "items",
        sa.Column("pk_id", sa.String(), primary_key=True),
        sa.Column("user_item_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="IN_STOCK"),
        sa.Column("order_number", sa.String(length=80), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("arrival_date", sa.Date(), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("brand", sa.String(length=255), nullable=False),
        sa.Column("item_description", sa.String(length=500), nullable=False),
        sa.Column("sku", sa.String(length=120), nullable=False),
        sa.Column("net_unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("freight_net", sa.Numeric(12, 2), nullable=False),
        sa.Column("vat_rate", sa.Numeric(6, 4), nullable=False, server_default="0.18"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.pk_id"]),
    )
    op.create_index("ix_items_status", "items", ["status"])
    op.create_index("ix_items_sku", "items", ["sku"])
    op.create_index("ix_items_order_number", "items", ["order_number"])
    op.create_index("ix_items_arrival_date", "items", ["arrival_date"])
    op.create_index("ix_items_order_date", "items", ["order_date"])
    op.create_index("uq_items_user_item_id", "items", ["user_item_id"], unique=True)

    op.create_table(
        "sales",
        sa.Column("pk_id", sa.String(), primary_key=True),
        sa.Column("item_pk_id", sa.String(), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("item_selling_price_gross", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_type", sa.String(length=10), nullable=True),
        sa.Column("discount_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("discount_amount_gross", sa.Numeric(12, 2), nullable=True),
        sa.Column("delivery_fee_charged_gross", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("packaging_net", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("delivery_cost_net", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("other_cost_net", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("item_net_revenue", sa.Numeric(12, 2), nullable=False),
        sa.Column("item_vat_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("item_profit", sa.Numeric(12, 2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.pk_id"]),
        sa.ForeignKeyConstraint(["item_pk_id"], ["items.pk_id"]),
        sa.UniqueConstraint("item_pk_id", name="uq_sales_item_pk_id"),
    )
    op.create_index("ix_sales_sale_date", "sales", ["sale_date"])

    op.create_table(
        "import_batches",
        sa.Column("pk_id", sa.String(), primary_key=True),
        sa.Column("filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("uploaded_by", sa.String(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_report", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.pk_id"]),
    )

    op.create_table(
        "audit_log",
        sa.Column("pk_id", sa.String(), primary_key=True),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_pk_id", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("field_name", sa.String(length=80), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.pk_id"]),
    )
    op.create_index("ix_audit_entity", "audit_log", ["entity_type", "entity_pk_id"])
    op.create_index("ix_audit_created_at", "audit_log", ["created_at"])


def downgrade():
    op.drop_index("ix_audit_created_at", table_name="audit_log")
    op.drop_index("ix_audit_entity", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_table("import_batches")

    op.drop_index("ix_sales_sale_date", table_name="sales")
    op.drop_table("sales")

    op.drop_index("uq_items_user_item_id", table_name="items")
    op.drop_index("ix_items_order_date", table_name="items")
    op.drop_index("ix_items_arrival_date", table_name="items")
    op.drop_index("ix_items_order_number", table_name="items")
    op.drop_index("ix_items_sku", table_name="items")
    op.drop_index("ix_items_status", table_name="items")
    op.drop_table("items")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
