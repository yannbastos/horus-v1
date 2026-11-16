import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import config from '../playwright.config';

const corsHeaders = () => ({
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type,idempotency-key',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
});

const PRODUCTION_ID = 'aabbccdd11223344556677889900aabb';
const IMPORT_ID = '11223344556677889900aabbccddeeff';
const AUDIT_ID = 'ffeeddccbbaa00998877665544332211';

const baseSecurityStatus = {
  schema_version: 'security.status.v1',
  checked_at: '2026-11-15T12:00:00Z',
  real_execution_allowed: false,
  blockers: [],
  processing: {
    local_only: true,
    workflow_executor_default: 'simulated',
    radar_executor: 'hermes_simulated',
    external_connections: [],
  },
  hermes: {
    profile: 'horus',
    configured: false,
    tools: { restricted: true, effective: [], dangerous: [] },
    memory: { isolated: true },
  },
  radar: { retention_days: 28 },
  private_mode: { available: true, enabled: true, reason: null },
  audit: { available: true, reason: null },
  credentials: { items: [] },
};

const sampleWorkflow = {
  id: 'wf-117-0001',
  status: 'approved',
  revision: 1,
  executor: 'hermes_simulated',
  simulated: true,
  request: {
    objective: 'Vídeo Teste Exclusão',
  },
  current_artifact_id: 'art-prop-117',
  pending_authorization: null,
  steps: [
    {
      id: 'step-1',
      workflow_id: 'wf-117-0001',
      workflow_revision: 1,
      step_key: 'proposal',
      step_index: 0,
      status: 'succeeded',
      attempt: 1,
      created_at: '2026-11-15T10:00:00Z',
      updated_at: '2026-11-15T10:01:00Z',
      depends_on: null,
      error: null,
      result: null,
    },
  ],
  artifacts: [
    {
      id: 'art-prop-117',
      workflow_id: 'wf-117-0001',
      revision: 1,
      stage: 'proposal',
      job_id: 'step-1',
      attempt_id: 'att-1',
      schema_version: 2,
      content: {
        schema_version: 2,
        simulated: true,
        stage: 'proposal',
        title: 'Vídeo Teste Exclusão',
        result_status: 'complete',
        recommendation: {
          title_options: ['Vídeo Teste Exclusão'],
          premise: 'Teste de exclusão segura',
          angle: 'Privacidade local',
          format: 'short',
          originality: 'Local',
          differs_from: [],
          risks: [],
          licensing_notes: 'Demo',
        },
        evidence: { sources: 1, facts: 1, hypotheses: 0 },
        requires_human_review: true,
      },
      content_hash: 'sha256-prop-117',
      created_at: '2026-11-15T10:01:00Z',
    },
  ],
  cognitive_runs: [],
  authorizations: [],
  created_at: '2026-11-15T10:00:00Z',
  updated_at: '2026-11-15T10:05:00Z',
};

const sampleScript = {
  id: 'scr-117-0001',
  workflow_id: 'wf-117-0001',
  workflow_revision: 1,
  proposal_artifact_id: 'art-prop-117',
  proposal_content_hash: 'sha256-prop-117',
  version: 1,
  status: 'approved',
  content_hash: 'sha256-script-117',
  document: {
    schema_version: 1,
    simulated: true,
    title: 'Roteiro Exclusão',
    sections: [
      {
        id: 'sec_1',
        kind: 'hook',
        heading: 'Intro',
        narration: 'Texto do roteiro.',
        source_ids: [],
      },
    ],
  },
  review: {
    decision: 'approve',
    feedback: 'Aprovado',
    reviewed_at: '2026-11-15T10:02:00Z',
  },
  created_at: '2026-11-15T10:01:00Z',
};

const sampleProduction = {
  id: PRODUCTION_ID,
  script_id: 'scr-117-0001',
  script_version: 1,
  script_content_hash: 'sha256-script-117',
  job_id: 'job-117-0001',
  mode: 'local_demo',
  stage: 'review',
  status: 'succeeded',
  created_at: '2026-11-15T10:03:00Z',
  updated_at: '2026-11-15T10:05:00Z',
  artifacts: [
    {
      id: 'art-video-1',
      kind: 'video',
      relative_path: 'output/video.mp4',
      size_bytes: 2048000,
      sha256: 'a'.repeat(64),
    },
    {
      id: 'art-plan-1',
      kind: 'plan',
      relative_path: 'plan.json',
      size_bytes: 1024,
      sha256: 'b'.repeat(64),
    },
  ],
  review: null,
  local_data: {
    state: 'present',
    audit_id: null,
    changed_at: null,
  },
};

const sampleImportSummary = {
  id: IMPORT_ID,
  production_id: PRODUCTION_ID,
  title: 'Projeto Importado Teste',
  imported_at: '2026-11-15T11:00:00Z',
  exported_at: '2026-11-15T10:30:00Z',
  horus_version: '0.1.0',
  source_package_name: 'pacote-teste.zip',
  publishable: false,
};

const sampleImportDetail = {
  ...sampleImportSummary,
  manifest: {
    schema_version: 1,
    horus_version: '0.1.0',
    database_schema_version: 10,
    id: IMPORT_ID,
    production_id: PRODUCTION_ID,
    created_at: '2026-11-15T10:30:00Z',
    publishable: false,
    metadata_redacted: true,
    files: [{ name: 'video.mp4', sha256: 'c'.repeat(64), size_bytes: 1048576 }],
  },
  script: {
    title: 'Roteiro Importado',
    sections: [{ heading: 'Cena 1', narration: 'Narração importada.' }],
    raw_text: '',
  },
  qc: { status: 'pass', reason: '', checks: [] },
  licenses: { publishable: false, items: [], history_count: 0, raw: null },
  review: {
    production_decision: 'approve',
    production_reviewed_at: '2026-11-15T10:20:00Z',
    production_notes: '',
    script_status: 'approved',
    script_version: 1,
    publishable: false,
  },
  video_path: '',
  subtitles_path: '',
};

type MockState = {
  productionDeleted: boolean;
  importDeleted: boolean;
  restored: boolean;
  blockedPreview: boolean;
  executeCalls: Array<{ plan_id: string; intent_token: string; phrase: string }>;
  restoreCalls: string[];
};

async function setupRoutes(page: Page, state: MockState) {
  const headers = corsHeaders();
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (pathname === '/health') {
      await route.fulfill({ status: 200, headers, json: { status: 'ready' } });
      return;
    }
    if (pathname === '/v1/hermes') {
      await route.fulfill({
        status: 200,
        headers,
        json: { state: 'ready', connected: true, cli_available: true, configured: true, features: [] },
      });
      return;
    }
    if (pathname === '/v1/diagnostics') {
      await route.fulfill({ status: 200, headers, json: { checks: [], notice: 'OK' } });
      return;
    }
    if (pathname === '/v1/security/status') {
      await route.fulfill({ status: 200, headers, json: baseSecurityStatus });
      return;
    }
    if (pathname === '/v1/jobs') {
      await route.fulfill({ status: 200, headers, json: [] });
      return;
    }
    if (pathname === '/v1/workflows') {
      await route.fulfill({ status: 200, headers, json: [sampleWorkflow] });
      return;
    }
    if (pathname === `/v1/workflows/${sampleWorkflow.id}`) {
      await route.fulfill({ status: 200, headers, json: sampleWorkflow });
      return;
    }
    if (pathname === `/v1/workflows/${sampleWorkflow.id}/scripts`) {
      await route.fulfill({ status: 200, headers, json: { items: [sampleScript] } });
      return;
    }
    if (pathname === `/v1/scripts/${sampleScript.id}/productions`) {
      const current = state.productionDeleted
        ? {
            ...sampleProduction,
            local_data: {
              state: 'deleted',
              audit_id: AUDIT_ID,
              changed_at: '2026-11-15T12:10:00Z',
            },
          }
        : sampleProduction;
      await route.fulfill({ status: 200, headers, json: { items: [current] } });
      return;
    }
    if (pathname === `/v1/productions/${PRODUCTION_ID}`) {
      const current = state.productionDeleted
        ? {
            ...sampleProduction,
            local_data: {
              state: 'deleted',
              audit_id: AUDIT_ID,
              changed_at: '2026-11-15T12:10:00Z',
            },
          }
        : sampleProduction;
      await route.fulfill({ status: 200, headers, json: current });
      return;
    }
    if (pathname === `/v1/productions/${PRODUCTION_ID}/exports`) {
      await route.fulfill({ status: 200, headers, json: { items: [] } });
      return;
    }
    if (pathname === `/v1/productions/${PRODUCTION_ID}/license-reviews`) {
      await route.fulfill({ status: 200, headers, json: { items: [] } });
      return;
    }
    if (pathname === `/v1/jobs/${sampleProduction.job_id}/events`) {
      await route.fulfill({
        status: 200,
        headers: { ...headers, 'Content-Type': 'text/event-stream' },
        body: 'data: {"sequence":1,"kind":"complete","message":"ok"}\n\n',
      });
      return;
    }
    if (pathname === '/v1/imports') {
      await route.fulfill({
        status: 200,
        headers,
        json: state.importDeleted ? [] : [sampleImportSummary],
      });
      return;
    }
    if (pathname === `/v1/imports/${IMPORT_ID}`) {
      await route.fulfill({ status: 200, headers, json: sampleImportDetail });
      return;
    }
    if (pathname === '/v1/media') {
      await route.fulfill({ status: 200, headers, json: [] });
      return;
    }
    if (pathname === '/v1/data-deletion/preview') {
      const body = request.postDataJSON() as { scope: 'production' | 'import'; id: string };
      const isProd = body.scope === 'production';
      const phrase = isProd
        ? `APAGAR producao ${body.id.slice(0, 8)}`
        : `APAGAR importacao ${body.id.slice(0, 8)}`;
      await route.fulfill({
        status: 200,
        headers,
        json: {
          plan_id: 'plan11223344556677889900aabbccdd',
          intent_token: 'intent-token-1234567890abcdef',
          expires_in_seconds: 300,
          scope: body.scope,
          id: body.id,
          confirmation_phrase: phrase,
          items: isProd
            ? [
                {
                  kind: 'production_folder',
                  path: `productions/${body.id}`,
                  exists: true,
                  files: 4,
                  bytes: 2049024,
                },
                {
                  kind: 'exports',
                  path: `exports/${body.id}`,
                  exists: false,
                  files: 0,
                  bytes: 0,
                },
              ]
            : [
                {
                  kind: 'import_folder',
                  path: `imports/${body.id}`,
                  exists: true,
                  files: 3,
                  bytes: 1048576,
                },
              ],
          totals: isProd ? { files: 4, bytes: 2049024 } : { files: 3, bytes: 1048576 },
          db_kept: isProd
            ? [
                {
                  table: 'productions',
                  rows: 1,
                  reason: 'preserva a trilha; o banco não é alterado nesta exclusão',
                },
                {
                  table: 'license_reviews',
                  rows: 2,
                  reason: 'append-only: não pode ser apagado nem alterado',
                },
              ]
            : [],
          dependencies: isProd
            ? [
                {
                  kind: 'script',
                  id: sampleScript.id,
                  version: 1,
                  note: 'roteiro aprovado usado pela produção; não é apagado',
                },
              ]
            : [],
          blockers: state.blockedPreview
            ? [
                {
                  code: 'active_jobs',
                  message: 'Há 1 tarefa(s) ativa(s); aguarde terminar ou cancele antes de excluir.',
                },
              ]
            : [],
          can_execute: !state.blockedPreview,
          notice:
            'Os arquivos vão para a quarentena (recuperáveis); o pacote local de pré-exclusão cobre banco e manifestos, não vídeos.',
        },
      });
      return;
    }
    if (pathname === '/v1/data-deletion/execute') {
      const body = request.postDataJSON() as { plan_id: string; intent_token: string; phrase: string };
      state.executeCalls.push(body);
      if (body.phrase.includes('producao')) {
        state.productionDeleted = true;
      } else {
        state.importDeleted = true;
      }
      await route.fulfill({
        status: 200,
        headers,
        json: {
          audit_id: AUDIT_ID,
          scope: body.phrase.includes('producao') ? 'production' : 'import',
          id: body.phrase.includes('producao') ? PRODUCTION_ID : IMPORT_ID,
          items: [
            {
              path: `productions/${PRODUCTION_ID}`,
              files: 4,
              bytes: 2049024,
              digest: 'd'.repeat(64),
            },
          ],
          backup: {
            name: 'pre-exclusao-20261115T120000Z-ffeeddcc.zip',
            sha256: 'e'.repeat(64),
            bytes: 12345,
            verified: true,
            location: 'horus-backups',
          },
          quarantine: `.quarentena/${AUDIT_ID}`,
          recoverable: true,
          totals: { files: 4, bytes: 2049024 },
        },
      });
      return;
    }
    if (pathname === '/v1/data-deletion/audit') {
      const events = [
        ...(state.restored
          ? [
              {
                seq: 3,
                id: 'ev-3',
                at: '2026-11-15T12:15:00Z',
                action: 'restored',
                audit_id: AUDIT_ID,
                scope: 'production',
                target_id: PRODUCTION_ID,
                prev_hash: '2'.repeat(64),
                hash: '3'.repeat(64),
              },
            ]
          : []),
        {
          seq: 2,
          id: 'ev-2',
          at: '2026-11-15T12:10:00Z',
          action: 'completed',
          audit_id: AUDIT_ID,
          scope: 'production',
          target_id: PRODUCTION_ID,
          prev_hash: '1'.repeat(64),
          hash: '2'.repeat(64),
        },
        {
          seq: 1,
          id: 'ev-1',
          at: '2026-11-15T12:09:50Z',
          action: 'started',
          audit_id: AUDIT_ID,
          scope: 'production',
          target_id: PRODUCTION_ID,
          items: [
            {
              path: `productions/${PRODUCTION_ID}`,
              files: 4,
              bytes: 2049024,
              digest: 'd'.repeat(64),
            },
          ],
          backup: {
            name: 'pre-exclusao-20261115T120000Z-ffeeddcc.zip',
            sha256: 'e'.repeat(64),
            bytes: 12345,
            verified: true,
            location: 'horus-backups',
          },
          prev_hash: '0'.repeat(64),
          hash: '1'.repeat(64),
        },
      ];
      await route.fulfill({
        status: 200,
        headers,
        json: { chain_ok: true, items: events, next_before: null },
      });
      return;
    }
    if (pathname === `/v1/data-deletion/${AUDIT_ID}/restore`) {
      state.restoreCalls.push(AUDIT_ID);
      state.restored = true;
      state.productionDeleted = false;
      await route.fulfill({
        status: 200,
        headers,
        json: {
          audit_id: AUDIT_ID,
          scope: 'production',
          id: PRODUCTION_ID,
          restored: [`productions/${PRODUCTION_ID}`],
        },
      });
      return;
    }

    await route.fulfill({ status: 404, headers, json: { detail: 'Not found' } });
  });
}

async function connectBackend(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill('test-session-token-0123456789');
  await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();
  await expect(page.getByText('Backend local conectado.')).toBeVisible();
}

test('exclusão segura de produção: prévia, frase exata, acessibilidade e estado Arquivos excluídos', async ({
  page,
}) => {
  const state: MockState = {
    productionDeleted: false,
    importDeleted: false,
    restored: false,
    blockedPreview: false,
    executeCalls: [],
    restoreCalls: [],
  };
  await setupRoutes(page, state);
  await connectBackend(page);

  await page.getByRole('button', { name: 'Produção', exact: true }).click();
  const deleteBtn = page.getByTestId('production-delete-local-data-btn');
  await expect(deleteBtn).toBeVisible();
  await deleteBtn.click();

  const dialog = page.getByTestId('data-deletion-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId('data-deletion-backup-notice')).toContainText(
    'quarentena recuperável'
  );
  await expect(dialog.getByTestId('data-deletion-items')).toContainText(`productions/${PRODUCTION_ID}`);
  await expect(dialog.getByTestId('data-deletion-db-kept')).toContainText('append-only');
  await expect(dialog.getByTestId('data-deletion-dependencies')).toContainText('scr-117-0001');

  const expectedPhrase = `APAGAR producao ${PRODUCTION_ID.slice(0, 8)}`;
  await expect(dialog.getByTestId('data-deletion-phrase')).toHaveText(expectedPhrase);

  const axeResult = await new AxeBuilder({ page }).include('[data-testid="data-deletion-dialog"]').analyze();
  expect(axeResult.violations).toEqual([]);

  const confirmBtn = dialog.getByTestId('data-deletion-confirm-btn');
  await expect(confirmBtn).toBeDisabled();

  const phraseInput = dialog.getByTestId('data-deletion-phrase-input');
  await phraseInput.fill('APAGAR errado');
  await expect(confirmBtn).toBeDisabled();

  await phraseInput.fill(expectedPhrase);
  await expect(confirmBtn).toBeEnabled();
  await confirmBtn.click();

  await expect(dialog).toHaveCount(0);
  expect(state.executeCalls).toHaveLength(1);
  expect(state.executeCalls[0].phrase).toBe(expectedPhrase);

  await expect(page.getByTestId('production-local-data-deleted-tag')).toHaveText('Arquivos excluídos');
  await expect(page.getByTestId('production-deleted-notice')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Prévia de vídeo (MP4)' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Abrir vídeo' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Exportar pacote/i })).toHaveCount(0);
});

test('exibe bloqueios na prévia e desabilita execução quando can_execute=false', async ({ page }) => {
  const state: MockState = {
    productionDeleted: false,
    importDeleted: false,
    restored: false,
    blockedPreview: true,
    executeCalls: [],
    restoreCalls: [],
  };
  await setupRoutes(page, state);
  await connectBackend(page);

  await page.getByRole('button', { name: 'Produção', exact: true }).click();
  await page.getByTestId('production-delete-local-data-btn').click();

  const dialog = page.getByTestId('data-deletion-dialog');
  await expect(dialog.getByTestId('data-deletion-blockers')).toContainText('tarefa(s) ativa(s)');
  await expect(dialog.getByTestId('data-deletion-confirm-btn')).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Cancelar', exact: true }).click();
  await expect(dialog).toHaveCount(0);
});

test('exclusão segura de projeto importado na Biblioteca', async ({ page }) => {
  const state: MockState = {
    productionDeleted: false,
    importDeleted: false,
    restored: false,
    blockedPreview: false,
    executeCalls: [],
    restoreCalls: [],
  };
  await setupRoutes(page, state);
  await connectBackend(page);

  await page.getByRole('button', { name: 'Biblioteca', exact: true }).click();
  await page.getByRole('button', { name: /Projeto Importado Teste/ }).click();
  await expect(page.getByTestId('imported-project-detail')).toBeVisible();

  await page.getByTestId('import-delete-local-data-btn').click();
  const dialog = page.getByTestId('data-deletion-dialog');
  const expectedPhrase = `APAGAR importacao ${IMPORT_ID.slice(0, 8)}`;
  await expect(dialog.getByTestId('data-deletion-phrase')).toHaveText(expectedPhrase);

  await dialog.getByTestId('data-deletion-phrase-input').fill(expectedPhrase);
  await dialog.getByTestId('data-deletion-confirm-btn').click();

  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId('import-delete-success')).toContainText('quarentena recuperável');
  await expect(page.getByTestId('imported-project-detail')).toHaveCount(0);
});

test('histórico de exclusões em Ajustes → Privacidade e Segurança com Restaurar e axe', async ({
  page,
}) => {
  const state: MockState = {
    productionDeleted: true,
    importDeleted: false,
    restored: false,
    blockedPreview: false,
    executeCalls: [],
    restoreCalls: [],
  };
  await setupRoutes(page, state);
  await connectBackend(page);

  await page.getByRole('tab', { name: 'PRIVACIDADE E SEGURANÇA' }).click();
  await expect(page.getByTestId('data-deletion-audit-list')).toBeVisible();

  const restoreBtn = page.getByTestId(`restore-deletion-${AUDIT_ID}`);
  await expect(restoreBtn).toBeVisible();

  const axeResult = await new AxeBuilder({ page }).analyze();
  expect(axeResult.violations).toEqual([]);

  await restoreBtn.click();
  expect(state.restoreCalls).toEqual([AUDIT_ID]);
  await expect(page.getByTestId('data-deletion-restore-success')).toContainText(
    'restaurados da quarentena com sucesso'
  );
  await expect(page.getByTestId(`restore-deletion-${AUDIT_ID}`)).toHaveCount(0);
});
