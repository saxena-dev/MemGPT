"""Make an blocks agents mapping table

Revision ID: 1c8880d671ee
Revises: f81ceea2c08d
Create Date: 2024-11-22 15:42:47.209229

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c8880d671ee"
down_revision: Union[str, None] = "f81ceea2c08d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_unique_constraint("unique_block_id_label", "block", ["id", "label"])

    op.create_table(
        "blocks_agents",
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("block_id", sa.String(), nullable=False),
        sa.Column("block_label", sa.String(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("_created_by_id", sa.String(), nullable=True),
        sa.Column("_last_updated_by_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
        ),
        sa.ForeignKeyConstraint(["block_id", "block_label"], ["block.id", "block.label"], name="fk_block_id_label"),
        sa.PrimaryKeyConstraint("agent_id", "block_id", "block_label", "id"),
        sa.UniqueConstraint("agent_id", "block_label", name="unique_label_per_agent"),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint("unique_block_id_label", "block", type_="unique")
    op.drop_table("blocks_agents")
    # ### end Alembic commands ###
