# ADR-0010 — Monólito modular

Estado: **Aceita** (humano, 2026-11-24)

## Contexto

As ADRs 0001–0009 fecharam o pipeline de RAG e a infraestrutura, e o workspace já separa a lógica em pacotes de domínio (`packages/contracts`, `llm`, `lineage`, `ingestion`, `observability`; depois `retrieval` e `generation`) e reserva `apps/api`, `apps/worker` e `apps/web` (ADR-0007). Faltava decidir o estilo arquitetural das aplicações: onde fica a regra de negócio, quantos deploys e quantos bancos.

Restrições que pesam:

- Um único desenvolvedor humano e agentes de provedores diferentes trabalhando em paralelo, cada papel dono de pastas próprias (`governanca/papeis/`).
- A linhagem exige gravação transacional junto com o turno do chat (RAG-SAI-002, ADR-0008); separar os registros em serviços distintos quebraria essa garantia.
- O caminho crítico do chat já tem quatro chamadas de LLM em série (arquitetura §10); saltos de rede extras entre serviços só somam latência.

## Decisão

1. **Monólito modular**: a regra de negócio fica nos pacotes de domínio (`packages/*`). As apps são **adaptadores finos** sobre eles, sem regra de negócio própria:
   - `apps/api` traduz HTTP para chamadas aos pacotes (ADR-0011);
   - `apps/worker` traduz jobs da fila para chamadas aos pacotes (ADR-0012);
   - `apps/web` é a interface e só fala com a `apps/api` (ADR-0013).
2. **Dependência só de app para pacote** (ARQ-APP-001): `apps/*` importa `packages/*`; nenhum módulo de `packages/*` importa `apps/*`. Entre pacotes, a dependência segue a direção já existente (todos dependem de `contracts`; nenhum ciclo).
3. **Um deploy e um banco da aplicação**: API e worker rodam o mesmo código de domínio e usam o mesmo Postgres da aplicação (ADR-0008), que guarda linhagem e fila (ADR-0012). Sem microserviços.
4. Um caso de uso que a API e o worker precisam executar (ex.: ingerir um documento) é uma função de pacote chamada pelos dois, nunca código duplicado nas apps.

## Alternativas consideradas

| Opção | Por que não |
| --- | --- |
| Microserviços (ingestão, recuperação, geração, avaliação como serviços separados) | multiplica deploy, configuração, observabilidade e contratos de rede sem ganho de escala que o volume atual justifique; quebra a transação entre turno e linhagem; soma latência no caminho crítico |
| Monólito sem fronteira de pacotes (toda a lógica dentro de `apps/api`) | o worker teria de importar a API ou duplicar código; mistura as pastas dos papéis e impede o dono de cada pacote de trabalhar e testar isolado |
| Serverless por etapa (uma função por E1–E12) | o reranker local (ADR-0003) e o Docling (ADR-0009) carregam modelos pesados, com partida a frio cara; estado do turno teria de trafegar entre funções |

## Consequências

- A fronteira entre módulos é o import Python, não a rede: o contrato entre pacotes é `packages/contracts` (ADR-0014 trata da fronteira HTTP).
- ARQ-APP-001 é verificável por análise estática dos imports; a verificação entra quando a primeira app existir.
- Escalar uma parte isolada (ex.: mais workers de ingestão) é escalar processos da mesma imagem, não separar serviços. Se uma parte precisar de escala ou ciclo de vida próprios, a extração para serviço é uma ADR nova que substitui esta.
- Os papéis continuam donos das suas pastas; uma app nunca é lugar para regra que deveria estar num pacote.
