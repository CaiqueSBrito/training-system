"""T-024 (G3): constantes e cliente HTTP dos casos de autenticação da apps/api.

Módulo de apoio só de teste, com nome único no workspace (evita colisão de
`conftest` entre pacotes no sys.path do pytest).
"""

from dataclasses import dataclass, field

from starlette.testclient import TestClient

BASE_URL = "https://testserver"  # cookie Secure só volta por HTTPS
SESSION_COOKIE = "training_session"
CSRF_HEADER = "X-CSRF-Token"
PASSWORD = "senha-correta-123"  # 17 caracteres, dentro da política
MIN_PASSWORD = 12
SESSION_LIFETIME = 3600
LOGIN_FAILURES_PER_ACCOUNT = 3
LOGIN_FAILURES_PER_ORIGIN = 6
LOGIN_WINDOW = 900
REGISTRATIONS_PER_ORIGIN = 3
REGISTRATION_WINDOW = 3600
ORIGIN_A = ("203.0.113.10", 40000)
ORIGIN_B = ("198.51.100.20", 40000)


@dataclass
class Session:
    token: str
    csrf: str
    set_cookie: str = field(repr=False)


class Api:
    """Cliente HTTP sem pote de cookies: cada chamada envia só a sessão pedida.

    Assim um cookie revogado pode ser reenviado de propósito e nenhuma sessão
    vaza de um caso para outro.
    """

    def __init__(self, app):
        self._app = app
        self._clients: dict[tuple[str, int], TestClient] = {}

    def client(self, origin=ORIGIN_A) -> TestClient:
        if origin not in self._clients:
            client = TestClient(self._app, base_url=BASE_URL, client=origin)
            client.__enter__()
            self._clients[origin] = client
        return self._clients[origin]

    def request(self, method, path, *, session: Session | None = None, csrf: str | None = None,
                cookie: str | None = None, origin=ORIGIN_A, **kwargs):
        client = self.client(origin)
        headers = dict(kwargs.pop("headers", {}) or {})
        token = cookie if cookie is not None else (session.token if session else None)
        if token is not None:
            headers["Cookie"] = f"{SESSION_COOKIE}={token}"
        if csrf is not None:
            headers[CSRF_HEADER] = csrf
        client.cookies.clear()
        try:
            return client.request(method, path, headers=headers, **kwargs)
        finally:
            client.cookies.clear()

    def register(self, email, password=PASSWORD, origin=ORIGIN_A, **extra):
        return self.request("POST", "/auth/register", json={"email": email, "password": password, **extra},
                            origin=origin)

    def login_response(self, email, password=PASSWORD, origin=ORIGIN_A, cookie=None):
        return self.request("POST", "/auth/login", data={"username": email, "password": password},
                            origin=origin, cookie=cookie)

    def login(self, email, password=PASSWORD, origin=ORIGIN_A, cookie=None) -> Session:
        response = self.login_response(email, password, origin=origin, cookie=cookie)
        assert response.status_code == 200, response.text
        return Session(token=response.cookies[SESSION_COOKIE], csrf=response.json()["csrf_token"],
                       set_cookie=response.headers["set-cookie"])

    def me(self, session=None, cookie=None, **kwargs):
        return self.request("GET", "/users/me", session=session, cookie=cookie, **kwargs)

    def close(self):
        for client in self._clients.values():
            client.__exit__(None, None, None)


