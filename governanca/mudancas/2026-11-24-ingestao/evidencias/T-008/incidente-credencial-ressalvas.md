# Incidente sanitizado — chave exposta

Categoria: chave exposta. Durante a primeira prova focal, a representação de argumento do pytest incluiu a credencial do `.env` ao exibir o mapping completo passado à guarda. Houve exposição no artefato local `pytest-slice-ressalvas.txt` e no histórico da ferramenta ao inspecioná-lo. Nenhum valor, prefixo ou fingerprint da credencial é reproduzido neste registro.

O artefato foi sanitizado antes de qualquer commit, preservando status e resumo 60 passed / 22 failed / 8 errors. O coordenador foi avisado e orientou o humano a revogar/substituir GEMINI_API_KEY no `.env`.

Correção: a guarda não recebe mapping, settings nem ambiente como argumento; retorna apenas categoria e nome constante do controle recusado. O complemento usa traceback curto e IDs de parâmetros fixos, sem valores de ambiente. Isso não promete controlar um chamador que deliberadamente imprime variáveis locais ou segredos. Artefatos atuais desta slice não devem conter o valor exposto; não leia/reimprima a captura original sensível do histórico.

Atualização (2026-11-24): o humano confirmou literalmente no formulário a rotação (`rotacao=revoguei_antiga_e_substitui_env`). Não observamos diretamente o console do provedor e portanto não atestamos independentemente a revogação externa, registrando apenas a confirmação humana literal e a presença sanitizada da nova credencial no `.env` (`has_api_key=True`, sem exibir valores, prefixos ou hashes). Após a revisão e aprovação local da guarda de logging (`ctx_d072ae09d0c3`), o corpus de 9 ataques (`R3_ATTACKS`) + 11 controles negativos (`R3_LEGITIMATE`) foi executado em tentativa única por caso (`pytest-juiz-real-ressalvas.txt`, `juiz-real-ressalvas.json`) sem reabrir artefatos brutos antigos.
