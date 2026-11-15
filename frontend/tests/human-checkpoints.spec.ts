import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const BACKEND_URL = 'http://127.0.0.1:8765';
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Authorization, Content-Type, Idempotency-Key',
};

const baseSecurityStatus = {
  schema_version: 'security.status.v1',
  checked_at: '2026-11-14T22:00:00Z',
  real_execution_allowed: true,
  blockers: [],
  processing: {
    local_only: true,
    external_connections: [],
  },
  hermes: {
    connection: 'loopback',
    profile_found: true,
    profile_label: 'horus',
    tools: { restricted: true, dangerous_tools_enabled: [], enabled_tools: [] },
    memory: { external_enabled: false, isolated: true },
    provider_configured: true,
    network_isolation: { isolated: true },
    cost_limits: { max_usd_per_run: '0.20' },
  },
  credentials: { items: [] },
  private_mode: { available: false, enabled: false, reason: 'Indisponível nesta versão.' },
  audit: { available: false, reason: 'Indisponível nesta versão.' },
};

function makeWorkflow(overrides: Record<string, unknown> = {}) {
  return {
    id: 'wf-hermes-107',
    revision: 2,
    status: 'awaiting_approval',
    executor: 'hermes',
    simulated: false,
    request: {
      snapshot_id: 'snap-1',
      revision: 1,
      objective: 'Analisar tema de vídeo sobre IA local',
      channel: { channel_id: 'ch-1', summary: 'Canal técnico' },
    },
    radar_block: null,
    current_artifact_id: null,
    steps: [
      { id: 's1', step_key: 'planning', status: 'succeeded', workflow_revision: 2 },
      { id: 's2', step_key: 'research', status: 'succeeded', workflow_revision: 2 },
      { id: 's3', step_key: 'analysis', status: 'awaiting_authorization', workflow_revision: 2 },
      { id: 's4', step_key: 'proposal', status: 'waiting', workflow_revision: 2 },
    ],
    artifacts: [],
    approvals: [],
    cognitive_runs: [],
    authorizations: [],
    pending_authorization: {
      job_id: 'job-cog-1',
      stage: 'analysis',
    },
    created_at: '2026-11-14T21:50:00Z',
    updated_at: '2026-11-14T21:55:00Z',
    ...overrides,
  };
}

async function connectSession(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill('test-session-token-0123456789');
  await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();
  await expect(page.getByText('Backend local conectado.')).toBeVisible();
}

async function assertNoAxeViolations(page: Page, context: string) {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  expect(results.violations, `Violações axe em ${context}: ${JSON.stringify(results.violations)}`).toEqual([]);
}

test.describe('Checkpoints humanos (HOR-107)', () => {
  test('ação no Hermes exibe diálogo com escopo, limite em USD, validade, uso único e confirmação explícita + axe', async ({
    page,
  }) => {
    let workflow = makeWorkflow();
    let authorizeCalls = 0;

    await page.route(`${BACKEND_URL}/**`, async route => {
      const request = route.request();
      if (request.method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers: corsHeaders });
        return;
      }
      const path = new URL(request.url()).pathname;

      if (path === '/health') {
        await route.fulfill({
          status: 200,
          headers: corsHeaders,
          json: { status: 'ready', database_id: 'db-1', hermes_connected: true, version: '0.1.0' },
        });
        return;
      }
      if (path === '/v1/hermes') {
        await route.fulfill({
          status: 200,
          headers: corsHeaders,
          json: { state: 'ready', connected: true, cli_available: true, configured: true, message: 'OK', checked_at: null, version: '1.0', features: [] },
        });
        return;
      }
      if (path === '/v1/workflows') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: [workflow] });
        return;
      }
      if (path === `/v1/workflows/${workflow.id}`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: workflow });
        return;
      }
      if (path === `/v1/workflows/${workflow.id}/scripts`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: [] });
        return;
      }
      if (path === `/v1/workflows/${workflow.id}/authorize` && request.method() === 'POST') {
        authorizeCalls += 1;
        workflow = makeWorkflow({
          revision: 3,
          status: 'running',
          pending_authorization: null,
        });
        await route.fulfill({ status: 200, headers: corsHeaders, json: workflow });
        return;
      }
      await route.fulfill({ status: 200, headers: corsHeaders, json: {} });
    });

    await connectSession(page);
    await page.getByRole('button', { name: 'Produção', exact: true }).click();
    await expect(page.getByText('CHECKPOINT HUMANO')).toBeVisible();

    await page.getByRole('button', { name: 'Autorizar uma execução' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    await expect(dialog.getByText(/O QUÊ/i)).toBeVisible();
    await expect(dialog.getByText(/Execução cognitiva no Hermes/i)).toBeVisible();
    await expect(dialog.getByText(/CUSTO ESTIMADO E LIMITE EM USD/i)).toBeVisible();
    await expect(dialog.getByText(/US\$ 0,20 \(USD\)/).first()).toBeVisible();
    await expect(dialog.getByText(/15 minutos \(900 segundos\)/)).toBeVisible();
    await expect(dialog.getByText(/Uso único: esta aprovação será consumida/i)).toBeVisible();

    const confirmBtn = dialog.getByRole('button', { name: /Confirmar checkpoint e autorizar/i });
    await expect(confirmBtn).toBeDisabled();

    await assertNoAxeViolations(page, 'diálogo de checkpoint humano');

    // Escape fecha sem autorizar
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    expect(authorizeCalls).toBe(0);

    // Reabre, marca confirmação explícita e envia
    await page.getByRole('button', { name: 'Autorizar uma execução' }).click();
    await expect(dialog).toBeVisible();
    await dialog.getByRole('checkbox').check();
    await expect(confirmBtn).toBeEnabled();
    await confirmBtn.click();

    expect(authorizeCalls).toBe(1);
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Autorizar uma execução' })).toHaveCount(0);
  });

  test('recusas 409 do SecurityGate são exibidas em linguagem simples', async ({ page }) => {
    const workflow = makeWorkflow();
    const scenarios = [
      {
        message: 'security_gate: O checkpoint expirou; peça uma nova aprovação humana.',
        expected: /prazo de validade da aprovação humana expirou/i,
      },
      {
        message: 'security_gate: O checkpoint já foi usado; uma nova aprovação humana é necessária.',
        expected: /já foi usada \(uso único\)/i,
      },
      {
        message: 'security_gate: O valor da ação excede o limite aprovado pelo humano.',
        expected: /ultrapassa o limite em USD aprovado/i,
      },
      {
        message: 'security_gate: Ação exige aprovação humana registrada (checkpoint) para este objeto.',
        expected: /falta uma aprovação humana \(checkpoint\) válida/i,
      },
    ];
    let attempt = 0;

    await page.route(`${BACKEND_URL}/**`, async route => {
      const request = route.request();
      if (request.method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers: corsHeaders });
        return;
      }
      const path = new URL(request.url()).pathname;

      if (path === '/health') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { status: 'ready', database_id: 'db-1', hermes_connected: true, version: '0.1.0' } });
        return;
      }
      if (path === '/v1/hermes') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { state: 'ready', connected: true, cli_available: true, configured: true, message: 'OK', checked_at: null, version: '1.0', features: [] } });
        return;
      }
      if (path === '/v1/workflows') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: [workflow] });
        return;
      }
      if (path === `/v1/workflows/${workflow.id}`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: workflow });
        return;
      }
      if (path === `/v1/workflows/${workflow.id}/scripts`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: [] });
        return;
      }
      if (path === `/v1/workflows/${workflow.id}/authorize` && request.method() === 'POST') {
        const current = scenarios[attempt] ?? scenarios[0];
        attempt += 1;
        await route.fulfill({
          status: 409,
          headers: corsHeaders,
          json: { detail: { code: 'security_gate', message: current.message } },
        });
        return;
      }
      await route.fulfill({ status: 200, headers: corsHeaders, json: {} });
    });

    await connectSession(page);
    await page.getByRole('button', { name: 'Produção', exact: true }).click();
    await page.getByRole('button', { name: 'Autorizar uma execução' }).click();

    const dialog = page.getByRole('dialog');
    await dialog.getByRole('checkbox').check();
    const confirmBtn = dialog.getByRole('button', { name: /Confirmar checkpoint e autorizar/i });

    for (const scenario of scenarios) {
      await confirmBtn.click();
      const alert = dialog.getByTestId('checkpoint-dialog-error');
      await expect(alert).toBeVisible();
      await expect(alert).toContainText(scenario.expected);
      await expect(alert).not.toContainText('security_gate:');
    }
  });

  test('lista de checkpoints recentes (ativo/usado/expirado) em Ajustes → Privacidade e Segurança + filtros + paginação + axe', async ({
    page,
  }) => {
    const page1 = {
      items: [
        {
          seq: 1,
          id: 'cp-expired-1',
          action: 'paid_narration',
          subject: 'a'.repeat(64),
          description: 'Narração paga vencida',
          approved_by: 'human',
          operator_id: 'local-operator',
          approved_at: '2026-11-10T10:00:00+00:00',
          expires_at: '2026-11-10T10:15:00+00:00',
          limit_amount: '1.50',
          currency: 'USD',
          consumed_at: null,
          consumed_ref: null,
        },
        {
          seq: 2,
          id: 'cp-used-2',
          action: 'hermes_run',
          subject: 'b'.repeat(64),
          description: 'Análise editorial aprovada',
          approved_by: 'human',
          operator_id: 'local-operator',
          approved_at: '2026-11-14T20:00:00+00:00',
          expires_at: '2026-11-14T20:15:00+00:00',
          limit_amount: '0.20',
          currency: 'USD',
          consumed_at: '2026-11-14T20:01:00+00:00',
          consumed_ref: 'attempt-99',
        },
        {
          seq: 3,
          id: 'cp-active-3',
          action: 'publish',
          subject: 'c'.repeat(64),
          description: 'Publicação do vídeo piloto',
          approved_by: 'human',
          operator_id: 'local-operator',
          approved_at: '2026-11-14T21:55:00+00:00',
          expires_at: '2099-01-01T00:00:00+00:00',
          limit_amount: null,
          currency: null,
          consumed_at: null,
          consumed_ref: null,
        },
      ],
      next_after: 3,
    };

    const page2 = {
      items: [
        {
          seq: 4,
          id: 'cp-hermes-script-4',
          action: 'hermes_script',
          subject: 'd'.repeat(64),
          description: 'Roteiro do vídeo piloto via Hermes',
          approved_by: 'human',
          operator_id: 'local-operator',
          approved_at: '2026-11-14T21:58:00+00:00',
          expires_at: '2099-01-01T00:00:00+00:00',
          limit_amount: '0.35',
          currency: 'USD',
          consumed_at: '2026-11-14T21:59:00+00:00',
          consumed_ref: 'script-1',
        },
      ],
      next_after: null,
    };

    await page.route(`${BACKEND_URL}/**`, async route => {
      const request = route.request();
      if (request.method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers: corsHeaders });
        return;
      }
      const url = new URL(request.url());

      if (url.pathname === '/health') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { status: 'ready', database_id: 'db-1', hermes_connected: true, version: '0.1.0' } });
        return;
      }
      if (url.pathname === '/v1/hermes') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { state: 'ready', connected: true, cli_available: true, configured: true, message: 'OK', checked_at: null, version: '1.0', features: [] } });
        return;
      }
      if (url.pathname === '/v1/security/status') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: baseSecurityStatus });
        return;
      }
      if (url.pathname === '/v1/checkpoints') {
        const after = url.searchParams.get('after');
        await route.fulfill({
          status: 200,
          headers: corsHeaders,
          json: after === '3' ? page2 : page1,
        });
        return;
      }
      await route.fulfill({ status: 200, headers: corsHeaders, json: {} });
    });

    await connectSession(page);
    await page.getByRole('tab', { name: 'Privacidade e Segurança' }).click();

    const section = page.getByRole('region', { name: 'Checkpoints Humanos Recentes' });
    await expect(section).toBeVisible();

    const table = section.getByRole('table', { name: 'Checkpoints humanos recentes' });
    await expect(table).toBeVisible();
    await expect(table.locator('tbody tr')).toHaveCount(3);
    await expect(table.getByText('Ativo', { exact: true })).toBeVisible();
    await expect(table.getByText('Usado', { exact: true })).toBeVisible();
    await expect(table.getByText('Expirado', { exact: true })).toBeVisible();
    await expect(table.getByText('US$ 1,50 (USD)')).toBeVisible();
    await expect(table.getByText('Sem limite monetário')).toBeVisible();

    // Filtros
    await section.getByRole('button', { name: 'Ativos', exact: true }).click();
    await expect(table.locator('tbody tr')).toHaveCount(1);
    await expect(table.getByText('Publicação do vídeo piloto')).toBeVisible();

    await section.getByRole('button', { name: 'Usados', exact: true }).click();
    await expect(table.locator('tbody tr')).toHaveCount(1);
    await expect(table.getByText('Análise editorial aprovada')).toBeVisible();

    await section.getByRole('button', { name: 'Expirados', exact: true }).click();
    await expect(table.locator('tbody tr')).toHaveCount(1);
    await expect(table.getByText('Narração paga vencida')).toBeVisible();

    await section.getByRole('button', { name: 'Todos', exact: true }).click();
    await expect(table.locator('tbody tr')).toHaveCount(3);

    // Paginação
    await section.getByRole('button', { name: 'Carregar mais checkpoints' }).click();
    await expect(table.locator('tbody tr')).toHaveCount(4);
    await expect(table.getByText('Roteiro via Hermes')).toBeVisible();

    await assertNoAxeViolations(page, 'Privacidade e Segurança com Checkpoints Humanos');
  });
});
