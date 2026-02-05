from __future__ import with_statement

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import create_app
from app.extensions import db

config = context.config

# IMPORTANT:
# Don't call fileConfig() here. In some hosted environments the ini path
# resolves incorrectly (e.g. migrations/alembic.ini) and crashes.
# Logging config is not required for migrations to run.

target_metadata = db.metadata


def get_url():
    app = create_app()
    return app.config["SQLALCHEMY_DATABASE_URI"]


def run_migrations_offline():
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
