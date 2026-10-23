"""Owner token on generation dispatch leases.

Revision ID: 0004_lease_owner_token
Revises: 0003_awaiting_input
Create Date: 2026-10-23

Schema changes
--------------
* New nullable column ``generation_dispatch_leases.owner_token`` (VARCHAR(64)). Every
  lease written by a worker step (dispatch/poll) or an operator reply carries a random
  token, and the holder deletes the lease only while the token still matches, so a
  holder that outlived its lease cannot delete a newer holder's lease.

Existing rows keep ``owner_token = NULL``: no holder can release them by token, they
are taken over by the next step/reply once they expire, exactly as before. Leases are
short-lived (bounded by the connection timeout), so no backfill is needed.

Downgrade
---------
Drops the column. Leases then go back to being released by task id only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_lease_owner_token"
down_revision = "0003_awaiting_input"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generation_dispatch_leases") as batch_op:
        batch_op.add_column(sa.Column("owner_token", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("generation_dispatch_leases") as batch_op:
        batch_op.drop_column("owner_token")
