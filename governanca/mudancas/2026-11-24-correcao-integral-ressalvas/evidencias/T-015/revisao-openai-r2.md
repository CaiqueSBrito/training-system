# Revisão independente OpenAI — T-015, rodada 2

Autor: Google. Revisor: sessão principal OpenAI. Veredito: **aprovado no escopo do runner e dos achados R1/R2**, condicionado à prova real de calibração e à integração já previstas; não é G4 nem aprovação antecipada dos scores.

Inspeção dos snapshots `52BF`, `F32D`, `F90A` de evals/runner_ressalvas.py, relatório de implementação e complementos focais preservados. Não reexecutou testes nem APIs nesta revisão.

- R1: cobertura determinística agora reprova; diff reprovado altera status/exit; sandbox real isolada limpa recursos inclusive em erro parcial e reporta falha de limpeza; scores exigem domínio finito [0,1]; calibração é executável e a baseline exige relatório compatível.
- R2: contexto negativo é enviado com bytes originais sem pista de resposta, usando seleção determinística; receita/limiares/prompt da métrica e versão RAGAS são vinculados nos manifestos e comparados. Regressões consumer-visible exercitadas nos complementos, incluindo os dois casos corrigidos que passaram em 106,20 s; histórico de falhas dos testes permanece preservado.
- A presença de approval_ref não é fronteira de segurança contra quem edita arquivos deliberadamente; documenta autorização do operador. A revisão não atribui a metadados uma assinatura criptográfica de autoria.
- Dataset, protocolo e limiares foram aprovados pelo humano em aprovacao-calibracao.md. A execução real pode avançar: primeiro calibração; só se Passou, primeira baseline do escopo realmente disponível. Não há baseline histórica e Faithfulness da geração permanece Não se aplica com motivo, pois E8 não existe neste corte.

Nenhum achado R1/R2 aberto no escopo revisado. Qualquer falha da prova real deve permanecer Falhou/Bloqueado com diagnóstico sanitizado; não alterar limiares/prompt/modelo para ajustar o resultado. A prova integrada T-017 ainda não foi executada. A determinação da causa histórica sem traceback continua Bloqueado por decisão humana.
