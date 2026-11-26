#!/usr/bin/env bash
# Caso de verificação da tarefa T-003 (OBS-INF-001 e OBS-INF-002).
#
# Verifica:
#   1. Existência e validade de infra/compose.yaml.
#   2. Serviços obrigatórios (Qdrant v1.20.x, Postgres da aplicação e pilha
#      completa do Langfuse), imagens fixadas por versão, healthchecks em todos
#      os serviços, volumes nomeados e perfis parciais.
#   3. Ausência de segredos versionados no repositório (.gitignore, .env fora do
#      git, .env.example sem segredos, infra/compose.yaml sem segredos/defaults
#      e varredura nos arquivos rastreados).
#   4. Cobertura de todas as variáveis de infra/compose.yaml em .env.example.
#   5. Subida da infraestrutura com um comando, todos os serviços "healthy",
#      Qdrant (/readyz e versão 1.20.x) e Langfuse (/api/public/health e
#      interface web) respondendo.
#   6. Isolamento real (OBS-INF-002 / ADR-0008): containers, volumes, bancos,
#      usuários e senhas diferentes entre aplicação e Langfuse, DATABASE_URL do
#      Langfuse apontando só para o próprio Postgres, conexão TCP própria
#      aceita e autenticação cruzada rejeitada nos dois sentidos.
#
# Uso:
#   ./infra/verificar.sh [--env-file <caminho>] [--wait-timeout <segundos>] [--down]

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/infra/compose.yaml"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"
ENV_FILE="${ENV_FILE:-}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-300}"
TEARDOWN=0
TEMP_ENV_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="${2:-}"
      shift 2
      ;;
    --wait-timeout)
      WAIT_TIMEOUT="${2:-300}"
      shift 2
      ;;
    --down)
      TEARDOWN=1
      shift
      ;;
    *)
      echo "Uso: $0 [--env-file <caminho>] [--wait-timeout <segundos>] [--down]" >&2
      exit 2
      ;;
  esac
done

PASSED=0
FAILED=0
FAILURES=()

pass() {
  PASSED=$((PASSED + 1))
  printf '[PASSOU] %s\n' "$1"
}

fail() {
  FAILED=$((FAILED + 1))
  FAILURES+=("$1")
  printf '[FALHOU] %s\n' "$1"
}

section() {
  printf '\n=== %s ===\n' "$1"
}

cleanup() {
  if [[ "$TEARDOWN" -eq 1 && -f "$COMPOSE_FILE" && -n "${ACTIVE_ENV_FILE:-}" && -f "${ACTIVE_ENV_FILE:-}" ]]; then
    echo ""
    echo "Encerrando containers (--down)..."
    docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" down >/dev/null 2>&1 || true
  fi
  if [[ -n "$TEMP_ENV_FILE" && -f "$TEMP_ENV_FILE" ]]; then
    rm -f "$TEMP_ENV_FILE"
  fi
}
trap cleanup EXIT

prepare_env_file() {
  if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
      fail "Arquivo de ambiente informado não encontrado: ${ENV_FILE}"
      return 1
    fi
    ACTIVE_ENV_FILE="$ENV_FILE"
    echo "Usando arquivo de ambiente informado: ${ACTIVE_ENV_FILE}"
    return 0
  fi

  if [[ -f "${REPO_ROOT}/.env" ]]; then
    ACTIVE_ENV_FILE="${REPO_ROOT}/.env"
    echo "Usando .env local do repositório."
    return 0
  fi

  if [[ ! -f "$ENV_EXAMPLE" ]]; then
    fail ".env.example não encontrado em ${ENV_EXAMPLE}"
    return 1
  fi

  TEMP_ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/training-system-verify-env.XXXXXX")"
  chmod 600 "$TEMP_ENV_FILE"

  python3 - "$ENV_EXAMPLE" "$TEMP_ENV_FILE" <<'PY'
import pathlib
import re
import secrets
import sys

src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
dst = pathlib.Path(sys.argv[2])

SECRET_RE = re.compile(
    r"(^|_)(PASSWORD|PASSWD|SECRET|ENCRYPTION_KEY|API_KEY|ACCESS_KEY|"
    r"SECRET_KEY|SECRET_ACCESS_KEY|PRIVATE_KEY|SALT|TOKEN|REDIS_AUTH)($|_)"
)

out_lines = []
for raw_line in src.splitlines():
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#") or "=" not in raw_line:
        out_lines.append(raw_line)
        continue

    key, val = raw_line.split("=", 1)
    key_clean = key.strip()
    val_clean = val.strip()

    if val_clean:
        out_lines.append(f"{key_clean}={val_clean}")
        continue

    if key_clean == "QDRANT_URL":
        gen = "http://localhost:6333"
    elif key_clean in {"LANGFUSE_HOST", "NEXTAUTH_URL"}:
        gen = "http://localhost:3000"
    elif key_clean.endswith("PUBLIC_KEY"):
        gen = f"pk-lf-{secrets.token_hex(16)}"
    elif key_clean.endswith("SECRET_KEY"):
        gen = f"sk-lf-{secrets.token_hex(16)}"
    elif SECRET_RE.search(key_clean):
        gen = secrets.token_hex(32)
    elif key_clean.endswith("_DB"):
        prefix = key_clean.lower().removesuffix("_db")
        gen = f"{prefix}_db"
    elif key_clean.endswith("_USER"):
        prefix = key_clean.lower().removesuffix("_user")
        gen = f"{prefix}_user"
    else:
        gen = f"verify_{secrets.token_hex(8)}"

    out_lines.append(f"{key_clean}={gen}")

dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
PY

  ACTIVE_ENV_FILE="$TEMP_ENV_FILE"
  echo "Arquivo .env ausente: criado .env efêmero a partir de .env.example (descartado ao sair)."
  return 0
}

echo "Verificação da infraestrutura local — T-003 (OBS-INF-001, OBS-INF-002)"
echo "Repositório: ${REPO_ROOT}"

# ============================================================================
# 1. Existência e validação sintática do infra/compose.yaml (OBS-INF-001)
# ============================================================================
section "1. Existência e validade de infra/compose.yaml (OBS-INF-001)"

COMPOSE_EXISTS=0
COMPOSE_CONFIG_JSON=""
ACTIVE_ENV_FILE=""

if [[ -f "$COMPOSE_FILE" ]]; then
  COMPOSE_EXISTS=1
  pass "infra/compose.yaml encontrado"
else
  fail "infra/compose.yaml não encontrado em ${COMPOSE_FILE}"
fi

if prepare_env_file; then
  pass "Arquivo de ambiente preparado a partir de .env / .env.example"
fi

if [[ "$COMPOSE_EXISTS" -eq 1 && -n "$ACTIVE_ENV_FILE" ]]; then
  if COMPOSE_CONFIG_JSON="$(docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" config --format json 2>&1)"; then
    pass "docker compose config validou infra/compose.yaml com sucesso"
  else
    fail "docker compose config falhou: ${COMPOSE_CONFIG_JSON}"
    COMPOSE_CONFIG_JSON=""
  fi
else
  fail "Validação do docker compose config não pôde ser executada (infra/compose.yaml ausente)"
fi

# ============================================================================
# 2. Estrutura do compose: serviços, versões, healthchecks, volumes, perfis
# ============================================================================
section "2. Estrutura do Compose (OBS-INF-001, ADR-0002, ADR-0005, ADR-0008)"

if [[ -n "$COMPOSE_CONFIG_JSON" ]]; then
  STRUCT_OUTPUT="$(
    COMPOSE_CONFIG_JSON="$COMPOSE_CONFIG_JSON" python3 - "$COMPOSE_FILE" "$ACTIVE_ENV_FILE" <<'PY'
import json
import os
import re
import subprocess
import sys

compose_file = sys.argv[1]
env_file = sys.argv[2]
data = json.loads(os.environ["COMPOSE_CONFIG_JSON"])
services = data.get("services") or {}
top_volumes = data.get("volumes") or {}

errors = []
notes = []

required_fixed = [
    "qdrant",
    "app-postgres",
    "langfuse-web",
    "langfuse-worker",
    "langfuse-postgres",
    "clickhouse",
    "minio",
]
for svc in required_fixed:
    if svc not in services:
        errors.append(f"serviço obrigatório ausente: {svc}")

cache_svc = "redis" if "redis" in services else ("valkey" if "valkey" in services else None)
if cache_svc is None:
    errors.append("serviço obrigatório ausente: redis (ou valkey)")

if not errors:
    notes.append(f"todos os 8 serviços obrigatórios presentes ({', '.join(sorted(services.keys()))})")

UNPINNED_TAGS = {"latest", "main", "master", "dev", "edge", "nightly", "stable", "beta", "alpha"}
PINNED_TAG_RE = re.compile(r"^(v?\d+\.\d+([.\-][A-Za-z0-9._-]+)?|RELEASE\.\d{4}-\d{2}-\d{2}.*)$")
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")

for name, cfg in sorted(services.items()):
    image = (cfg.get("image") or "").strip()
    if not image:
        errors.append(f"serviço '{name}' sem campo image")
        continue

    if DIGEST_RE.search(image):
        pinned = True
    else:
        last_part = image.rsplit("/", 1)[-1]
        if ":" not in last_part:
            pinned = False
            tag = "<sem tag>"
        else:
            tag = last_part.split(":", 1)[1]
            pinned = tag.lower() not in UNPINNED_TAGS and bool(PINNED_TAG_RE.match(tag))

    if not pinned:
        errors.append(f"serviço '{name}' com imagem sem versão fixada: '{image}'")

    if name == "qdrant":
        if not re.search(r"qdrant/qdrant:v?1\.20(\.\d+)?(@sha256:[0-9a-f]{64})?$", image):
            errors.append(f"serviço 'qdrant' deve usar versão v1.20.x (ADR-0002); encontrado: '{image}'")

    hc = cfg.get("healthcheck") or {}
    hc_test = hc.get("test")
    if hc.get("disable") is True or not hc_test:
        errors.append(f"serviço '{name}' sem healthcheck configurado")

stateful = ["qdrant", "app-postgres", "langfuse-postgres", "clickhouse", "minio"]
if not top_volumes:
    errors.append("nenhum volume nomeado declarado no nível superior de volumes:")
for svc in stateful:
    if svc not in services:
        continue
    vols = services[svc].get("volumes") or []
    named = [v for v in vols if isinstance(v, dict) and v.get("type") == "volume" and v.get("source") in top_volumes]
    if not named:
        errors.append(f"serviço com estado '{svc}' não monta volume nomeado declarado")

prof_proc = subprocess.run(
    ["docker", "compose", "-f", compose_file, "--env-file", env_file, "config", "--profiles"],
    capture_output=True,
    text=True,
    check=False,
)
profiles = [p.strip() for p in prof_proc.stdout.splitlines() if p.strip()]
if not profiles:
    errors.append("nenhum perfil configurado no docker compose (perfis são obrigatórios para subida parcial)")
else:
    partial_ok = False
    for prof in profiles:
        sub_proc = subprocess.run(
            ["docker", "compose", "-f", compose_file, "--env-file", env_file, "--profile", prof, "config", "--services"],
            capture_output=True,
            text=True,
            check=False,
        )
        sub_svcs = [s.strip() for s in sub_proc.stdout.splitlines() if s.strip()]
        if 0 < len(sub_svcs) < len(services):
            partial_ok = True
            notes.append(f"perfil '{prof}' seleciona subconjunto parcial ({len(sub_svcs)}/{len(services)} serviços)")
            break
    if not partial_ok:
        errors.append(f"perfis existentes ({profiles}) não permitem subir apenas parte dos serviços")

if errors:
    for err in errors:
        print(f"ERR: {err}")
    sys.exit(1)

for note in notes:
    print(f"OK: {note}")
PY
  )"
  if [[ $? -eq 0 ]]; then
    while IFS= read -r line; do
      [[ -n "$line" ]] && echo "  $line"
    done <<<"$STRUCT_OUTPUT"
    pass "Estrutura do compose conforme (8 serviços, Qdrant v1.20.x, imagens fixadas, healthchecks, volumes nomeados e perfis)"
  else
    while IFS= read -r line; do
      [[ -n "$line" ]] && echo "  $line"
    done <<<"$STRUCT_OUTPUT"
    fail "Estrutura do compose não atende todos os requisitos"
  fi
else
  fail "Verificação estrutural do compose não executada (infra/compose.yaml ausente ou inválido)"
fi

# ============================================================================
# 3. Nenhum segredo da infraestrutura versionado (OBS-INF-001, cenário 2)
# ============================================================================
section "3. Ausência de segredos versionados (OBS-INF-001)"

if git -C "$REPO_ROOT" check-ignore -q .env && [[ -z "$(git -C "$REPO_ROOT" ls-files .env)" ]]; then
  pass ".env está no .gitignore e não está rastreado pelo git"
else
  fail ".env precisa estar no .gitignore e fora do índice do git"
fi

SECRET_SCAN_OUTPUT="$(
  python3 - "$REPO_ROOT" "$ENV_EXAMPLE" "$COMPOSE_FILE" <<'PY'
import pathlib
import re
import subprocess
import sys

repo_root = pathlib.Path(sys.argv[1])
env_example = pathlib.Path(sys.argv[2])
compose_file = pathlib.Path(sys.argv[3])

SECRET_VAR_RE = re.compile(
    r"(^|_)(PASSWORD|PASSWD|SECRET|ENCRYPTION_KEY|API_KEY|ACCESS_KEY|"
    r"SECRET_KEY|SECRET_ACCESS_KEY|PUBLIC_KEY|PRIVATE_KEY|SALT|TOKEN|REDIS_AUTH)($|_)"
)

errors = []

if not env_example.is_file():
    errors.append(".env.example não encontrado")
else:
    for lineno, raw in enumerate(env_example.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if SECRET_VAR_RE.search(k) and v != "":
            errors.append(f".env.example:{lineno} define valor não vazio para segredo '{k}'")

if not compose_file.is_file():
    errors.append("infra/compose.yaml não encontrado para auditoria de segredos")
else:
    compose_text = compose_file.read_text(encoding="utf-8")
    # 1) Proíbe segredos padrão conhecidos de exemplos upstream
    forbidden_literals = [
        "mysecret",
        "mysalt",
        "miniosecret",
        "myredissecret",
        "CHANGEME",
        "0000000000000000000000000000000000000000000000000000000000000000",
    ]
    for lit in forbidden_literals:
        if lit in compose_text:
            errors.append(f"infra/compose.yaml contém segredo/placeholder proibido: '{lit}'")

    # 2) Proíbe credenciais literais embutidas em URLs (ex.: postgresql://user:pass@)
    for lineno, raw in enumerate(compose_text.splitlines(), 1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"://[^/\s:@]+:(?!\$\{)[^/\s@]+@", stripped):
            errors.append(f"infra/compose.yaml:{lineno} contém credencial literal em URL")

        # 3) Atribuições de variáveis secretas em environment: devem vir do .env sem default literal
        m = re.match(r"^(?:-\s*)?([A-Z][A-Z0-9_]*)\s*[:=]\s*(.+?)\s*$", stripped)
        if m:
            var_name, raw_val = m.group(1), m.group(2).strip().strip('"').strip("'")
            if SECRET_VAR_RE.search(var_name):
                # Deve referenciar variável ${...} e não pode ter default (:- ou -)
                if not re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*(?::?\?[^}]*)?\}", raw_val):
                    errors.append(
                        f"infra/compose.yaml:{lineno} '{var_name}' deve vir só do .env (sem literal nem default :-)"
                    )

# 4) Varredura de assinaturas de chaves reais em arquivos rastreados no git
tracked = subprocess.run(
    ["git", "-C", str(repo_root), "ls-files"],
    capture_output=True,
    text=True,
    check=True,
).stdout.splitlines()

SIG_PATTERNS = [
    ("Langfuse secret key", re.compile(r"sk-lf-[A-Za-z0-9_-]{8,}")),
    ("Langfuse public key", re.compile(r"pk-lf-[A-Za-z0-9_-]{8,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{20,}")),
    ("OpenRouter API key", re.compile(r"sk-or-v1-[0-9a-fA-F]{20,}")),
]

for rel in tracked:
    if rel == "infra/verificar.sh":
        continue
    path = repo_root / rel
    if not path.is_file():
        continue
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for label, pat in SIG_PATTERNS:
        if pat.search(content):
            errors.append(f"arquivo rastreado '{rel}' contém assinatura de {label}")

if errors:
    for e in errors:
        print(f"ERR: {e}")
    sys.exit(1)

print("OK: nenhum segredo versionado encontrado em .env.example, infra/compose.yaml ou arquivos rastreados")
PY
)"
if [[ $? -eq 0 ]]; then
  echo "  ${SECRET_SCAN_OUTPUT}"
  pass "Auditoria de segredos versionados aprovada"
else
  while IFS= read -r line; do
    [[ -n "$line" ]] && echo "  $line"
  done <<<"$SECRET_SCAN_OUTPUT"
  fail "Auditoria de segredos versionados falhou"
fi

# ============================================================================
# 4. Variáveis referenciadas em infra/compose.yaml declaradas em .env.example
# ============================================================================
section "4. Cobertura de variáveis em .env.example (OBS-INF-001)"

if [[ "$COMPOSE_EXISTS" -eq 1 ]]; then
  ENV_COV_OUTPUT="$(
    python3 - "$COMPOSE_FILE" "$ENV_EXAMPLE" <<'PY'
import pathlib
import re
import sys

compose_text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
env_example_text = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")

declared = set()
for raw in env_example_text.splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    declared.add(line.split("=", 1)[0].strip())

# Extrai ${VAR...} ignorando $$ escapado para dentro do container
referenced = set(re.findall(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)(?:[:?+-][^}]*)?\}", compose_text))
missing = sorted(referenced - declared)

if not referenced:
    print("ERR: infra/compose.yaml não referencia nenhuma variável de ambiente do .env")
    sys.exit(1)

if missing:
    print("ERR: variáveis usadas em infra/compose.yaml mas ausentes em .env.example: " + ", ".join(missing))
    sys.exit(1)

print(f"OK: todas as {len(referenced)} variável(is) de infra/compose.yaml estão declaradas em .env.example")
PY
  )"
  if [[ $? -eq 0 ]]; then
    echo "  ${ENV_COV_OUTPUT}"
    pass "Todas as variáveis de infra/compose.yaml estão declaradas em .env.example"
  else
    echo "  ${ENV_COV_OUTPUT}"
    fail "Variáveis de infra/compose.yaml ausentes em .env.example"
  fi
else
  fail "Cobertura de variáveis não pôde ser verificada (infra/compose.yaml ausente)"
fi

# ============================================================================
# 5. Subida com um comando, healthchecks healthy, Qdrant e Langfuse ativos
# ============================================================================
section "5. Subida da infraestrutura, healthchecks e endpoints (OBS-INF-001)"

STACK_UP=0

if [[ -n "$COMPOSE_CONFIG_JSON" ]]; then
  echo "Executando: docker compose -f infra/compose.yaml --env-file <env> --profile \"*\" up -d --wait --wait-timeout ${WAIT_TIMEOUT}"
  if docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" up -d --wait --wait-timeout "$WAIT_TIMEOUT"; then
    STACK_UP=1
    pass "Subida da infraestrutura concluída com um comando"
  else
    fail "Falha ao subir a infraestrutura ou aguardar healthchecks (timeout=${WAIT_TIMEOUT}s)"
    echo "--- docker compose ps ---"
    docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" ps -a || true
  fi

  echo "--- docker compose ps ---"
  docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" ps

  PS_JSON="$(docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" ps --format json 2>/dev/null || true)"
  HEALTH_OUTPUT="$(
    COMPOSE_CONFIG_JSON="$COMPOSE_CONFIG_JSON" PS_JSON="$PS_JSON" python3 - <<'PY'
import json
import os
import sys

compose_data = json.loads(os.environ["COMPOSE_CONFIG_JSON"])
expected_services = set((compose_data.get("services") or {}).keys())

raw_ps = os.environ.get("PS_JSON", "").strip()
containers = []
if raw_ps:
    if raw_ps.startswith("["):
        containers = json.loads(raw_ps)
    else:
        for line in raw_ps.splitlines():
            line = line.strip()
            if line:
                containers.append(json.loads(line))

by_service = {c.get("Service"): c for c in containers if c.get("Service")}
errors = []

for svc in sorted(expected_services):
    c = by_service.get(svc)
    if not c:
        errors.append(f"serviço '{svc}' sem container em execução")
        continue
    state = (c.get("State") or "").lower()
    health = (c.get("Health") or "").lower()
    if state != "running" or health != "healthy":
        errors.append(f"serviço '{svc}' não está healthy (State={state!r}, Health={health!r})")

if errors:
    for err in errors:
        print(f"ERR: {err}")
    sys.exit(1)

print(f"OK: todos os {len(expected_services)} serviços estão running e healthy")
PY
  )"
  if [[ $? -eq 0 ]]; then
    echo "  ${HEALTH_OUTPUT}"
    pass "Todos os serviços reportam status healthy"
  else
    while IFS= read -r line; do
      [[ -n "$line" ]] && echo "  $line"
    done <<<"$HEALTH_OUTPUT"
    fail "Nem todos os serviços estão healthy"
  fi

  # Qdrant HTTP /readyz e versão 1.20.x
  QDRANT_ADDR="$(docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" port qdrant 6333 2>/dev/null | head -n1 | sed 's/^0\.0\.0\.0:/127.0.0.1:/; s/^\[::\]:/127.0.0.1:/')"
  if [[ -n "$QDRANT_ADDR" ]] && curl -fsS --max-time 10 "http://${QDRANT_ADDR}/readyz" >/dev/null; then
    QDRANT_INFO="$(curl -fsS --max-time 10 "http://${QDRANT_ADDR}/" 2>/dev/null || true)"
    if python3 -c 'import json, sys; d=json.loads(sys.argv[1]); v=str(d.get("version","")); assert v.startswith("1.20."), f"versão inesperada: {v}"; print(f"Qdrant v{v} pronto em http://{sys.argv[2]}")' "$QDRANT_INFO" "$QDRANT_ADDR"; then
      pass "Qdrant responde em /readyz com versão 1.20.x"
    else
      fail "Qdrant respondeu em /readyz, mas versão reportada não é 1.20.x (${QDRANT_INFO})"
    fi
  else
    fail "Qdrant não respondeu em /readyz (endereço resolvido: '${QDRANT_ADDR:-<vazio>}')"
  fi

  # Langfuse API /api/public/health?failIfDatabaseUnavailable=true e interface web
  LF_ADDR="$(docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" port langfuse-web 3000 2>/dev/null | head -n1 | sed 's/^0\.0\.0\.0:/127.0.0.1:/; s/^\[::\]:/127.0.0.1:/')"
  if [[ -n "$LF_ADDR" ]]; then
    LF_HEALTH="$(curl -fsS --max-time 15 "http://${LF_ADDR}/api/public/health?failIfDatabaseUnavailable=true" 2>/dev/null || true)"
    if python3 -c 'import json, sys; d=json.loads(sys.argv[1]); assert str(d.get("status","")).upper() == "OK", f"status={d}"; print(f"Langfuse health OK em http://{sys.argv[2]}")' "$LF_HEALTH" "$LF_ADDR"; then
      pass "Langfuse /api/public/health?failIfDatabaseUnavailable=true respondeu OK"
    else
      fail "Langfuse /api/public/health?failIfDatabaseUnavailable=true não retornou OK (${LF_HEALTH})"
    fi

    LF_UI_BODY="$(curl -fsSL --max-time 15 "http://${LF_ADDR}/" 2>/dev/null || true)"
    if grep -qiE '<html|<!doctype html' <<<"$LF_UI_BODY" && grep -qi 'langfuse' <<<"$LF_UI_BODY"; then
      pass "Interface web do Langfuse responde HTML em http://${LF_ADDR}/"
    else
      fail "Interface web do Langfuse não retornou página HTML válida em http://${LF_ADDR}/"
    fi
  else
    fail "Porta do serviço langfuse-web (3000) não resolvida"
  fi
else
  fail "Subida da infraestrutura, healthchecks e endpoints não executados (infra/compose.yaml ausente ou inválido)"
fi

# ============================================================================
# 6. Isolamento entre Postgres da aplicação e do Langfuse (OBS-INF-002)
# ============================================================================
section "6. Isolamento de bancos e credenciais (OBS-INF-002 / ADR-0008)"

if [[ -n "$COMPOSE_CONFIG_JSON" && "$STACK_UP" -eq 1 ]]; then
  ISOLATION_META="$(
    COMPOSE_CONFIG_JSON="$COMPOSE_CONFIG_JSON" python3 - <<'PY'
import json
import os
import shlex
import sys
from urllib.parse import urlparse

data = json.loads(os.environ["COMPOSE_CONFIG_JSON"])
services = data.get("services") or {}
errors = []

app_pg = services.get("app-postgres") or {}
lf_pg = services.get("langfuse-postgres") or {}
lf_web = services.get("langfuse-web") or {}
lf_worker = services.get("langfuse-worker") or {}

app_env = app_pg.get("environment") or {}
lf_env = lf_pg.get("environment") or {}

app_db = (app_env.get("POSTGRES_DB") or "").strip()
app_user = (app_env.get("POSTGRES_USER") or "").strip()
app_pass = (app_env.get("POSTGRES_PASSWORD") or "").strip()

lf_db = (lf_env.get("POSTGRES_DB") or "").strip()
lf_user = (lf_env.get("POSTGRES_USER") or "").strip()
lf_pass = (lf_env.get("POSTGRES_PASSWORD") or "").strip()

for label, val in [
    ("app-postgres POSTGRES_DB", app_db),
    ("app-postgres POSTGRES_USER", app_user),
    ("app-postgres POSTGRES_PASSWORD", app_pass),
    ("langfuse-postgres POSTGRES_DB", lf_db),
    ("langfuse-postgres POSTGRES_USER", lf_user),
    ("langfuse-postgres POSTGRES_PASSWORD", lf_pass),
]:
    if not val:
        errors.append(f"{label} está vazio")

if app_db and lf_db and app_db == lf_db:
    errors.append(f"app-postgres e langfuse-postgres usam o mesmo nome de banco: '{app_db}'")
if app_user and lf_user and app_user == lf_user:
    errors.append(f"app-postgres e langfuse-postgres usam o mesmo usuário: '{app_user}'")
if app_pass and lf_pass and app_pass == lf_pass:
    errors.append("app-postgres e langfuse-postgres usam a mesma senha")

app_vols = {v.get("source") for v in (app_pg.get("volumes") or []) if isinstance(v, dict)}
lf_vols = {v.get("source") for v in (lf_pg.get("volumes") or []) if isinstance(v, dict)}
shared_vols = app_vols & lf_vols
if shared_vols:
    errors.append(f"app-postgres e langfuse-postgres compartilham volume(s): {sorted(shared_vols)}")

for svc_name, svc_cfg in [("langfuse-web", lf_web), ("langfuse-worker", lf_worker)]:
    dep = svc_cfg.get("depends_on") or {}
    dep_names = set(dep.keys()) if isinstance(dep, dict) else set(dep)
    if "app-postgres" in dep_names:
        errors.append(f"{svc_name} não deve depender de app-postgres")

    db_url = ((svc_cfg.get("environment") or {}).get("DATABASE_URL") or "").strip()
    if not db_url:
        errors.append(f"{svc_name} sem DATABASE_URL configurado")
        continue
    parsed = urlparse(db_url)
    if parsed.hostname != "langfuse-postgres":
        errors.append(f"{svc_name} DATABASE_URL aponta para host '{parsed.hostname}', esperado 'langfuse-postgres'")
    if parsed.username != lf_user or parsed.username == app_user:
        errors.append(f"{svc_name} DATABASE_URL usa usuário incompatível com isolamento")
    if parsed.password != lf_pass or parsed.password == app_pass:
        errors.append(f"{svc_name} DATABASE_URL usa senha incompatível com isolamento")
    url_db = parsed.path.lstrip("/")
    if url_db != lf_db or url_db == app_db:
        errors.append(f"{svc_name} DATABASE_URL aponta para banco '{url_db}', esperado '{lf_db}'")

if errors:
    for e in errors:
        print(f"ERR: {e}", file=sys.stderr)
    sys.exit(1)

# Emite atribuições seguras para os testes reais de conexão via psql
for k, v in [
    ("APP_PG_DB", app_db),
    ("APP_PG_USER", app_user),
    ("APP_PG_PASS", app_pass),
    ("LF_PG_DB", lf_db),
    ("LF_PG_USER", lf_user),
    ("LF_PG_PASS", lf_pass),
]:
    print(f"{k}={shlex.quote(v)}")
PY
  )"
  if [[ $? -eq 0 ]]; then
    eval "$ISOLATION_META"
    pass "Configuração garante volumes, bancos, usuários, senhas e DATABASE_URL separados"

    APP_CID="$(docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" ps -q app-postgres)"
    LF_CID="$(docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" ps -q langfuse-postgres)"
    if [[ -n "$APP_CID" && -n "$LF_CID" && "$APP_CID" != "$LF_CID" ]]; then
      pass "app-postgres e langfuse-postgres rodam em containers distintos"
    else
      fail "app-postgres e langfuse-postgres não têm containers distintos (app='${APP_CID}', langfuse='${LF_CID}')"
    fi

    # Conexão legítima ao Postgres da aplicação (TCP 127.0.0.1 exige senha)
    APP_CONN="$(docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" exec -T -e PGPASSWORD="$APP_PG_PASS" app-postgres psql -h 127.0.0.1 -U "$APP_PG_USER" -d "$APP_PG_DB" -tAc "SELECT current_database() || '|' || current_user" 2>/dev/null || true)"
    if [[ "$APP_CONN" == "${APP_PG_DB}|${APP_PG_USER}" ]]; then
      pass "Conexão real ao app-postgres com credenciais da aplicação sucedeu (${APP_CONN})"
    else
      fail "Conexão real ao app-postgres com credenciais da aplicação falhou (saída='${APP_CONN}')"
    fi

    # Conexão legítima ao Postgres do Langfuse (TCP 127.0.0.1 exige senha)
    LF_CONN="$(docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" exec -T -e PGPASSWORD="$LF_PG_PASS" langfuse-postgres psql -h 127.0.0.1 -U "$LF_PG_USER" -d "$LF_PG_DB" -tAc "SELECT current_database() || '|' || current_user" 2>/dev/null || true)"
    if [[ "$LF_CONN" == "${LF_PG_DB}|${LF_PG_USER}" ]]; then
      pass "Conexão real ao langfuse-postgres com credenciais do Langfuse sucedeu (${LF_CONN})"
    else
      fail "Conexão real ao langfuse-postgres com credenciais do Langfuse falhou (saída='${LF_CONN}')"
    fi

    # Isolamento cruzado: credenciais do Langfuse NÃO autenticam no app-postgres
    if docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" exec -T -e PGPASSWORD="$LF_PG_PASS" app-postgres psql -h 127.0.0.1 -U "$LF_PG_USER" -d "$APP_PG_DB" -c "SELECT 1" >/dev/null 2>&1; then
      fail "Isolamento violado: credenciais do Langfuse autenticaram no app-postgres"
    else
      pass "Credenciais do Langfuse rejeitadas no app-postgres"
    fi

    # Isolamento cruzado: credenciais da aplicação NÃO autenticam no langfuse-postgres
    if docker compose -f "$COMPOSE_FILE" --env-file "$ACTIVE_ENV_FILE" --profile "*" exec -T -e PGPASSWORD="$APP_PG_PASS" langfuse-postgres psql -h 127.0.0.1 -U "$APP_PG_USER" -d "$LF_PG_DB" -c "SELECT 1" >/dev/null 2>&1; then
      fail "Isolamento violado: credenciais da aplicação autenticaram no langfuse-postgres"
    else
      pass "Credenciais da aplicação rejeitadas no langfuse-postgres"
    fi
  else
    fail "Isolamento de configuração entre app-postgres e langfuse-postgres falhou"
  fi
else
  fail "Verificação de isolamento entre bancos não executada (infraestrutura não está no ar)"
fi

# ============================================================================
# Resumo
# ============================================================================
section "Resumo"
echo "Passou: ${PASSED}"
echo "Falhou: ${FAILED}"

if [[ "$FAILED" -gt 0 ]]; then
  echo "Falhas registradas:"
  for f in "${FAILURES[@]}"; do
    echo "  - ${f}"
  done
  exit 1
fi

echo "Todas as verificações de OBS-INF-001 e OBS-INF-002 passaram."
exit 0
