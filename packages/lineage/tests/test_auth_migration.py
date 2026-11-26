"""T-024 R3: cabeça explícita e recusa do downgrade com conta, antes do código."""

from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from training_lineage import create_lineage_engine, migrate
import training_lineage.migration as migration_module


def test_auth_head_is_0006_and_occupied_downgrade_preserves_data():
    config = Config()
    config.set_main_option('script_location', str(Path(migration_module.__file__).with_name('migrations')))
    assert ScriptDirectory.from_config(config).get_current_head() == '0006'
    admin = create_lineage_engine()
    name = 'test_auth_migration_' + uuid4().hex
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    engine = create_engine(admin.url.set(database=name), hide_parameters=True)
    try:
        migrate(engine, 'head')
        user_id = uuid4()
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO app_user(id,email,hashed_password) VALUES (:id,'migration@example.com','$argon2id$test-only')"), {'id': user_id})
            connection.execute(text('INSERT INTO auth_access_token(token,user_id,csrf_token) VALUES (:token,:id,:csrf)'), {'id': user_id, 'token': 'a'*64, 'csrf': 'b'*64})
            connection.execute(text("INSERT INTO auth_attempt(kind,scope,key) VALUES ('register','origin',:key)"), {'key': 'c'*64})
        def snapshot():
            with engine.connect() as connection:
                return (connection.scalar(text('SELECT version_num FROM alembic_version')),
                        inspect(connection).get_table_names(),
                        [connection.execute(text(f'SELECT * FROM {table} ORDER BY 1')).all()
                         for table in ('app_user', 'auth_access_token', 'auth_attempt')])
        before = snapshot()
        assert before[0] == '0006'
        with pytest.raises(DBAPIError, match='contas existentes'):
            migrate(engine, '0005')
        assert snapshot() == before
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
