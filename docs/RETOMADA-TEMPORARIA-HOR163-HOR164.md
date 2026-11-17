# Retomada temporária — HOR-163 e HOR-164 (17/11/2026)

Documento TEMPORÁRIO (apague ao concluir). O trabalho incompleto está em branches, NÃO na `main`:

- `yannbastos/pg-integration` — migração SQLite → Supabase PostgreSQL (HOR-164), incompleta; o guia completo está em `docs/handoff-temporario-2026-11-17/README.md` DESTA branch (estado, 5 defeitos abertos, passos para fechar, decisões pendentes, relatórios e especificações dos workers).
- `yannbastos/sb-porte-a` — porte de testes legados, grupo A (WIP).

Como começar na outra máquina: `git fetch origin && git checkout yannbastos/pg-integration` e leia o README citado; depois `uv sync --project backend --frozen --all-extras`. Não faça merge da integração na `main` antes de seguir a seção "Para fechar o HOR-164". HOR-163 (instalador e atualizações) está integrado na `main`; faltam provisionamento Cloudflare, chaves e testes reais (ver o README).

Regras do usuário: não tocar o banco SQLite operacional nem a instalação real; sem migrar dados reais; sem fallback silencioso para SQLite; governança em `docs/AGENT-GOVERNANCE.md`.
