import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { Page, Route } from '@playwright/test';
import {
  connectSession,
  corsHeaders,
  installBackend,
  newBackendState,
  openProduction,
  PRODUCTION_ID,
  productionFixture,
  SCRIPT_ID,
  WORKFLOW_ID,
} from './production-journey-helpers';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

type MockNotification = {
  id: string;
  kind: string;
  severity: 'success' | 'error' | 'warning' | 'info';
  title: string;
  message: string;
  at: string;
  ref: { kind: string; id: string };
  pending: boolean;
  read: boolean;
};

function makeNotificationFixtures(): MockNotification[] {
  return [
    {
      id: 'notif-1',
      kind: 'workflow.awaiting_approval',
      severity: 'warning',
      title: 'Proposta aguardando aprovação',
      message: 'Revise a proposta editorial antes de gerar o roteiro.',
      at: '2026-11-14T10:15:00Z',
      ref: { kind: 'workflow', id: WORKFLOW_ID },
      pending: true,
      read: false,
    },
    {
      id: 'notif-2',
      kind: 'script.awaiting_review',
      severity: 'info',
      title: 'Roteiro pronto para revisão',
      message: 'A versão 1 do roteiro aguarda sua decisão editorial.',
      at: '2026-11-14T10:10:00Z',
      ref: { kind: 'script', id: SCRIPT_ID },
      pending: true,
      read: false,
    },
    {
      id: 'notif-3',
      kind: 'production.succeeded',
      severity: 'success',
      title: 'Produção concluída',
      message: 'Render de rascunho finalizado e pronto para revisão.',
      at: '2026-11-14T10:05:00Z',
      ref: { kind: 'production', id: PRODUCTION_ID },
      pending: false,
      read: true,
    },
  ];
}

async function runAxeAudit(page: Page, label: string) {
  await page.evaluate(() =>
    Promise.all(document.getAnimations().map(anim => anim.finished.catch(() => undefined))),
  );
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  const violations = results.violations.map(v => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.map(n => n.target),
  }));
  expect(violations, `Violações axe-core em [${label}]`).toEqual([]);
}

test.describe('Central de notificações na interface (HOR-098)', () => {
  test('404 no endpoint de notificações: oculta o sino e interrompe o polling sem spam', async ({ page }) => {
    await page.addInitScript(() => {
      (window as { __HORUS_NOTIFICATIONS_POLL_MS__?: number }).__HORUS_NOTIFICATIONS_POLL_MS__ = 70;
    });
    let unreadCalls = 0;
    const state = newBackendState({
      extra: async (route, path, method) => {
        if (path === '/v1/notifications/unread-count' && method === 'GET') {
          unreadCalls += 1;
          await route.fulfill({ status: 404, headers: corsHeaders, json: { detail: 'Not found' } });
          return true;
        }
        return false;
      },
    });
    await installBackend(page, state);
    await connectSession(page);

    await page.waitForTimeout(260);
    await expect(page.getByTestId('notifications-bell')).toHaveCount(0);
    expect(unreadCalls).toBe(1);
  });

  test('polling com backoff, abertura por teclado, Escape e auditoria axe-core (temas claro e escuro)', async ({ page }) => {
    await page.addInitScript(() => {
      (window as { __HORUS_NOTIFICATIONS_POLL_MS__?: number }).__HORUS_NOTIFICATIONS_POLL_MS__ = 80;
    });
    const items = makeNotificationFixtures();
    const pollTimestamps: number[] = [];
    let pollAttempts = 0;

    const state = newBackendState({
      extra: async (route: Route, path: string, method: string) => {
        if (path === '/v1/notifications/unread-count' && method === 'GET') {
          pollAttempts += 1;
          pollTimestamps.push(Date.now());
          // Primeira e segunda falham (500) para exercitar backoff; terceira recupera
          if (pollAttempts <= 2) {
            await route.fulfill({ status: 500, headers: corsHeaders, json: { detail: 'Erro temporário' } });
            return true;
          }
          const unread = items.filter(i => !i.read).length;
          await route.fulfill({ status: 200, headers: corsHeaders, json: { unread_count: unread } });
          return true;
        }
        if (path === '/v1/notifications' && method === 'GET') {
          const unread = items.filter(i => !i.read).length;
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: { items, unread_count: unread, next_after: null },
          });
          return true;
        }
        return false;
      },
    });

    await installBackend(page, state);
    await connectSession(page);

    const bell = page.getByTestId('notifications-bell');
    await expect(bell).toBeVisible();
    await expect(page.getByTestId('notifications-unread-badge')).toHaveText('2');
    expect(pollAttempts).toBeGreaterThanOrEqual(3);
    // Intervalo entre 2a e 3a tentativa (após 2 erros -> backoff 2x) é maior que entre 1a e 2a
    const gap1 = pollTimestamps[1] - pollTimestamps[0];
    const gap2 = pollTimestamps[2] - pollTimestamps[1];
    expect(gap2).toBeGreaterThanOrEqual(gap1);

    // Abre painel pelo sino e verifica foco e pendentes destacadas
    await bell.click();
    const panel = page.getByTestId('notifications-panel');
    await expect(panel).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Fechar painel de notificações' })).toBeFocused();
    await expect(page.getByTestId('notification-item-notif-1')).toHaveAttribute('data-pending', 'true');
    await expect(page.getByTestId('notification-item-notif-1')).toContainText('PENDENTE');
    await expect(page.getByTestId('notification-item-notif-3')).toHaveAttribute('data-pending', 'false');

    // Auditoria axe-core no tema atual e no tema claro
    await runAxeAudit(page, 'painel de notificações - tema padrão');

    await page.keyboard.press('Escape');
    await expect(panel).toHaveCount(0);
    await expect(bell).toBeFocused();

    // Revalida axe-core nos temas claro e escuro com painel aberto
    await bell.click();
    await expect(panel).toBeVisible();
    for (const theme of ['light', 'dark'] as const) {
      await page.evaluate(t => {
        document.querySelector('.horus')?.setAttribute('data-theme', t);
      }, theme);
      await runAxeAudit(page, `painel de notificações - tema ${theme}`);
    }
  });

  test('filtros (não lidas e tipo), paginação, estados vazio e erro com StatusMessage', async ({ page }) => {
    const allItems = makeNotificationFixtures();
    const extraItem: MockNotification = {
      id: 'notif-4',
      kind: 'workflow.awaiting_authorization',
      severity: 'warning',
      title: 'Workflow aguardando autorização',
      message: 'Autorize a execução externa antes de prosseguir.',
      at: '2026-11-14T09:50:00Z',
      ref: { kind: 'workflow', id: WORKFLOW_ID },
      pending: true,
      read: false,
    };
    let shouldFailList = true;
    const requestedQueries: string[] = [];

    const state = newBackendState({
      extra: async (route, path, method) => {
        if (path === '/v1/notifications/unread-count' && method === 'GET') {
          await route.fulfill({ status: 200, headers: corsHeaders, json: { unread_count: 3 } });
          return true;
        }
        if (path === '/v1/notifications' && method === 'GET') {
          const url = new URL(route.request().url());
          requestedQueries.push(url.search);
          if (shouldFailList) {
            await route.fulfill({ status: 500, headers: corsHeaders, json: { detail: 'Falha ao consultar notificações.' } });
            return true;
          }
          const after = url.searchParams.get('after');
          const unreadOnly = url.searchParams.get('unread') === 'true';
          const kind = url.searchParams.get('kind') ?? '';

          if (after === 'cursor-1') {
            await route.fulfill({
              status: 200,
              headers: corsHeaders,
              json: { items: [extraItem], unread_count: 3, next_after: null },
            });
            return true;
          }

          let filtered = allItems.filter(item => {
            if (unreadOnly && item.read) return false;
            if (kind && item.kind !== kind) return false;
            return true;
          });
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: {
              items: filtered,
              unread_count: 3,
              next_after: !unreadOnly && !kind ? 'cursor-1' : null,
            },
          });
          return true;
        }
        return false;
      },
    });

    await installBackend(page, state);
    await connectSession(page);

    await page.getByTestId('notifications-bell').click();
    await expect(page.getByTestId('notifications-error')).toContainText('Falha ao consultar notificações.');

    // Recupera ao clicar em Tentar novamente
    shouldFailList = false;
    await page.getByRole('button', { name: 'Tentar novamente', exact: true }).click();
    await expect(page.getByTestId('notification-item-notif-1')).toBeVisible();

    // Paginação Carregar mais
    await page.getByRole('button', { name: 'Carregar mais', exact: true }).click();
    await expect(page.getByTestId('notification-item-notif-4')).toBeVisible();

    // Filtro Somente não lidas
    await page.getByRole('button', { name: 'Somente não lidas', exact: true }).click();
    await expect(page.getByTestId('notification-item-notif-3')).toHaveCount(0);
    await expect(page.getByTestId('notification-item-notif-1')).toBeVisible();

    // Filtro por tipo sem correspondência -> StatusMessage empty
    await page.getByLabel('Filtrar por tipo').selectOption('production.failed');
    await expect(page.getByTestId('notifications-empty')).toContainText('Nenhuma notificação corresponde aos filtros.');

    // Filtro por tipo com correspondência
    await page.getByLabel('Filtrar por tipo').selectOption('script.awaiting_review');
    await expect(page.getByTestId('notification-item-notif-2')).toBeVisible();
    expect(requestedQueries.some(q => q.includes('unread=true') && q.includes('kind=script.awaiting_review'))).toBe(true);
  });

  test('marcar uma notificação como lida e marcar todas como lidas atualiza contador e aria-live', async ({ page }) => {
    let items = makeNotificationFixtures();
    const readPayloads: Record<string, unknown>[] = [];

    const state = newBackendState({
      extra: async (route, path, method) => {
        if (path === '/v1/notifications/unread-count' && method === 'GET') {
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: { unread_count: items.filter(i => !i.read).length },
          });
          return true;
        }
        if (path === '/v1/notifications' && method === 'GET') {
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: {
              items,
              unread_count: items.filter(i => !i.read).length,
              next_after: null,
            },
          });
          return true;
        }
        if (path === '/v1/notifications/read-state' && method === 'POST') {
          const body = route.request().postDataJSON() as { ids?: string[]; all?: boolean; read: boolean };
          readPayloads.push(body);
          if (body.all) {
            items = items.map(i => ({ ...i, read: body.read }));
          } else if (Array.isArray(body.ids)) {
            const set = new Set(body.ids);
            items = items.map(i => (set.has(i.id) ? { ...i, read: body.read } : i));
          }
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: { unread_count: items.filter(i => !i.read).length },
          });
          return true;
        }
        return false;
      },
    });

    await installBackend(page, state);
    await connectSession(page);

    await page.getByTestId('notifications-bell').click();
    await expect(page.getByTestId('notifications-unread-badge')).toHaveText('2');

    // Marca notif-1 como lida
    await page.getByTestId('notification-item-notif-1').getByRole('button', { name: 'Marcar como lida' }).click();
    await expect(page.getByTestId('notifications-unread-badge')).toHaveText('1');
    await expect(page.getByTestId('notifications-live-region')).toContainText('Notificação marcada como lida.');

    // Marca todas como lidas
    await page.getByRole('button', { name: 'Marcar todas como lidas', exact: true }).click();
    await expect(page.getByTestId('notifications-unread-badge')).toHaveCount(0);
    await expect(page.getByTestId('notifications-live-region')).toContainText('Todas as notificações marcadas como lidas.');
    expect(readPayloads).toEqual([
      { ids: ['notif-1'], read: true },
      { all: true, read: true },
    ]);
  });

  test('navegação para produção dispensa aviso de produção correspondente sem duplicar', async ({ page }) => {
    await page.addInitScript(() => {
      (window as { __HORUS_PRODUCTION_POLL_MS__?: number }).__HORUS_PRODUCTION_POLL_MS__ = 100;
    });
    const items = makeNotificationFixtures();
    let prodPollCount = 0;

    const state = newBackendState({
      extra: async (route, path, method) => {
        if (path === '/v1/notifications/unread-count' && method === 'GET') {
          await route.fulfill({ status: 200, headers: corsHeaders, json: { unread_count: 2 } });
          return true;
        }
        if (path === '/v1/notifications' && method === 'GET') {
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: { items, unread_count: 2, next_after: null },
          });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}` && method === 'GET') {
          prodPollCount += 1;
          const finished = prodPollCount >= 2;
          const updated = productionFixture({
            status: finished ? 'succeeded' : 'running',
            stage: finished ? 'review' : 'render',
            updated_at: '2026-11-14T10:05:00Z',
          });
          state.productions = [updated];
          await route.fulfill({ status: 200, headers: corsHeaders, json: updated });
          return true;
        }
        return false;
      },
    });

    await installBackend(page, state);
    const region = await openProduction(page);
    await region.getByRole('button', { name: 'Iniciar produção simulada', exact: true }).click();
    await page.getByRole('button', { name: 'Visão geral', exact: true }).click();

    await expect(page.getByTestId('production-notice')).toBeVisible();

    await page.getByTestId('notifications-bell').click();
    await page.getByTestId('notification-item-notif-3').getByRole('button', { name: 'Abrir produção' }).click();

    await expect(page.getByTestId('notifications-panel')).toHaveCount(0);
    await expect(page.getByTestId('production-notice')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Da ideia à próxima entrega.' })).toBeVisible();
  });
});
