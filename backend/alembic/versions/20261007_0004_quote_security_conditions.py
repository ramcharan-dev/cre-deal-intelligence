"""quote security and conditions

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quotes", sa.Column("security", sa.Text(), nullable=True))
    op.add_column("quotes", sa.Column("conditions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("quotes", "conditions")
    op.drop_column("quotes", "security")
