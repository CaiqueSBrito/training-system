"""T-024 (G3): casos de AUT-CAD-001, AUT-SES-001, AUT-SES-002, AUT-CSR-001,
AUT-LIM-001 e AUT-OWN-001, escritos antes do produto.

Tudo pela interface HTTP pública (cliente ASGI) sobre Postgres descartável real;
as consultas SQL diretas só leem/envelhecem o estado gravado para provar o que a
resposta HTTP não mostra (hash, linha de sessão, validade).

Ficam para a T-025, com a rota real `POST /chat`: o cenário de AUT-CSR-001
"POST /chat sem CSRF → 403 e nenhum turno criado" e os dois cenários de AUT-OWN-001
sobre o chat (`owner_id` no corpo ignorado/recusado; 401 sem cookie no chat e na
linhagem). Aqui eles são cobertos pela mesma dependência de sessão nas rotas reais
já planejadas para a T-024 (`GET /users/me`, `POST /auth/logout`,
`POST /auth/change-password`).
"""

import re
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from api_auth_support import (
    CSRF_HEADER, LOGIN_FAILURES_PER_ACCOUNT, LOGIN_FAILURES_PER_ORIGIN, MIN_PASSWORD, ORIGIN_A,
    ORIGIN_B, PASSWORD, REGISTRATIONS_PER_ORIGIN, SESSION_COOKIE, SESSION_LIFETIME,
)

NEW_PASSWORD = "outra-senha-forte-456"


def users(database, email=None):
    query = "SELECT id, email, hashed_password FROM app_user"
    with database.connect() as connection:
        if email is None:
            return connection.execute(text(query)).mappings().all()
        return connection.execute(text(query + " WHERE lower(email) = lower(:e)"), {"e": email}).mappings().all()


def session_count(database, user_id=None):
    with database.connect() as connection:
        if user_id is None:
            return connection.execute(text("SELECT count(*) FROM auth_access_token")).scalar_one()
        return connection.execute(text("SELECT count(*) FROM auth_access_token WHERE user_id = :u"),
                                  {"u": UUID(str(user_id))}).scalar_one()


def cookie_attributes(set_cookie):
    first = [part.strip() for part in set_cookie.split(";")]
    name, _, value = first[0].partition("=")
    return name, value, {part.split("=")[0].lower(): part.partition("=")[2] for part in first[1:]}


# ---------------------------------------------------------------- AUT-CAD-001

def test_AUT_CAD_001_cadastro_cria_conta_com_hash_argon2id(api, database):
    response = api.register("nova@example.com")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "nova@example.com"
    UUID(body["id"])
    assert "password" not in body and "hashed_password" not in body
    assert PASSWORD not in response.text
    [row] = users(database, "nova@example.com")
    assert str(row["id"]) == body["id"]
    assert row["hashed_password"].startswith("$argon2id$")
    assert PASSWORD not in row["hashed_password"]
    with database.connect() as connection:  # a senha não aparece em nenhuma coluna de nenhuma tabela de conta
        dump = connection.execute(text("SELECT row_to_json(u)::text FROM app_user u")).scalars().all()
    assert all(PASSWORD not in line for line in dump)


@pytest.mark.parametrize("repeated", ["alice@example.com", "Alice@Example.COM"])
def test_AUT_CAD_001_email_repetido_recusado_sem_dados_da_conta(api, database, alice, repeated):
    response = api.register(repeated, password="senha-diferente-789")
    assert response.status_code == 400
    assert alice["id"] not in response.text
    assert "hashed_password" not in response.text and "argon2" not in response.text
    assert set(response.json()) == {"detail"}
    assert response.json()["detail"] == "REGISTER_USER_ALREADY_EXISTS"
    assert len(users(database)) == 1
    assert api.login_response("alice@example.com", "senha-diferente-789").status_code == 400
    assert api.login_response("alice@example.com", PASSWORD).status_code == 200


def test_AUT_CAD_001_senha_abaixo_do_minimo_recusada_sem_criar_conta(api, database):
    short = "x" * (MIN_PASSWORD - 1)
    response = api.register("curta@example.com", password=short)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "REGISTER_INVALID_PASSWORD"
    assert short not in response.text
    assert users(database) == []
    assert api.register("curta@example.com", password="x" * MIN_PASSWORD).status_code == 201


def test_AUT_CAD_001_cadastro_nao_aceita_privilegio_do_corpo(api, database):
    response = api.register("intrusa@example.com", is_superuser=True, is_verified=True, is_active=False)
    assert response.status_code == 201, response.text
    with database.connect() as connection:
        row = connection.execute(text("SELECT is_superuser, is_active FROM app_user")).one()
    assert tuple(row) == (False, True)


# ---------------------------------------------------------------- AUT-SES-001

def test_AUT_SES_001_login_cria_sessao_no_banco_e_cookie_seguro(api, database, alice):
    assert session_count(database, alice["id"]) == 0
    response = api.login_response("alice@example.com")
    assert response.status_code == 200, response.text
    assert session_count(database, alice["id"]) == 1
    name, value, attributes = cookie_attributes(response.headers["set-cookie"])
    assert name == SESSION_COOKIE and value
    assert "httponly" in attributes
    assert "secure" in attributes
    assert attributes.get("samesite", "").lower() == "lax"
    assert attributes.get("path") == "/"
    assert value.count(".") != 2, "cookie deve ser opaco, não JWT"
    assert alice["id"] not in value and "alice" not in value
    csrf = response.json()["csrf_token"]
    assert isinstance(csrf, str) and len(csrf) >= 32 and csrf != value
    assert value not in response.text  # o token de sessão só vai no cookie HttpOnly
    me = api.me(cookie=value)
    assert me.status_code == 200 and me.json()["id"] == alice["id"]


def test_AUT_SES_001_falha_identica_para_email_inexistente_e_senha_errada(api, database, alice):
    missing = api.login_response("ninguem@example.com", PASSWORD)
    wrong = api.login_response("alice@example.com", "senha-errada-000")
    assert missing.status_code == wrong.status_code == 400
    assert missing.content == wrong.content
    assert missing.json() == {"detail": "LOGIN_BAD_CREDENTIALS"}
    assert missing.headers.get("content-type") == wrong.headers.get("content-type")
    assert "set-cookie" not in missing.headers and "set-cookie" not in wrong.headers
    assert session_count(database) == 0


def test_AUT_SES_001_login_sempre_emite_token_novo_sem_fixacao(api, database, alice):
    planted = "token-plantado-pelo-atacante"
    first = api.login("alice@example.com", cookie=planted)
    assert first.token != planted
    assert api.me(cookie=planted).status_code == 401
    second = api.login("alice@example.com", cookie=first.token)
    assert second.token != first.token
    assert second.csrf != first.csrf
    assert session_count(database, alice["id"]) == 2
    assert api.me(cookie=second.token).status_code == 200


# ---------------------------------------------------------------- AUT-SES-002

def test_AUT_SES_002_logout_revoga_o_mesmo_cookie(api, database, alice):
    session = api.login("alice@example.com")
    assert api.me(session).status_code == 200
    response = api.request("POST", "/auth/logout", session=session, csrf=session.csrf)
    assert response.status_code == 204, response.text
    name, value, attributes = cookie_attributes(response.headers["set-cookie"])
    assert name == SESSION_COOKIE and (value in ("", '""') or attributes.get("max-age") == "0")
    assert session_count(database, alice["id"]) == 0
    assert api.me(session).status_code == 401
    assert api.request("POST", "/auth/logout", session=session, csrf=session.csrf).status_code == 401


def test_AUT_SES_002_logout_revoga_so_a_propria_sessao(api, alice):
    one, two = api.login("alice@example.com"), api.login("alice@example.com")
    assert api.request("POST", "/auth/logout", session=one, csrf=one.csrf).status_code == 204
    assert api.me(one).status_code == 401
    assert api.me(two).status_code == 200


def test_AUT_SES_002_troca_de_senha_revoga_todas_as_sessoes(api, database, alice):
    one, two = api.login("alice@example.com"), api.login("alice@example.com")
    response = api.request("POST", "/auth/change-password", session=one, csrf=one.csrf,
                           json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert response.status_code == 204, response.text
    assert api.me(one).status_code == 401
    assert api.me(two).status_code == 401
    assert session_count(database, alice["id"]) == 0
    assert api.login_response("alice@example.com", PASSWORD).status_code == 400
    assert api.me(api.login("alice@example.com", NEW_PASSWORD)).status_code == 200
    [row] = users(database, "alice@example.com")
    assert row["hashed_password"].startswith("$argon2id$")


def test_AUT_SES_002_troca_de_senha_exige_senha_atual_e_politica(api, database, alice):
    session = api.login("alice@example.com")
    wrong = api.request("POST", "/auth/change-password", session=session, csrf=session.csrf,
                        json={"current_password": "senha-errada-000", "new_password": NEW_PASSWORD})
    assert wrong.status_code == 400
    short = api.request("POST", "/auth/change-password", session=session, csrf=session.csrf,
                        json={"current_password": PASSWORD, "new_password": "x" * (MIN_PASSWORD - 1)})
    assert short.status_code == 400
    assert api.me(session).status_code == 200
    assert api.login_response("alice@example.com", PASSWORD).status_code == 200


def test_AUT_SES_002_sessao_vencida_recebe_401(api, database, alice):
    session = api.login("alice@example.com")
    assert api.me(session).status_code == 200
    with database.begin() as connection:
        connection.execute(text("UPDATE auth_access_token SET created_at = now() - make_interval(secs => :s) "
                                "WHERE user_id = :u"),
                           {"s": SESSION_LIFETIME + 60, "u": UUID(alice["id"])})
    assert api.me(session).status_code == 401
    assert api.request("POST", "/auth/logout", session=session, csrf=session.csrf).status_code == 401


@pytest.mark.parametrize("cookie", ["", "inexistente", "a" * 500, "x.y.z"])
def test_AUT_SES_002_cookie_desconhecido_recebe_401(api, alice, cookie):
    assert api.me(cookie=cookie).status_code == 401


# ---------------------------------------------------------------- AUT-CSR-001

def _logout(api, session, **csrf):
    return api.request("POST", "/auth/logout", session=session, **csrf)


def _change(api, session, **csrf):
    return api.request("POST", "/auth/change-password", session=session,
                       json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}, **csrf)


@pytest.mark.parametrize("action", [_logout, _change], ids=["logout", "troca-de-senha"])
def test_AUT_CSR_001_sem_cabecalho_csrf_recebe_403_sem_efeito(api, database, alice, action):
    session = api.login("alice@example.com")
    response = action(api, session)
    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert session_count(database, alice["id"]) == 1
    assert api.me(session).status_code == 200
    assert api.login_response("alice@example.com", PASSWORD).status_code == 200


@pytest.mark.parametrize("origin_of_token", ["outro-usuario", "outra-sessao-do-mesmo-usuario", "aleatorio", "vazio"])
@pytest.mark.parametrize("action", [_logout, _change], ids=["logout", "troca-de-senha"])
def test_AUT_CSR_001_csrf_de_outra_sessao_ou_invalido_recebe_403(api, database, alice, bob, action, origin_of_token):
    session = api.login("alice@example.com")
    token = {
        "outro-usuario": lambda: api.login("bob@example.com").csrf,
        "outra-sessao-do-mesmo-usuario": lambda: api.login("alice@example.com").csrf,
        "aleatorio": lambda: uuid4().hex + uuid4().hex,
        "vazio": lambda: "",
    }[origin_of_token]()
    before = session_count(database, alice["id"])
    response = action(api, session, csrf=token)
    assert response.status_code == 403
    assert session_count(database, alice["id"]) == before
    assert api.me(session).status_code == 200
    assert api.login_response("alice@example.com", PASSWORD).status_code == 200


def test_AUT_CSR_001_csrf_nao_vale_sem_cookie(api, alice):
    session = api.login("alice@example.com")
    response = api.request("POST", "/auth/logout", csrf=session.csrf)
    assert response.status_code == 401
    assert api.me(session).status_code == 200


def test_AUT_CSR_001_leitura_nao_exige_csrf(api, alice):
    session = api.login("alice@example.com")
    response = api.me(session)
    assert response.status_code == 200
    assert CSRF_HEADER not in response.request.headers


# ---------------------------------------------------------------- AUT-LIM-001

def _fail(api, email, origin):
    response = api.login_response(email, "senha-errada-000", origin=origin)
    assert response.status_code == 400, response.text


def _assert_limited(response, database=None, user_id=None):
    assert response.status_code == 429
    assert "set-cookie" not in response.headers
    assert "csrf_token" not in response.text
    if database is not None:
        assert session_count(database, user_id) == 0


def test_AUT_LIM_001_login_por_conta_429_mesmo_com_senha_correta(api, database, alice):
    for _ in range(LOGIN_FAILURES_PER_ACCOUNT):
        _fail(api, "alice@example.com", ORIGIN_A)
    _assert_limited(api.login_response("alice@example.com", PASSWORD, origin=ORIGIN_A), database, alice["id"])
    # o limite é da conta: outra origem também é barrada
    _assert_limited(api.login_response("alice@example.com", PASSWORD, origin=ORIGIN_B), database, alice["id"])


def test_AUT_LIM_001_limite_por_conta_nao_revela_se_a_conta_existe(api, alice):
    for _ in range(LOGIN_FAILURES_PER_ACCOUNT):
        _fail(api, "alice@example.com", ORIGIN_A)
        _fail(api, "ninguem@example.com", ORIGIN_B)
    existing = api.login_response("alice@example.com", PASSWORD, origin=ORIGIN_A)
    missing = api.login_response("ninguem@example.com", PASSWORD, origin=ORIGIN_B)
    _assert_limited(existing)
    _assert_limited(missing)
    assert existing.content == missing.content


def test_AUT_LIM_001_login_por_origem_429_para_qualquer_conta(api, database, alice, bob):
    for index in range(LOGIN_FAILURES_PER_ORIGIN):
        _fail(api, f"alvo{index}@example.com", ORIGIN_A)  # nenhuma conta atinge o limite próprio
    _assert_limited(api.login_response("alice@example.com", PASSWORD, origin=ORIGIN_A), database, alice["id"])
    # o limite é da origem: a mesma conta, de outra origem, entra
    assert api.login_response("bob@example.com", PASSWORD, origin=ORIGIN_B).status_code == 200


def test_AUT_LIM_001_abaixo_do_limite_login_correto_passa(api, alice):
    for _ in range(LOGIN_FAILURES_PER_ACCOUNT - 1):
        _fail(api, "alice@example.com", ORIGIN_A)
    assert api.login_response("alice@example.com", PASSWORD, origin=ORIGIN_A).status_code == 200


def test_AUT_LIM_001_cadastro_por_origem_429(api, database):
    for index in range(REGISTRATIONS_PER_ORIGIN):
        assert api.register(f"pessoa{index}@example.com", origin=ORIGIN_A).status_code == 201
    response = api.register("excedente@example.com", origin=ORIGIN_A)
    assert response.status_code == 429
    assert users(database, "excedente@example.com") == []
    # um e-mail já existente também recebe 429, sem revelar que existe
    repeated = api.register("pessoa0@example.com", origin=ORIGIN_A)
    assert repeated.status_code == 429 and repeated.content == response.content
    assert api.register("outra-origem@example.com", origin=ORIGIN_B).status_code == 201


# ---------------------------------------------------------------- AUT-OWN-001

@pytest.mark.parametrize("method, path", [
    ("GET", "/users/me"),
    ("POST", "/auth/logout"),
    ("POST", "/auth/change-password"),
])
def test_AUT_OWN_001_rota_de_dados_sem_sessao_recebe_401(api, alice, method, path):
    body = {"json": {"current_password": PASSWORD, "new_password": NEW_PASSWORD}} if path.endswith("password") else {}
    response = api.request(method, path, **body)
    assert response.status_code == 401
    assert alice["id"] not in response.text


def test_AUT_OWN_001_dono_vem_da_sessao_e_nao_da_query(api, alice, bob):
    session = api.login("alice@example.com")
    response = api.me(session, params={"owner_id": bob["id"], "id": bob["id"], "user_id": bob["id"]})
    assert response.status_code == 200
    assert response.json()["id"] == alice["id"]
    assert bob["id"] not in response.text


def test_AUT_OWN_001_owner_id_do_corpo_nunca_e_usado(api, database, alice, bob):
    session = api.login("alice@example.com")
    response = api.request("POST", "/auth/change-password", session=session, csrf=session.csrf,
                           json={"current_password": PASSWORD, "new_password": NEW_PASSWORD,
                                 "owner_id": bob["id"], "user_id": bob["id"], "id": bob["id"]})
    # recusado pela validação (422) ou ignorado (204); nunca aplicado a Bob
    assert response.status_code in (204, 422), response.text
    assert api.login_response("bob@example.com", PASSWORD).status_code == 200
    assert api.login_response("bob@example.com", NEW_PASSWORD).status_code == 400
    if response.status_code == 204:
        assert api.login_response("alice@example.com", NEW_PASSWORD).status_code == 200


def test_AUT_OWN_001_owner_id_e_o_uuid_textual_do_usuario(api, alice):
    session = api.login("alice@example.com")
    body = api.me(session).json()
    assert body["id"] == alice["id"]
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", body["id"])
    assert set(body) >= {"id", "email"} and "hashed_password" not in body


def test_AUT_OWN_001_rotas_de_email_e_edicao_de_usuario_nao_montadas(api, alice):
    session = api.login("alice@example.com")
    for method, path in [("POST", "/auth/forgot-password"), ("POST", "/auth/reset-password"),
                         ("POST", "/auth/request-verify-token"), ("POST", "/auth/verify"),
                         ("PATCH", "/users/me"), ("GET", f"/users/{alice['id']}"),
                         ("PATCH", f"/users/{alice['id']}"), ("DELETE", f"/users/{alice['id']}")]:
        response = api.request(method, path, session=session, csrf=session.csrf, json={})
        assert response.status_code in (404, 405), (method, path, response.status_code)
