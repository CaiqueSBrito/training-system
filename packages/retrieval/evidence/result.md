# T018 — implementação E5–E7

Executor efetivo OpenAI/Codex; dispatch `ctx_bdc63d216c9e`, task `task_6c7c5f863949`.
Revisão independente Anthropic **Não executado**. Sem commit/push/deploy.

## Resultados

| Prova | Status | Evidência |
| --- | --- | --- |
| Focal original (17 casos) | Falhou | `focal.txt`: 12 passed, 5 failed, 100.60s; preservado |
| Cinco casos afetados após correção concreta | Passou | `focal-correction.txt`: 5 passed, 4 deselected, 8.98s |
| Oito regressões existentes de reconciliação/SafeRetriever | Passou | `focal.txt`, sem repetir no complemento |
| BGE CPU real e calibração (4 positivos/4 negativos) | Passou | `../calibration/bge-v1.json`; download e calibração única na focal original |
| Inferência BGE real pelo RetrievalService | Passou | complemento offline com artefato original, sem recalibrar |
| Separação dos pares de calibração e avaliação | Passou | `calibration-separation.json` |
| Workspace único, preservação dos pins e carência7dias | Passou | `uv-sync.txt`, `lock-before.json`, `static-verification.json`; nenhuma versão existente alterada |
| Baseline antes v1.0.1 preservada | Passou | `baseline-before-v1.0.1.json`, SHA256 `f0b5e3b60373fa30d9da2c65ef580f1c42a682b9af634b2925b5e2cc75e4edad`, igual ao original |
| Diff RAGAS sobre o novo RetrievalService | Bloqueado | runner atual instancia SafeRetriever diretamente (`evals/runner_ressalvas.py:729,759`) e não possui adaptador para E5–E7; pacote evals fora desta slice; principal deve integrar adapter/manifesto compatível e executar comparação, sem alegar diff histórico |
| LSP Python | Bloqueado | pyright/pylsp ausentes neste shell; inventário AST e referências locais substituem somente navegação estática |
| AST de todos os módulos novos e barreira compartilhada | Passou | parse AST local, sem formatter/lint/build extra |
| G3 T001 congelados | Passou | hashes abaixo coincidem com a ficha arquivada |
| Gemini real / geração / Langfuse remoto | Não executado | fora da focal; embedding/juiz são dublês apenas nos testes, Langfuse segue desligado |

O complemento foi explicitamente autorizado pelo coordenador em `msg_e280f308b614`, após
pedido que preservou a falha original (`msg_616a7aabb897`). Não foi loop até verde: não
houve repetição de calibração, dos oito testes de reconciliação nem de API de IA.

## Causa e correção

A fixture nova consumia `Ingestor` sem reconciliação. O `protocol.py:219-220` inicial chama
`index.upsert(...,owner_id)` sem evento/revisão/hash, enquanto a barreira existente exige
esses campos; o runner RAGAS e regressões existentes já executam `Reconciler`. A barreira
corretamente negou tais pontos. A fixture passou a reconciliar pelo protocolo real/CAS antes
de consultar; a restauração do ack ausente também passou a usar `Reconciler`, pois o trigger
nega inserir ack numa geração publicada/ready. Nenhum fencing/ack foi contornado, nem
se alterou `protocol.py` fora do escopo. A fixture usa chunks pequenos para exercer janela
com continuação e subseções, sem enfraquecer asserts. O caso real agora carrega a calibração
versionada (já provada) e verifica inferência; não recalibra toda execução.

## Requisitos

- RAG-REC-001 **Passou** na prova de armazenamento: dois prefetches server-side dense/BM25
  português + RRF, mesmo filtro, owner/versões/prefixo exato PG, união deduplicada50/top5.
  PG/Qdrant são reais e descartáveis; embedding é dublê restrito ao teste.
- RAG-REC-002 **Passou**: ordem/hierarquia por caminho completo, janela entre subseções,
  largest remainder inteiro 3:1/8=>6:2, allocations e cursor retomável sem repetir chunks.
- RAG-REC-003 **Passou**: BGE real fixado, singleton lazy CPU batch2/max512, scores sigmoid
  explícitos, top configurável, sem modelo/score alternativo. Inferência integrada real no complemento.
- RAG-REC-004 **Passou**: calibração separada real, corte obrigatório `0.48544316837251245`,
  nenhum selecionado => context None/chunks[]; não cria contrato canônico vazio inválido.
- Aceite global de qualidade RAGAS permanece **Bloqueado**, distinto das provas focais acima.

## Integração e limites

API em `../README.md`: `RetrievalService(...,chat_turn_id=...).retrieve(owner_id,plan,
classification,RetrievalScope(...),count) -> RetrievalResult`. Aggregate próprio contém
contratos canônicos (não classes paralelas deles); `Allocation` informa seção/count/chunk_ids.
`context=None` sinaliza insuficiência. T020 guarda cursor sob owner/session; T021 deve
recarregá-lo do servidor, reconciliar projeções antes de consultar e revalidar autorização
antes de entregar após geração. Cursor não é token autenticado para aceitar do usuário.

Calibração inicial separa controles simples; não mede qualidade de produção nem cobre
hard negatives amplamente. Corte não foi mudado após falha. Nenhuma chamada Gemini ou
OpenRouter foi executada. Memória de BGE foi liberada ao sair dos dois processos de prova;
T019 avisado antes da carga e depois da liberação. Só Postgres/Qdrant compartilhados
permanecem ativos; recursos de prova criados pelo teste foram descartados pelo finally.

Hashes G3 T001 preservados:

- test_classificacao.py: `b15e416b015824a090e553346703bee137191d58a247b61012a74c626f526b8e`
- test_plano.py: `c897e706a7d3b2161e80559fe79a9b160d6febdf3811d0f1a21bfc5aba241c5b`
- test_itens.py: `1623c0e17a7493acc7eb0323b419a44d03ce62ed894fa680bfe07ff762c19784`

T017 anterior não foi alterada/arquivada. Validador de retomada foi executado conforme
AGENTS antes da implementação; validador de fechamento registrado separadamente em
`validator.txt`. Nenhuma revisão própria é apresentada como revisão independente.

## Atualização após revisão R1 — 2026-11-25

As afirmações locais anteriores de REC002 Passou não constituem aprovação: Anthropic R1 não aprovou o cálculo proporcional da janela. Correção e novas regressões em `rec002-review-fix.md`; REC002 permanece não aprovado até reavaliação independente. REC001/003/004, barreira e calibração receberam aprovação parcial R1. Histórico e falhas acima preservados.
