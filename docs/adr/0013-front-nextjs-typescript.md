# ADR-0013 — Front com Next.js, React e TypeScript

Estado: **Aceita** (humano, 2026-11-24)

> A premissa de que o E11 escapa o conteúdo no servidor foi substituída pela [ADR-0018](0018-saida-texto-puro.md): o conteúdo chega como texto puro e o front o renderiza só como texto (`textContent`/texto do React), o que mantém a regra do item 3.

## Contexto

A `apps/web` é a interface do chat: envia a mensagem, mostra o progresso por etapa vindo por SSE (ADR-0011) e renderiza flashcards, questionários e as fontes de cada item. Ela só fala com a `apps/api` (ADR-0010).

Restrições:

- Os dados que ela exibe seguem os contratos de `packages/contracts`, publicados no OpenAPI da API (ADR-0014). Um tipo escrito à mão no front divergiria do contrato em silêncio.
- O conteúdo dos itens é gerado por LLM a partir de documentos do usuário. HTML, markdown ou script dentro de um card é vetor de XSS (OWASP LLM05; arquitetura §5). A E11 já escapa o conteúdo no servidor (RAG-SAI-001); o front não pode desfazer essa proteção.
- O papel `frontend` (google) trabalha só em `apps/web`, fora do workspace Python.

## Decisão

1. **Next.js** com **React** e **TypeScript** em modo estrito.
2. **Tipos gerados do OpenAPI** publicado pela `apps/api` (ADR-0014). Nenhum tipo de contrato é escrito à mão no front; mudar um contrato regenera os tipos, e a divergência aparece como erro de compilação.
3. **Conteúdo gerado sempre renderizado escapado** (RAG-SAI-001): o texto dos itens e das fontes entra como texto do React, nunca por `dangerouslySetInnerHTML` nem por renderizador de markdown/HTML sem sanitização. Se um dia o card precisar de formatação, ela vem de um subconjunto fechado e sanitizado, decidido em mudança própria.
4. O progresso do chat é lido como stream SSE do endpoint do chat; o evento final substitui o estado da mensagem pela `ChatResponse`.
5. O front não chama LLM, Qdrant nem banco: todo dado passa pela `apps/api`.

## Alternativas consideradas

| Opção | Por que não |
| --- | --- |
| React com Vite (SPA pura) | atende, mas sem roteamento, renderização no servidor e convenções de projeto prontas; o projeto teria de montar e manter essas peças |
| SvelteKit / Nuxt (Vue) | equivalentes técnicos com ecossistema e oferta de componentes menores que o do React |
| Interface server-side em Python (Jinja + HTMX, Streamlit, Gradio) | mistura o front no workspace Python e nas pastas do back; Streamlit/Gradio limitam a experiência de flashcards e questionários; perde-se a geração de tipos do OpenAPI |
| Tipos TypeScript escritos à mão | divergem do contrato sem aviso; duplicam o trabalho do papel `arquiteto` |

## Consequências

- O front tem toolchain própria (Node, gerenciador de pacotes, lockfile) em `apps/web`, fora do uv workspace. Node, Next.js, React e o gerador de tipos são dependências novas e entram com a mudança da `apps/web`, com aprovação do humano; a regra da carência de 7 dias (ADR-0007) vale também para o lockfile do front.
- A geração de tipos depende do OpenAPI da API: a `apps/web` só começa a consumir contratos depois que a `apps/api` publicar o OpenAPI.
- A revisão de segurança do front confere a regra do item 3 (nenhum HTML bruto vindo do conteúdo gerado).
