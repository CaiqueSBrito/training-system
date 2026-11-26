# T018 — correção após revisão Anthropic R1

Data 2026-11-25. Executor OpenAI/Codex, dispatch `ctx_fbacca0b2246`,
task `task_a9a3087c9da1`. Produto anterior `ctx_bdc63d216c9e` preservado.

Revisão R1 Anthropic `ctx_6048186daf1c`: **aprovado parcialmente** — REC001/003/004,
barreira e calibração aprovados; **REC002 não aprovado**. Relatório original em
`governanca/mudancas/2026-11-24-pipeline-chat-rag/evidencias/T-018/revisao-anthropic.md`.
A reavaliação desta correção é **Não executado**; a prova local não é aprovação REC002.

## Causa e produto corrigido

A versão revisada pesava apenas o texto dos chunks selecionados em round-robin. O peso
mudava com top-N e overlap, e o único `section_path` do chunk apagava partes de H2/H3.
Agora `coverage.py` lê somente metadados/proveniência dos chunks já autorizados pela
barreira existente, com join de owner/versão. Calcula interseções exatas do mapping com
os spans canônicos, agrupa por versão/receita/ocorrência H1/caminho completo e une os
intervalos. Cada codepoint autorizado conta uma vez; separadores não são conteúdo.
Títulos, parágrafos e tabelas são contabilizados com seu tamanho real, sem normalizar
para obter proporção. Um chunk que atravessa H3 contribui corretamente para ambas as
subseções, e o prefixo encontra esse chunk pelo span.

Todos os chunks do escopo, inclusive os usados em janelas anteriores, são autorizados e
revalidados antes da medida global; os usados não são escolhidos novamente. Isso mantém
o tamanho canônico estável entre páginas, salvo mudança legítima da autorização. Fontes
revogadas não entram em pesos nem em contexto. A barreira não foi modificada, nem sua
chamada com dict de revisão observada substituída por None; repository/locks T020 intactos.

A orientação `msg_c5b6412c04cc` do coordenador explicitou o achado2: cobertura não é
busca temática top-N. Não executa BGE nem elimina subseções por corte/relevância de
consultas; `run.cutoff=None`, `rerank_score=None`. A insuficiência limita-se à falta de
conteúdo canônico autorizado disponível. O orçamento de itens determina a janela de
contexto, depois da alocação proporcional global, enquanto top-N/corte BGE seguem só
na consulta, sem mudança de modelo/score/calibração. A atualização é uma interpretação
explícita da arquitetura §2.1 e REC002; não uma redução do requisito para aceitar o bug.

Alocação usa todos os tamanhos globais das subseções ainda com chunks disponíveis,
antes de escolher fontes da janela. Quotas0 e subseções esgotadas são explícitas, com
count0; só fontes escolhidas são consumidas. Uma subseção esgotada não recebe quota sem
fonte; as demais conservam seus tamanhos globais e recebem o próximo orçamento. Continua
sem reset de binding/cursor v1. Não se pretende calibrar dificuldade pedagógica pelo tamanho.

## Regressões e resultados exatos

- **Falhou** — `rec002-red.txt`: teste de consumidor novo, escrito antes da correção,
  reproduz exatamente 5:3 onde a spec exige6:2; 1 failed,9 deselected,2.65s. Postgres e
  Qdrant descartáveis reais; protocolo de ingestão/reconciliação reais. Dublês somente no
  teste (parser de referência canônico, embedding/juiz e score), sem implementação fake
  em app. Fonte de400codepoints,300:100 em H1/H2/H3, chunks de50 sobrepostos por25 e um
  chunk atravessando a fronteira das subseções. É o caso do revisor, sem ajustar asserts.
- **Passou** — `rec002-correction.txt`: 3 passed,7 deselected,4.87s; versão intermediária
  que corrigiu os tamanhos. Preservada como histórico; não é prova da decisão seguinte.
- **Passou** — `rec002-final-correction.txt`: 4 passed,6 deselected,4.88s, após orientação
  explícita sobre cobertura sem score/top-N. Prova consumidor6:2 com canonical_size300:100,
  deduplicação de overlap, chunk misto/H3/prefixo, ordem/continuação até consumir todos,
  owner alheio recusado, escopo inacessível, e insuficiência de consulta preservada.
- **Passou** — `rec002-large-section.txt`: 1 passed,10 deselected,4.09s; seis subseções
  pequenas antes de uma grande, fora dos primeiros5chunks de leitura. Grande900 versus
  total pequeno300 =>6itens para grande,2 para pequenas, todas7subseções explícitas;
  reranker levanta erro se chamado (não é chamado). Impede perder a grande por score,
  posição, top-N ou amostragem. Sem nova alteração de produto entre estas duas provas.
- **Passou** — AST dos módulos locais. LSP permanece **Bloqueado**, indisponível no shell.
- Validador de fechamento em `rec002-validator.txt`.

O caso antigo de continuação agora pede2itens para exercer duas páginas; top_n=2 já não
limita cobertura. Mantém assertions de progresso, ordem e owner, sem normalizar proporções.
A nova regressão exige8itens e6:2 pelo serviço, não só pela função aritmética isolada.

Nenhuma API de IA, inferência/calibração BGE, download, suíte cara, sync/lock ou commit.
A conexão HTTP usada é somente Qdrant descartável local. Pesos/corte aprovados e barreira
conservam os mesmos hashes do manifesto original:

- calibração: `f824b7e67c06fba2110a184569daf40617ca5c316eccab413c9f20ce2923d9e2`
- eligibility: `845cfc90c5268dfddea45c3d3c87534996f8e997f07cb153b7da8fe68440cbb1`

## Interface final T021 e pendências

Assinatura de retrieve, contratos canônicos e continuação v1 preservados. `Allocation`
acrescenta `canonical_size`, `canonical_id`, `h1_id` (opcionais/None em consulta) às
propriedades anteriores. T021 deve passar as quotas/IDs por subseção ao gerador; fonte
mista pode aparecer em duas alocações com partes canônicas distintas, sem duplicar contexto.
API comunicada diretamente a T021 `ctx_c55621c67ed1` em `msg_56b8cff88ab9`.

Reavaliação Anthropic de REC002 **Não executado**; estado continua revisar, não aprovado.
Diff RAGAS permanece **Bloqueado** conforme entrega anterior; não foi repetido nem
fabricado. Integração/qualidade de E1–E12 não são declaradas prontas. Histórico de falhas
anteriores, T017, revisão parcial e baseline/calibração permanecem preservados.
