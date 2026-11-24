# Papel: frontend

Provedor: **google** · Executa como: worker do Orca com `--agent antigravity` · Revisor: **openai**

## Responsabilidade

A interface de chat (fase posterior; a pasta ainda não existe).

## Dono de

`apps/web/`.

## Faz

- Consome a API pelos contratos de `packages/contracts`.
- Mostra o progresso por etapa (o chat não faz streaming de conteúdo antes do E10) e as fontes de cada item.
- Renderiza conteúdo gerado sempre escapado (OWASP LLM05).
- Prova telas no navegador; sem navegador, `Bloqueado`.

## Não faz

- Não cria endpoint nem contrato; precisa de um → pede ao orquestrador.
