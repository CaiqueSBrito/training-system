# ADR-0018 — Saída do conteúdo gerado em texto puro, renderizada por `textContent`

Estado: **Aceita** (humano, 2026-11-26, decisão A4). Substitui, na ADR-0004, só o trecho de escape do E11 (Decisão, item 5: "sanitização e escape para a web").

## Contexto

A ADR-0004 (Decisão, item 5) definiu o E11 como "sanitização e escape para a web": o servidor escapava HTML do conteúdo gerado antes da entrega, contra XSS por HTML, markdown ou script dentro dos cards (OWASP LLM05). A arquitetura §5 e §10.1 e a ADR-0013 (Contexto) repetiram essa premissa.

Com a `apps/api` (mudança `2026-11-26-apps-api-autenticacao`), o mesmo conteúdo passou a sair por duas rotas:

- o evento final do SSE do chat (ADR-0011), com os itens já escapados pelo E11;
- a consulta de linhagem de item e de turno (OBS-LIN-001), que lê do banco a evidência e o original literais, sem escape.

A revisão de segurança da T-025 (achado A4) mostrou que isso deixa o escape **inconsistente**: o cliente teria de saber, rota a rota e campo a campo, se o texto já veio escapado. Se tratar tudo como escapado e usar `innerHTML`, a rota sem escape vira XSS; se tratar tudo como texto, a rota escapada mostra `&lt;` literal ao aluno, e o escape duplo corrompe conteúdo legítimo (um aluno estudando HTML pede cards com `<div>`). O escape no servidor também não protege um cliente que use `innerHTML` sobre um campo esquecido. A proteção que vale para todas as rotas é o cliente nunca interpretar o conteúdo como HTML.

Na mesma revisão (achado I2), a linhagem do turno devolvia o veredito completo do juiz de entrada (`malicioso`, `categoria`, `motivo`); o `motivo` descreve como a mensagem foi julgada e não precisa chegar ao cliente.

## Decisão

1. **Texto puro em todas as rotas** (RAG-SAI-001, MODIFICA): o conteúdo gerado (frente e verso, enunciado, alternativas, explicação, evidência das citações) sai da API como texto puro, **sem escape HTML** e sem decodificar entidades, igual no evento final do SSE e na consulta de linhagem. HTML literal gerado (`<script>`, `<div>`) chega ao cliente como os caracteres que são.
2. **O E11 remove só controles e invisíveis**: caracteres das categorias Unicode **Cc** (exceto `\n` e `\t`), **Cf** e **Cs**. Depois valida o item contra o schema de `packages/contracts`; um item que viola o schema é omitido, como antes. A regra fica num único lugar (`training_contracts.rendering`), usada pelo E11 e pela entrega.
3. **Todo cliente renderiza como texto**: o conteúdo gerado entra no DOM por `textContent` (ou o equivalente, como texto do React), **nunca** por `innerHTML`, `dangerouslySetInnerHTML` nem renderizador de markdown/HTML. Vale para itens, fontes, mensagens de recusa e erro. Formatação no card, se um dia for necessária, vem de um subconjunto fechado e sanitizado, decidido em mudança própria (como já prevê a ADR-0013).
4. **Linhagem do turno expõe só a categoria do veredito de entrada** (decisão I2): a consulta de turno devolve `input_verdict` como `{categoria}` ou `null`; `malicioso` e `motivo` não saem pela API. O registro persistido continua íntegro.
5. O framing do SSE continua serializando JSON com escape ASCII (`ensure_ascii`), para que o texto puro não injete quadros no stream; isso é codificação de transporte, não escape HTML do conteúdo.

O resto da ADR-0004 (juízes, groundedness, injeção indireta, higiene de entrada) continua valendo sem mudança.

## Alternativas consideradas

| Opção | Por que não |
| --- | --- |
| Manter o escape HTML no E11 e também escapar a linhagem | o cliente continua dependendo de o servidor escapar todos os campos de todas as rotas, inclusive as futuras; o escape duplo corrompe conteúdo legítimo; um cliente com `textContent` mostra `&lt;` literal |
| Escapar só na fronteira HTTP (serializador único da API) | resolve a inconsistência entre rotas, mas amarra o formato à web: CLI, exportação e avaliação recebem entidades HTML; e ainda protege menos que o cliente nunca interpretar HTML |
| Sanitizar HTML com lista de tags permitidas no servidor | traz dependência nova e superfície de bypass conhecida; não há requisito de formatação que justifique |

## Consequências

- **Dados anteriores à A4**: turnos gravados antes da decisão guardam a resposta com HTML escapado; a linhagem devolve esses dados como estão, e um cliente com `textContent` mostra `&lt;` literal. Existem só em ambientes de desenvolvimento; não há migração, e qualquer migração passa pelo humano.
- **`apps/web` (ADR-0013)** segue esta regra: a Decisão, item 3, da ADR-0013 (texto do React, sem `dangerouslySetInnerHTML`) já é compatível. A frase do Contexto da ADR-0013 segundo a qual "a E11 já escapa o conteúdo no servidor" deixa de valer; a proteção é a renderização como texto no cliente.
- A arquitetura §5 (E11) e §10.1 (`apps/web`) passam a descrever texto puro e `textContent`; a spec viva (`RAG-SAI-001`) é atualizada pelo arquivamento do delta da mudança.
- Todo cliente novo (CLI, exportação, outra interface) herda a obrigação do item 3; revisão de segurança de cliente confere ausência de `innerHTML` sobre conteúdo gerado.
- Os testes que esperavam escape foram ajustados só pela decisão humana A4 (T-025); os casos que provam HTML literal idêntico nas duas rotas e remoção de controles cobrem esta ADR.
