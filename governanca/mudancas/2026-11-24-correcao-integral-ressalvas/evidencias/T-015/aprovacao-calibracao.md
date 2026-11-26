# Aprovação humana da calibração inicial

Resposta literal ao formulário:

```json
{"calibracao":"aprovo_dataset_protocolo_limiares_iniciais","openrouter":"configurei_env","historico":"manter_causa_historica_bloqueada"}
```

Dataset autorizado: `ressalvas-recuperacao-segura-v1`, 3 documentos educacionais sem dados pessoais e 6 perguntas, com 6 controles negativos por deslocamento de contexto sem rótulo revelado ao juiz. Manifesto dataset SHA-256 `dc09921247fa7e1c92eede508437ecc0d065a21bc14b7461912818628e429a27`.

| Critério | Média mínima | Piso individual |
| --- | --- | --- |
| context_precision_with_reference | 0.80 | 0.65 |
| context_recall | 0.85 | 0.75 |
| cobertura determinística de evidência/proveniência | 1.00 | 1.00 |

Controles negativos: score zero; queda máxima em comparação futura: 0.02 sem violar mínimos. Calibração real autorizada após aprovação do código corrigido; primeira baseline somente se os controles passarem. Sem comparação histórica fabricada, sem alegar geração avaliada ou certificação de produção com este dataset inicial.

Humano declarou configuração de `OPENROUTER_API_KEY` no `.env`. A presença será verificada sem exibir valor. Não há autorização para trocar juiz, modelo, prompt ou limiares após observar scores para fazer a avaliação passar.

O humano decidiu **manter a determinação da causa histórica bloqueada**. A instrumentação nova não é atribuída retroativamente à falha antiga; os artefatos sem traceback ficam preservados. Este documento não é G4 nem autorização para commit, deploy ou exclusão de dados existentes.

## Revisão administrativa sob autonomia delegada — `dataset_version: 1.0.1`

- Fonte da autonomia humana contínua registrada em `msg_a9eee96626ab` e autorizada para este ajuste pelo principal em `msg_d3ddf4ce61fd`: **"De agora em diante até eu falar que pare, você não precisa de minha autorização para nada, continue de forma autônoma"**.
- Histórico preservado de `dataset_version: 1.0.0` (`sha256: dc09921247fa7e1c92eede508437ecc0d065a21bc14b7461912818628e429a27`): na execução `ragas-calib-a4390ee523d3` (`evidencias/T-015/calibracao-real.json`, status `Falhou`), o juiz `openrouter/openai/gpt-6.1-sol` pontuou `1.00` nos 6 controles positivos e `0.00` nos 6 controles negativos deslocados sem pista, porém `EVAL-RES-006` reprovou no gate determinístico de substring porque `expected_evidence_substrings` trazia `"nunca expor chaves de API"` (transcrito da paráfrase de `reference`), enquanto o texto literal de `docs/seguranca_ia_educacional.md` (linha 15) é `"não podem expor chaves de API"`.
- Correção aplicada exclusivamente na substring literal de `EVAL-RES-006` (`"nunca expor chaves de API"` → `"não podem expor chaves de API"`), sem alterar documentos, `reference`, modelo, prompt ou limiares, incrementando `dataset_version` para **`1.0.1`** com novo manifesto SHA-256 **`8f0d3582ffca643727089879959982f25c87387b56db93fb2a95f1ce339bae8d`** e adicionando validação determinística em `load_dataset_spec` que exige que toda `expected_evidence_substring` exista literalmente no documento fonte.
- Saídas versionadas para `v1.0.1`: `evidencias/T-015/calibracao-real-v1.0.1.json` e `evidencias/T-015/baseline-inicial-v1.0.1.json`.

