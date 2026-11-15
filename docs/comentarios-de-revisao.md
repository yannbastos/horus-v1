# Comentários de revisão por timecode e versão (HOR-088, backend)

Comentários de revisão de uma produção, ligados a um instante do vídeo e à **versão** (SHA-256 do vídeo registrado na produção), com trilha de decisão. Contrato aditivo (snapshot em `backend/tests/snapshots/openapi.json`); o endpoint `/review` existente não muda. A interface fica a cargo do frontend; a nova renderização controlada não faz parte deste incremento.

Armazenamento sem migração: um JSON atômico por produção em `<data_dir>/production-comments/<production_id>.json` (temporário + `os.replace`, lock por produção). Arquivo ilegível nunca é sobrescrito: a API responde 409 até o operador restaurá-lo ou movê-lo. Esses arquivos entram no pacote de `local_data_export` como `comments/<production_id>.json` (hash no manifesto) e voltam em `production-comments/` na restauração para pasta nova; o `verify` também recusa, antes de qualquer escrita, comentário com `author_kind` diferente de `human`, produção divergente do nome do arquivo, estrutura ou trilha inválidas. Arquivo ilegível, inválido ou com formato de credencial é excluído do pacote e listado em `excluded`.

| Rota | Descrição |
|---|---|
| `POST /v1/productions/{id}/comments` (`Idempotency-Key`) | `{artifact_sha256, at_seconds (0–86400), text (1–2000), author (1–80)}` → 201 (200 em repetição idempotente). 409 se o hash não for de um vídeo da produção, chave reutilizada com outro corpo ou 500 comentários atingidos |
| `GET /v1/productions/{id}/comments?status=open\|resolved&artifact_sha256=&limit=1–200&after=` | `{items, counts{open,resolved}, next_after}`; ordem por `at_seconds`, `id` |
| `POST .../comments/{cid}/resolve` e `/reopen` (`Idempotency-Key`) | `{author, note≤500}`; 409 se já estiver no estado pedido ou a trilha atingir 50 eventos |

Cada comentário traz `author_kind: "human"` (rótulo informado pelo operador; não há identidade de usuário no produto local) e `trail` append-only (`created`/`resolved`/`reopened`, autor, horário, nota). Não há edição nem remoção. Textos com caracteres de controle e corpos fora do esquema são recusados (422, sem eco do conteúdo); 401 sem token.
