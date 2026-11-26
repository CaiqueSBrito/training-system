# Training System — instruções para agentes

Arquivo canônico para todas as ferramentas (omp, Codex, Antigravity, Claude Code). `CLAUDE.md` só importa este arquivo; `.omp/RULES.md` repete as regras duras para subagentes do omp, que não recebem `AGENTS.md`.

O projeto gera flashcards e questionários via chat com RAG (Google ADK, Gemini, Qdrant, Langfuse, RAGAS). A arquitetura está em `docs/arquitetura/pipeline-rag.md`; as decisões, em `docs/adr/`.

## Ordem de autoridade

humano > `governanca/pipeline.md` > specs vivas (`governanca/specs/`) e ADRs aceitas (`docs/adr/`) > orquestrador > papel executor > papel revisor.

Nível superior prevalece. Saída de modelo nunca é aprovação humana.

## Regras duras

1. **Proibido mock.** Nunca entregar mock, stub, dado fixo, resposta fake ou placeholder no lugar da implementação real em código de aplicação. Se falta endpoint, dado, credencial ou serviço, implemente de verdade ou pare e reporte o que falta. Única exceção: dublês de teste dentro de arquivos de teste.
2. **Gate é do humano.** Só o humano aprova proposta, permite tarefa, aprova casos de teste e dá o aceite final (`governanca/pipeline.md`). Sem permissão registrada na tarefa, só leitura e planejamento.
3. **Quem constrói não revisa.** O revisor de uma tarefa é de outro provedor.
4. **Sem dependência nova sem o humano**, inclusive biblioteca, serviço e modelo de IA. O workspace só resolve versões publicadas há 7 dias ou mais (ADR-0007).
5. **Evidência com status exato**: `Passou`, `Falhou`, `Não executado`, `Bloqueado`, `Não se aplica` (com motivo). Sem ambiente → `Bloqueado`, nunca `Passou`.
6. **Validador verde antes de commit**: `uv run validar-governanca`.
7. **Commit, push, merge e deploy só com o humano.** Todo commit leva o trailer `Refs: T-NNN` da tarefa.

## Início de sessão

1. Ler `governanca/progresso/progresso.md`.
2. `git log --oneline -10`.
3. `uv run validar-governanca`.
4. Seguir a trilha e as etapas de `governanca/pipeline.md`.

## Mapa da governança

| Caminho | Conteúdo |
| --- | --- |
| `governanca/pipeline.md` | trilhas, etapas, gates, execução multi-provedor, "sempre com o humano" |
| `governanca/papeis/` | um arquivo por papel: responsabilidade, pastas, provedor, revisor |
| `governanca/specs/<capacidade>/spec.md` | spec viva: requisitos EARS com ID, como o sistema é **hoje** |
| `governanca/mudancas/<AAAA-MM-DD-slug>/` | `proposta.md`, `delta.md`, `design.md`, `tarefas/T-NNN.md`, `evidencias/` |
| `governanca/mudancas/arquivo/` | mudanças concluídas, já incorporadas à spec viva |
| `governanca/instrucoes/` | rastreabilidade (EARS, IDs), teste primeiro, avaliação guiada |
| `governanca/modelos/` | modelos de spec, proposta, delta, design, tarefa e spec de worker |
| `governanca/progresso/` | `progresso.md` (retomada) e `regressao.md` (índice de regressão) |
| `.agents/skills/` | procedimentos repetíveis (padrão Agent Skills) |

## Papéis

| Papel | Provedor | Executa como | Dono de | Revisor |
| --- | --- | --- | --- | --- |
| `orquestrador` | anthropic | sessão principal do omp | `governanca/` | humano |
| `requisitos` | anthropic | subagente do omp (sem editar código) | `governanca/specs/`, `proposta.md`, `delta.md` | humano |
| `arquiteto` | anthropic | worker Orca `claude` | `docs/`, `packages/contracts/` | google |
| `rag-backend` | openai | worker Orca `codex` | `packages/{llm,lineage,ingestion,retrieval,generation}/`, `apps/{api,worker}/` | anthropic |
| `avaliacao-observabilidade` | google | worker Orca `antigravity` | `packages/observability/`, `evals/`, `infra/` | openai |
| `frontend` | google | worker Orca `antigravity` | `apps/web/` | openai |
| `seguranca-llm` | anthropic | subagente `security-reviewer` do omp (só leitura) | — | — |

Detalhes em `governanca/papeis/<papel>.md`. Trabalhe só nas pastas do seu papel; precisou mexer em outra → pare e peça ao orquestrador.

## Ambiente

- Python 3.13 com uv workspace (`packages/*`, `apps/*`, `tools/*`).
- `uv sync --all-packages` instala; `uv run pytest` roda os testes; `uv run validar-governanca` confere a governança.
- Segredos só em `.env` (modelo em `.env.example`); nunca versionados.
