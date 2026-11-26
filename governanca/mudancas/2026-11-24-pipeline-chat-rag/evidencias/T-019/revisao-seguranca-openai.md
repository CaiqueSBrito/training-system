# T-019 — revisão de segurança independente OpenAI

Data: 2026-11-25. Provedor efetivo do revisor: OpenAI/Codex, dispatch `ctx_e9413fc31c0a`; autor: Anthropic/Opus (`ctx_cc9dc1cd7150`, correção `ctx_d12b5ad850e6`). Revisão explicitamente solicitada com o checklist de `governanca/papeis/seguranca-llm.md`; a substituição efetiva de provedor fica registrada, sem se apresentar como revisão Anthropic.

Veredito: **aprovado com ressalvas**, para a superfície inspecionada de T-019. Não certifica ausência universal de vazamento, resistência empírica a injeções, prontidão de produção, integração E1–E12 ou G4. Nenhum código de aplicação foi editado.

## Achados e recomendações

### S1 — baixo — código de erro com filtragem sintática, não lista fechada

Local: `packages/generation/src/training_generation/agents.py:111`, `:116`, `:127` e `:325`.

O mapper ignora `error_message`, mas um `error_code` desconhecido que combine com a regex é conservado em `provider_code`, na mensagem/repr de GenerationError e no atributo do span. A regex limita caracteres/tamanho; não demonstra que o conteúdo é categórico. Por inspeção, um marcador não secreto como `CONTEUDO_PRIVADO_DE_TESTE` passaria por esse caminho. O ADK instalado (`google/adk/workflow/_node_runner.py:211`) copia qualquer `exception.status` textual para `Event.error_code`.

Risco: se um SDK/provedor devolver conteúdo sensível nesse campo, a alegação de sanitização absoluta não se sustenta. Não foi observada exploração nem credencial em status real; isso é uma lacuna de defesa, não evidência de vazamento ocorrido.

Correção sugerida: permitir somente códigos canônicos conhecidos/HTTP e categorias de exceção controladas; mapear desconhecidos para categoria fixa sem reproduzir o valor externo. Acrescentar regressão com status desconhecido de caracteres permitidos, verificando exceção e span. Não mudar o mapeamento 400/429/503 já provado.

### S2 — baixo — regressão não isola filho tardio e lastResort

Local: `packages/generation/tests/test_geracao_agentes.py:470`; implementação em `packages/generation/src/training_generation/settings.py:65`.

A correção inspecionada é adequada ao caminho ADK observado: eleva os ancestrais acima de CRITICAL (filhos com nível herdado) e filtra `logging.lastResort`, além dos handlers existentes. A regressão usa caplog e a infraestrutura de logging do pytest; não configura explicitamente uma execução sem handlers, nem garante criação de um logger novo somente depois da guarda. Logo, os 79 sucessos não constituem prova isolada das duas condições que originaram a correção.

Correção sugerida: teste offline isolado que preserve/restaure o estado global de logging, remova handlers para exercitar lastResort e crie um filho ADK após a guarda; conferir stderr/stdout e o marcador sintético. Considerar também filho com nível explícito e handler próprio criado após a guarda, caso essa configuração venha a ser suportada. Não houve reexecução nem modificação de testes nesta revisão.

## Controles conferidos

| Risco | Status | Inspeção |
| --- | --- | --- |
| LLM01 direta / falha fechada | Passou | `validation.py:64` normaliza/limita sem blacklist de SQL; `agents.py:399` não libera entrada quando juiz falha. Prompts separam mensagem como JSON e recusam manipulação; a interrupção do turno depende de T-021. |
| LLM01 indireta | Passou | `agents.py:475` mantém trechos, página e seção dentro do bloco de dados; `:511` delimita o lote julgado. Sem tools ou transferência (`:258`). Nenhuma promessa de imunidade baseada somente em delimitador. |
| LLM05 saída | Passou | `validation.py:239`/`:243` escapa HTML e mantém a origem pré-escape separada. Não há frontend nesta slice; renderização e persistência corretas ainda dependem dos consumidores. |
| LLM07 / configuração | Passou | Prompts e rubrica inspecionados sem credenciais/configuração sensível; `.env.example` é apenas modelo. `settings.py:135` exclui chaves do repr; `agents.py:280`/`:376` entrega chave ao cliente correspondente. Nenhuma leitura do `.env`. |
| Logs / erros comuns | Passou | `_provider_error` e `raise ... from None` não incorporam mensagem do provedor; ADK `error_message` não é lido. Guarda recusa debug/callbacks inseguros via training_llm. Spans registram modelo/hash e categorias, não prompt/resposta. Ressalva S1 limita a garantia para códigos externos desconhecidos. |
| Caminho do traceback ADK | Passou | A leitura local do SDK confirmou `logger.exception` em `google_adk.google.adk.workflow._node_runner` e formação do evento; a guarda trata o ancestral e lastResort. Não foi aberto o artefato antigo sensível, nem inferida sua causa a partir desta inspeção. Ressalva S2 limita a prova de regressão. |

## Limites da conclusão

Conferência estática e dos registros existentes: **Passou**, com as ressalvas acima. Testes/APIs/validador/git nesta revisão: **Não executado**, conforme despacho. Os seis hashes de prompts foram recalculados e conferem; isso prova identidade dos arquivos atuais, não calibração da rubrica. Langfuse permaneceu desligado; não se declarou publicação remota.

Não foi constatado vazamento novo nesta revisão. A fragilidade de logging relatada pelo executor em `packages/llm` permanece responsabilidade de outro dono; não foi corrigida ou aprovada por esta entrega. E2 real/corpus adversarial do novo serviço, E4/E8 reais, calibração do juiz, persistência/isolamento e fluxo de recusa/reparo fim a fim ainda exigem prova própria, sem repetir chamadas até passar nem substituir modelo. A avaliação de T-018/T-020 e o aceite de T-021 ficam fora deste veredito.
