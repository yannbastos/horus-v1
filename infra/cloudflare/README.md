# Canal privado de atualização Hórus (HOR-163)

Node 24+; execute em `infra/cloudflare`. Nada foi criado/publicado na conta.
Local: `npm ci`, `npm test`, `npm run typecheck`, `npm run dev` (somente loopback).
Os testes usam R2/KV Miniflare e uma assinatura pública de fixture, sem chave privada.

## Ações do usuário, após autorização de publicação

No painel, mantenha Workers Free e R2 Standard privado (sem r2.dev/domínio público).
R2 requer habilitação pelo titular; não ative plano pago nem exceda a franquia gratuita.
Autentique com `npx wrangler login` ou injete `CLOUDFLARE_API_TOKEN` e
`CLOUDFLARE_ACCOUNT_ID` no ambiente (nunca em arquivo versionado ou linha de comando).
Comandos de provisionamento, a executar pelo usuário:
```powershell
npx wrangler r2 bucket create horus-updates-test
npx wrangler r2 bucket create horus-updates-prod
npx wrangler kv namespace create INSTALLATIONS --env test
npx wrangler kv namespace create INSTALLATIONS --env production
```
Substitua os IDs fictícios `000...001`/`000...002` em `wrangler.toml` pelos retornados,
cada um no seu ambiente; confirme os buckets/IDs antes do deploy:
```powershell
npx wrangler deploy --env test
npx wrangler deploy --env production
```
Cada ambiente cria seu Worker `workers.dev` HTTPS; rotas são `/<canal>/latest.json`
e `/<canal>/releases/<versão>/<arquivo>`. O Worker não possui endpoint administrativo.
Admin: credencial KV Read/Edit + R2 Object Read/Write apenas dos recursos respectivos;
login/deploy/provisionamento têm permissões próprias, não repasse essas ao app/CI de release.
```powershell
npm run admin -- issue test "Estação de ensaio" --remote
npm run admin -- list test --remote
npm run admin -- revoke test ID_SHA256_DA_INSTALACAO --remote
```
Sem `--remote`, admin usa apenas armazenamento local do Wrangler. Token aleatório de
256 bits aparece UMA vez em terminal interativo (CI/redirecionamento recusados); guarde no cofre Windows.
Só hash/rótulo/canal/criação/revogado ficam no KV. Token novo pode levar ~60 s para valer.
Revogação é imediata para novas requisições: primeiro R2 `<canal>/revoked/<hash>`,
depois flag KV; falha na consulta R2 nega acesso. Nunca apague marcadores de revogação.
Downloads já iniciados não podem ser recolhidos; a listagem da flag KV é eventual.

## Publicação manual/CI (ainda depende de autorização)

Envie primeiro EXE, `.exe.sig` Tauri e `release.json` ao prefixo do canal/versionamento;
use `npx wrangler r2 object put BUCKET/CHAVE --file ARQUIVO --remote --env CANAL`.
`release.json`: `{version,sha256,size,notes,pub_date,commit}` (hash minúsculo, bytes,
commit Git de 40 hex); notas/data iguais ao candidato. Não sobrescreva/apague releases.
No CI, injete `CLOUDFLARE_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
(credencial S3 limitada ao bucket do canal); chave pública vem de arquivo público.
```powershell
npm run validate-release -- --channel test --version 1.2.3-test.1 --tag v1.2.3-test.1 --candidate candidate.json --pubkey test.pub --base-url https://horus-updates-test.SUBDOMINIO.workers.dev
npm run publish-manifest -- --channel test --version 1.2.3-test.1 --tag v1.2.3-test.1 --candidate candidate.json --pubkey test.pub --base-url https://horus-updates-test.SUBDOMINIO.workers.dev
```
`--directory PASTA` simula R2 em disco, sem rede. Publicar revalida tudo, recusa versão
duplicada/regressiva e grava `latest.json` por último, num PUT condicional atômico (ETag).
Produção aceita `vX.Y.Z`; teste `vX.Y.Z-test.N`; CI confere os três manifests do app.
Chaves test/production distintas; custódia/geração da chave de produção segue pendente.
Revogação acrescenta **1 HEAD R2 por requisição autenticada**; manifesto usa ainda 1 GET
+ 3 HEAD; EXE usa 1 HEAD + 1 GET. Limite local: 120 requisições/IP/min, não cota global.
Confira franquias/uso no painel antes de publicar: [R2](https://developers.cloudflare.com/r2/pricing/).
