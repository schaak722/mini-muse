from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import text

from ..decorators import require_admin
from ..extensions import db
from ..models import User
from .forms import UserCreateForm, UserEditForm

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# -----------------------
# Users
# -----------------------

@admin_bp.get("/users")
@login_required
@require_admin
def users_list():
    q = (request.args.get("q") or "").strip().lower()

    query = User.query
    if q:
        query = query.filter(
            db.or_(
                db.func.lower(User.email).contains(q),
                db.func.lower(User.name).contains(q),
                db.func.lower(User.role).contains(q),
            )
        )

    users = query.order_by(User.created_at.desc()).all()
    return render_template("admin/users_list.html", users=users, q=q)


@admin_bp.get("/users/new")
@admin_bp.post("/users/new")
@login_required
@require_admin
def users_new():
    form = UserCreateForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if User.query.filter_by(email=email).first():
            flash("A user with that email already exists.", "danger")
            return render_template("admin/user_form.html", form=form, mode="create")

        user = User(
            email=email,
            name=form.name.data.strip(),
            role=form.role.data,
            is_active=bool(form.is_active.data),
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        flash("User created.", "success")
        return redirect(url_for("admin.users_list"))

    return render_template("admin/user_form.html", form=form, mode="create")


@admin_bp.get("/users/<int:user_id>/edit")
@admin_bp.post("/users/<int:user_id>/edit")
@login_required
@require_admin
def users_edit(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin.users_list"))

    form = UserEditForm(obj=user)

    if form.validate_on_submit():
        user.name = form.name.data.strip()
        user.role = form.role.data
        user.is_active = bool(form.is_active.data)

        if form.new_password.data:
            user.set_password(form.new_password.data)

        # prevent admin from locking themselves out accidentally
        if user.id == current_user.id and user.is_active is False:
            flash("You cannot deactivate your own account.", "danger")
            db.session.rollback()
            return render_template("admin/user_form.html", form=form, mode="edit", user=user)

        db.session.commit()
        flash("User updated.", "success")
        return redirect(url_for("admin.users_list"))

    return render_template("admin/user_form.html", form=form, mode="edit", user=user)


# -----------------------
# DB Patch (poor-man's migrations)
# -----------------------

def _db_patch_statements():
    """
    Idempotent schema patch for common drift points.
    Safe to run repeatedly.
    """
    stmts = []

    # ---- saved_searches
    stmts.append("""
    CREATE TABLE IF NOT EXISTS saved_searches (
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL,
      context VARCHAR(40) NOT NULL,
      name VARCHAR(120) NOT NULL,
      params JSONB NOT NULL DEFAULT '{}'::jsonb,
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    stmts.append("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_saved_searches_user_id'
      ) THEN
        ALTER TABLE saved_searches
          ADD CONSTRAINT fk_saved_searches_user_id
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
      END IF;
    END$$;
    """)

    stmts.append("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_saved_search_user_context_name'
      ) THEN
        ALTER TABLE saved_searches
          ADD CONSTRAINT uq_saved_search_user_context_name UNIQUE (user_id, context, name);
      END IF;
    END$$;
    """)

    stmts.append("CREATE INDEX IF NOT EXISTS ix_saved_searches_user_id ON saved_searches(user_id);")
    stmts.append("CREATE INDEX IF NOT EXISTS ix_saved_searches_context ON saved_searches(context);")

    # ---- import_batches (preview/resolve)
    stmts.append("""
    CREATE TABLE IF NOT EXISTS import_batches (
      id SERIAL PRIMARY KEY,
      kind VARCHAR(40),
      filename VARCHAR(255),
      payload JSONB NOT NULL DEFAULT '{}'::jsonb,
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    stmts.append("ALTER TABLE import_batches ADD COLUMN IF NOT EXISTS kind VARCHAR(40);")
    stmts.append("ALTER TABLE import_batches ADD COLUMN IF NOT EXISTS filename VARCHAR(255);")
    stmts.append("ALTER TABLE import_batches ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb;")
    stmts.append("ALTER TABLE import_batches ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();")
    stmts.append("CREATE INDEX IF NOT EXISTS ix_import_batches_kind ON import_batches(kind);")

    # ---- purchase_orders / purchase_lines
    stmts.append("""
    CREATE TABLE IF NOT EXISTS purchase_orders (
      id SERIAL PRIMARY KEY,
      supplier_name VARCHAR(120),
      brand VARCHAR(80),
      order_number VARCHAR(80),
      order_date DATE,
      arrival_date DATE,
      currency VARCHAR(10),
      freight_total NUMERIC(12,2),
      allocation_method VARCHAR(20),
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    stmts.append("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS supplier_name VARCHAR(120);")
    stmts.append("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS brand VARCHAR(80);")
    stmts.append("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS order_number VARCHAR(80);")
    stmts.append("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS order_date DATE;")
    stmts.append("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS arrival_date DATE;")
    stmts.append("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS currency VARCHAR(10);")
    stmts.append("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS freight_total NUMERIC(12,2);")
    stmts.append("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS allocation_method VARCHAR(20);")
    stmts.append("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();")
    stmts.append("CREATE INDEX IF NOT EXISTS ix_purchase_orders_order_number ON purchase_orders(order_number);")

    stmts.append("""
    CREATE TABLE IF NOT EXISTS purchase_lines (
      id SERIAL PRIMARY KEY,
      purchase_order_id INTEGER NOT NULL,
      item_id INTEGER,
      sku VARCHAR(80),
      description VARCHAR(255),
      colour VARCHAR(80),
      size VARCHAR(40),
      qty INTEGER,
      unit_cost_net NUMERIC(12,4),
      packaging_per_unit NUMERIC(12,4),
      freight_allocated_total NUMERIC(12,4),
      freight_allocated_per_unit NUMERIC(12,4),
      landed_unit_cost NUMERIC(12,4),
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS purchase_order_id INTEGER;")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS item_id INTEGER;")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS sku VARCHAR(80);")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS description VARCHAR(255);")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS colour VARCHAR(80);")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS size VARCHAR(40);")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS qty INTEGER;")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS unit_cost_net NUMERIC(12,4);")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS packaging_per_unit NUMERIC(12,4);")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS freight_allocated_total NUMERIC(12,4);")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS freight_allocated_per_unit NUMERIC(12,4);")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS landed_unit_cost NUMERIC(12,4);")
    stmts.append("ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();")

    stmts.append("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_purchase_lines_po'
      ) THEN
        ALTER TABLE purchase_lines
          ADD CONSTRAINT fk_purchase_lines_po
          FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id) ON DELETE CASCADE;
      END IF;
    END$$;
    """)

    stmts.append("CREATE INDEX IF NOT EXISTS ix_purchase_lines_sku ON purchase_lines(sku);")
    stmts.append("CREATE INDEX IF NOT EXISTS ix_purchase_lines_purchase_order_id ON purchase_lines(purchase_order_id);")

    # ---- sales_orders / sales_lines
    stmts.append("""
    CREATE TABLE IF NOT EXISTS sales_orders (
      id SERIAL PRIMARY KEY,
      order_number VARCHAR(80) NOT NULL,
      order_date DATE,
      channel VARCHAR(40) NOT NULL DEFAULT 'unknown',
      currency VARCHAR(10) DEFAULT 'EUR',
      customer_name VARCHAR(120),
      customer_email VARCHAR(255),
      shipping_charged_gross NUMERIC(12,2),
      order_discount_gross NUMERIC(12,2),
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    stmts.append("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS order_number VARCHAR(80);")
    stmts.append("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS order_date DATE;")
    stmts.append("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS channel VARCHAR(40);")
    stmts.append("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS currency VARCHAR(10);")
    stmts.append("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS customer_name VARCHAR(120);")
    stmts.append("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS customer_email VARCHAR(255);")
    stmts.append("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS shipping_charged_gross NUMERIC(12,2);")
    stmts.append("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS order_discount_gross NUMERIC(12,2);")
    stmts.append("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();")

    stmts.append("CREATE INDEX IF NOT EXISTS ix_sales_orders_order_date ON sales_orders(order_date);")
    stmts.append("CREATE INDEX IF NOT EXISTS ix_sales_orders_channel ON sales_orders(channel);")

    stmts.append("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_sales_orders_channel_order_number'
      ) THEN
        ALTER TABLE sales_orders
          ADD CONSTRAINT uq_sales_orders_channel_order_number UNIQUE (channel, order_number);
      END IF;
    END$$;
    """)

    stmts.append("""
    CREATE TABLE IF NOT EXISTS sales_lines (
      id SERIAL PRIMARY KEY,
      sales_order_id INTEGER NOT NULL,
      item_id INTEGER,
      sku VARCHAR(80),
      description VARCHAR(255),
      qty INTEGER,
      unit_price_gross NUMERIC(12,4),
      line_discount_gross NUMERIC(12,4),
      order_discount_alloc_gross NUMERIC(12,4),
      vat_rate NUMERIC(5,2),
      unit_price_net NUMERIC(12,4),
      revenue_net NUMERIC(12,4),
      cost_method VARCHAR(20),
      unit_cost_basis NUMERIC(12,4),
      cost_total NUMERIC(12,4),
      profit NUMERIC(12,4),
      cost_source_po_id INTEGER,
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS sales_order_id INTEGER;")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS item_id INTEGER;")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS sku VARCHAR(80);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS description VARCHAR(255);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS qty INTEGER;")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS unit_price_gross NUMERIC(12,4);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS line_discount_gross NUMERIC(12,4);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS order_discount_alloc_gross NUMERIC(12,4);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS vat_rate NUMERIC(5,2);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS unit_price_net NUMERIC(12,4);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS revenue_net NUMERIC(12,4);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS cost_method VARCHAR(20);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS unit_cost_basis NUMERIC(12,4);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS cost_total NUMERIC(12,4);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS profit NUMERIC(12,4);")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS cost_source_po_id INTEGER;")
    stmts.append("ALTER TABLE sales_lines ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();")

    stmts.append("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_sales_lines_so'
      ) THEN
        ALTER TABLE sales_lines
          ADD CONSTRAINT fk_sales_lines_so
          FOREIGN KEY (sales_order_id) REFERENCES sales_orders(id) ON DELETE CASCADE;
      END IF;
    END$$;
    """)

    stmts.append("CREATE INDEX IF NOT EXISTS ix_sales_lines_sku ON sales_lines(sku);")
    stmts.append("CREATE INDEX IF NOT EXISTS ix_sales_lines_sales_order_id ON sales_lines(sales_order_id);")
    stmts.append("CREATE INDEX IF NOT EXISTS ix_sales_lines_item_id ON sales_lines(item_id);")

    # ---- dashboard metrics tables (Phase 4)
    stmts.append("""
    CREATE TABLE IF NOT EXISTS daily_metrics (
      id SERIAL PRIMARY KEY,
      metric_date DATE NOT NULL UNIQUE,
      revenue_net NUMERIC(14,2) NOT NULL DEFAULT 0,
      cogs NUMERIC(14,2) NOT NULL DEFAULT 0,
      profit NUMERIC(14,2) NOT NULL DEFAULT 0,
      discount_net NUMERIC(14,2) NOT NULL DEFAULT 0,
      orders_count INTEGER NOT NULL DEFAULT 0,
      recomputed_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    stmts.append("""
    CREATE TABLE IF NOT EXISTS sku_metrics_daily (
      id SERIAL PRIMARY KEY,
      metric_date DATE NOT NULL,
      sku VARCHAR(80) NOT NULL,
      units INTEGER NOT NULL DEFAULT 0,
      revenue_net NUMERIC(14,2) NOT NULL DEFAULT 0,
      profit NUMERIC(14,2) NOT NULL DEFAULT 0,
      disc_net NUMERIC(14,2) NOT NULL DEFAULT 0,
      recomputed_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    stmts.append("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_sku_metrics_daily_date_sku'
      ) THEN
        ALTER TABLE sku_metrics_daily
          ADD CONSTRAINT uq_sku_metrics_daily_date_sku UNIQUE (metric_date, sku);
      END IF;
    END$$;
    """)

    stmts.append("CREATE INDEX IF NOT EXISTS ix_sku_metrics_daily_sku ON sku_metrics_daily(sku);")
    stmts.append("CREATE INDEX IF NOT EXISTS ix_sku_metrics_daily_date ON sku_metrics_daily(metric_date);")

    return [s.strip() for s in stmts if s.strip()]


@admin_bp.get("/db-patch")
@login_required
@require_admin
def db_patch_home():
    return render_template("admin/db_patch.html", results=None, error=None)


@admin_bp.post("/db-patch")
@login_required
@require_admin
def db_patch_run():
    results = []
    error = None
    stmts = _db_patch_statements()

    try:
        for sql in stmts:
            try:
                db.session.execute(text(sql))
                results.append({"ok": True, "sql": sql})
            except Exception as e:
                results.append({"ok": False, "sql": sql})
                raise

        db.session.commit()
        flash("DB patch completed successfully.", "success")

    except Exception as e:
        db.session.rollback()
        error = str(e)
        flash("DB patch failed. See details below.", "danger")

    # show newest first
    results = list(reversed(results))
    return render_template("admin/db_patch.html", results=results, error=error)
