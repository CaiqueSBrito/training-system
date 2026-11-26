# Sistemas Distribuídos e Bancos de Dados Relacionais

Em sistemas gerenciadores de bancos de dados relacionais (SGBDs), uma transação é uma unidade lógica de trabalho que preserva as propriedades ACID: Atomicidade, Consistência, Isolamento e Durabilidade. O controle de concorrência por múltiplas versões (MVCC) permite que leitores não bloqueiem escritores, mantendo snapshots consistentes.

## Isolamento, Locks e Transações Curtas

Manter uma transação aberta com BEGIN durante operações de rede externas ou processamento intensivo de CPU retém locks e conexões do pool, degradando a vazão e aumentando o risco de contenção. Protocolos duráveis executam reservas curtas com compare-and-swap (CAS), liberam a conexão durante chamadas remotas e confirmam o avanço em nova transação curta validada por geração monotônica (fencing token).

### Integridade Referencial e Trilhas Append-Only

Tabelas de auditoria imutáveis utilizam restrições CHECK, chaves estrangeiras compostas e triggers BEFORE UPDATE, DELETE ou TRUNCATE para impedir alteração histórica. Em programação Python, o verificador de tipos estático (`type checker: mypy`) e o treinamento de modelos (`train a classifier`) analisam código e dados sem modificar o estado transacional do banco.

# Índices Vetoriais Derivados e Reconciliação

Quando um índice vetorial externo como o Qdrant armazena embeddings ao lado de metadados de filtragem, não existe commit atômico distribuído (2PC) nativo entre o banco relacional e o motor de busca vetorial.

## Autoridade da Linhagem e Reconciliação Idempotente

O banco relacional (PostgreSQL) atua como fonte de verdade autoritativa das versões e decisões de segurança, enquanto o índice vetorial é uma projeção derivada reparável. Antes de devolver qualquer trecho recuperado ao consumidor, o sistema revalida no PostgreSQL se a versão está publicada (`ready`), se o dono coincide e se a decisão efetiva vigente na maior revisão autoriza o chunk.
