# Incidente de credencial em saída de teste

## Evidência e alcance conhecido

O executor OpenAI do backend (`ctx_3017e7ea9815`) informou em `msg_5e070fb69ba9` que um traceback pytest expôs uma credencial ao histórico de uma ferramenta ao testar uma guarda de logging que recebia o ambiente completo. O executor informou sanitização do artefato local e correção da passagem do ambiente. O principal não reabriu nem reproduziu a saída sensível. A sanitização local não revoga a credencial nem remove cópias anteriores do histórico.

Não há prova de uso indevido ou de acesso externo. Não se declara ausência de comprometimento. Nenhum valor, prefixo ou hash da credencial é registrado neste documento.

## Contenção

- Principal orientou o humano revogar/substituir `GEMINI_API_KEY` no `.env`, sem enviar a nova chave pelo chat.
- Em `msg_8d39a69b346e`, o principal suspendeu novas chamadas reais com a credencial exposta até confirmação da rotação; autorizou somente complemento offline e serviços locais.
- Contratos T-010 continuam sem dependência de chamadas reais. Langfuse permanece desligado.
- Os nove ataques/controles com juiz real não executados ficam `Bloqueado` por rotação, não são declarados aprovados ou falhas de classificação.

## Correção exigida e verificação

A guarda deve receber somente campos não sensíveis necessários, recusar configuração insegura antes de encaminhar conteúdo, e produzir códigos enumerados de diagnóstico sem ambiente/settings/valores. Testes devem capturar logs, stdout/stderr e exceções, incluindo mutação do estado do SDK entre chamadas, usando sentinelas sintéticas apenas em testes. A suíte não deve receber dicionário de ambiente completo como argumento de validação exibível na traceback.

Estado atualizado: **rotação confirmada pelo humano** no formulário (`rotacao=revoguei_antiga_e_substitui_env`); o principal e os workers não observaram a revogação externa no console do provedor e não a atestam independentemente. Proteções locais de logging passaram no complemento e na revisão Google `ctx_d072ae09d0c3`. Chamadas reais foram retomadas e concluídas em tentativa única por caso para o corpus delimitado de 9 ataques (`R3_ATTACKS`) + 11 controles negativos (`R3_LEGITIMATE`) no despacho `ctx_c4f6ee93846d` (`task_b7aa8e585174`, executado por Google após esgotamento de créditos de Codex Luna/Sol), sem reabrir saídas antigas sensíveis e sem exibir valores da nova chave (`evidencias/T-016/resultado.md`, `evidencias/T-016/juiz-real-ressalvas.json`). A falha histórica anterior da r2 (`run3`) é um incidente diferente, sem diagnóstico comprovado, e permanece `Bloqueado` sem atribuição retroativa.

## Autorizações adicionais de avaliação

O mesmo formulário autorizou `autorizar_ragas_runner_dataset_proposta` e `baseline_inicial_escopo_disponivel`: instalar RAGAS e implementar o runner, preparar dataset/calibração/limiares para aprovação, e estabelecer primeira baseline do escopo implementado. Isso não prova ausência de regressão histórica nem avalia geração ainda não implementada. T-015 executada por Google `ctx_a1e20214f917`, dono exclusivo do lock durante esta etapa. Chave OpenRouter e aprovação da calibração continuam pré-requisitos para avaliação correspondente.
