# Auditoria offline do Rust/Tauri (HOR-130)

O teste `backend/tests/test_rust_supply_chain.py` usa somente a biblioteca padrão
do Python 3.11+ e arquivos locais; não instala crates nem consulta advisories:

```powershell
python -m unittest discover -s backend/tests -p test_rust_supply_chain.py -v
```

Também é coletado pelo pytest do backend. Exige `Cargo.lock` versionado e
presente, versões exatas, checksums SHA-256 e origem crates.io para todas as
dependências externas, referências transitivas resolvidas e alinhamento das
dependências diretas com os ranges numéricos aprovados no `Cargo.toml`.
A allowlist de dependências/features é intencional: alterações precisam ser
revisadas junto com o teste, sem aceitar automaticamente plugins novos.

A única capability aprovada é `capabilities/main.json`, local à janela `main`,
com `core:default`, `dialog:allow-open` e `dialog:allow-save`. O teste rejeita
capabilities adicionais, remotas ou inline, permissões customizadas e permissões
amplas de shell/fs. Isso protege a configuração declarada, mas não substitui
auditoria do código Rust nem sandbox do sistema operacional.

O manifesto Tauri mantém `features = []` para `tauri` e `tauri-build`: nenhuma
feature opcional adicional é solicitada. As features padrão upstream continuam
ativas (no Tauri 2.12.5: `wry`, `compression`, `common-controls-v6`, `dynamic-acl`,
`x11`, `dbus`; as duas últimas têm efeito nas plataformas correspondentes).
Isso não significa `default-features = false`. Não há plugin shell/fs declarado;
o único plugin direto/registrado é dialog. O lock contém `tauri-plugin-fs` como
dependência transitiva de dialog, sem conceder permissões `fs:*` ao frontend.
`core:default` é o conjunto padrão do Tauri, não uma
permissão de acesso arbitrário a arquivos ou execução de shell.

Para verificar advisories novos, execute separadamente uma versão confiável do
`cargo-audit`, baixando apenas o banco público RustSec para a auditoria:

```powershell
cargo audit --file frontend/src-tauri/Cargo.lock
cargo check --locked --manifest-path frontend/src-tauri/Cargo.toml
```

O check padrão requer o backend empacotado em `backend/dist/horus-backend`.
Em checkout sem esse artefato, pode-se verificar somente a compilação Rust com
`TAURI_CONFIG='{"bundle":{"resources":[]}}'` no ambiente do comando; isso não
valida os recursos de bundle nem o instalador e não deve alterar a configuração
versionada.

O teste offline não detecta vulnerabilidades recém-publicadas e não comprova os
bytes dos crates apenas lendo seus checksums. O Cargo verifica os artefatos no
download/build; a auditoria usa o banco RustSec na data da execução. Atualizações
de dependências exigem nova auditoria e `cargo check --locked`; mudanças major
devem ser registradas para decisão na Issue antes de aplicação.

## Resultado em 14/11/2026

`cargo-audit 0.23.0`, banco RustSec com 1.509 advisories, commit
`4ef2df8e806a65e2f1ecc14f56f51249062b23d6`: zero entradas em
`vulnerabilities`, com dois alertas informativos preservados:

- `proc-macro-error 1.0.4`: RUSTSEC-2024-0370, sem manutenção e sem versão
  corrigida indicada pelo advisory.
- `glib 0.18.5`: RUSTSEC-2024-0429, unsoundness em `VariantStrIter`; correção
  indicada em `>=0.20.0`, fora da linha compatível 0.18. Exige evolução da cadeia
  upstream, sem forçar versão incompatível neste incremento.

`cargo tree --locked --offline --target x86_64-pc-windows-msvc -i` não encontra
nenhum dos dois pacotes na árvore Windows atual. Continuam no lockfile
multiplataforma e não devem ser omitidos da auditoria. Nenhuma dependência foi
atualizada. Cinco testes offline passaram. `cargo check --locked` passou com
`TAURI_CONFIG` retirando apenas os recursos do bundle; o comando padrão falhou
pela ausência do backend empacotado, sem homologar instalador ou execução real.
