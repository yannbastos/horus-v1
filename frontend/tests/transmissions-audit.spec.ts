import config from '../playwright.config';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const corsHeaders = {
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type,idempotency-key',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
};

function buildSecurityStatus() {
  return {
    schema_version: '1.0',
    checked_at: '2026-11-14T20:00:00Z',
    real_execution_allowed: true,
    blockers: [],
    processing: {
      local_only: true,
      workflow_executor_default: 'local',
      radar_executor: 'local',
      external_connections: [
        {
          id: 'hermes',
          kind: 'loopback',
          target: 'http://127.0.0.1:8780',
          enabled: true,
          purpose: 'Orquestração editorial local',
        },
      ],
    },
    hermes: {
      connection: 'loopback',
      profile_found: true,
      profile_label: 'editorial-restrito',
      tools: {
        source: 'profile',
        restricted: true,
        effective: ['read_file'],
        dangerous: ['shell'],
        required_for_editorial: ['read_file'],
      },
      memory: {
        local_enabled: true,
        user_profile_enabled: false,
        write_approval: true,
        external_provider: null,
      },
      provider_configured: true,
      network_isolation: { enforced: true, mechanism: 'loopback', reason: null },
      cost_limits: { configured: true, max_tokens_per_run: 4000, max_tokens_per_day: 20000 },
    },
    radar: {
      retention_days: 28,
      network_enabled: false,
      automatic_cleanup: { available: true, interval_hours: 6, last_run_at: null },
    },
    credentials: { items: [] },
    private_mode: { available: false, enabled: false, reason: 'Ainda não implementado no backend.' },
    audit: {
      available: true,
      endpoint: '/v1/security/transmissions',
      append_only: true,
      content_stored: false,
    },
  };
}

const sampleItems = [
  {
    seq: 1,
    id: 'tx-1',
    at: '2026-11-14T18:10:00Z',
    kind: 'submit',
    stage: 'analysis',
    workflow_id: 'wf-1',
    job_id: 'job-1',
    attempt_id: 'att-1',
    run_id: 'run-1',
    destination: 'http://127.0.0.1:8780',
    provider: 'hermes',
    model: null,
    envelope_bytes: 1536,
    envelope_sha256: 'a'.repeat(64),
    authorization_id: 'auth-1',
    result: 'sent',
    detail: null,
    tokens_total: null,
    cost_usd: null,
  },
  {
    seq: 2,
    id: 'tx-2',
    at: '2026-11-14T18:15:00Z',
    kind: 'gate_refusal',
    stage: 'script',
    workflow_id: 'wf-2',
    job_id: null,
    attempt_id: null,
    run_id: null,
    destination: null,
    provider: 'hermes',
    model: null,
    envelope_bytes: null,
    envelope_sha256: null,
    authorization_id: null,
    result: 'refused_gate',
    detail: 'security_gate',
    tokens_total: null,
    cost_usd: null,
  },
  {
    seq: 3,
    id: 'tx-3',
    at: '2026-11-14T18:20:00Z',
    kind: 'probe',
    stage: 'diagnostic',
    workflow_id: null,
    job_id: null,
    attempt_id: null,
    run_id: null,
    destination: 'http://127.0.0.1:8780',
    provider: 'hermes',
    model: null,
    envelope_bytes: null,
    envelope_sha256: null,
    authorization_id: null,
    result: 'sent',
    detail: null,
    tokens_total: null,
    cost_usd: null,
  },
];

type TransmissionsRouteHandler = (params: { after: number; limit: number }) => Promise<{
  status?: number;
  delayMs?: number;
  body?: unknown;
}> | {
  status?: number;
  delayMs?: number;
  body?: unknown;
};

async function setupRoutes(page: Page, handleTransmissions: TransmissionsRouteHandler) {
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: corsHeaders });
    }
    if (url.pathname === '/health') {
      return route.fulfill({ status: 200, headers: corsHeaders, json: { status: 'ready' } });
    }
    if (url.pathname === '/v1/hermes') {
      return route.fulfill({
        status: 200,
        headers: corsHeaders,
        json: { state: 'ready', connected: true, cli_available: true, configured: true, features: [] },
      });
    }
    if (url.pathname === '/v1/diagnostics') {
      return route.fulfill({ status: 200, headers: corsHeaders, json: { checks: [], notice: '' } });
    }
    if (url.pathname === '/v1/security/status') {
      return route.fulfill({ status: 200, headers: corsHeaders, json: buildSecurityStatus() });
    }
    if (url.pathname === '/v1/security/transmissions') {
      const after = Number(url.searchParams.get('after') ?? '0');
      const limit = Number(url.searchParams.get('limit') ?? '20');
      const result = await handleTransmissions({ after, limit });
      if (result.delayMs) {
        await new Promise(resolve => setTimeout(resolve, result.delayMs));
      }
      return route.fulfill({
        status: result.status ?? 200,
        headers: corsHeaders,
        json: result.body ?? { items: [], next_after: null },
      });
    }
    return route.fulfill({ status: 200, headers: corsHeaders, json: {} });
  });
}

async function openPrivacyTab(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill('test-session-token-0123456789');
  await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();
  await expect(page.getByText('Backend local conectado.')).toBeVisible();
  await page.getByRole('tab', { name: 'PRIVACIDADE E SEGURANÇA', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Auditoria de Transmissões' })).toBeVisible();
}

test.describe('HOR-115 · Registro de transmissões em Privacidade e Segurança', () => {
  test('exibe tabela acessível com metadados (quando, destino, finalidade, decisão e tamanho) e passa no axe', async ({
    page,
  }) => {
    await setupRoutes(page, () => ({
      body: { items: sampleItems, next_after: null },
    }));
    await openPrivacyTab(page);

    const auditRegion = page.getByRole('region', { name: 'Auditoria de Transmissões' });
    await expect(auditRegion.getByText('ATIVO', { exact: true })).toBeVisible();

    const table = auditRegion.getByRole('table', { name: 'Registro de transmissões' });
    await expect(table).toBeVisible();

    await expect(table.getByTestId('transmission-row-tx-1')).toContainText('1,5 KB');
    await expect(table.getByTestId('transmission-row-tx-1')).toContainText('PERMITIDA');
    await expect(table.getByTestId('transmission-row-tx-1')).toContainText('Análise editorial');

    await expect(table.getByTestId('transmission-row-tx-2')).toContainText('RECUSADA');
    await expect(table.getByTestId('transmission-row-tx-2')).toContainText('Geração de roteiro');
    await expect(table.getByTestId('transmission-row-tx-2')).toContainText('—');

    await expect(table.getByTestId('transmission-row-tx-3')).toContainText('Teste de conexão');

    const axe = await new AxeBuilder({ page })
      .include('[data-testid="transmissions-audit"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    expect(axe.violations).toEqual([]);
  });

  test('filtra por destino, finalidade e decisão e permite limpar filtros', async ({ page }) => {
    await setupRoutes(page, () => ({
      body: { items: sampleItems, next_after: null },
    }));
    await openPrivacyTab(page);

    const audit = page.getByTestId('transmissions-audit');
    await expect(audit.locator('tbody tr')).toHaveCount(3);

    await audit.getByLabel('Decisão').selectOption('refused');
    await expect(audit.locator('tbody tr')).toHaveCount(1);
    await expect(audit.getByTestId('transmission-row-tx-2')).toBeVisible();

    await audit.getByLabel('Decisão').selectOption('all');
    await audit.getByLabel('Finalidade').selectOption('diagnostic');
    await expect(audit.locator('tbody tr')).toHaveCount(1);
    await expect(audit.getByTestId('transmission-row-tx-3')).toBeVisible();

    await audit.getByLabel('Finalidade').selectOption('all');
    await audit.getByLabel('Destino').fill('127.0.0.1');
    await expect(audit.locator('tbody tr')).toHaveCount(2);

    await audit.getByLabel('Destino').fill('inexistente.local');
    await expect(audit.getByTestId('transmissions-filter-empty')).toBeVisible();

    await audit.getByRole('button', { name: 'LIMPAR FILTROS' }).click();
    await expect(audit.locator('tbody tr')).toHaveCount(3);
  });

  test('pagina via cursor (after / next_after) entre páginas', async ({ page }) => {
    const requestedAfters: number[] = [];
    await setupRoutes(page, ({ after }) => {
      requestedAfters.push(after);
      if (after === 0) {
        return { body: { items: sampleItems.slice(0, 2), next_after: 2 } };
      }
      return { body: { items: sampleItems.slice(2), next_after: null } };
    });
    await openPrivacyTab(page);

    const audit = page.getByTestId('transmissions-audit');
    const prevBtn = audit.getByRole('button', { name: 'PÁGINA ANTERIOR' });
    const nextBtn = audit.getByRole('button', { name: 'PRÓXIMA PÁGINA' });

    await expect(audit.getByTestId('transmissions-page-info')).toContainText('Página 1');
    await expect(prevBtn).toBeDisabled();
    await expect(nextBtn).toBeEnabled();

    await nextBtn.click();
    await expect(audit.getByTestId('transmissions-page-info')).toContainText('Página 2');
    await expect(audit.getByTestId('transmission-row-tx-3')).toBeVisible();
    await expect(prevBtn).toBeEnabled();
    await expect(nextBtn).toBeDisabled();

    await prevBtn.click();
    await expect(audit.getByTestId('transmissions-page-info')).toContainText('Página 1');
    await expect(audit.getByTestId('transmission-row-tx-1')).toBeVisible();
    // O React StrictMode (dev) executa o efeito inicial duas vezes: a 1ª requisição é abortada pelo cleanup, mas já pode ter
    // chegado à rota. O que se verifica é a sequência de cursores (0 → 2 → 0), sem exigir uma única busca inicial.
    const cursors = requestedAfters.filter((value, index) => index === 0 || value !== requestedAfters[index - 1]);
    expect(cursors).toEqual([0, 2, 0]);
    expect(requestedAfters.length).toBeLessThanOrEqual(4);
  });

  test('exibe estados carregando, vazio e erro com recuperação via StatusMessage', async ({ page }) => {
    let mode: 'empty' | 'error' | 'ok' = 'empty';

    await setupRoutes(page, () => {
      if (mode === 'empty') {
        return { delayMs: 400, body: { items: [], next_after: null } };
      }
      if (mode === 'error') {
        return { status: 500, body: { detail: 'erro interno' } };
      }
      return { body: { items: [sampleItems[0]], next_after: null } };
    });

    await openPrivacyTab(page);

    const audit = page.getByTestId('transmissions-audit');
    await expect(audit.getByTestId('transmissions-loading')).toHaveAttribute('role', 'status');
    await expect(audit.getByTestId('transmissions-empty')).toBeVisible();

    mode = 'error';
    await audit.getByRole('button', { name: 'ATUALIZAR REGISTRO' }).click();
    const errorMsg = audit.getByTestId('transmissions-error');
    await expect(errorMsg).toBeVisible();
    await expect(errorMsg).toHaveAttribute('role', 'alert');

    mode = 'ok';
    await audit.getByRole('button', { name: 'TENTAR NOVAMENTE' }).click();
    await expect(audit.getByTestId('transmission-row-tx-1')).toBeVisible();
  });
});
