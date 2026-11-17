import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import config from '../playwright.config';

const CORS_HEADERS = () => ({
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type',
  'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS',
});

type DbStateName =
  | 'not_configured'
  | 'connecting'
  | 'connected'
  | 'unreachable'
  | 'auth_failed'
  | 'schema_mismatch'
  | 'migrating'
  | 'vault_unavailable';

type MockDbStatus = {
  state: DbStateName;
  latency_ms: number | null;
  schema_version: string | null;
  expected_schema_version: string | null;
  last_error_code: string | null;
  host_label: string | null;
  checked_at: string | null;
};

function makeStatus(overrides: Partial<MockDbStatus> = {}): MockDbStatus {
  return {
    state: 'connected',
    latency_ms: 42,
    schema_version: '20261117_01',
    expected_schema_version: '20261117_01',
    last_error_code: null,
    host_label: 'aws-0-sa-east-1.pooler.supabase.com:5432/postgres',
    checked_at: '2026-11-17T16:00:00Z',
    ...overrides,
  };
}

async function connectSession(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.locator('.sidebar').getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill('token-sessao-horus-playwright-0123456789');
  await page.getByRole('button', { name: 'CONECTAR BACKEND' }).click();
  await expect(page.getByText('Backend local conectado.')).toBeVisible();
}

async function handleCommonRoutes(
  route: import('@playwright/test').Route,
  url: URL,
  headers: Record<string, string>,
): Promise<boolean> {
  if (route.request().method() === 'OPTIONS') {
    await route.fulfill({ status: 204, headers });
    return true;
  }
  if (url.pathname === '/health') {
    await route.fulfill({ status: 200, headers, json: { status: 'ready' } });
    return true;
  }
  if (url.pathname === '/v1/hermes') {
    await route.fulfill({
      status: 200,
      headers,
      json: { state: 'not_checked', connected: false, cli_available: true, configured: false, features: [] },
    });
    return true;
  }
  if (url.pathname === '/v1/diagnostics') {
    await route.fulfill({ status: 200, headers, json: { checks: [], notice: 'OK' } });
    return true;
  }
  if (url.pathname === '/v1/security/status') {
    await route.fulfill({
      status: 200,
      headers,
      json: { blockers: [], credentials: [], external_connections: [] },
    });
    return true;
  }
  return false;
}

test.describe('HOR-164 (M2) — Conexão e diagnóstico do banco Supabase PostgreSQL', () => {
  test('exibe cada estado do contrato e orienta no primeiro uso (not_configured)', async ({ page }) => {
    let currentStatus: MockDbStatus = makeStatus({
      state: 'not_configured',
      latency_ms: null,
      schema_version: null,
      expected_schema_version: '20261117_01',
      last_error_code: 'database_not_configured',
      host_label: null,
    });

    await page.route('http://127.0.0.1:8765/**', async route => {
      const url = new URL(route.request().url());
      const headers = CORS_HEADERS();
      if (route.request().method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers });
        return;
      }
      if (url.pathname === '/health') {
        await route.fulfill({ status: 200, headers, json: { status: 'ready' } });
        return;
      }
      if (url.pathname === '/v1/hermes') {
        await route.fulfill({
          status: 200,
          headers,
          json: { state: 'not_checked', connected: false, cli_available: true, configured: false, features: [] },
        });
        return;
      }
      if (url.pathname === '/v1/diagnostics') {
        await route.fulfill({ status: 200, headers, json: { checks: [], notice: 'OK' } });
        return;
      }
      if (url.pathname === '/v1/database/status') {
        await route.fulfill({ status: 200, headers, json: currentStatus });
        return;
      }
      if (url.pathname === '/v1/database/test' && route.request().method() === 'POST') {
        await route.fulfill({ status: 200, headers, json: currentStatus });
        return;
      }
      await route.fulfill({ status: 200, headers, json: [] });
    });

    await connectSession(page);
    await page.locator('.sidebar').getByRole('button', { name: 'Visão geral', exact: true }).click();

    // Primeiro uso: faixa global e orientação na Visão Geral
    await expect(page.getByTestId('database-global-banner')).toBeVisible();
    await expect(page.getByTestId('database-gate-card')).toContainText('Banco de dados não configurado');

    // Clicar em "Configurar banco de dados" leva para Ajustes -> Banco de dados
    await page.getByTestId('database-gate-open-settings').click();
    await expect(page.getByTestId('database-settings-section')).toBeVisible();
    await expect(page.getByTestId('database-status-text')).toHaveAttribute('role', 'status');
    await expect(page.getByTestId('database-state-badge')).toHaveText('CONFIGURAÇÃO PENDENTE');

    const statesToCheck: { status: MockDbStatus; badge: string; textMatch: RegExp }[] = [
      {
        status: makeStatus({ state: 'connecting', latency_ms: null, last_error_code: null }),
        badge: 'CONECTANDO',
        textMatch: /Aguarde a verificação inicial/,
      },
      {
        status: makeStatus({
          state: 'unreachable',
          latency_ms: null,
          last_error_code: 'database_unreachable',
        }),
        badge: 'BANCO INACESSÍVEL',
        textMatch: /Sem conexão ativa/,
      },
      {
        status: makeStatus({
          state: 'auth_failed',
          latency_ms: 19,
          last_error_code: 'database_auth_failed',
        }),
        badge: 'CREDENCIAL RECUSADA',
        textMatch: /recusou o usuário ou a senha/,
      },
      {
        status: makeStatus({
          state: 'schema_mismatch',
          latency_ms: 33,
          schema_version: '20261001_00',
          expected_schema_version: '20261117_01',
          last_error_code: 'database_schema_mismatch',
        }),
        badge: 'ESQUEMA INCOMPATÍVEL',
        textMatch: /20261001_00.*20261117_01/,
      },
      {
        status: makeStatus({
          state: 'migrating',
          latency_ms: 28,
          last_error_code: 'database_migrating',
        }),
        badge: 'MIGRANDO ESQUEMA',
        textMatch: /bloqueadas até a conclusão da migração/,
      },
      {
        status: makeStatus({
          state: 'vault_unavailable',
          latency_ms: null,
          last_error_code: 'database_vault_unavailable',
        }),
        badge: 'COFRE INDISPONÍVEL',
        textMatch: /cofre de credenciais do sistema operacional/,
      },
      {
        status: makeStatus({ state: 'connected', latency_ms: 27 }),
        badge: 'CONECTADO',
        textMatch: /Conexão operacional ativa/,
      },
    ];

    for (const entry of statesToCheck) {
      currentStatus = entry.status;
      await page.getByTestId('database-test-button').click();
      await expect(page.getByTestId('database-state-badge')).toHaveText(entry.badge);
      await expect(page.getByTestId('database-status-text')).toContainText(entry.textMatch);
    }

    await expect(page.getByTestId('database-latency')).toHaveText('27 ms');
    await expect(page.getByTestId('database-schema-version')).toHaveText('20261117_01 × 20261117_01');
    await expect(page.getByTestId('database-global-banner')).toHaveCount(0);
  });

  test('configura com sucesso (senha especial preservada sem trim), exclusão idempotente e nunca expõe segredo', async ({
    page,
  }) => {
    const SECRET = "Supa!@#$%^&*()_+-=';:\\/<>?[]{}|~2026 ";
    const consoleLogs: string[] = [];
    const requestUrls: string[] = [];
    page.on('console', msg => consoleLogs.push(msg.text()));
    page.on('request', req => requestUrls.push(req.url()));

    let currentStatus: MockDbStatus = makeStatus({
      state: 'not_configured',
      latency_ms: null,
      schema_version: null,
      last_error_code: 'database_not_configured',
      host_label: null,
    });

    await page.route('http://127.0.0.1:8765/**', async route => {
      const url = new URL(route.request().url());
      const headers = CORS_HEADERS();
      if (route.request().method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers });
        return;
      }
      if (url.pathname === '/health') {
        await route.fulfill({ status: 200, headers, json: { status: 'ready' } });
        return;
      }
      if (url.pathname === '/v1/hermes') {
        await route.fulfill({
          status: 200,
          headers,
          json: { state: 'not_checked', connected: false, cli_available: true, configured: false, features: [] },
        });
        return;
      }
      if (url.pathname === '/v1/database/status') {
        await route.fulfill({ status: 200, headers, json: currentStatus });
        return;
      }
      if (url.pathname === '/v1/database/connection' && route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as Record<string, unknown>;
        expect(body.password).toBe(SECRET);
        expect(body.host).toBe('aws-0-sa-east-1.pooler.supabase.com');
        expect(body.port).toBe(5432);
        currentStatus = makeStatus({
          state: 'connected',
          latency_ms: 31,
          host_label: 'aws-0-sa-east-1.pooler.supabase.com:5432/postgres',
          last_error_code: null,
        });
        // Resposta nunca retorna senha
        await route.fulfill({ status: 200, headers, json: currentStatus });
        return;
      }
      if (url.pathname === '/v1/database/connection' && route.request().method() === 'DELETE') {
        currentStatus = makeStatus({
          state: 'not_configured',
          latency_ms: null,
          schema_version: null,
          last_error_code: 'database_not_configured',
          host_label: null,
        });
        await route.fulfill({ status: 200, headers, json: currentStatus });
        return;
      }
      await route.fulfill({ status: 200, headers, json: [] });
    });

    await connectSession(page);
    await page.locator('.sidebar').getByRole('button', { name: 'Visão geral', exact: true }).click();
    await page.getByTestId('database-banner-open-settings').click();

    const form = page.getByTestId('database-connection-form');
    const passwordInput = form.locator('input[name="db-password"]');
    await expect(passwordInput).toHaveAttribute('type', 'password');
    await expect(passwordInput).toHaveAttribute('autocomplete', 'off');

    await form.locator('input[name="db-host"]').fill('aws-0-sa-east-1.pooler.supabase.com');
    await form.locator('input[name="db-port"]').fill('5432');
    await form.locator('input[name="db-name"]').fill('postgres');
    await form.locator('input[name="db-user"]').fill('postgres.projeto');
    await passwordInput.fill(SECRET);

    await page.getByTestId('database-save-button').click();

    await expect(page.getByTestId('database-state-badge')).toHaveText('CONECTADO');
    await expect(page.getByTestId('database-host-label')).toHaveText(
      'aws-0-sa-east-1.pooler.supabase.com:5432/postgres',
    );
    await expect(passwordInput).toHaveValue('');

    // Verifica ausência absoluta do segredo no DOM, storage, console e URLs
    const leakCheck = await page.evaluate(secret => {
      const domHtml = document.documentElement.innerHTML;
      const ls = JSON.stringify(localStorage);
      const ss = JSON.stringify(sessionStorage);
      return {
        inDom: domHtml.includes(secret),
        inLocalStorage: ls.includes(secret),
        inSessionStorage: ss.includes(secret),
      };
    }, SECRET);

    expect(leakCheck.inDom).toBe(false);
    expect(leakCheck.inLocalStorage).toBe(false);
    expect(leakCheck.inSessionStorage).toBe(false);
    expect(consoleLogs.some(line => line.includes(SECRET))).toBe(false);
    expect(requestUrls.some(u => u.includes(SECRET))).toBe(false);

    // Remover credenciais (idempotente)
    await page.getByTestId('database-delete-button').click();
    await expect(page.getByTestId('database-state-badge')).toHaveText('CONFIGURAÇÃO PENDENTE');
    await expect(page.getByTestId('database-global-banner')).toBeVisible();
  });

  test('contrato HTTP 200-diagnóstico vs 409/422/503/401: preserva snapshot em 409 e trata cofre falho em 503', async ({
    page,
  }) => {
    const connectedSnapshot = makeStatus({ state: 'connected', latency_ms: 29 });
    let currentSnapshot = connectedSnapshot;
    let nextPostMode: 'ok_auth_failed' | '409_busy' | '422_invalid' | '503_vault' | '401_local' = 'ok_auth_failed';

    await page.route('http://127.0.0.1:8765/**', async route => {
      const url = new URL(route.request().url());
      const headers = CORS_HEADERS();
      if (await handleCommonRoutes(route, url, headers)) return;
      if (url.pathname === '/v1/database/status') {
        await route.fulfill({ status: 200, headers, json: currentSnapshot });
        return;
      }
      if (url.pathname === '/v1/database/connection' && route.request().method() === 'POST') {
        if (nextPostMode === 'ok_auth_failed') {
          await route.fulfill({
            status: 200,
            headers,
            json: makeStatus({
              state: 'auth_failed',
              latency_ms: 18,
              last_error_code: 'database_auth_failed',
            }),
          });
          return;
        }
        if (nextPostMode === '409_busy') {
          await route.fulfill({
            status: 409,
            headers,
            json: {
              detail: {
                code: 'database_busy',
                message: 'Existe uma produção ativa; não é possível alterar a conexão agora.',
                status: connectedSnapshot,
              },
            },
          });
          return;
        }
        if (nextPostMode === '422_invalid') {
          await route.fulfill({
            status: 422,
            headers,
            json: {
              detail: {
                code: 'database_endpoint_not_allowed',
                message: 'Endpoint informado não é permitido pela política de conexão.',
                status: connectedSnapshot,
              },
            },
          });
          return;
        }
        if (nextPostMode === '503_vault') {
          currentSnapshot = makeStatus({
            state: 'vault_unavailable',
            latency_ms: null,
            last_error_code: 'database_vault_unavailable',
          });
          await route.fulfill({
            status: 503,
            headers,
            json: {
              detail: {
                code: 'database_vault_unavailable',
                message: 'Cofre do Windows indisponível para gravar credenciais.',
                status: currentSnapshot,
              },
            },
          });
          return;
        }
        await route.fulfill({
          status: 401,
          headers,
          json: { detail: 'Token local inválido.' },
        });
        return;
      }
      await route.fulfill({ status: 200, headers, json: [] });
    });

    await connectSession(page);
    await page.locator('#tab-database').click();
    await expect(page.getByTestId('database-state-badge')).toHaveText('CONECTADO');

    const form = page.getByTestId('database-connection-form');
    const fillValid = async () => {
      await form.locator('input[name="db-host"]').fill('aws-0-sa-east-1.pooler.supabase.com');
      await form.locator('input[name="db-port"]').fill('5432');
      await form.locator('input[name="db-name"]').fill('postgres');
      await form.locator('input[name="db-user"]').fill('horus_app.projeto');
      await form.locator('input[name="db-password"]').fill('SenhaSegura#2026');
    };

    // 1) HTTP 200 com state: auth_failed -> configuração persistida, mas UI lê state (não assume connected)
    nextPostMode = 'ok_auth_failed';
    await fillValid();
    await page.getByTestId('database-save-button').click();
    await expect(page.getByTestId('database-state-badge')).toHaveText('CREDENCIAL RECUSADA');
    await expect(page.getByTestId('database-feedback')).toContainText('Configuração salva no cofre');

    // 2) HTTP 409 database_busy -> operação recusada e snapshot anterior preservado
    nextPostMode = '409_busy';
    await fillValid();
    await page.getByTestId('database-save-button').click();
    await expect(page.getByTestId('database-feedback')).toContainText('produção ativa');
    await expect(page.getByTestId('database-state-badge')).toHaveText('CONECTADO');

    // 3) HTTP 422 database_endpoint_not_allowed -> erro claro sem trocar snapshot
    nextPostMode = '422_invalid';
    await fillValid();
    await page.getByTestId('database-save-button').click();
    await expect(page.getByTestId('database-feedback')).toContainText('não é permitido');
    await expect(page.getByTestId('database-state-badge')).toHaveText('CONECTADO');

    // 4) HTTP 503 database_vault_unavailable -> atualiza snapshot para COFRE INDISPONÍVEL
    nextPostMode = '503_vault';
    await fillValid();
    await page.getByTestId('database-save-button').click();
    await expect(page.getByTestId('database-state-badge')).toHaveText('COFRE INDISPONÍVEL');
    await expect(page.getByTestId('database-feedback')).toContainText('Cofre do Windows indisponível');

    // 5) HTTP 401 auth local -> tratado por HTTP antes do envelope
    nextPostMode = '401_local';
    await fillValid();
    await page.getByTestId('database-save-button').click();
    await expect(page.getByTestId('database-feedback')).toContainText('Sessão local recusada pelo backend (401)');
  });

  test('exibe dicas para credencial recusada, esquema incompatível e retry em inacessível', async ({
    page,
  }) => {
    let currentStatus: MockDbStatus = makeStatus({
      state: 'auth_failed',
      latency_ms: 15,
      last_error_code: 'database_auth_failed',
    });

    await page.route('http://127.0.0.1:8765/**', async route => {
      const url = new URL(route.request().url());
      const headers = CORS_HEADERS();
      if (await handleCommonRoutes(route, url, headers)) return;
      if (url.pathname === '/v1/database/status') {
        await route.fulfill({ status: 200, headers, json: currentStatus });
        return;
      }
      if (url.pathname === '/v1/database/test' && route.request().method() === 'POST') {
        await route.fulfill({ status: 200, headers, json: currentStatus });
        return;
      }
      await route.fulfill({ status: 200, headers, json: [] });
    });

    await connectSession(page);
    await page.locator('.sidebar').getByRole('button', { name: 'Visão geral', exact: true }).click();
    await page.getByTestId('database-banner-open-settings').click();

    await expect(page.getByTestId('database-hints')).toContainText('horus_app.<project-ref>');
    await expect(page.getByTestId('database-error-code')).toContainText('database_auth_failed');

    // Muda para esquema incompatível
    currentStatus = makeStatus({
      state: 'schema_mismatch',
      schema_version: '1',
      expected_schema_version: '4',
      last_error_code: 'database_schema_mismatch',
    });
    await page.getByTestId('database-test-button').click();
    await expect(page.getByTestId('database-schema-version')).toHaveText('1 × 4');
    await expect(page.getByTestId('database-hints')).toContainText('migrações pendentes');

    // Muda para inacessível e testa retry pela faixa global
    currentStatus = makeStatus({
      state: 'unreachable',
      latency_ms: null,
      last_error_code: 'database_unreachable',
    });
    await page.getByTestId('database-test-button').click();
    await expect(page.getByTestId('database-hints')).toContainText('VPN');

    // Simula volta da conexão e retry pela faixa global
    currentStatus = makeStatus({ state: 'connected', latency_ms: 22 });
    await page.getByTestId('database-banner-retry').click();
    await expect(page.getByTestId('database-global-banner')).toHaveCount(0);
    await expect(page.getByTestId('database-state-badge')).toHaveText('CONECTADO');
  });

  test('queda no meio de uma ação preserva dados do formulário e permite repetir quando conexão volta', async ({
    page,
  }) => {
    let dbUp = true;
    let createAttempts = 0;

    await page.route('http://127.0.0.1:8765/**', async route => {
      const url = new URL(route.request().url());
      const headers = CORS_HEADERS();
      if (await handleCommonRoutes(route, url, headers)) return;
      if (url.pathname === '/v1/database/status') {
        await route.fulfill({
          status: 200,
          headers,
          json: dbUp
            ? makeStatus({ state: 'connected' })
            : makeStatus({ state: 'unreachable', latency_ms: null, last_error_code: 'database_unreachable' }),
        });
        return;
      }
      if (url.pathname === '/v1/database/test' && route.request().method() === 'POST') {
        await route.fulfill({
          status: 200,
          headers,
          json: dbUp
            ? makeStatus({ state: 'connected' })
            : makeStatus({ state: 'unreachable', latency_ms: null, last_error_code: 'database_unreachable' }),
        });
        return;
      }
      if (url.pathname === '/v1/workflows/manual' && route.request().method() === 'POST') {
        createAttempts += 1;
        if (!dbUp) {
          await route.fulfill({
            status: 503,
            headers,
            json: {
              detail: {
                code: 'database_unreachable',
                message: 'Banco de dados inacessível no momento. Nenhuma alteração foi salva.',
              },
            },
          });
          return;
        }
        await route.fulfill({
          status: 200,
          headers,
          json: {
            workflow_id: 'wf-manual-1',
            proposal_artifact_id: 'art-1',
          },
        });
        return;
      }
      if (url.pathname === '/v1/workflows/wf-manual-1') {
        await route.fulfill({
          status: 200,
          headers,
          json: {
            workflow_id: 'wf-manual-1',
            title: 'Roteiro que não se perdeu',
            status: 'awaiting_authorization',
            current_stage: 'proposal',
            steps: [],
            artifacts: [],
          },
        });
        return;
      }
      if (url.pathname === '/v1/security/status') {
        await route.fulfill({
          status: 200,
          headers,
          json: { blockers: [], credentials: [], external_connections: [] },
        });
        return;
      }
      await route.fulfill({ status: 200, headers, json: [] });
    });

    await connectSession(page);
    await page.locator('.sidebar').getByRole('button', { name: 'Visão geral', exact: true }).click();
    await page.locator('.sidebar').getByRole('button', { name: 'Produção', exact: true }).click();

    await page.getByRole('button', { name: 'Escrever roteiro do zero' }).click();
    const titleInput = page.getByLabel('Título do roteiro');
    await titleInput.fill('Roteiro que não se perdeu');

    // Queda do banco antes de enviar
    dbUp = false;
    await page.getByRole('button', { name: 'Criar e abrir editor' }).click();

    expect(createAttempts).toBe(1);
    await expect(page.getByTestId('database-global-banner')).toBeVisible();
    await expect(page.getByTestId('database-gate-card')).toBeVisible();
    // Texto digitado pelo usuário permanece preservado
    await expect(titleInput).toHaveValue('Roteiro que não se perdeu');

    // Conexão volta e usuário tenta novamente sem redigitar
    dbUp = true;
    await page.getByTestId('database-gate-retry').click();
    await expect(page.getByTestId('database-global-banner')).toHaveCount(0);

    await page.getByRole('button', { name: 'Criar e abrir editor' }).click();
    expect(createAttempts).toBe(2);
    await expect(page.getByTestId('manual-script-create-form')).toHaveCount(0);
  });

  test('navegação por teclado, foco e acessibilidade em Ajustes → Banco de dados', async ({ page }) => {
    await page.route('http://127.0.0.1:8765/**', async route => {
      const url = new URL(route.request().url());
      const headers = CORS_HEADERS();
      if (await handleCommonRoutes(route, url, headers)) return;
      if (url.pathname === '/v1/database/status') {
        await route.fulfill({ status: 200, headers, json: makeStatus({ state: 'connected' }) });
        return;
      }
      await route.fulfill({ status: 200, headers, json: [] });
    });

    await connectSession(page);
    await page.locator('.sidebar').getByRole('button', { name: 'Visão geral', exact: true }).click();
    await page.locator('.sidebar').getByRole('button', { name: 'Ajustes', exact: true }).click();

    const tabGeneral = page.locator('#tab-general');
    const tabDatabase = page.locator('#tab-database');
    const tabPrivacy = page.locator('#tab-privacy');

    await tabGeneral.focus();
    await page.keyboard.press('ArrowRight');
    await expect(tabPrivacy).toBeFocused();
    await expect(tabPrivacy).toHaveAttribute('aria-selected', 'true');

    await page.keyboard.press('ArrowRight');
    await expect(tabDatabase).toBeFocused();
    await expect(tabDatabase).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('database-settings-section')).toBeVisible();

    await page.keyboard.press('ArrowLeft');
    await expect(tabPrivacy).toBeFocused();

    await page.keyboard.press('End');
    await expect(tabDatabase).toBeFocused();

    const axeResults = await new AxeBuilder({ page })
      .include('#settings-database')
      .analyze();
    expect(axeResults.violations).toEqual([]);
  });

  test('degradação graciosa em backend antigo sem /v1/database/status (404)', async ({ page }) => {
    await page.route('http://127.0.0.1:8765/**', async route => {
      const url = new URL(route.request().url());
      const headers = CORS_HEADERS();
      if (await handleCommonRoutes(route, url, headers)) return;
      if (url.pathname === '/v1/database/status') {
        await route.fulfill({ status: 404, headers, json: { detail: 'Not Found' } });
        return;
      }
      await route.fulfill({ status: 200, headers, json: [] });
    });

    await connectSession(page);
    await page.locator('.sidebar').getByRole('button', { name: 'Visão geral', exact: true }).click();
    // Não deve exibir faixa de erro nem bloquear telas
    await expect(page.getByTestId('database-global-banner')).toHaveCount(0);
    await expect(page.getByTestId('database-gate-card')).toHaveCount(0);

    await page.locator('.sidebar').getByRole('button', { name: 'Ajustes', exact: true }).click();
    await page.locator('#tab-database').click();
    await expect(page.getByTestId('database-legacy-notice')).toBeVisible();
  });
});
