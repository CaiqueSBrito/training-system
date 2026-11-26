# Revisão rodada 2 — T-006/T-007/T-008 (anthropic)
Execuções: pytest lineage+ingestion+llm 5x: 4x 136 passed; 1x (run3) teste #94 `test_ING_INJ_002_real_judge_only_suspects` = F e processo terminou com SIGTERM (143) antes do resumo (origem do sinal desconhecida). validar-governanca: Passou. Sem segredo versionado (.env ignorado; varredura AIza/sk- vazia). Sem mock fora de testes.

## T-006 — aprovado com ressalvas
1 resolvido: 0002_document_immutable.py:9-19 (triggers UPDATE/DELETE/TRUNCATE); teste test_lineage.py:224-234.
2 resolvido: test_lineage.py:237-242 (compare_metadata). Ressalva: compare_metadata não compara CHECK nem triggers; downgrade não verifica remoção de reject_document_version_mutation; teste novo sem ID ING-LIN-001 no nome (só docstring do módulo). Nada afrouxado.

## T-007 — aprovado com ressalvas
1 resolvido: core.py:186 (sobreposição só com >1 unidade); teste test_ingestion.py:215. Verificado por propriedade (3000 docs aleatórios): teto, offsets e nenhum chunk contido no seguinte.
2 resolvido: core.py:142-147 corta em espaço/fim de frase; palavra > limite recusada (core.py:147). Ressalva: texto sem espaços (URL/base64 longo, CJK) agora falha a ingestão inteira.
3 parcialmente: fixture DOCX com Heading2 e caminho equivalente nos 3 formatos (test_ingestion.py:230) ok; política de quebrar em toda troca de section_path (core.py:176) não mudou — chunks pequenos continuam; precisa decisão do humano (aceitar como ressalva ou corrigir).
Teste "adaptado" (two_large_paragraphs, :189) ainda exige teto e sobreposição; versão antiga não verificável (arquivo não versionado).

## T-008 — reprovado
- REGRESSÃO (A3): high_precision_reason marca independentemente do juiz (indexing.py:68-69) e _CHECKER (indexing.py:19-21) casa texto jurídico/CS comum: "O juiz, ao analisar...", "dirigida ao juiz da vara", "recurso para o juiz", "spell checker,"; _ROLE (:18) casa "Model: ..."/"User: ..." no início de linha. Testado: todos marcados. Chunk imutável + reaproveitamento por (dono, sha256) tornam a exclusão permanente mesmo após corrigir a heurística. injection_reason grava motivo do juiz que disse "seguro". Corrigir: restringir alto-precisão a delimitadores de turno/controle inequívocos (ou deixar "dirigido ao verificador" só como suspeito) e adicionar negativos jurídicos ao corpus.
- Teste test_ING_INJ_001_ING_EMB_001_ING_IDX_001_real_end_to_end (test_indexing.py:167) grava na coleção REAL gemini-embedding-2@3072@fmt-v1 e não limpa: 13 pontos órfãos de owner integration-* lá agora. Corrigir: apagar por document_version_id no finally.
- A resolvido (llm/__init__.py:131-143); B resolvido (:49, :75); C resolvido (indexing.py:77, teste :252); D/A1 resolvido no escopo (corpus :210-234) — nit: "a partir de agora" comum em PT gera chamadas ao juiz; E resolvido (core.py:262-267, Postgres só grava após I4-I6; reconciliação é ressalva aceita); F resolvido (core.py:88-89, 255; teste :285).
- A2 resolvido (indexing.py:63). A4 resolvido. A5 resolvido em grande parte (from None, mensagem fixa); ressalva: suppress_debug_info não impede LITELLM_LOG=DEBUG de logar o chunk.

## Teste instável
Provável: test_ING_INJ_002_real_judge_only_suspects (falhou na minha run3; candidato irmão: real_end_to_end e test_ING_INJ_001_real_judge). 15 chamadas diretas ao juiz real com o mesmo valor: 15/15 injection=True — veredito estável; causa provável é erro transitório da API (429/5xx/timeout) sem retry, mascarado por ValueError('Falha na verificação de injeção') from None. Para determinizar sem afrouxar: (1) retry com backoff só para erros de transporte (litellm num_retries / tenacity já existente? sem dependência nova → laço simples) no cliente, contando chamadas lógicas, não tentativas; (2) preservar a categoria do erro (ex.: atributo kind='transport'|'invalid_output') sem conteúdo bruto, para o teste distinguir; (3) manter asserts calls==3 e flagged==3; a contagem determinística já é provada pelo teste com juiz dublê (:22). Opcional: marcar testes de API real com marcador próprio para rodar separados.
