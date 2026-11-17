# HOR-158 (frontend) — Limites reais de checkpoint do backend na UI

## Resumo
A interface de aprovação humana (`EditorialWorkflows.tsx` → `HumanCheckpointDialog.tsx`) deixou de usar textos fixos (`US$ 0,20` / `15 minutos (900 segundos)`) e passou a ler e exibir exclusivamente os limites reais retornados por `GET /v1/security/status`:
- `hermes.cost_limits.reservation_per_run_usd` (`string | null`)
- `hermes.checkpoint_limits` (`{ default_ttl_seconds, max_ttl_seconds }`)

## Arquivos alterados
- `frontend/src/services/api.ts`: tipagem aditiva e opcional para `reservation_per_run_usd` em `SecurityHermesCostLimits` e `SecurityHermesCheckpointLimits` em `SecurityHermes`.
- `frontend/src/services/checkpoints.ts`: adicionados `formatDecimalUsdPtBr` (formatação pt-BR de string decimal sem conversão para float), `formatCheckpointValidityPtBr` e `resolveCheckpointLimits`.
- `frontend/src/components/HumanCheckpointDialog.tsx`: suporte a `loadingLimits` e `limitStatus` (`configured` | `unconfigured` | `unknown`), exibindo `StatusMessage` acessível (`role="alert"`) e ocultando o checkbox e o botão de confirmação quando não há limite configurado/informado (mantendo apenas `Fechar`, com foco acessível).
- `frontend/src/features/EditorialWorkflows.tsx`: consulta `GET /v1/security/status` ao conectar e ao abrir o diálogo, aplica `resolveCheckpointLimits` e impede envio de confirmação se `limitStatus !== 'configured'`. A requisição `POST /v1/workflows/:id/authorize` continua enviando apenas `{ expected_revision, job_id }`, sem campos editáveis nem ampliação de limites.
- `frontend/tests/human-checkpoints.spec.ts`: atualizados os mocks dos testes existentes para o contrato atual e adicionados 3 testes cobrindo `0.37` USD / `1200` s (sem campo editável e sem ampliação no payload), `null` (bloqueio sem contorno + axe) e backend antigo sem os campos (`Não informado`, sem erro + axe).

## Tratamento dos 3 cenários
1. **Valor configurado (ex.: `0.37` e `1200` s)**: exibe `US$ 0,37 (USD)` (`Até US$ 0,37 (USD)`) e `20 minutos (1200 segundos)`; habilita checkbox e confirmação sem enviar valores acima do informado.
2. **`reservation_per_run_usd: null`**: exibe `Não configurado` e alerta acessível informando que a execução real permanece bloqueada pelo backend, sem checkbox ou botão de contorno.
3. **Backend antigo (campos ausentes / erro em `/v1/security/status`)**: degrada sem erros JS para `Não informado`, exibe alerta acessível de bloqueio e não oferece ação de contorno.

## Validação
- `npm --prefix frontend run build`: aprovado (`tsc --noEmit && vite build`).
- `npm --prefix frontend test -- tests/human-checkpoints.spec.ts`: 6 passed.
- `npm --prefix frontend test` (suíte completa): 296 passed, 4 skipped (`e2e-production-journey.spec.ts`), 1 failed (`tests/privacy-security-null-regression.spec.ts:426:3`, por ausência de `backend/.venv/Scripts/python.exe` no worktree isolado).
