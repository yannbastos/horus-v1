# Segurança da persistência remota (Supabase) — revisão independente, Fase A (HOR-137)

**Atualização da Fase B (15/11/2026):** o [registro de implementação e evidências](persistencia-fase-b.md) distingue o schema/RLS já ensaiado no horus-dev dos controles ainda pendentes do futuro serviço, sessão e pooler. A análise original abaixo e R1–R13 são preservados; sua redação de estado refere-se à Fase A.

**Estado: análise e requisitos. Nada foi implementado, migrado ou enviado à nuvem.** Escopo: o que a implementação futura (Fases B–E) precisa cumprir para não criar um risco novo. O inventário do SQLite e a divisão local/remota são do trabalho paralelo do Codex e não são repetidos aqui; o que esta análise precisa dele está em "Dependências". Premissas dadas: projeto de desenvolvimento `horus-dev` (plano Free), **Data API desativada**, exposição automática de tabelas desativada, RLS automático ligado. Nenhum segredo, senha, string de conexão ou token entra neste documento; afirmações sobre o comportamento do Supabase marcadas *(verificar)* devem ser conferidas na documentação/painel antes da Fase B.

## 1. Modelo de ameaça do backend FastAPI local

O backend roda como processo do usuário, em `127.0.0.1`, com token por execução (HOR-127). Isso protege contra páginas web e outros hosts; **não protege contra código do mesmo usuário ou da mesma máquina**. Portanto o backend local e o executável são tratados como **não confiáveis para guardar segredos compartilhados**: qualquer coisa neles pode ser extraída por quem controle a máquina, o disco ou o processo.

| Ameaça | Situação verificada hoje | Consequência para a persistência remota |
|---|---|---|
| Processo do mesmo usuário lê o ambiente/memória do backend | O token local vive em variável de ambiente (limitação registrada em HOR-127) | Nenhuma credencial remota de longa duração ou privilegiada no ambiente do backend |
| Leitura da pasta de dados por outro usuário/grupo | A ACL herdada de `%LOCALAPPDATA%\Horus` concede leitura a um grupo local de sandbox de ferramentas de IA, além de SYSTEM, administradores e o usuário (leitura apenas de ACL, sem alterar) | Qualquer segredo gravado em arquivo ali é legível por esse grupo |
| Executável distribuído inspecionado | Binário e pacote de UI são extraíveis (strings, `asar`/bundle, memória) | Nada privilegiado no executável: ver seção 2 |
| Ferramentas do agente Hermes | O Hermes tem seu próprio perfil; ferramentas de arquivo/terminal podem ler pastas do usuário se habilitadas (gate de segurança bloqueia hoje) | Credenciais do Supabase nunca na pasta de dados, no perfil do Hermes, em prompts ou logs |
| Cópias fora do controle | Backups do Windows, OneDrive, pacotes de dados locais (`.zip` não cifrado) | O pacote local não pode conter token de sessão nem segredo remoto (ver R6) |
| Máquina perdida/roubada | Sem criptografia própria; depende de BitLocker (HOR-119 só verifica) | Sessão remota revogável no servidor; dado local mínimo (seção 4) |

Princípio: **a segurança dos dados remotos deve vir do servidor (Postgres + RLS + Auth), nunca de um segredo embutido no cliente.**

## 2. Opções de acesso com a Data API desativada

Fatos que condicionam a decisão *(verificar na documentação do Supabase antes da Fase B)*: com a Data API (PostgREST) desligada, `supabase-js` não lê nem grava tabelas; o Supabase Auth continua disponível e emite JWT por usuário; **RLS vale apenas para papéis sem `BYPASSRLS`** (`anon`, `authenticated`, papéis próprios); `service_role`, o papel `postgres` e `supabase_admin` **ignoram RLS**; uma conexão direta ao Postgres autentica por **papel e senha do banco**, não por JWT, de modo que o JWT do usuário só ajuda se um servidor confiável o validar e então assumir um papel restrito (`SET LOCAL ROLE authenticated` + `request.jwt.claims`).

| Opção | Como funciona | Autenticação individual / isolamento | Segredo no executável | Avaliação |
|---|---|---|---|---|
| **A. Conexão direta do app ao Postgres** (pooler) | O app abre conexão com a string do banco | O banco só conhece papéis/senhas; "por usuário" exigiria um papel de banco por pessoa, sem Auth/JWT | **Sim**: a senha do papel está no cliente. Com `postgres` seria o pior caso (ignora RLS) | **Rejeitada** para o executável. Aceitável só em ambiente confiável (servidor ou estação de administração) para migração/backup, com papel dedicado e mínimo |
| **B. Backend remoto autenticado** (função/servidor que o app chama) | O app entra pelo Supabase Auth, envia o JWT ao backend remoto; este valida e fala com o Postgres com papel restrito, escopando por `auth.uid()` | Individual (Auth) e isolamento por RLS + escopo no servidor; o segredo do banco fica só no servidor | **Nenhum** (só URL pública e chave pública) | **Recomendada se a Data API permanecer desligada.** Custo: um componente remoto a operar, deploy, observabilidade e limites do Free; precisa de conexão Postgres no servidor (não `supabase-js`, que usa a Data API) |
| **C. Data API restrita + RLS** | Reativa a Data API só para um schema dedicado (ex.: `app`), `anon` sem permissões, `authenticated` com políticas por `auth.uid()` | Individual (Auth) e isolamento por RLS no próprio banco | Apenas a URL e a chave `anon`/publishable (públicas por desenho) | **Tecnicamente a mais simples e a mais segura para cliente desktop**, mas **exige habilitar a Data API**: decisão do usuário, fora do que esta fase pode fazer. Requer RLS em toda tabela exposta, `security_invoker` em views e revisão de funções `SECURITY DEFINER` |
| **D. Backend FastAPI local como proxy com segredo de banco** | O backend local guarda a senha e fala com o Postgres | Sem isolamento real: quem controla a máquina tem o papel do banco | **Sim**, no processo/disco local | **Rejeitada** |
| **E. Edge Function com `service_role`** | Função remota usa a chave privilegiada | RLS ignorada; o isolamento passa a depender 100% do código da função | Não no executável, mas é um papel que ignora RLS no servidor | Só como último recurso, com escopo estrito por `auth.uid()` em código e testes; preferir B com papel restrito |

**Recomendação.** Decidir entre **B** (mantém a Data API desligada; mais componentes) e **C** (exige ativá-la; menos código e mais simples de auditar). Na ausência de objeção do usuário à ativação, C é a de menor superfície de erro; se a Data API deve continuar desligada, B. **A, D e qualquer uso de `service_role`/`postgres` no cliente são proibidos.** A escolha é do usuário (ela envolve habilitar um recurso hoje desativado) e deve ser registrada no ADR da #124.

**O que nunca pode ir no executável, no repositório, na pasta de dados, em logs, relatórios ou prompts:** senha do Postgres e string de conexão, chave `service_role`/`sb_secret_*`, JWT secret, tokens de acesso/refresh de qualquer pessoa (exceto no cofre do SO, seção 5), segredos de funções remotas. Chaves `anon`/`publishable` são públicas por desenho e só são seguras **com RLS correto**; não são autorização.

**Autenticação e isolamento.** Autenticação individual por Supabase Auth (uma conta por pessoa); dono do dado = `auth.uid()` em coluna `owner_id` não nula; políticas de RLS por operação (`SELECT/INSERT/UPDATE/DELETE`), com `WITH CHECK` além de `USING` (o usuário não pode "doar" nem reatribuir linhas); papéis que ignoram RLS jamais usados em caminhos disparados por usuário; compartilhamento entre pessoas só por tabela de membros explícita, nunca por convenção; testes automatizados de isolamento com duas contas (A não lê, não altera nem conta linhas de B).

## 3. Trânsito, repouso, backup, integridade, conexão e conflitos

- **Trânsito:** só TLS. Conexões Postgres com verificação de certificado (`verify-full`/equivalente); HTTP simples recusado; sem `sslmode=disable` em configuração alguma.
- **Repouso:** o provedor cifra o armazenamento do projeto *(verificar)*; o Hórus **não** conta com isso como controle próprio nem implementa criptografia própria. Dados sensíveis mínimos no remoto: nada de credenciais em tabelas; mídia fica local (somente hashes/metadados); campos de texto do usuário são dados privados com RLS.
- **Backup/restauração do remoto:** premissa a verificar: o plano Free **não** oferece backups gerenciados/PITR *(verificar)* e projetos Free podem ser pausados por inatividade *(verificar)*. Requisito: rotina de `pg_dump` **de ambiente confiável** (nunca do executável) para pasta fora de nuvem sincronizada, com hash, e **ensaio de restauração** em projeto/instância descartável antes de qualquer migração real. Sem esse ensaio, a Fase E não começa.
- **Backup do local mínimo:** o pacote de dados locais (HOR-117) continua o mecanismo; não pode incluir token de sessão do Supabase nem fila com segredos; restauração em pasta nova já é o padrão.
- **Integridade referencial:** chaves estrangeiras e `NOT NULL`/`CHECK` no Postgres são a fonte da verdade; o cache local nunca é autoritativo. Ids gerados no cliente (UUID) para criar offline sem colisão; exclusão lógica ou trilha append-only (como nos comentários de revisão) em vez de apagar o que outros computadores ainda referenciam.
- **Perda de conexão:** escritas entram numa **fila local (outbox)** com chave de idempotência; leitura mostra o estado "local/pendente/sincronizado" (domínio do Antigravity); a produção local nunca depende da nuvem (AGENTS.md: funções essenciais funcionam offline). Reenvio idempotente; tempo limite e recuo exponencial; nenhum retry infinito que esconda falha.
- **Conflitos entre computadores:** concorrência otimista por coluna de versão (`version` incrementada no servidor, `UPDATE ... WHERE version = :base`); conflito devolve 409 com o estado atual, **nunca sobrescreve em silêncio** (last-write-wins só para campos declarados descartáveis); relógios do cliente não decidem ordem (carimbo do servidor); trilhas append-only dispensam merge.

## 4. Reavaliação do #108 (HOR-121, SQLCipher)

O SQLCipher protegia um banco local **completo** (roteiros, mídia registrada, execuções). Com a persistência remota, o SQLite remanescente tende a ser mínimo: cache de leitura, fila de saída, registro de arquivos de mídia local e estado do worker (o inventário do Codex define o conteúdo exato). Recomendação: **não adotar SQLCipher agora** e reclassificar a #108 como *adiada, a reavaliar após a Fase B*. Motivos: (1) a maior redução de risco vem de **guardar menos** dado privado no local (minimização) e de limpar o cache ao encerrar sessão; (2) a proteção de repouso do volume (BitLocker/criptografia de dispositivo, HOR-119) cobre o que restar sem criptografia própria; (3) SQLCipher exige dependência nativa, chave no cofre do SO e migração/backup mais complexos, para proteger pouco. Reabrir se, após o inventário, o local mínimo ainda reter conteúdo privado relevante em máquinas sem criptografia de disco. A decisão é do Maestro/usuário.

## 5. Bloqueios 7.x

**Token do Hermes em texto puro (`integrations/hermes.json`) → cofre do SO (HOR-120).** Plano sem revogar a credencial real: (1) leitor com preferência pelo cofre e *fallback* ao arquivo (nada muda para quem não migrou); (2) migração explícita e confirmada pelo usuário: ler o token, gravá-lo no cofre com usuário/serviço próprios, **reler e comparar** (sem registrar o valor); (3) trocar a fonte ativa para o cofre mantendo o arquivo; (4) só com nova confirmação, remover o campo `token` do arquivo (a URL fica); (5) reversão: o arquivo original preservado até a confirmação final. Nenhuma revogação nem rotação do token real; testes só com valores sintéticos e cofre em memória (a suíte já é isolada do cofre real). O mesmo cofre guardará, futuramente, a sessão do Supabase (refresh token) por usuário do Windows, nunca em SQLite/`localStorage`.

**Permissões da pasta de dados.** Achado: ACL herdada concede leitura a um grupo local de sandbox de ferramentas de IA (ver seção 1). Plano: (1) diagnóstico somente leitura que **avisa** quando a ACL é mais ampla que {usuário, SYSTEM, administradores}; (2) teste de ACL **somente em pasta temporária isolada** criada pelo teste (nunca na pasta real): aplicar a ACL restrita (desabilitar herança, conceder ao usuário atual), conferir a leitura por `icacls`/API e que o usuário continua com acesso; (3) aplicar na pasta real apenas com autorização do usuário, com cópia prévia da ACL (`icacls /save`) para reversão. Efeito colateral a tratar: agentes de IA que hoje leem a pasta perderiam acesso.

**Rotação de tokens com validação e recuperação.** Token local do backend: já é por execução; rotação em sessão longa exige reconexão da UI (aditivo). Tokens de sessão do Supabase: rotação do refresh token é do Auth (reuso detectado revoga a cadeia); o cliente guarda no cofre. Token do Hermes e demais credenciais reais: rotação em duas fases (emitir o novo → **validar** com chamada somente leitura → trocar → só então revogar o antigo), com rollback para o antigo até a validação. **Rotação real exige autorização do usuário**; nos testes, apenas credenciais sintéticas e servidores simulados.

## 6. Requisitos verificáveis para a implementação

| # | Requisito | Como verificar |
|---|---|---|
| R1 | Nenhuma senha/string de conexão, `service_role`, `sb_secret_*` ou JWT privilegiado em arquivos versionados nem no executável/UI gerados | `backend/tests/test_no_remote_db_secrets_versioned.py` (já existe) estendido ao pacote Tauri final; varredura do binário em CI |
| R2 | Nenhuma tabela acessível por `anon`; RLS ativo e com política por operação em toda tabela com dados de usuário; `WITH CHECK` em INSERT/UPDATE | Teste SQL de catálogo (`pg_class.relrowsecurity`, `pg_policies`, `has_table_privilege('anon', ...)`) em instância descartável |
| R3 | Isolamento entre usuários | Teste com duas contas: A não vê, não conta, não altera nem apaga dados de B; reatribuir `owner_id` falha |
| R4 | Papéis que ignoram RLS fora de qualquer caminho de usuário; views com `security_invoker`; funções `SECURITY DEFINER` revisadas e com `search_path` fixo | Revisão + teste de catálogo |
| R5 | TLS obrigatório e verificado | Teste de configuração: recusa `sslmode` fraco e HTTP; conexão a servidor com certificado inválido falha |
| R6 | Pacote de dados locais, logs, banco local e respostas da API sem segredo remoto/sessão | Estender `test_secrets_at_rest_scan.py` com os padrões do Supabase |
| R7 | Sessão do Supabase só no cofre do SO; sair da conta remove a sessão e limpa o cache privado | Teste com cofre em memória; inspeção do disco após logout |
| R8 | Backup remoto restaurável | Ensaio documentado: dump → restauração em instância descartável → contagens e hashes iguais; faz parte da porta de entrada da Fase E |
| R9 | Fila local idempotente: reenvio não duplica; offline não perde nem bloqueia a produção local | Testes com rede simulada (queda no meio, reenvio, duplicata) |
| R10 | Conflito entre dois computadores nunca sobrescreve em silêncio | Teste de duas sessões com versões divergentes → 409 e estado preservado |
| R11 | Migração do token do Hermes reversível e sem revogação | Testes sintéticos de cada passo do plano da seção 5 |
| R12 | ACL restritiva da pasta de dados validada em pasta isolada | Teste somente em pasta temporária; diagnóstico avisa ACL ampla |
| R13 | Nenhum dado real enviado à nuvem antes da Fase E; Fases B–D só com dados sintéticos | Revisão de PR + varredura dos pedidos de rede em testes |

## 7. Dependências e pendências

- **Do Codex (inventário):** conteúdo exato do SQLite mínimo (para fechar a seção 4); quais tabelas guardam texto privado/credenciais indiretas; estratégia de ids e de versão por linha (R9/R10).
- **Do Antigravity:** estados e mensagens de conexão (local/pendente/sincronizado/conflito).
- **Decisões do usuário:** B ou C (habilitar a Data API ou não); autorização para ACL real, migração do token do Hermes, rotação de credenciais reais; adiar a #108.
- **A verificar antes da Fase B:** itens marcados *(verificar)* na documentação do Supabase e no painel do `horus-dev`. Nenhuma credencial foi solicitada ou usada nesta fase; se a verificação exigir login, será feita pelo usuário no próprio terminal (`supabase login`).
