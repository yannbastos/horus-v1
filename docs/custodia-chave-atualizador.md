# Custódia, geração e recuperação da chave minisign do atualizador (HOR-100)

**Estado em 16/11/2026:** procedimento preparatório para aprovação do usuário (etapa A4 de [HOR-100](https://github.com/yannbastos/horus-management/issues/88)). **A chave definitiva de produção ainda não foi gerada.**

**Documentos relacionados:** [ADR do atualizador](adr-atualizador.md), [política de governança dos agentes](AGENT-GOVERNANCE.md) e [regras do repositório](../AGENTS.md).

## Decisão vigente e proibições

Conforme decisão do usuário em 16/11/2026:

- Está autorizada apenas a preparação do procedimento e das opções de custódia; a geração definitiva da chave aguarda revisão e aprovação deste documento.
- A verificação criptográfica do atualizador (`minisign`) continua obrigatória.
- A contratação e o uso de certificado **Authenticode** ficam adiados para a fase de distribuição pública.
- **Proibições permanentes:** nunca incluir chave privada ou senha em commits, logs, histórico de shell, código-fonte ou backups de projeto; nunca publicar a chave privada; nunca usar serviço externo de armazenamento sem autorização explícita do usuário.

---

## 1. Papel de cada assinatura

As duas assinaturas atuam em camadas diferentes e **uma não substitui a outra**:

| Aspecto | Minisign (`tauri-plugin-updater`) | Authenticode (Windows) |
|---|---|---|
| **Finalidade** | Autenticar o pacote de atualização (instalador NSIS) e sua versão dentro do fluxo do atualizador do Hórus | Identificar o publicador para o sistema operacional Windows e para o Microsoft Defender SmartScreen |
| **Quem verifica** | O próprio aplicativo desktop (`tauri-plugin-updater`, via `pubkey` embutida) | Windows, SmartScreen e mecanismos de confiança do SO |
| **Proteção contra adulteração** | Impede que o app instale pacote alterado ou assinado por outra chave; com `requireSignedVersion=true`, vincula a versão do manifesto à assinatura | Impede alteração silenciosa dos binários/instalador assinados após a emissão do certificado |
| **Impacto no SmartScreen** | Nenhum: o Windows não reconhece assinatura minisign | Interage com a reputação do publicador no SmartScreen, mas **não garante ausência imediata de alertas** (conforme [documentação Tauri](https://v2.tauri.app/distribute/sign/windows/), nem certificados EV conferem reputação imediata) |
| **Estado no projeto** | Obrigatória para o atualizador | Adiada para distribuição pública |

Quando o Authenticode for adotado no futuro, a ordem obrigatória de assinatura em uma release é:

1. Assinar os binários e o instalador NSIS com **Authenticode** (incluindo *timestamp*).
2. Gerar a assinatura **minisign** (`.sig`) sobre os **bytes finais** do instalador já assinado pelo Authenticode.

---

## 2. Geração da chave definitiva

### Versões fixadas e comando oficial

O projeto fixa `@tauri-apps/cli` na versão `2.12.4` (`frontend/package-lock.json`) e `tauri-plugin-updater` na versão `2.14.1` (`frontend/src-tauri/Cargo.toml`). Conforme a [documentação oficial do Tauri Updater](https://v2.tauri.app/plugin/updater/), a geração do par Ed25519 compatível com minisign usa `tauri signer generate`:

```powershell
npm --prefix frontend run tauri -- signer generate -w <caminho-fora-do-repo>\horus-updater-prod.key
```

Ou diretamente pelo binário da CLI fixada:

```powershell
node frontend/node_modules/@tauri-apps/cli/tauri.js signer generate --write-keys <caminho-fora-do-repo>\horus-updater-prod.key
```

Regras da geração de produção:

- **Não usar `--ci`:** a flag `--ci` gera a chave sem senha para automação de teste. Em produção, execute sem `--ci` para que a CLI solicite uma **senha forte** interativamente e cifre a chave privada.
- **Não usar `--force`:** evita sobrescrever acidentalmente uma chave existente.
- **Ambiente confiável:** gerar em máquina confiável, sem compartilhamento de tela, sem gravação de sessão e sem transcrição de terminal ativa.
- **Localização temporária segura:** o diretório `<caminho-fora-do-repo>` deve ficar **fora** de qualquer clone Git, pasta sincronizada em nuvem (OneDrive, Dropbox, Google Drive) ou backup automático de projeto. Após transferir a chave para as mídias offline escolhidas, remover a cópia temporária da pasta de geração.
- **Saída de terminal:** não salvar a saída do comando em arquivo de log; limpar o histórico/buffer do terminal após conferir a criação dos arquivos.

### Registro da chave pública e fingerprint

O comando gera dois arquivos:

- `<nome>.key`: chave privada cifrada pela senha escolhida (segredo crítico).
- `<nome>.key.pub`: chave pública em formato minisign.

O conteúdo textual de `<nome>.key.pub` (e não o caminho do arquivo) é o valor a ser configurado em `plugins.updater.pubkey` no overlay de distribuição.

Registrar em local versionado ou inventário operacional (somente dados públicos, **sem** chave privada nem senha):

- Identificador lógico da chave (ex.: `horus-updater-prod-v1`).
- Data de criação e algoritmo (`Ed25519 / minisign`).
- Responsável/custodiante designado.
- Conteúdo completo da chave **pública** (`.key.pub`).
- **Fingerprint / hash de conferência** da chave pública (ex.: saída de `Get-FileHash <caminho>.key.pub -Algorithm SHA256` e o Key ID exibido no cabeçalho minisign), para validar que qualquer cópia restaurada corresponde à mesma chave pública.

### Separação estrita entre chave de ensaio e chave de produção

- **Chave de ensaio:** os scripts `scripts/build-updater-test.ps1` e `scripts/updater-e2e.mjs` geram chaves descartáveis com `--ci` (sem senha) em `%LOCALAPPDATA%\Horus\dev-updater-test\horus-updater-test.key` e em diretórios temporários de E2E. Essas chaves servem exclusivamente para testes locais em loopback.
- **Chave de produção:** nunca é colocada em `%LOCALAPPDATA%\Horus\dev-updater-test\`, em `%TEMP%`, em overlays E2E ou no repositório. Chaves de ensaio nunca são usadas em builds distribuídos.

---

## 3. Opções de armazenamento (para escolha do usuário)

A chave privada cifrada (`.key`) e sua senha devem ficar **separadas**: nunca guarde a senha em texto puro no mesmo arquivo ou mídia da chave privada. Por padrão, todas as opções recomendadas são **offline e locais**.

| Opção | Como funciona | Prós | Contras e riscos |
|---|---|---|---|
| **(a) Dois pendrives cifrados em locais físicos distintos** | Dois pendrives dedicados, formatados com BitLocker To Go ou contêiner cifrado (ex.: VeraCrypt), contendo a chave privada `.key` e o registro da `.key.pub`; guardados em locais físicos separados (ex.: local principal + cofre/outro endereço). | 100% offline; resistente a falha ou perda de uma mídia; sem dependência de terceiros ou rede. | Risco de degradação de memória flash ao longo dos anos sem regravação/ensaio; se ambos ficarem no mesmo imóvel, incêndio ou furto atinge os dois; depende da disponibilidade da senha. |
| **(b) Gerenciador de senhas local/offline com cópia em mídia separada** | Arquivo de cofre local (ex.: KeePass `.kdbx`) protegido por senha mestra forte (e opcionalmente arquivo-chave separado), replicado em mídia externa offline. Guarda a senha da chave minisign (e pode manter cópia cifrada adicional). | Facilita senha longa e aleatória; evita guardar senha junto da chave; fácil de auditar e replicar offline. | Se a senha mestra for esquecida, o cofre é irrecuperável; exige disciplina para atualizar a cópia em mídia separada após alterações. |
| **(c) Cofre do Windows / DPAPI (somente cópia de uso)** | Armazenamento protegido pelo perfil do usuário Windows (DPAPI / Windows Credential Manager / EFS) na estação de release. | Conveniência operacional durante assinatura local, sem expor texto puro em disco comum. | **Nunca usar como único backup:** vinculado ao SID/perfil/TPM daquela instalação Windows; reinstalação do SO, troca de placa-mãe ou corrupção de perfil inviabiliza a recuperação. |
| **(d) Serviço externo (nuvem privada, cofre gerenciado ou Secrets de CI)** | Armazenamento em cofre em nuvem ou segredos de CI/CD para assinatura automatizada. **Exige autorização explícita do usuário.** | Permite pipeline remoto e recuperação independente do hardware local. | Amplia superfície de ataque; depende de conta, autenticação, disponibilidade e eventual custo recorrente; risco de exposição por erro de configuração de CI ou logs. |

### Recomendação fundamentada

Recomenda-se combinar **(a) + (b)**:

1. Armazenar a chave privada cifrada (`.key`) em **dois pendrives cifrados (BitLocker To Go ou contêiner cifrado)** mantidos em **locais físicos distintos** — atendendo à exigência de duas cópias cifradas independentes offline.
2. Armazenar a **senha forte** da chave privada em um **gerenciador de senhas local/offline** (ex.: arquivo KeePass `.kdbx`), com cópia desse arquivo em mídia separada da mídia principal da chave.
3. Usar **(c)** no máximo como cópia operacional temporária se necessário (preferindo carregar a chave apenas em memória durante a sessão de assinatura) e manter **(d)** desabilitado enquanto não houver autorização explícita para pipeline remoto.

---

## 4. Uso na assinatura de uma release

### Fornecimento das variáveis sem vazar em disco, log ou histórico

O Tauri CLI lê a chave privada e a senha pelas variáveis de ambiente `TAURI_SIGNING_PRIVATE_KEY` (caminho do arquivo `.key` ou conteúdo da chave) e `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` (ou pelas flags `-f`/`--private-key-path` e `-p`/`--password` em `tauri signer sign`).

Regras operacionais durante o build ou assinatura manual:

- **Não usar `.env`:** conforme a documentação oficial do Tauri, arquivos `.env` não carregam `TAURI_SIGNING_PRIVATE_KEY` corretamente e aumentam o risco de commit ou vazamento em disco.
- **Não passar senha na linha de comando:** nunca usar `-p "<senha>"` em comandos, pois argumentos ficam visíveis em histórico de shell (`ConsoleHost_history.txt`) e na lista de processos.
- **Fornecer em memória na sessão e limpar ao final:** ler a senha via `Read-Host -AsSecureString` (ou deixar a CLI solicitar interativamente no terminal) e restaurar/limpar `TAURI_SIGNING_PRIVATE_KEY` e `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` em bloco `try / finally`, seguindo o padrão já usado em `scripts/build-updater-test.ps1`.
- **Sem logs de segredo:** desativar transcrições de PowerShell (`Stop-Transcript` se ativa) e nunca imprimir variáveis de assinatura ou conteúdo da chave privada.

Exemplo de fluxo manual seguro para assinar um instalador final (quando houver chave de produção aprovada):

```powershell
# Executar somente em máquina confiável, com mídia da chave montada temporariamente
$oldKey = $env:TAURI_SIGNING_PRIVATE_KEY
$oldPwd = $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD
try {
    # Sem -p na linha de comando: a CLI solicita a senha interativamente se a variável de senha não for usada,
    # ou a senha pode ser lida em SecureString sem gravar no histórico.
    npm --prefix frontend run tauri -- signer sign `
        --private-key-path "<unidade-cifrada>:\horus-updater-prod.key" `
        --app-version "<versao-semver>" `
        "<caminho-artefato>\HORUS_<versao-semver>_x64-setup.exe"
} finally {
    $env:TAURI_SIGNING_PRIVATE_KEY = $oldKey
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $oldPwd
}
```

### Conferência e ordem de publicação

1. **Conferir versão e hashes:** confirmar `node scripts/product-version.mjs --check`, gerar e registrar SHA-256 do instalador `.exe` e do arquivo `.sig`.
2. **Validar contra a chave pública oficial:** conferir que o `.sig` gerado valida o instalador final com a `pubkey` registrada para produção e que o *trusted comment* da assinatura corresponde exatamente à versão da release (`requireSignedVersion=true`).
3. **Ordem de publicação:** enviar primeiro os artefatos imutáveis (instalador `.exe` e `.sig`), verificar integridade no destino e **publicar o manifesto (`latest.json`) por último**.
4. **Ação humana obrigatória:** a publicação da release é sempre manual e explícita pelo usuário; nunca automatizar publicação sem autorização.

---

## 5. Recuperação e ensaio de restauração

Backups nunca testados não comprovam recuperabilidade. Recomenda-se executar um ensaio periódico (ex.: semestral, antes da primeira release pública e sempre após troca de mídia ou máquina) em **ambiente descartável** (VM Windows limpa ou diretório temporário isolado sem dados operacionais):

1. **Restaurar a partir do backup secundário:** conectar a mídia de backup (ex.: pendrive cifrado reserva), desbloquear com a senha recuperada do cofre offline e copiar temporariamente o arquivo `.key` para pasta isolada fora de qualquer repositório.
2. **Conferir identidade da chave:** comparar a `.key.pub` correspondente (ou fingerprint SHA-256 registrado) com a chave pública oficial documentada.
3. **Assinar arquivo sintético de teste:** criar um arquivo descartável (ex.: `probe-restore.txt`) e assinar com a chave restaurada:
   ```powershell
   npm --prefix frontend run tauri -- signer sign `
       --private-key-path "<pasta-descartavel>\horus-updater-prod.key" `
       --app-version "0.0.0-restore" `
       "<pasta-descartavel>\probe-restore.txt"
   ```
4. **Verificar contra a chave pública oficial:** validar `probe-restore.txt` + `probe-restore.txt.sig` usando a chave pública oficial registrada (via ferramenta minisign ou verificador `minisign-verify` em ambiente de teste), confirmando assinatura válida e versão esperada.
5. **Limpeza imediata:** desmontar a mídia, excluir a cópia temporária da chave, `probe-restore.txt` e `.sig` do ambiente descartável (sem deixar vestígio em disco/lixeira/Git).
6. **Registro:** anotar apenas data, responsável que executou, qual mídia foi testada e resultado (`aprovado`/`falha`), sem registrar senha, chave privada ou caminhos pessoais.

---

## 6. Perda e comprometimento

### Perda da chave privada (ou da senha sem backup)

- **Consequência:** todas as instalações existentes do Hórus contêm a `pubkey` antiga embutida e recusarão qualquer atualização assinada por uma nova chave (`signature_invalid`).
- **Recuperação:** gerar um novo par de chaves seguindo a seção 2, compilar uma nova release com a nova `pubkey` e orientar/executar **reinstalação manual** a partir de fonte confiável, preservando os dados do usuário em `%LOCALAPPDATA%\com.horus.audiovisual\workspace` (e realizando backup prévio do SQLite).

### Rotação planejada por versão de transição

Enquanto a chave antiga ainda estiver íntegra e disponível:

1. Gerar a nova chave de produção (`v2`) seguindo a seção 2.
2. Publicar uma **versão de transição** assinada pela **chave antiga (`v1`)**, mas já compilada com a **nova `pubkey` (`v2`)** (ou aceitação explícita da nova chave).
3. Após os clientes instalarem a versão de transição, as releases seguintes passam a ser assinadas exclusivamente com a chave `v2`.

### Suspeita ou confirmação de comprometimento

- **Trocar apenas `latest.json` não revoga a confiança:** clientes já instalados continuam confiando na `pubkey` embutida; se um atacante possuir a chave privada e puder interceptar ou servir no canal, poderá assinar pacotes maliciosos válidos.
- **Ações imediatas:**
  1. Suspender imediatamente novas publicações e retirar/pausar o canal de atualização afetado.
  2. Revogar acessos, tokens ou sessões da máquina/ambiente suspeito e preservar logs para investigação.
  3. Gerar nova chave em máquina sabidamente limpa e confiável.
  4. Comunicar os usuários/operadores e distribuir pacote de recuperação por canal confiável fora do fluxo comprometido (reinstalação manual verificada preservando dados locais).

---

## 7. Lista de verificação final e pendências do usuário

### Checklist antes da geração definitiva

- [ ] Opção de armazenamento offline aprovada pelo usuário.
- [ ] Mídias físicas cifradas e cofre de senhas offline preparados e testados antes de gerar a chave.
- [ ] Responsável(is) pela custódia e pela recuperação definidos.
- [ ] Máquina de geração verificada (confiável, sem gravação/transcrição, fora de pastas sincronizadas e fora do repositório).
- [ ] Plano de registro da chave pública e do fingerprint definido (onde ficará o registro oficial da `pubkey`).
- [ ] Periodicidade do ensaio de restauração definida.

### Decisões que continuam pendentes do usuário

1. **Escolha do modelo de custódia offline:** confirmar a recomendação `(a) + (b)` (dois pendrives cifrados em locais distintos + gerenciador de senhas offline com cópia separada) ou definir alternativa.
2. **Responsáveis e locais físicos:** definir quem guarda as cópias e onde cada mídia física ficará armazenada.
3. **Periodicidade dos ensaios de restauração:** definir o intervalo (ex.: semestral).
4. **Autorização para gerar a chave definitiva:** a geração só ocorrerá quando o usuário aprovar este procedimento e autorizar a execução em sessão dedicada.
5. **Decisões complementares do [ADR do atualizador](adr-atualizador.md):** escolha do canal privado HTTPS (etapa B1/B2), separação núcleo × recursos (etapa A5/A6) e momento de contratação do Authenticode.
