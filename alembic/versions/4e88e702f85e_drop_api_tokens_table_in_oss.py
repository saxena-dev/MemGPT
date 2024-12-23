"""Drop api tokens table in OSS

Revision ID: 4e88e702f85e
Revises: d05669b60ebe
Create Date: 2024-12-13 17:19:55.796210

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4e88e702f85e"
down_revision: Union[str, None] = "d05669b60ebe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("tokens_idx_key", table_name="tokens")
    op.drop_index("tokens_idx_user", table_name="tokens")
    op.drop_table("tokens")
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "tokens",
        sa.Column("id", sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column("key", sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column("name", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.PrimaryKeyConstraint("id", name="tokens_pkey"),
    )
    op.create_index("tokens_idx_user", "tokens", ["user_id"], unique=False)
    op.create_index("tokens_idx_key", "tokens", ["key"], unique=False)
    # ### end Alembic commands ###
