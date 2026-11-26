"""ING-LIN-001: versões de documento e chunks imutáveis."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_version",
        sa.Column("document_version_id", sa.Text(), primary_key=True),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(document_version_id) > 0 AND length(document_id) > 0 AND length(owner_id) > 0 AND length(parser_version) > 0", name="document_version_identifiers"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="document_version_sha256"),
        sa.UniqueConstraint("owner_id", "sha256", name="document_version_owner_sha256"),
    )
    op.create_table(
        "chunk",
        sa.Column("chunk_id", sa.Text(), primary_key=True),
        sa.Column("document_version_id", sa.Text(), sa.ForeignKey("document_version.document_version_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("chunker_version", sa.Text(), nullable=False),
        sa.Column("page", sa.BigInteger()),
        sa.Column("section_path", ARRAY(sa.Text()), nullable=False),
        sa.Column("start_offset", sa.BigInteger(), nullable=False),
        sa.Column("end_offset", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("injection_flagged", sa.Boolean(), nullable=False),
        sa.Column("injection_reason", sa.Text()),
        sa.CheckConstraint("length(chunk_id) > 0 AND length(chunker_version) > 0", name="chunk_identifiers"),
        sa.CheckConstraint("page IS NULL OR page >= 1", name="chunk_page"),
        sa.CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="chunk_offsets"),
        sa.CheckConstraint("text ~ '[^[:space:]]'", name="chunk_text"),
        sa.CheckConstraint("injection_flagged = (injection_reason IS NOT NULL) AND (injection_reason IS NULL OR injection_reason ~ '[^[:space:]]')", name="chunk_injection"),
        sa.CheckConstraint("array_position(section_path, NULL) IS NULL", name="chunk_section_path"),
    )
    op.create_index("chunk_version_reading_order", "chunk", ["document_version_id", "page", "start_offset", "end_offset", "chunk_id"])
    op.execute("""
        CREATE FUNCTION reject_chunk_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'chunk is immutable' USING ERRCODE = '23514';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER chunk_immutable_rows BEFORE UPDATE OR DELETE ON chunk
        FOR EACH ROW EXECUTE FUNCTION reject_chunk_mutation()
    """)
    op.execute("""
        CREATE TRIGGER chunk_immutable_truncate BEFORE TRUNCATE ON chunk
        FOR EACH STATEMENT EXECUTE FUNCTION reject_chunk_mutation()
    """)


def downgrade():
    op.drop_table("chunk")
    op.execute("DROP FUNCTION reject_chunk_mutation()")
    op.drop_table("document_version")
