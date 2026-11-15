# Importação de projeto a partir do pacote de exportação — backend (HOR-099)

Arquivo morto verificado: **sem tabelas, sem migração**. O pacote gerado por "Exportar pacote" (manifest SHA-256, vídeo, legendas, roteiro, QC, licenças e revisão originais) é verificado por completo, copiado para `<pasta de dados>/imports/<id novo>/` e consultado como histórico. Não vira produção: não herda aprovação, não é reexportável nem publicável (`publishable=false`, `approved=false` sempre).

| Rota | Função |
|---|---|
| `POST /v1/imports` (`Idempotency-Key`, `{"path": "<pacote.zip>"}`) | 201 criado; 200 repetição idempotente; 409 `already_imported` (com `existing_id`) se o mesmo manifesto já foi importado; 422 pacote/caminho recusado |
| `GET /v1/imports` · `GET /v1/imports/{id}` | lista e detalhe (roteiro, QC, licenças e revisão originais como histórico) |
| `GET /v1/imports/{id}/video` · `/subtitles` | fluxo do arquivo confinado (autenticado) |

Verificação (nada é escrito antes de terminar): só nomes da lista fixa da exportação (sem `..`, barras, `:`, duplicatas por maiúsculas, links, itens que não são arquivo comum, criptografia), no máximo 12 membros e 8 GiB, razão de compressão ≤ 200× fora do MP4, soma dos tamanhos comprimidos ≤ tamanho do arquivo (sem sobreposição), manifesto `schema_version=1` com `publishable=false`, ids válidos, conjunto de arquivos igual ao do manifesto, SHA-256 e tamanho de cada membro em fluxo (para de ler se exceder o declarado), MP4 (`ftyp`), imagens, UTF-8, roteiro no contrato e QC no contrato. Depois, a cópia reconfere os hashes e a pasta é promovida com troca atômica; falha apaga a preparação. O `import.json` não guarda o caminho do pacote. Leituras posteriores recusam (409) arquivo adulterado ou com link.

Limites: o pacote não traz áudio, cenas nem evidências, então um importado não pode ser re-renderizado; as licenças originais ficam apenas como histórico (não viram proveniência efetiva). A interface fica para depois.
