# Teste primeiro

Vale para tarefa que implementa requisito novo ou modificado (trilhas Padrão e LLM).

1. **Casos antes do código.** Quem vai implementar escreve, a partir dos cenários do requisito, os testes (pytest) e, na trilha LLM, os casos de avaliação (dataset em `evals/`). Os testes falham porque o comportamento ainda não existe — e falham pelo motivo certo.
2. **Gate G3.** O humano aprova os casos. Registro na tarefa: quais arquivos, e o `sha256` de cada um no momento da aprovação.
3. **Implementar sem tocar nos casos.** Caso aprovado errado → parar e pedir ao humano; nunca ajustar o teste para passar.
4. **Provar.** Todos os casos aprovados passam; resultado anexado como evidência.

Dublês (`unittest.mock`, fixtures) só dentro de arquivos de teste e só para o que é externo ao código sob teste (API de modelo, rede). Teste que só confere que um mock foi chamado não prova requisito.
