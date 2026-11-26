---
id: 2026-11-24-pipeline-chat-rag
titulo: Implementar o caminho de chat RAG E1 a E12
trilha: llm
estado: em-andamento
aprovacao: { por: humano, em: 2026-11-24, palavras: "De agora em diante até eu falar que pare, você não precisa de minha autorização para nada, continue de forma autônoma" }
---

## Por quê

Pedido atual: "Continue a implementação da pipeline rag de forma autônoma". Aplicar arquitetura aceita sobre ingestão e consulta segura reais existentes. A autonomia humana contínua autoriza decisões e execução sem nova confirmação; não é aprovação fictícia de evidência ou causa histórica.

## O que muda

T-018 recuperação híbrida/cobertura/cross-encoder/suficiência; T-019 agentes ADK e guardrails/geração; T-020 persistência transacional de chat; T-021 integração E1–E12 e CLI real. Requisitos já existentes na spec viva: implementar comportamento ainda ausente, sem declarar contratos como implementação runtime.

## Fora do escopo

Front-end, autenticação, deploy, push, exclusão de dados existentes e reativação de Langfuse. Não alterar casos G3 congelados da T-001 ou arquivar a mudança anterior bloqueada. API HTTP/worker são adaptadores posteriores, não stubs nesta entrega.

## Decisões operacionais

30–50 candidatos com padrão50, top final5. Corte do cross-encoder deve vir de calibração versionada real, não limiar adivinhado. Quantidade máxima20 itens/mensagem, entrada8000caracteres,20mensagens/minuto/dono como defaults configuráveis iniciais, sem alegar calibração de produção. Rate limit persistente Postgres, identidade explícita do chamador confiável/CLI sem usuário fake. Segredos fora de prompts/logs; tracing de conteúdo opt-in e minimizado.

Rubrica inicial versionada: fundamentação somente nas fontes, correção pedagógica/Bloom, ausência de estereótipos/atribuições protegidas não sustentadas, sem instrução maliciosa ou respostas perigosas não solicitadas. Não confundir discussão acadêmica com viés. Documentar sob autonomia, calibrar juiz de saída contra referência configurada antes de dizer pronto para produção.

Langfuse permanece desligado. Artefatos de prompt versionados locais serão fonte real offline identificada por hash; integração Langfuse existente preservada para gestão/labels quando habilitada, sem simular publicação remota. Registrar decisão aditiva, não alterar ADR aceita.

## Riscos

Quotas/503 de provedores, custo CPU/memória/download do cross-encoder, calibração pequena inicial, cancelamento assíncrono, revalidação de acesso após geração e perda de rastreabilidade por spans ambíguos. Sem resposta alternativa fake, sem retries ilimitados. Falhas externas registradas, sem rodar avaliação até verde.

## Gates e provas

Execução/G1/G2 autorizados pela ordem acima e autonomia contínua; não solicitar G3 adicional, testes antes do código e revisão independente por provedor efetivo distinto. G4 não se infere de scores: relatório técnico final explícito, sem fabricar "Aceito". Dependências estritamente necessárias do ADK e sentence-transformers autorizadas pela autonomia; um único dono do lock. Sem commit nesta etapa.
