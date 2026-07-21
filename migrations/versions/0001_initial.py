"""Create the application and point-in-time research schema.

Revision ID: 0001_initial
"""

from alembic import op

from models import Base


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # checkfirst=True makes this migration safe for databases created by the
    # pre-Alembic application while still fully initializing a blank database.
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
