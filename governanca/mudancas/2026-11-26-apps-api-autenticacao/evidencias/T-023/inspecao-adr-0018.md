# T-023 complemento — inspeção da ADR-0018 contra RAG-SAI-001 (MODIFICA)

Data: 2026-11-26 · Papel: arquiteto (anthropic) · Revisor: google

ADR inspecionada: `docs/adr/0018-saida-texto-puro.md`. Origem: decisões humanas A4 e I2 ("autorizo", registradas em `tarefas/T-025.md`), achado D1 de `evidencias/T-025/revisao-anthropic-r2.md`, e a confirmação de A4/I2 aplicados em `evidencias/T-025/revisao-seguranca-llm-r2.md`.

## RAG-SAI-001 (MODIFICA, `delta.md`) × ADR-0018

| Parte do requisito | Onde na ADR | Status |
| --- | --- | --- |
| Conteúdo gerado entregue como texto puro, sem escape HTML | Decisão, item 1 | Passou |
| Remoção de caracteres de controle e invisíveis (Cc exceto `\n` e `\t`, Cf, Cs) | Decisão, item 2 | Passou |
| Validação contra o schema antes da entrega; violação omite o item | Decisão, item 2 | Passou |
| Todo cliente renderiza com `textContent`, nunca como HTML | Decisão, item 3 | Passou |
| Cenário: item com `<script>` chega idêntico no SSE e na linhagem, sem escape parcial | Decisão, item 1 ("igual no evento final do SSE e na consulta de linhagem"); Contexto (inconsistência entre rotas) | Passou |
| Cenário: caractere de controle removido | Decisão, item 2 | Passou |

## Fidelidade ao brief e às decisões humanas

| Verificação | Onde na ADR | Status |
| --- | --- | --- |
| Estado `**Aceita** (humano, 2026-11-26, decisão A4)` | cabeçalho | Passou |
| Contexto: escape inconsistente entre SSE e linhagem (OWASP LLM05) | Contexto | Passou |
| I2: linhagem do turno expõe só a categoria do veredito de entrada | Decisão, item 4 | Passou |
| Substitui, na ADR-0004, só o trecho de escape do E11 (item 5); o resto da 0004 continua | cabeçalho; Decisão, parágrafo final | Passou |
| Consequência: dados anteriores à A4 com HTML escapado (só desenvolvimento), sem migração sem o humano | Consequências, item 1 | Passou |
| Consequência: `apps/web` (ADR-0013) segue a regra | Consequências, item 2 | Passou |
| Seções Contexto, Decisão, Alternativas consideradas, Consequências | ADR | Passou |
| Número 0018 livre e sequencial após a 0017 | `docs/adr/` | Passou |

## Conferência com o código já entregue (só leitura)

| Verificação | Onde | Status |
| --- | --- | --- |
| A regra do item 2 é a implementada: remove Cf, Cs e Cc exceto `\n`/`\t`, sem escape nem decodificação de entidades | `packages/contracts/src/training_contracts/rendering.py` (`render_item`) | Passou |
| Documentação da `apps/api` exige `textContent` | `apps/api/README.md:186` | Passou |

## Documentos alinhados

| Verificação | Onde | Status |
| --- | --- | --- |
| ADR-0004: só a linha de aviso no topo, resto inalterado | `docs/adr/0004-guardrails-e-juizes.md` (`git diff`: +2 linhas) | Passou |
| §2 tabela de etapas: E11 "texto puro (sem controles nem invisíveis)" | `docs/arquitetura/pipeline-rag.md:54` | Passou |
| §5: E11 sem escape HTML, `textContent` no cliente, link para a 0018 | `docs/arquitetura/pipeline-rag.md:110` | Passou |
| §10.1: `apps/web` renderiza como texto (`textContent`), ADRs 0013 e 0018 | `docs/arquitetura/pipeline-rag.md:194` | Passou |
| Nenhuma outra menção a escape do E11 na arquitetura | `grep -i escap docs/arquitetura/pipeline-rag.md` só acha a §5 nova ("não escapa HTML") | Passou |
| Índice: linha da 0018 e nota na linha da 0004 | `docs/README.md` | Passou |
| Nenhum código, dependência ou arquivo de `governanca/` fora destas evidências editado | `git status` | Passou |
| Cenários de RAG-SAI-001 em execução | — | Não se aplica: tarefa só de documentação; a prova em execução é da T-025 (rodada 3) |
| Validador | `validador-adr-0018.txt` | Passou (exit 0) |

## Observações para o orquestrador

- A ADR-0013 (aceita, não editada) diz no Contexto que "a E11 já escapa o conteúdo no servidor". A ADR-0018 registra nas Consequências que essa frase deixa de valer; a Decisão, item 3, da 0013 continua compatível. Se o humano quiser também um aviso no topo da 0013, como na 0004, é uma linha a autorizar.
- A docstring de `packages/generation/src/training_generation/validation.py` (linhas 4–6) ainda fala em "cópia escapada para entrega". É código do papel `rag-backend`; o arquiteto não edita.
- A spec viva (`governanca/specs/chat/spec.md`, RAG-SAI-001) é atualizada no arquivamento do delta, não aqui.
- O cabeçalho "Decisões:" de `docs/arquitetura/pipeline-rag.md` continua listando só 0001–0007 (observação já registrada na T-009).
