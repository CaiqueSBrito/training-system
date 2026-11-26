# ADR-0016 — Prompts versionados e execução offline da gestão remota

Estado: decisão técnica sob autonomia delegada pelo humano em 2026-11-24; revisão independente pendente, sem alegar aceite humano específico.

## Contexto

ADR-0005 e arquitetura §8 preveem gestão de prompts no Langfuse. O humano ordenou mantê-lo desligado e autorizou continuar a pipeline autonomamente. A indisponibilidade da telemetria não pode tornar o chat indisponível nem produzir prompts ou versões fictícios. Esta decisão adiciona um modo operacional explícito; não altera silenciosamente ADRs aceitas.

## Decisão

- Artefatos reais de prompt e rubrica são arquivos versionados junto ao pacote de geração. Cada etapa registra origem, identificador, versão e SHA-256 dos bytes efetivamente usados. Configuração de modelos não contém segredos nos prompts.
- Com gestão remota desabilitada, o pacote usa esses artefatos como fonte configurada, não como resposta fake nem fallback após falha de outra integração. Não registrar publicação, label ou versão do Langfuse que não ocorreu.
- Quando gestão remota for habilitada, deve selecionar um artefato real com versão identificável e registrar seus bytes/hash. Indisponibilidade pode usar somente artefato previamente carregado e identificado; ausência de artefato válido é erro explícito, não prompt improvisado.
- O exportador de spans continua assíncrono e tolerante à falha conforme ADR-0005. Nenhum worker deve reativar Langfuse para este corte.
- Rubrica inicial versionada de saída cobre sustentação factual pelas fontes, adequação pedagógica ao objetivo/Bloom e ausência de estereótipos ou atribuições sensíveis não sustentadas. Discussão acadêmica legítima não é reprovada somente por mencionar grupos, direito, programação ou segurança. O juiz deve apontar motivo objetivo por item, sem inventar uma proibição de tema.
- A rubrica e o modelo de saída devem ser calibrados em casos rotulados antes de afirmar prontidão para produção; autonomia técnica não torna scores verdadeiros nem dispensa evidência. Nenhuma mudança de rubrica/modelo/limiar para fazer a avaliação observada passar.

## Alternativas

1. Exigir Langfuse ativo para todo chat: contradiz a ordem de desligamento e o requisito de disponibilidade.
2. Inventar versões remotas ou retornar resultado fixo: proibido.
3. Fonte offline explícita, imutável e rastreável: escolhida, preservando possibilidade de gestão remota real posteriormente.

## Consequências

A linhagem precisa guardar a origem e a identidade reais dos prompts além do modelo. Prompts locais não provam integração de gestão remota. A indisponibilidade de um juiz externo continua sendo falha fechada, não um motivo para liberar itens por ausência de verificação. A fonte deve ser escolhida explicitamente na configuração de execução.
