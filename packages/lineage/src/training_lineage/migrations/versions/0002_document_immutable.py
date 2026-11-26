"""Imutabilidade também para versões já existentes."""
from alembic import op
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE FUNCTION reject_document_version_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'document_version is immutable' USING ERRCODE = '23514';
        END; $$""")
    op.execute('''CREATE TRIGGER document_version_immutable_rows
        BEFORE UPDATE OR DELETE ON document_version FOR EACH ROW
        EXECUTE FUNCTION reject_document_version_mutation()''')
    op.execute('''CREATE TRIGGER document_version_immutable_truncate
        BEFORE TRUNCATE ON document_version FOR EACH STATEMENT
        EXECUTE FUNCTION reject_document_version_mutation()''')


def downgrade():
    op.execute('DROP TRIGGER document_version_immutable_rows ON document_version')
    op.execute('DROP TRIGGER document_version_immutable_truncate ON document_version')
    op.execute('DROP FUNCTION reject_document_version_mutation()')
