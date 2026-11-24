# Pipeline de trabalho — Training System

Nível 2 da ordem de autoridade: abaixo do humano, acima das specs vivas, das ADRs e do orquestrador. Só muda com aprovação do humano.

## Princípios

1. **Spec viva é a verdade.** `governanca/specs/` descreve como o sistema **é**. Toda mudança de comportamento é um delta sobre ela, e o delta é incorporado à spec viva ao arquivar a mudança.
2. **Uma mudança por intenção; uma tarefa por arquivo.** A mudança agrupa o porquê; a tarefa é a menor unidade executável por um papel.
3. **Sem permissão, só leitura e planejamento.** Nenhum código de produto é escrito sem a permissão do humano registrada na tarefa.
4. **Requisito testável.** Todo requisito é escrito em EARS e tem ID (`governanca/instrucoes/rastreabilidade.md`); ambiguidade vira `[ESCLARECER: pergunta]`, nunca palpite.
5. **Evidência, não afirmação.** Status exatos: `Passou`, `Falhou`, `Não executado`, `Bloqueado`, `Não se aplica` (este com motivo).
6. **Quem constrói não revisa.** O revisor é sempre de outro provedor (`governanca/papeis/`).
7. **Comportamento de LLM se prova com avaliação.** Mudança em prompt, modelo, parâmetro, chunker, recuperação, guardrail, rubrica ou juiz só passa com diff do RAGAS contra a linha de base (`governanca/instrucoes/avaliacao-guiada.md`).
8. **Erro vira regressão.** Toda falha encontrada vira caso permanente (`governanca/progresso/regressao.md`).
9. **A máquina confere a governança.** `uv run validar-governanca` precisa passar antes de qualquer commit.

## Trilhas

| Trilha | Quando | O que exige | Gates do humano |
| --- | --- | --- | --- |
| **Rápida** | cabe numa frase e não muda comportamento: texto, rename, documentação, log | registro em `progresso.md`; nada em `mudancas/` | a ordem direta é a permissão |
| **Padrão** | muda comportamento, toca vários arquivos ou a abordagem é incerta | mudança completa (etapas 0–8) | G1, G2, G3 (se houver requisito novo), G4 |
| **LLM** | toca prompt, modelo, parâmetro de geração, chunker, embedding, recuperação, reranking, guardrail, rubrica, juiz ou linhagem | Padrão + avaliação guiada + revisão do papel `seguranca-llm` quando tocar guardrail ou montagem de prompt | G1, G2, G3, G4 |

Na dúvida, a trilha mais pesada. A trilha pode subir durante o trabalho; nunca desce em silêncio.

## Etapas

**0. Retomar** (início de toda sessão): ler `governanca/progresso/progresso.md`, `git log --oneline -10` e rodar `uv run validar-governanca`. Validador falhando → tratar antes de trabalho novo, sem corrigir alterações humanas fora do escopo.

**1. Propor** (orquestrador + `requisitos`, só leitura): criar `governanca/mudancas/<AAAA-MM-DD-slug>/proposta.md` a partir de `governanca/modelos/proposta.md`, com o porquê, o escopo, o fora do escopo e os `[ESCLARECER]`.
**Gate G1:** o humano aprova a proposta; a aprovação é registrada no frontmatter com data e as palavras do humano.

**2. Especificar** (orquestrador + `requisitos` + `arquiteto` se houver decisão técnica):
- `delta.md`: requisitos EARS que a mudança ADICIONA, MODIFICA ou REMOVE na spec viva;
- `design.md`, só se houver decisão técnica local; decisão que vale para o projeto todo vira ADR em `docs/adr/`;
- `tarefas/T-NNN.md`: uma por arquivo, a partir de `governanca/modelos/tarefa.md`, com papel, provedor, revisor, requisitos e dependências.

**3. Permitir.** **Gate G2:** o humano permite a execução **de cada tarefa**; a permissão vai para o frontmatter (`permissao`). Escopo mudou → pedir de novo.

**4. Casos primeiro** (requisito novo): testes e casos de avaliação escritos antes da implementação (`governanca/instrucoes/teste-primeiro.md`).
**Gate G3:** o humano aprova os casos; depois disso, quem implementa não os altera.

**5. Construir** (papel dono, via worker do Orca, seção "Execução multi-provedor"): a menor mudança que cumpre os requisitos. Descoberta que muda o escopo → volta para a etapa 2.

**6. Provar** (quem construiu): testes, avaliação guiada (trilha LLM) e validador. Cada requisito da tarefa ganha uma evidência no frontmatter. Sem ambiente, credencial ou serviço → `Bloqueado` com o motivo, nunca `Passou`.

**7. Revisar** (papel revisor, de outro provedor): confere código e evidências contra os requisitos; veredito e achados vão para a tarefa. O orquestrador confere cada requisito contra a evidência.
**Gate G4:** aceite final do humano.

**8. Registrar e arquivar:** incorporar o `delta.md` em `governanca/specs/`, mover a pasta da mudança para `governanca/mudancas/arquivo/`, atualizar `progresso.md`, transformar cada falha encontrada em caso de regressão e rodar o validador.

### Estados

- **Mudança:** `proposta` → `aprovada` → `em-andamento` → `concluida` | `cancelada`.
- **Tarefa:** `entender` → `planejar` → `aguardando-permissao` → `construir` → `provar` → `revisar` → `concluida`; a qualquer momento `bloqueada` (com `bloqueio` descrevendo a condição de desbloqueio) ou `cancelada`.

## Execução multi-provedor

- O orquestrador (sessão principal do omp) não implementa código de produto: ele despacha cada tarefa para o provedor do papel dono como **worker do Orca**:
  `orca orchestration worker-start --environment local --spec-file <spec> --worktree new-child --name <T-NNN> --agent <codex|antigravity|claude>`
- A spec do worker é gerada de `governanca/modelos/spec-worker.md`; o `dispatch_id` devolvido vai para o campo `orca_dispatch` da tarefa.
- Tarefas independentes rodam em paralelo (até ~4 workers); as dependências (`depende_de`) definem a ordem.
- A revisão é outro worker, do provedor revisor, com a mesma spec mais as evidências.
- Pergunta de worker que só o humano responde → o orquestrador leva ao humano; nunca responde por ele.

## Sempre com o humano

- Commit, push, merge, deploy e qualquer operação destrutiva.
- Aprovar proposta (G1), permitir tarefa (G2), aprovar casos (G3), aceite final (G4).
- Interpretar requisito ambíguo ou divergir de spec viva / ADR aceita.
- Nova dependência (biblioteca, serviço, modelo de IA) ou troca de modelo.
- Limiares de avaliação, rubrica de viés/padrão, calibração de juiz.
- Aceitar regressão, evidência `Falhou` ou queda de métrica.
- Usar dados reais de pessoas ou credenciais fora do `.env` local.
- Alterar casos de teste ou de avaliação já aprovados.
- Mudar este pipeline, os papéis ou a ordem de autoridade.
