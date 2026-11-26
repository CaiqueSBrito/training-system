# Delta — Correção integral das ressalvas

Specs de destino ao arquivar: ingestao (ING), seguranca (SEC), observabilidade (OBS), avaliacao (EVAL). Capacidades novas são propostas, não specs atuais. IDs não repetem deltas ativos.

## ADICIONA

### ING-MIG-001 — Migração intermediária
QUANDO um banco estiver na revisão 0001 ou 0002, o sistema DEVE oferecer upgrade incremental até a revisão nova sem recriar tabelas nem alterar registros imutáveis.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Upgrade de cada revisão preserva IDs, hashes e quantidades.

### ING-MIG-002 — Integridade e downgrade
QUANDO a migração for validada em banco descartável real, o sistema DEVE demonstrar CHECKs, FKs, triggers de UPDATE/DELETE/TRUNCATE e downgrade suportado sem enfraquecer proteções anteriores.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: DML inválida falha com SQLSTATE esperado; downgrade destrutivo recusa sem autorização.

### ING-PRO-001 — Citações por spans
QUANDO uma citação selecionar um intervalo do chunk, o sistema DEVE resolver todos os spans canônicos intersectados com suas páginas e seções reais sem propagar a página inicial ao trecho inteiro.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Página 2→3 resolve trecho só da 3 e trecho cruzado corretamente; DOCX/MD usa null.

### ING-CAN-001 — Texto canônico persistido
QUANDO um chunk ou citação for persistido, o sistema DEVE validar intervalo semiaberto e conteúdo contra texto canônico persistido identificado por versão e SHA-256.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Hash, texto ou offset divergentes são rejeitados; fonte antiga ausente fica insuficiente.

### ING-EST-001 — Agrupamento estrutural fiel
QUANDO chunks forem formados, o sistema DEVE respeitar H1 e teto sem impor corte em toda H2/H3 e registrar spans e caminhos de todas as subseções cobertas.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: H2/H3 pequenos cabem juntos; H1 distinto não se mistura.

### ING-UNI-001 — Fragmentação Unicode
QUANDO parágrafo contínuo exceder o orçamento, o sistema DEVE fragmentar URLs, base64 e CJK por codepoints sem recusa por falta de espaços, perda ou duplicação fora da sobreposição declarada.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Reconstrução por intervalos únicos preserva UTF-8 inclusive emoji e marcas combinantes.

### ING-TXN-001 — Reserva durável
QUANDO uma ingestão concorrente iniciar, o sistema DEVE reservar duravelmente a chave de deduplicação, fazer parse e operações remotas fora de transação aberta e publicar somente trabalho do detentor vigente com recuperação após falha.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Dois workers e quedas após reserva/persistência/upsert convergem; fencing impede publicação do vencido.

### ING-PAR-001 — Conversor seguro
QUANDO múltiplos parses ocorrerem no mesmo processo, o sistema DEVE reutilizar o conversor Docling sob exclusão mútua por instância sem compartilhar estado de documentos concorrentes.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Dois documentos simultâneos não contaminam texto/spans; uma inicialização por instância viva.

### SEC-ATQ-001 — Nove ataques reais
QUANDO os nove casos R3_ATTACKS forem avaliados, o sistema DEVE registrar por caso veredito do juiz real aprovado, decisão efetiva e ausência entre candidatos recuperáveis.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Nove IDs A01–A09 com chamadas reais; dublê não comprova juiz.

### OBS-DIA-001 — Diagnóstico sanitizado
QUANDO uma chamada remota falhar, o sistema DEVE registrar categoria sanitizada, correlação e timestamp sem conteúdo/segredo e sem atribuir causa histórica não comprovada.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Provider e invalid_output distinguíveis; captura histórica ausente fica Bloqueado.

### EVAL-INT-001 — Prova final integrada
QUANDO todas as correções e pré-requisitos estiverem disponíveis e aprovados, o sistema DEVE produzir uma execução integrada final identificada com evidências por requisito e diff RAGAS contra baseline compatível.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Uma execução consolidada, sem repetir suíte procurando verde; falha exige decisão e plano focal explícito.

### ING-AUD-001 — Decisão append-only
QUANDO um chunk for triado, o sistema DEVE persistir eventos imutáveis correlacionando heurística, juiz quando acionado e decisão inclusive liberação, versões de configuração/prompt, motivo sanitizado, identificadores e timestamp.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Suspeito liberado conserva todas as etapas; falha do juiz não inventa veredito.

### ING-REP-001 — Reconciliação efetiva
QUANDO um ponto estiver ausente ou divergente, o sistema DEVE reconciliá-lo idempotentemente a partir da linhagem, título/contexto persistidos, embedding_key e decisões efetivas filtradas pelo dono sem expor chunk rejeitado.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Reparo repetido converge; queda e bloqueio concorrente não vazam candidato.

### ING-LEG-001 — Reclassificação conservadora
QUANDO política nova reclassificar versão antiga, o sistema DEVE acrescentar eventos sem atualizar chunk/document_version e separar inventário dry-run de remoção de fixture comprovada especificamente autorizada.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Hashes históricos iguais; prefixo ou anti-join não autorizam excluir ponto.

### SEC-LOG-001 — DEBUG impedido
QUANDO logging LiteLLM inseguro for configurado ou ativado, o sistema DEVE impedir chamadas com conteúdo sob DEBUG e bloquear vazamento em logs, exceções, stdout e stderr.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: DEBUG de ambiente e ativação programática em subprocesso não vazam sentinela nem chave.

### ING-OPS-001 — Coleção estrita e lotes
QUANDO índice for aberto ou verificado, o sistema DEVE exigir coleção exatamente igual à embedding_key sem allow_test_collection no produto, índices payload e consultas em lotes limitados filtradas pelo dono.

- Cenário: Dado o contexto descrito, Quando o caso for executado, Então: Coleção divergente recusa; esquema payload e todos os lotes verificados sem N chamadas por N chunks.

## MODIFICA

Nenhum. As adições refinam os requisitos existentes sem enfraquecê-los e preservam obrigações da ingestão ainda aberta.

## REMOVE

Nenhum.
