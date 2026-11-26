# Investigação da falha instável — sem nova reprodução

Status da causa: **Bloqueado** — saída original `pytest-r2-run3-incompleto.txt` contém só uma linha de progresso, com F no teste identificado pelo revisor e sem traceback ou resumo. O relatório r2 registra SIGTERM/143, origem desconhecida, quatro outras execuções com 136 testes passando e 15/15 vereditos positivos em chamadas diretas. Isso não comprova transporte, 429, 5xx, timeout nem erro de veredito; são hipóteses, não diagnóstico confirmado.

Não repetimos suíte para reproduzir e não adicionamos retry. O cliente passa a expor somente a categoria sanitizada `provider` ou `invalid_output`, preservando a mensagem fixa e `from None`; nenhum conteúdo bruto é adicionado à exceção. Regressão determinística simula os dois erros em arquivo de teste. LITELLM_LOG=DEBUG permanece inseguro em produção e está documentado; suppress_debug_info não é bloqueio de logs DEBUG.

## Fechamento da atividade de investigação

A investigação dos artefatos disponíveis foi executada e está encerrada com conclusão **inconclusiva**. A determinação da causa histórica continua **Bloqueado**, conforme resposta literal do humano `manter_causa_historica_bloqueada`. Encerrar a atividade no checklist não significa diagnosticar uma causa inexistente, corrigir retroativamente a falha nem aprovar o gate integrado.

As observações HTTP 429 e HTTP 503 das integrações posteriores têm diagnóstico sanitizado nos artefatos de T-017, mas não comprovam o que aconteceu na execução antiga. Não há ação local capaz de reconstruir o traceback ausente.

O registro acima descreve a etapa r3. Posteriormente, a proteção técnica de logging foi implementada e revisada em T-016; DEBUG inseguro passou a ser recusado antes das chamadas. As provas e a revisão finais estão em `governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-017/`. O gate integrado continua reprovado por um HTTP 503 externo na R2, sem retry nem declaração de sucesso artificial.
