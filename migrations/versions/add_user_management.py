"""Add user management fields

Revision ID: add_user_management
Revises: 0001_init
Create Date: 2026-02-08

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_user_management'
down_revision = '0001_init'
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to users table
    op.add_column('users', sa.Column('full_name', sa.String(length=200), nullable=True))
    op.add_column('users', sa.Column('last_login_at', sa.DateTime(), nullable=True))
    
    # Update role default from 'ADMIN' to 'user' for consistency
    op.alter_column('users', 'role', server_default='user')


def downgrade():
    # Revert role default
    op.alter_column('users', 'role', server_default='ADMIN')
    
    # Remove columns
    op.drop_column('users', 'last_login_at')
    op.drop_column('users', 'full_name')
