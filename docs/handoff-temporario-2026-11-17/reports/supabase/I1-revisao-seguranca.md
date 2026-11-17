# Revisão de Segurança Independente — HOR-164 Incremento I1

- **Repositório:** `C:\Users\yannn\01. Projetos\HORUS\horus` (worktree inspecionada: `C:\Users\yannn\orca\workspaces\horus\sb-i1`)
- **Branch / Commits:** `yannbastos/sb-i1` (`aa1ecd1`, `8d4c36b`) sobre `origin/main` (`6b53867`)
- **Escopo:** somente leitura no repositório; nenhuma edição, commit, rede ou conexão com Supabase real.
- **Veredito:** **COM RESSALVAS** (nenhum achado Crítico ou Alto; 1 achado Médio de contrato/disponibilidade em `api.py` e 4 achados Baixos).

---

## Tabela de Achados

| ID | Severidade | Tipo | Localização | Descrição | Correção recomendada |
|---|---|---|---|---|---|
| A1 | **Médio** | Fato | `backend/src/horus_backend/api.py:190-193` | O middleware `authorize` bloqueia qualquer mutação quando `update_reserved or app.state.database.changing`. Como `POST /v1/database/test` é tratado como mutação, uma segunda chamada concorrente a `/v1/database/test` recebe **HTTP 409 `database_busy`** em vez de chegar a `manager.test()` (`manager.py:154-155`), que deveria deduplicar e retornar **HTTP 200 `connecting`** (`M1-plano-v2-emendas.md:94`). Além disso, mutações fora de `/v1/database/` recebem 409 com a mensagem incorreta `"Atualização reservada; tente após reiniciar."` enquanto `database.changing` está ativo. | Separar `if update_reserved:` (aplica a todas as mutações) de `if app.state.database.changing:` (aplicar apenas a `POST/DELETE /v1/database/connection`, nunca a `/v1/database/test` nem a rotas fora de `/v1/database/`). |
| A2 | **Baixo** | Fato | `backend/src/horus_backend/database/manager.py:139, 149` | Em `configure()` e `delete()`, quando o cofre falha (`VaultUnavailable`), `self._set("vault_unavailable", "database_vault_unavailable")` usa o padrão `configured=True`, expondo `host_label="Supabase (sessão 5432)"` mesmo se nenhuma configuração existia antes. | Passar `configured=False` (ou preservar o estado anterior de configuração) ao registrar `vault_unavailable` em `configure()`/`delete()`. |
| A3 | **Baixo** | Fato | `backend/src/horus_backend/database/manager.py:291-292` | No `except psycopg.OperationalError` de `connection()`, `self._close()` e `self._set(...)` são executados sem adquirir `self._operation`. Embora `self._active > 0` impeça sobreposição com `_mutation()` e `test()`, duas UoWs falhando simultaneamente podem chamar `_close()`/`_set()` em paralelo. | Serializar a transição de falha com lock dedicado ou método auxiliar sincronizado. |
| A4 | **Baixo** | Fato | `backend/src/horus_backend/database/transport.py:62` | `any(name.startswith("PG") for name in os.environ)` depende da normalização para maiúsculas do `os.environ` no Windows; `name.upper().startswith("PG")` é mais defensivo para compatibilidade futura. | Usar `name.upper().startswith("PG")`. |
| A5 | **Baixo** | Fato | `backend/tests/pg/test_database_contract.py` | (1) `test_concurrent_test_is_deduplicated` (L168-173) monta apenas `database_router` sem `create_app`, não detectando A1; (2) faltam casos explícitos na parametrização de `host` para maiúsculas, unicode/IDN, `@`, `#`, `/`, ponto final e IPv6/decimal (embora `models.py:42` os rejeite); (3) limite de 16 KiB sem `Content-Length` (chunked) não é exercitado diretamente nos testes de contrato (embora `BodyLimitMiddleware` conte bytes em streaming). | Adicionar teste de `/v1/database/test` concorrente via `create_app` após corrigir A1, e ampliar casos negativos de `host` e streaming sem `Content-Length`. |

---

## Verificação por Domínio

### 1. Segredos (Senha write-only e Cofre)
- **Fato (`models.py:30, 35, 53-58`):** `ConnectionRequest` usa `password: SecretStr` (`min_length=1, max_length=4096, json_schema_extra={"writeOnly": True}`) e `ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)`. Caracteres `\x00` são rejeitados sem eco.
- **Fato (`router.py:51-53, 60-63`):** `ValidationError` usa `error.errors(include_input=False)` e `raise DatabaseRefused(...) from None`; exceções inesperadas fecham o gerenciador e retornam `database_internal_error` (500) sem traceback ou detalhes do driver.
- **Fato (`vault.py:22-70`):** Aceita exclusivamente `WinVaultKeyring` (Windows), `Keyring` (macOS) ou `SecretService.Keyring` (Linux) com `priority > 0`. Sem fallback para arquivo ou variável de ambiente. Falhas em `read`, `write` (com releitura e `hmac.compare_digest`) e `delete` levantam `VaultUnavailable` (`from None`), resultando em `vault_unavailable` fechado (`_pool = None`, `_coordination = None`).
- **Fato (`transport.py:57-76`, `manager.py:190`):** `ConnectionPool("", ...)` não armazena DSN/senha; `VerifiedConnection.connect` captura exceções e relança `ConnectFailure(code) from None` apenas com código fixo.

### 2. TLS e CA Embarcada
- **Fato (`transport.py:67, 70`):** `sslmode="verify-full"`, `sslrootcert=str(CA_FILE)`, `sslcert=""`, `sslkey=""`, `sslcrl=""`, `gssencmode="disable"`. Nenhum caminho faz fallback para `require`, `prefer`, `allow` ou `disable`, nem confia em CA corporativa/sistema.
- **Fato (`database/supabase-ca.crt`):** Verificado diretamente no arquivo:
  - Contém apenas 1 certificado X.509 público (`CN=Supabase Root 2021 CA`, validade `28/04/2021` a `26/04/2031`), **sem chave privada** (`b"PRIVATE"` ausente).
  - **SHA-256 do arquivo:** `700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7` (idêntico ao registrado em `database/README.md:44` e `I1-database-layer.md:61`).
  - **Fingerprint SHA-256 (DER):** `807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa` (idêntico ao registrado em `database/README.md:45` e `I1-database-layer.md:59`).

### 3. Destino, Bypass e TOCTOU
- **Fato (`models.py:32-45, 46-51`):** `host` exige `re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.pooler\.supabase\.com", value)`. Verificado em execução local do Python/Pydantic do projeto: rejeita unicode/IDN, maiúsculas, `@`, `#`, `/`, `\`, espaços, pontos finais, `\n` final, IPs literais/decimais/IPv6 e subdomínios extras. `port` exige `type(value) is int and value == 5432` (rejeita `bool`, `float`, `str`). `database` e `user` (`^horus_app\.[a-z0-9]{20}$`) também rejeitam `\n` final e espaços/opções.
- **Fato (`transport.py:33-47, 62-70`):** `resolve_public` resolve `socket.getaddrinfo` a **cada conexão**, desembrulha `ipv4_mapped`, exige `address.is_global and not address.is_multicast` em **todos** os endereços retornados e fixa `hostaddr=address` mantendo `host=config.host` (elimina TOCTOU entre validação e libpq, preservando SNI/`verify-full`). Parâmetros são passados como `kwargs` tipados (impede injeção de `options`, `sslrootcert`, `service`, `passfile`), e variáveis `PG*` em `os.environ` são bloqueadas.

### 4. Endpoints e Middleware
- **Fato (`api.py:157-184`):** Ordem de middlewares Starlette garante que `authorize` (loopback Host, Origin e Bearer token) executa **antes** de `CORSMiddleware` e `DatabaseBodyLimitMiddleware`.
- **Fato (`database/body_limit.py:7-30`, `body_limit.py:41-75`, `router.py:22-28`):** Limite de 16 KiB aplicado em streaming (`counted_receive`, cobrindo `Content-Length` ausente e `Transfer-Encoding: chunked`) com resposta 422 sanitizada e `Cache-Control: no-store`.
- **Fato (`router.py:65-91`):** `GET /v1/database/status` lê apenas `manager.snapshot` sem abrir conexão nem ler o cofre; `DELETE /v1/database/connection` é idempotente; códigos 409/422/503/200 aderem ao contrato, ressalvado o achado **A1** em `api.py:190`.

### 5. Pool, Estado e SQLite
- **Fato (`manager.py:67-75, 105-124, 169-206`):** `configure()` e `delete()` serializam via `_operation`, ativam `changing`, drenam UoWs ativas por até 3 s (`_units.wait`), incrementam `_generation`, fecham coordenação e pool antigos e instanciam nova classe de conexão e novo `ConnectionPool` na rotação.
- **Fato:** Nenhum import de `sqlite3`/`_sqlite3` ou criação de arquivos `.db`/`.sqlite*` no pacote `database/`.

### 6. Testes e Skips
- **Fato:** As suítes cobrem amplamente caminhos negativos reais (payloads inválidos, >16 KiB e >2 MiB autenticados, falha de cofre, `database_busy`/drenagem, papel privilegiado/grants/schema inválidos, SCRAM real com senha errada, TLS autoassinado recusado pelo libpq, DNS privado/misto, `PGSERVICE`).
- **Skips (5):** 1 skip em `test_database_postgres.py:344` (`test_expected_schema_contract_with_i2`, aguardando merge com `sb-i2`; conferido por leitura que `sb-i2` termina em `20261117160300_horus_coordination.sql`, igual a `expected_schema.py:3`) e 4 skips pré-existentes de symlink Windows em `test_endpoint_auth.py`. Aceitáveis neste corte, devendo o integrador rodar `test_expected_schema_contract_with_i2` após integrar I1+I2.

### 7. `api.py`, Snapshot OpenAPI e Inventário
- **Fato (`git diff origin/main...yannbastos/sb-i1`):** `api.py` (12+/3-), `test_network_inventory.py` (1+), `docs/inventario-fluxos-dados.md` (19+) e `backend/tests/snapshots/openapi.json` (420+, zero linhas removidas) contêm apenas as adições necessárias ao escopo de I1.

---

## O que corrigir
1. **Corrigir A1 (`api.py:190-193`)** antes ou no momento da integração: separar `update_reserved` de `app.state.database.changing` para que `POST /v1/database/test` concorrente retorne 200 `connecting` (deduplicado) e mutações não-database não recebam mensagem de atualização reservada.
2. **Endereçar A2, A3, A4 e A5** (severidade Baixa) no polimento ou na integração de I1+I2, e executar `test_expected_schema_contract_with_i2` após o merge com `sb-i2`.
