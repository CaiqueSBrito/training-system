# T-021 — reavaliação independente das correções

Revisor: sessão principal OpenAI, modelo informado pelo ambiente `openai-codex/gpt-6.1-sol`. Correções revisadas: Anthropic, dispatches `ctx_3ddd9b4abdb8` e `ctx_2edefa516adc`. A revisão Anthropic do produto OpenAI original permanece histórica; esta revisão cobre as correções do outro provedor.

## Parecer

Correções técnicas aprovadas no escopo inspecionado. Não equivale a aceite de produção, G4, avaliação real ou suíte integral verde.

Leitura estática de `generation/chat.py`, `generation/cli.py`, `lineage/chat.py` e migração 0005, comparada aos relatórios de revisão e às evidências das rodadas 1 e 2. Nenhuma API, suíte ou instalação foi executada nesta revisão; nenhum segredo foi lido.

- Cursor: só `gerar_mais` herda cursor; pedidos novos abrem progressão explícita. O fence compara a cabeça da sessão dentro da transação de persistência, inclusive para estado anterior inválido.
- Escopo: contexto inicial, juiz e reparo derivam de interseções canônicas filtradas. Reparo combinado mantém um chunk por entrada e remapeia referências; `_resolve` recusa evidência fora da alocação e da seção. Referência local inexistente não é reinterpretada como global.
- Orçamento: uma chamada global de reparo, incluindo reparo de envelope. Chamadas iniciais de outros grupos não reabrem esse orçamento.
- Achado desta revisão corrigido: envelope reparado não pode ser capturado como tentativa inicial. A rodada 2 registra chamada inicial falha separadamente, reparo com `repairs_call_id`, itens/vereditos como tentativa 2 e não inventa bruto inicial. A validação persistente exige chamada concluída e coerência entre tipo, tentativa e vínculo de envelope.
- Entrada: prompt persistido limitado; tamanho e hash do bruto auditados no mesmo savepoint.
- CLI: captura nova com O_EXCL/O_NOFOLLOW; cancelamento recebe código 130 e mensagem fixa; clientes, provider e engine fecham no finally. Processor de latências compartilhado por provider, com retenção limitada.
- Transporte: evidência do autor exercita LiteLLM/cliente HTTP reais contra servidor local 429/503, com uma requisição em E2/E10 e max_retries=0. Não é chamada a provedor externo.

## Evidências e limites

`correcao-revisao.md` preserva 64 testes passados e 4 falhas na focal da rodada 2; complemento dos quatro corrigidos passou. Não se declara execução integral verde no snapshot final. Teste C02 passou a comparar revisão/tabelas/contagens observadas, em vez de fixar cabeça 0003.

A execução CLI real anterior é anterior às correções: comprova o caminho original, não este snapshot. A migração 0005 foi reescrita antes de aplicação em banco real; bancos descartáveis com a forma anterior precisam ser recriados. Não aplicar em banco compartilhado sem provisionamento explícito. Langfuse permanece desligado.

RAGAS, calibração real e comparação compatível permanecem tarefas separadas. Não há inferência de prontidão de produção a partir destes testes.
