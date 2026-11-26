"""ADR-0017: contas, sessões opacas e tentativas; revisão aditiva ajustável antes da primeira aplicação persistente."""

from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE app_user (
        id UUID PRIMARY KEY, email VARCHAR(320) NOT NULL,
        hashed_password VARCHAR(1024) NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT true,
        is_superuser BOOLEAN NOT NULL DEFAULT false,
        is_verified BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT app_user_argon2id CHECK (hashed_password LIKE '$argon2id$%')
    )""")
    op.execute('CREATE UNIQUE INDEX app_user_email_lower_unique ON app_user (lower(email))')
    op.execute("""CREATE TABLE auth_access_token (
        token VARCHAR(64) PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
        csrf_token VARCHAR(64) NOT NULL UNIQUE,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    op.execute('CREATE INDEX auth_access_token_created_at ON auth_access_token (created_at)')
    op.execute('CREATE INDEX auth_access_token_last_seen_at ON auth_access_token (last_seen_at)')
    op.execute("""CREATE TABLE auth_attempt (
        id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL,
        key TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT auth_attempt_kind CHECK (kind IN ('login_failure','register')),
        CONSTRAINT auth_attempt_scope CHECK (scope IN ('account','origin')),
        CONSTRAINT auth_attempt_key_sha256 CHECK (key ~ '^[0-9a-f]{64}$')
    )""")
    op.execute('CREATE INDEX auth_attempt_window ON auth_attempt (kind, scope, key, created_at)')


def downgrade():
    # Impede cadastro concorrente entre a verificação e o DROP.
    op.execute('LOCK TABLE app_user IN ACCESS EXCLUSIVE MODE')
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM app_user) THEN
            RAISE EXCEPTION 'contas existentes impedem downgrade' USING ERRCODE='23514';
        END IF;
    END $$""")
    op.drop_table('auth_attempt')
    op.drop_table('auth_access_token')
    op.drop_table('app_user')
