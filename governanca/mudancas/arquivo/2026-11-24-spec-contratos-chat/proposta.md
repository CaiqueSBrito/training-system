---
id: 2026-11-24-spec-contratos-chat
titulo: Spec viva e contratos da pipeline de chat e da ingestão
trilha: padrao
estado: concluida
aprovacao: { por: humano, em: 2026-11-24, palavras: "aprovo, aprovo a proposta, aprovo mudança, continue, vai, ok, ta" }
---

## Por quê

A arquitetura (`docs/arquitetura/pipeline-rag.md`) está aceita, mas ainda não existe:

- **spec viva testável**: os comportamentos estão descritos em prosa e tabelas, sem requisitos com ID. Sem ID não há rastreabilidade, teste primeiro nem evidência por requisito;
- **contratos entre pacotes**: os três provedores vão implementar partes diferentes (`rag-backend`, `avaliacao-observabilidade`, depois `frontend`). Sem uma interface comum fechada antes, cada um inventa a sua e a integração quebra.

Esta mudança é o pré-requisito para paralelizar o trabalho entre os provedores.

## O que muda

1. **Spec viva** com requisitos EARS, cada um com ID e cenários, derivados **só** do que a arquitetura aceita já define:
   - `governanca/specs/chat/spec.md`: etapas E1–E12, os dois modos de recuperação, validação determinística, falha fechada dos juízes, conteúdo insuficiente, limite de 1 nova tentativa, gravação da linhagem antes de entregar;
   - `governanca/specs/ingestao/spec.md`: idempotência por `sha256`, chunk por estrutura, verificação de injeção indireta, embedding (uma requisição por chunk, prefixos de tarefa), indexação com BM25 em português, recusa de subir com chave de embedding divergente.
   - Áreas de ID: `RAG`, `ING`, `SEC`, `OBS`.
2. **`packages/contracts`**: modelos Pydantic e JSON Schema exportado para os dados que cruzam fronteiras de pacote:
   - mensagem de chat (entrada e resposta, inclusive recusa, conteúdo insuficiente e omissões com motivo);
   - saídas estruturadas dos agentes: veredito do juiz de entrada, classificação (intenção, modo, escopo), plano de busca (objetivos, nível de Bloom, 1–3 consultas);
   - contexto numerado, `Flashcard`, `Question`, `Citation`;
   - vereditos de E9 e E10;
   - registros de linhagem (`document_version`, `chunk`, `chat_turn`, `search_plan`, `retrieval_run`, `item`, `citation`, `verdict`), **independentes de onde serão gravados**.
3. Testes dos contratos: validação dos invariantes (ex.: `correct_index` dentro das alternativas, 1–3 consultas no plano, citação obrigatória por item) e estabilidade do JSON Schema exportado.

Tarefas previstas após o G1 (definidas no `delta.md` e em `tarefas/`):
- `requisitos`: delta com os requisitos;
- `arquiteto` (worker `claude`, revisor `google`): `packages/contracts` e testes.

## Fora do escopo

- Implementar qualquer etapa (E1–E12) ou a ingestão.
- Infraestrutura (Qdrant, Langfuse) e configuração de modelos.
- Onde gravar a linhagem (arquitetura §11, item 1): os contratos definem os registros, não o armazenamento.
- Parser, tamanho de chunk, rubrica de viés/padrão, calibração de juiz, limiares de avaliação.
- Requisitos novos além da arquitetura aceita: qualquer acréscimo vira outra proposta.

## Riscos

- **Contrato prematuro**: fechar um campo que a implementação vai mostrar errado. Mitigação: mudar um contrato é uma mudança Padrão comum; os contratos são versionados pelo JSON Schema exportado.
- **`pydantic` vira dependência direta** de `packages/contracts`. Hoje ela já entra indiretamente pelo `litellm`. Entra na mesma carência de 7 dias.

## Esclarecido

- Intenções do classificador (E3), decididas pelo humano no G1: `gerar_flashcards`, `gerar_questionario`, `gerar_mais`, `fora_de_escopo`.
