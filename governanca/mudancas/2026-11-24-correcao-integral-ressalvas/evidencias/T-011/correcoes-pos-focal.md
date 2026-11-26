# Correções após a única focal T-011

Resultado preservado em pytest.txt: 35 passed / 6 failed, 6,35 s. Quatro falhas de citação/legado e uma do caminho ready ocorreram nas consultas de mapas/entradas com inferência ambígua de FROM no SQLAlchemy. Uma falha recebeu erro de sequência/hash em vez da categoria de revisão obsoleta esperada. Casos não foram alterados.

As consultas novas usam agora árvore FROM e ON explícitos; a revisão/ordem dos eventos é classificada separadamente da identidade/hash. Status desta correção: Não executado; nenhum complemento foi executado sem autorização. Migração, restrições/triggers, CAS concorrente e histórico permaneceram como no código da primeira prova. Aguardar autorização do principal para repetir somente os seis caminhos falhos, sem repetir os outros 35 nem uma suíte cara.

Complemento autorizado pelo principal em msg_735056d6d974: Passou, 6 passed/35 deselected, 1,22 s. Ver pytest-complemento.txt. Registro anterior Não executado é histórico anterior à autorização; não foi apagado.
