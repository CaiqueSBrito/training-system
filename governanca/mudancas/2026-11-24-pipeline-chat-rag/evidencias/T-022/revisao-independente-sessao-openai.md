# T-022 — revisão independente da sessão principal OpenAI

Revisor efetivo: OpenAI, modelo informado pelo ambiente `openai-codex/gpt-6.1-sol`. Autor das correções: Anthropic. O parecer anterior do agente `RevisaoAvaliacao` é técnico, do mesmo provedor do autor, e não satisfaz independência; permanece histórico sem alteração.

Parecer: aprovado para executar avaliação real controlada, não para declarar produção ou calibração humana.

## Conferência estática

A sessão principal leu diretamente as fronteiras de manifesto/loader, `_case_protocol`, `VerifiedArtifact`, logging, orçamento/Instructor, classificação de exceções, avaliação, comparação e CLI em `evals/runner_geracao.py`, além dos relatórios das correções. Não reexecutou testes nem APIs nesta revisão.

F1: admissão ocorre antes de cada transporte; Instructor recebe max_retries=0 e LiteLLM num_retries=0/max_retries=0. F2: registros dos SDKs são redigidos na fábrica antes de handlers/lastResort; namespaces adicionais incluídos. F3/F4: manifesto fixado por hash externo, fontes e identidade completas revalidadas; avaliação usa artefato remontado, não declarações de verificação do arquivo armazenado. F5: comparação exige protocolo do pedido/caso/dataset compatível e não trata baseline histórica incompatível como antes/depois. F6: erros de entrada e configuração têm categorias sem payload.

Achados adicionais da sessão corrigidos: mensagens não interpolam case_id/doc_id hostis; comparação usa score validado finito int/float, rejeitando string/bool e normalizando antes de somar; calibração observa a fronteira efetiva LiteLLM, exige retries zero explícitos, admite cada chamada no orçamento e bloqueia sucesso sem transporte observado. A troca temporária do atributo é apropriada ao runner CLI sequencial, não uma API concorrente de produção.

Evidência do autor: 86 testes na correção F1–F6, seleções posteriores 24 e 37 passadas, com falhas anteriores preservadas nos relatórios. Não são alegados como execução independente nem somados como casos únicos. A prova de transporte do produto contra servidor HTTP local é registrada na T-021.

## Limites da autorização técnica

Uma execução Faithfulness com orçamento explícito de 2 chamadas por item e uma calibração técnica de 15 casos, sem retentativas para obter sucesso. Falha de provedor deve permanecer resultado bloqueado, sem score fabricado. Langfuse desligado. Nenhuma mudança em prompts, modelos, rubrica, dataset ou limiares para acomodar resultados.

Manifesto antigo sem pedido não permite comparação antes/depois. Captura final só pode ser avaliada após reverificação das fontes e do hash do manifesto correspondente. Os 15 rótulos técnicos não substituem 50–100 casos rotulados por humano; production_ready permanece false. Histórico de T-017 e baseline anterior não é reescrito.
