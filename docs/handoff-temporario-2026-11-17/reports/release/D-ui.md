# HOR-163 D — Experiência de atualização no aplicativo (UI + Playwright)

Data: 17/11/2026. Branch: `yannbastos/rel-d-ui`.
Commit: `a8036ba3a29d748027ab2a51ee6749bc8006c1f4`.

## O que mudou

- **`frontend/src/services/updates.ts`**:
  - Contrato aditivo alinhado ao `C-tauri.md` (Codex C, commit `6fe7c46`): `installed_version`, `available_version`, `notes`, `channel`, `enrolled`, `last_error`, `just_updated`, `updated_from_version`, fase `restarting` e `progress` com `phase` e `downloaded` opcional/anulável.
  - `normalizeUpdateState` para degradação graciosa quando o backend omite campos novos ou usa nomes aditivos.
  - Novos comandos IPC: `enrollUpdateToken(token)` (`update_enroll`) e `clearUpdateEnrollment()` (`update_clear_enrollment`).
  - Mensagens úteis por código sem expor detalhes técnicos ou segredos: `configuration_invalid`, `enrollment_invalid`, `enrollment_required`, `credential_unavailable`, além de `token_rejected`, `token_revoked`, `incompatible_version` e `blocked_by_production`.
- **`frontend/src/components/UpdateSection.tsx`**:
  - Seção **Acesso ao canal** (`data-testid="update-channel-access"`) quando `enrolled` é booleano: exibe estado `Inscrito` / `Não inscrito`, campo mascarado (`type="password"`, `autoComplete="off"`) para colar o token, envio com limpeza imediata do estado/input (nunca exibido depois nem logado) e botão `REMOVER TOKEN`.
  - Metadados com `VERSÃO INSTALADA`, `CANAL` (quando presente) e `ÚLTIMA VERIFICAÇÃO`.
  - Confirmação humana explícita (`INSTALAR E REINICIAR` + diálogo) com aviso visível de que a instalação é adiada ou bloqueada durante produção ou operação crítica em andamento.
  - Progresso (`role="progressbar"`) em `downloading` (determinado com bytes/% ou indeterminado com `Fase: <phase>` quando `downloaded`/`total` ausentes), `installing` e `restarting`, além de aviso pós-atualização (`just_updated`).
  - Notas de versão renderizadas exclusivamente como texto seguro (sem HTML arbitrário).
  - Identidade visual preservada com tokens de `tokens.css` e classes existentes de `app.css`.
- **`frontend/tests/updater-settings.spec.ts` e `frontend/tests/updater-security.spec.ts`**:
  - Cobertura ampliada para 33 testes Playwright (mocks alinhados ao contrato do Codex C): fluxo completo até reinício e pós-atualização, inscrição/remoção de token via teclado + `axe-core`, degradação graciosa sem campos novos, progresso indeterminado sem `downloaded`/`total`, segredo ausente do DOM/storage/console/requisições HTTP, códigos de erro de inscrição/token/compatibilidade/produção e notas com HTML/script escapadas.

## Verificações

- `npm --prefix frontend run build`: **aprovado** (`tsc --noEmit && vite build`, após última edição).
- `npm --prefix frontend test -- tests/updater-settings.spec.ts tests/updater-security.spec.ts`: **33 passed / 0 failed**.
- `npm --prefix frontend test` (suíte completa, 315 testes): **310 passed / 4 skipped / 1 failed**. A única falha foi `tests/privacy-security-null-regression.spec.ts:426:3` por ausência de `backend/.venv/Scripts/python.exe` neste worktree isolado (`backend` fora do escopo).

## Contrato IPC / alinhamento com Codex C

- Alinhado integralmente aos comandos (`update_enroll`, `update_clear_enrollment`, `update_set_auto_check`, `update_check`, `update_install`, `update_get_state`) e campos aditivos de `C-tauri.md`.
- Observação para integração: caso o backend passe a distinguir HTTP 401/403 como `token_rejected`/`token_revoked` ou emitir `restarting`/`just_updated`/`updated_from_version`, a UI já os consome nativamente; no contrato atual do Codex C, 401/403 ainda chegam como `network` e a UI já oferece "Tentar novamente" e orienta verificar conexão/acesso.
