# ADR-0014 — Contratos publicados pelo OpenAPI da API

Estado: **Aceita** (humano, 2026-11-24). Substitui a decisão do G1 de `2026-11-24-spec-contratos-chat` que versionava os contratos pelo JSON Schema exportado.

## Contexto

A mudança `2026-11-24-spec-contratos-chat` criou `packages/contracts` com modelos Pydantic e decidiu versionar os contratos pelo **JSON Schema exportado** em `packages/contracts/schema/`, regravado por `exportar-schema-contratos` e guardado por `test_schema.py`. Era a única forma de expor os contratos enquanto não havia API.

Com a API em FastAPI (ADR-0011), o OpenAPI é gerado dos mesmos modelos Pydantic, e o front gera seus tipos dele (ADR-0013). Manter os dois artefatos cria duas fontes para a mesma informação:

- o JSON exportado e o OpenAPI podem divergir (configuração de geração, modelos que só um deles inclui);
- o front teria de escolher qual dos dois consumir;
- os modelos internos entre pacotes (ex.: `NumberedContext`, `RawGenerationOutput`) não cruzam a fronteira HTTP e não precisam de artefato publicado: entre pacotes, o contrato é o próprio modelo Python (ADR-0010).

## Decisão

1. **O OpenAPI da `apps/api` é o artefato publicado dos contratos** (ARQ-CTR-001): todo modelo de `packages/contracts` que cruza a fronteira HTTP aparece nele, gerado do próprio modelo Pydantic, com o mesmo schema de `model_json_schema()`.
2. **Deixa de existir o JSON Schema exportado à parte**: `packages/contracts/schema/`, o comando `exportar-schema-contratos` e `test_schema.py`.
3. Quando o JSON Schema for necessário em tempo de execução (saída estruturada de modelo, ex.: E2, E3, E4, E8, E10), ele vem de `model_json_schema()` do modelo, nunca de arquivo.
4. A guarda de mudança dos contratos passa a ser um teste da `apps/api` que confere o OpenAPI contra os modelos de `packages/contracts` usados nos endpoints.
5. **Transição**: a remoção dos itens do passo 2 acontece na mudança que criar a `apps/api`, junto com o teste do passo 4. Até lá, os arquivos e o teste atuais continuam, para os contratos não ficarem sem guarda de mudança.

## Alternativas consideradas

| Opção | Por que não |
| --- | --- |
| Manter o JSON Schema exportado e também publicar o OpenAPI | duas fontes da mesma informação que podem divergir; o teste de estabilidade do JSON não protege o que o front consome |
| Só o JSON Schema exportado, front gerando tipos dele | o JSON exportado não descreve endpoints, eventos nem códigos de resposta; o front precisaria do OpenAPI de qualquer forma |
| Pacote de tipos compartilhado escrito à mão (Python + TypeScript) | duplicação manual, sem garantia de igualdade |
| Remover o JSON Schema exportado já nesta mudança | os contratos ficariam sem guarda de mudança até a API existir |

## Consequências

- Mudança de contrato continua trilha Padrão no mínimo e avisa os donos dos pacotes consumidores; o aviso passa a incluir o front, que regenera os tipos.
- Até a mudança da `apps/api`, `packages/contracts/schema/` segue versionado e o `test_schema.py` segue valendo, sem edição por esta ADR.
- Modelos usados só entre pacotes deixam de ter artefato publicado; sua estabilidade é guardada pelos testes de `packages/contracts`.
- A decisão de 2026-11-24 em `governanca/mudancas/arquivo/2026-11-24-spec-contratos-chat/` permanece como registro histórico; esta ADR é a que vale.
