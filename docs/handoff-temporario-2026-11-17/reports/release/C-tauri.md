# HOR-163 C — Tauri/Rust e WebView2

Data: 17/11/2026. Branch: `yannbastos/rel-c-tauri`.
Commit: `6fe7c462d76980904296dd7e44797187cebe52d9`.

## Entrega

Somente `frontend/src-tauri/Cargo.toml` e `frontend/src-tauri/src/{main,updater,update_enrollment}.rs` foram alterados. Backend, frontend React, capabilities, configuração base, scripts, infra e CI ficaram intactos. Sem push, PR, merge, publicação, Cloudflare, instalação ou execução do produto/instalador.

`update_enrollment.rs` usa Windows Credential Manager via a dependência `windows-sys` já existente, habilitando somente `Win32_Security_Credentials`. É o cofre nativo também utilizado pelo keyring Python existente; não foi adicionada outra biblioteca de credenciais nem endpoint que transfira o segredo ao backend. O acesso Windows fica isolado por `cfg(windows)`; plataformas ainda não suportadas falham fechado. Conta/target: `<identifier>.updater.<channel>`; canais `test` e `production` ficam separados. Credencial por usuário, persistida localmente, sem elevação e sem arquivo de fallback. Cadastro verifica a releitura; exclusão é idempotente. Os testes usam um `Vault` simulado e não acessam o cofre real.

Aceita a representação de 256 bits como 64 caracteres hexadecimais ou 43 caracteres base64url sem padding. O admin continua sendo o único emissor/revogador; o app apenas recebe e guarda o segredo emitido. Nada de senha, chave privada ou token real foi solicitado ou usado.

## Contrato exato para Antigravity D

Todos os comandos devolvem o mesmo objeto `UpdateState`, sem token:

- `update_enroll`, argumentos IPC `{token: string}`: guarda no cofre e invalida a atualização previamente consultada.
- `update_clear_enrollment`, sem argumentos: remove do cofre e invalida a atualização consultada.
- `update_set_auto_check`, argumento já existente `{enabled: boolean}`: preferência persistida em `updater/preferences.json`, contendo somente o booleano.
- Mantidos `update_get_state`, `update_check` e `update_install({confirmed_version: string})`.
- Evento mantido `update://state`.

Campos aditivos: `installed_version: string`, `available_version: string|null`, `notes: string|null`, `channel: string|null`, `enrolled: boolean`, `last_error: {code,message}|null`. `auto_check: boolean` já existia e foi preservado. `progress`, já existente e anulável, passa a conter `{phase:"downloading",downloaded:number,total:number|null}` durante o download. Campos antigos, fases, reserva e confirmação mantidos. `enrolled` informa credencial local válida quanto ao formato; não afirma que um token ainda não consultado é aceito pelo servidor.

Novos códigos estáveis: `configuration_invalid`, `enrollment_invalid`, `enrollment_required`, `credential_unavailable`. Códigos anteriores mantidos, inclusive `network`, `manifest_invalid`, `signature_invalid`, `downgrade_refused`, `backup_failed`, `install_failed`, `startup_failed`; operação ocupada devolve o estado vigente. O plugin não distingue HTTP 401/403 de outros status não exitosos na consulta: atualmente esses casos continuam classificados como `network`, sem expor corpo remoto ou segredo.

## Contrato para Codex B — overlay de release

O gerador do overlay deve transformar as variáveis de build já acordadas nestes campos, sem guardar token no overlay:

```json
{
  "plugins": {
    "horusUpdater": {"channel": "test"},
    "updater": {
      "endpoints": ["https://HOST/test/latest.json"],
      "pubkey": "CHAVE_PUBLICA_DO_CANAL",
      "requireSignedVersion": true,
      "dangerousInsecureTransportProtocol": false,
      "windows": {"installMode": "passive"}
    }
  },
  "bundle": {
    "windows": {
      "nsis": {"installMode": "currentUser"},
      "webviewInstallMode": {"type": "offlineInstaller", "silent": true}
    }
  }
}
```

Mapeamento: `HORUS_UPDATE_CHANNEL` → `plugins.horusUpdater.channel`; `HORUS_UPDATE_BASE_URL` + `/<channel>/latest.json` → único elemento de `plugins.updater.endpoints`; `HORUS_UPDATER_PUBKEY` → `plugins.updater.pubkey`. Neste contrato BASE_URL é somente a origem HTTPS, sem canal. O app lê exclusivamente o overlay embutido, nunca variáveis de ambiente para trocar canal, host ou chave em runtime. O worker B deve incorporar esse mapeamento no gerador que lhe pertence; nenhum overlay foi editado por C.

HTTPS e canal explícito são obrigatórios nos canais remotos. Credenciais embutidas na URL, query, fragmento, flags de certificado inválido e múltiplos endpoints são recusados. Pacote só pode vir da mesma origem HTTPS do manifesto, em `/<channel>/releases/<version>/HORUS_<version>_x64-setup.exe`. Manifestos de produção aceitam SemVer estável; teste aceita `X.Y.Z-test.N`; regressão/igualdade continuam recusadas. Nenhuma mudança nos contratos fixos de R2/KV/assinatura/emissão.

Compatibilidade de ensaio HOR-100: a exceção HTTP/sem token continua restrita ao identificador `com.horus.installer-test`, endpoint literal `127.0.0.1`, assinatura/versionamento obrigatórios e transporte inseguro explicitamente habilitado no overlay. Aceita canal omitido no overlay legado ou `test`, nunca `production`. Assim S1–S6 não exigem credencial real nem alterações nos scripts existentes. O build operacional nunca obtém essa exceção.

## Headers e política

Conferido no fonte local exato `tauri-plugin-updater 2.14.1`, `src/updater.rs`: `UpdaterBuilder::headers` documenta aplicação em check e download; `check` envia `.headers(headers.clone())`, copia `self.headers` para `Update`, e `Update::download` envia esse mapa. Um teste executa as duas requisições reais contra servidor loopback e exige Bearer em ambas. O HeaderValue é marcado sensível para redigir Debug. Proxy e redirects continuam desabilitados; download é validado antes de qualquer envio a sua URL. Antes de instalar, o app relê a credencial, evitando usar um header em cache após remoção externa.

Ao iniciar, consulta automaticamente somente com configuração válida + token local válido + preferência habilitada; o padrão da preferência é habilitado. O ensaio legado isolado mantém sua consulta sem token. Cadastro não dispara download/instalação. Instalação continua exigindo a versão explicitamente confirmada, consulta de atividade, reserva exclusiva, nova consulta de atividade, backup verificável, download/verificação de assinatura antes de cache/shutdown/install e recibo de saúde autenticada após reiniciar. Os endpoints backend existentes já cobrem jobs/produção, previews e limpeza final de processos; não foi necessário alterá-los.

## WebView2

Decisão para produção: `bundle.windows.webviewInstallMode={"type":"offlineInstaller","silent":true}` no overlay de release, mantendo NSIS `currentUser`. Evita a dependência de rede no computador de instalação, em coerência com funções essenciais locais; custa aumento de tamanho. O modo `downloadBootstrapper` é menor, mas exige rede durante a instalação. Referência oficial: [Tauri — WebView2 installation options](https://v2.tauri.app/distribute/windows-installer/#webview2-installation-options).

Verificação executada sem VM: teste estático do template NSIS efetivamente referenciado pela configuração, conferindo ramo `offlineInstaller`, payload `${WEBVIEW2INSTALLERPATH}`, detecção HKCU, execução com `${WEBVIEW2INSTALLERARGS} /install` e aborto em falha. Não foi gerado NSIS de release nem baixado runtime. Conferir posteriormente no NSIS gerado que o overlay resultou em `INSTALLWEBVIEW2MODE=offlineInstaller` e argumentos silenciosos. A configuração base permanece `skip`, por responsabilidade exclusiva do overlay B.

Ainda exige VM Windows descartável sem WebView2, usuário sem admin e autorização do Maestro: detecção de runtime ausente, instalação Evergreen por usuário sem rede/elevação, comportamento de falha/cancelamento, abertura do desktop e preservação/atualização/desinstalação. Não declarar essas jornadas homologadas pelo teste estático. Persistência, leitura e exclusão no cofre Windows real também precisam de ensaio com identidade e credenciais fictícias, fora da instalação operacional.

## Verificações

- `cargo test --locked --offline --manifest-path frontend/src-tauri/Cargo.toml`: **22 aprovados**, nenhum ignorado/falhou. Inclui enrollment com mock, falha do cofre, header sensível e em ambas as requisições, assinatura inválida no plugin, HTTP/canais/host/rota inválidos, SemVer por canal, produção ocupada, ordem reserva/backup, confirmação, preservação do recibo/saúde e preferência persistida. Testes anteriores preservados.
- `pytest backend/tests/test_update_reservation.py backend/tests/test_update_backup.py backend/tests/test_updater_packaging.py backend/tests/test_network_inventory.py -q`: **20 aprovados**. Executado com o Python de `C:/Users/yannn/01. Projetos/HORUS/horus/backend/.venv/Scripts/python.exe`, `PYTHONPATH` apontando para o backend deste worktree e bytecode desabilitado; dados exclusivamente temporários. Nenhuma dependência instalada e nenhum ambiente externo alterado.
- `rustfmt` nos dois módulos alterados; `git diff --cached --check` aprovado; worktree limpo depois do commit.
- Primeira tentativa Cargo detectou ausência de `backend/dist/horus-backend` neste checkout isolado. Foi criado somente `backend/dist/horus-backend/test-placeholder.txt` ignorado pelo Git, contendo uma descrição de fixture, sem executável; isso permite o build dos testes sem empacotar/iniciar backend. A segunda execução e a execução final passaram. Não representa build de produto validado.
- Logs resumidos completos: `C-cargo-test.log` e `C-pytest.log`, ao lado deste relatório.
- S1–S6 instalados e toda jornada de instalador: **não executados**, conforme proibição desta fase; as guardas e testes unitários/regressões correspondentes foram preservados.

## Pendências para integração

Maestro encaminhar os contratos acima a B e D; B gerar o overlay e atualizar documentação/inventário de rede no seu escopo de integração, se essa responsabilidade ainda não estiver atribuída. A documentação histórica ainda descreve exclusivamente loopback/sem credenciais e `skip`; ela não foi editada por C porque o escopo de arquivos desta tarefa é explícito. Revisar em integração que F-21 passe a registrar apenas envio do token revogável ao host/canal fixados, sem telemetria. A validação final em máquina real continua pendente e não foi autorizada nesta fase.
