"""add product attributes

Revision ID: 0002
Revises: 0001
Create Date: 2025-02-06

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    # Add optional product attribute columns to items table
    op.add_column('items', sa.Column('colour', sa.String(length=120), nullable=True))
    op.add_column('items', sa.Column('size', sa.String(length=120), nullable=True))
    op.add_column('items', sa.Column('dimension', sa.String(length=120), nullable=True))
    op.add_column('items', sa.Column('weight', sa.String(length=120), nullable=True))
    op.add_column('items', sa.Column('comments', sa.Text(), nullable=True))


def downgrade():
    # Remove the columns if rolling back
    op.drop_column('items', 'comments')
    op.drop_column('items', 'weight')
    op.drop_column('items', 'dimension')
    op.drop_column('items', 'size')
    op.drop_column('items', 'colour')
