---
id: 2026-11-24-correcao-integral-ressalvas
titulo: Corrigir integralmente as ressalvas de ingestão e linhagem
trilha: llm
estado: em-andamento
aprovacao: { por: humano, em: 2026-11-24, palavras: "aprovo_proposta_contratos_e_execucao_T010_T017" }
---

## Por quê

O humano rejeitou aceitar ressalvas e ordenou literalmente: **"corrija todas as ressalvas"**. Essa é a autorização da correção e deste planejamento completo; não constitui G1 de uma proposta ainda não apresentada nem G3 de casos novos. Fontes: revisões r2 de T-006/T-007 e r3 de T-008 da mudança `2026-11-24-ingestao`.

## O que muda

As 16 linhas de `matriz.md` cobrem migração incremental verificável; texto canônico e proveniência por spans; chunking estrutural e Unicode; protocolo durável sem chamadas remotas em transações; conversor reutilizável seguro; decisões append-only; reconciliação; reclassificação; diagnóstico sanitizado; proteção técnica de DEBUG; coleção estrita e prova integrada final.

`delta.md`, `design.md`, `contratos.md`, `casos.md`, `implantacao.md` e T-010–T-017 são propostas para deliberação. Nova ADR aditiva em rascunho nesta pasta; número definitivo conferido somente no despacho futuro. Nenhuma alteração de ADR aceita, mudança arquivada ou spec viva.

## Fora do escopo

Implementação de produto nesta etapa; alterar casos G3 antigos; excluir dados humanos; aplicar migração/deploy; adicionar dependências ou modelos; mudar lock; religar Langfuse; suíte cara; revisão independente pelo autor.

## Riscos

Qdrant e Postgres não têm transação conjunta: o consumidor deve conferir a decisão efetiva na linhagem mesmo com payload atrasado. Fontes antigas podem ser insuficientes para reconstrução fiel; isso precisa ficar explícito. Mudança de chunker e juiz exige avaliação guiada, além dos testes.

## Gates a apresentar ao humano

1. G1 **aprovado** pelo humano no formulário: `aprovo_proposta_contratos_e_execucao_T010_T017`.
2. G2 **permitido para T-010–T-017** pela mesma resposta, respeitando escopos, dependências e bloqueios; implantação e exclusão de dados existentes não estão autorizadas.
3. G3 **dispensado para T-010–T-017** pelo humano no formulário: `dispenso_G3_T010_T017`. Testes continuam antes da implementação; casos antigos aprovados não podem ser alterados.
4. Revisão independente Google do produto OpenAI e revisão `seguranca-llm` para guardrail/prompt. Indisponibilidade bloqueia revisão.
5. G4 **pendente**: evidências das 16 linhas, prova integrada e diff RAGAS aprovado. Causa histórica sem captura continua lacuna incontornável, sem diagnóstico inventado.

## Esclarecer

- [ESCLARECER: aprovar novos contratos, nova ADR aditiva e tratamento explícito de fonte antiga insuficiente?]
- [ESCLARECER: aprovar dataset, baseline, limiares e calibração do juiz RAGAS existente e disponibilizar OPENROUTER_API_KEY em .env local?]
- [ESCLARECER: autorizar posteriormente implantação sobre dados existentes com backup, escopo e janela definidos? Exclusão de fixture exige autorização específica por manifesto.]

## Execução desta preparação

Arquiteto + requisitos nominal anthropic/Opus 5.5 indisponível por créditos; fallback autorizado GPT-6.1 Sol, provedor efetivo OpenAI; revisão Google obrigatória. Dispatch `ctx_c2a6792f6832`, task Orca `task_ae3ae804a0cc`. Registro conforme despacho, sem trocar modelo da aplicação. Skill `.agents/skills/governanca-mudanca/SKILL.md`: a ordem explícita do despacho autoriza preparar o pacote completo sem fingir G1.

## Respostas do humano posteriores ao planejamento

- Formulário: `{"execucao":"aprovo_proposta_contratos_e_execucao_T010_T017","g3":"dispenso_G3_T010_T017"}`.
- Implementar contratos e corrigir antes do G4; não interpretar G1/G2/G3 como aceite final, autorização de deploy, exclusão ou nova calibração RAGAS.
