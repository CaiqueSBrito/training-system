Regras duras do Training System (cópia de `AGENTS.md` para subagentes, que não recebem arquivos `AGENTS.md`):

1. Leia `AGENTS.md` e `governanca/pipeline.md` antes de agir.
2. Proibido mock, stub, dado fixo, resposta fake ou placeholder em código de aplicação; falta algo real → pare e reporte. Exceção: dublês dentro de arquivos de teste.
3. Só o humano aprova gates (G1 proposta, G2 permissão por tarefa, G3 casos, G4 aceite). Sem permissão registrada na tarefa: só leitura e planejamento.
4. Quem constrói não revisa; o revisor é de outro provedor.
5. Nenhuma dependência nova (biblioteca, serviço, modelo de IA) sem o humano.
6. Evidência com status exato: Passou, Falhou, Não executado, Bloqueado, Não se aplica (com motivo).
7. `uv run validar-governanca` verde antes de commit; commit, push, merge e deploy só com o humano.
