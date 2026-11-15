import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import {
  connectSession,
  corsHeaders,
  installBackend,
  newBackendState,
  PRODUCTION_ID,
  WORKFLOW_ID,
} from './production-journey-helpers';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

type MockErrorItem = {
  id: string;
  kind: 'production' | 'workflow_step' | 'job' | 'workflow';
  status: 'failed' | 'blocked' | 'interrupted';
  code: string;
  stage: string;
  at: string;
  title: string;
  message_pt: string;
  suggested_action_pt: string;
  ref: {
    kind: 'production' | 'workflow' | 'job';
    id: string;
    workflow_id: string | null;
  };
  duration_seconds: number | null;
  exit_code: number | null;
};

function makeErrorFixtures(): MockErrorItem[] {
  return [
    {
      id: 'production:prod-err-1',
      kind: 'production',
      status: 'failed',
      code: 'render_failed',
      stage: 'render',
      at: '2026-11-15T08:15:00Z',
      title: 'Falha no render',
      message_pt: 'A produção prod-err falhou na etapa «render». Falha no render.',
      suggested_action_pt: 'Abra o Diagnóstico técnico da produção e crie nova produção após corrigir a causa.',
      ref: { kind: 'production', id: PRODUCTION_ID, workflow_id: null },
      duration_seconds: 18.4,
      exit_code: 1,
    },
    {
      id: 'workflow_step:step-err-1',
      kind: 'workflow_step',
      status: 'blocked',
      code: 'workflow_step_blocked',
      stage: 'analysis',
      at: '2026-11-15T07:50:00Z',
      title: 'Etapa da análise bloqueada',
      message_pt: 'A etapa da análise step-err foi bloqueado na etapa «analysis». Etapa da análise bloqueada.',
      suggested_action_pt: 'Abra a análise, confira a causa mostrada e use «tentar de novo».',
      ref: { kind: 'workflow', id: WORKFLOW_ID, workflow_id: WORKFLOW_ID },
      duration_seconds: null,
      exit_code: null,
    },
    {
      id: 'job:job-err-1',
      kind: 'job',
      status: 'interrupted',
      code: 'factory_interrupted',
      stage: 'prepare',
      at: '2026-11-15T07:20:00Z',
      title: 'Operação da fábrica interrompida',
      message_pt: 'A tarefa job-err- foi interrompido na etapa «prepare». Operação da fábrica interrompida.',
      suggested_action_pt: 'Repita a operação.',
      ref: { kind: 'job', id: 'job-err-1', workflow_id: null },
      duration_seconds: 4.2,
      exit_code: 130,
    },
  ];
}

async function runAxeInBothThemes(page: Page, contextLabel: string) {
  const originalTheme = await page.evaluate(
    () => document.querySelector('.horus')?.getAttribute('data-theme') ?? 'light',
  );

  for (const theme of [originalTheme, originalTheme === 'dark' ? 'light' : 'dark']) {
    await page.evaluate(value => {
      document.querySelector('.horus')?.setAttribute('data-theme', value);
    }, theme);
    await page.evaluate(() =>
      Promise.all(document.getAnimations().map(anim => anim.finished.catch(() => undefined))),
    );
    const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
    const formatted = results.violations.map(v => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      nodes: v.nodes.map(n => n.target),
    }));
    expect(
      formatted,
      `Violações axe-core (${WCAG_TAGS.join(', ')}) em [${contextLabel} · tema ${theme}]`,
    ).toEqual([]);
  }

  await page.evaluate(value => {
    document.querySelector('.horus')?.setAttribute('data-theme', value);
  }, originalTheme);
}

async function installErrorCenterMocks(
  page: Page,
  options: {
    items?: MockErrorItem[];
    failNextErrorsRequest?: boolean;
    onErrorsRequest?: (url: URL) => void;
    onSummaryRequest?: (url: URL) => void;
  } = {},
) {
  const allItems = options.items ?? makeErrorFixtures();
  let failOnce = Boolean(options.failNextErrorsRequest);

  await page.route('http://127.0.0.1:8765/v1/errors/summary**', async route => {
    const url = new URL(route.request().url());
    options.onSummaryRequest?.(url);

    const byCodeMap = new Map<string, { code: string; count: number; title: string; suggested_action_pt: string }>();
    const byStageMap = new Map<string, number>();
    const byKindMap = new Map<string, number>();

    for (const item of allItems) {
      const existing = byCodeMap.get(item.code);
      if (existing) {
        existing.count += 1;
      } else {
        byCodeMap.set(item.code, {
          code: item.code,
          count: 1,
          title: item.title,
          suggested_action_pt: item.suggested_action_pt,
        });
      }
      byStageMap.set(item.stage, (byStageMap.get(item.stage) ?? 0) + 1);
      byKindMap.set(item.kind, (byKindMap.get(item.kind) ?? 0) + 1);
    }

    await route.fulfill({
      status: 200,
      headers: { ...corsHeaders, 'content-type': 'application/json' },
      body: JSON.stringify({
        total: allItems.length,
        by_code: Array.from(byCodeMap.values()),
        by_stage: Array.from(byStageMap.entries()).map(([stage, count]) => ({ stage, count })),
        by_kind: Array.from(byKindMap.entries()).map(([kind, count]) => ({ kind, count })),
      }),
    });
  });

  await page.route('http://127.0.0.1:8765/v1/errors?**', async route => {
    const url = new URL(route.request().url());
    options.onErrorsRequest?.(url);

    if (failOnce) {
      failOnce = false;
      await route.fulfill({
        status: 500,
        headers: { ...corsHeaders, 'content-type': 'application/json' },
        body: JSON.stringify({ detail: 'Falha simulada ao consultar erros.' }),
      });
      return;
    }

    const kind = url.searchParams.get('kind') ?? '';
    const stage = url.searchParams.get('stage') ?? '';
    const code = url.searchParams.get('code') ?? '';
    const after = url.searchParams.get('after');

    const filtered = allItems.filter(item => {
      if (kind && item.kind !== kind) return false;
      if (stage && item.stage !== stage) return false;
      if (code && item.code !== code) return false;
      return true;
    });

    // Simula paginação de 2 itens por página quando há mais de 2 itens sem filtro
    if (!after && filtered.length > 2) {
      await route.fulfill({
        status: 200,
        headers: { ...corsHeaders, 'content-type': 'application/json' },
        body: JSON.stringify({
          items: filtered.slice(0, 2),
          next_after: 'cursor-page-2',
        }),
      });
      return;
    }

    if (after === 'cursor-page-2') {
      await route.fulfill({
        status: 200,
        headers: { ...corsHeaders, 'content-type': 'application/json' },
        body: JSON.stringify({
          items: filtered.slice(2),
          next_after: null,
        }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      headers: { ...corsHeaders, 'content-type': 'application/json' },
      body: JSON.stringify({
        items: filtered,
        next_after: null,
      }),
    });
  });
}

test.describe('Central de erros (HOR-095)', () => {
  test('estado desconectado exibe orientação acessível', async ({ page }) => {
    await page.goto('/');
    await page.locator('.sidebar').getByRole('button', { name: 'Histórico operacional', exact: true }).click();
    await expect(page.getByRole('heading', { level: 2, name: 'Central de erros' })).toBeVisible();
    await expect(page.getByTestId('error-center-disconnected')).toBeVisible();
    await runAxeInBothThemes(page, 'Central de erros desconectada');
  });

  test('resumo, lista filtrável, paginação, ação sugerida, navegação para item, atalho Alt+E e axe', async ({ page }) => {
    const state = newBackendState();
    await installBackend(page, state);
    await page.route('http://127.0.0.1:8765/v1/jobs', async route => {
      await route.fulfill({
        status: 200,
        headers: { ...corsHeaders, 'content-type': 'application/json' },
        body: JSON.stringify([
          {
            id: 'job-err-1',
            status: 'interrupted',
            attempt: 1,
            created_at: '2026-11-15T07:19:00Z',
            updated_at: '2026-11-15T07:20:00Z',
            payload: { operation: 'factory.profile', channel_id: 'canal01', video_id: null },
            error: null,
            result: { failure: { code: 'factory_interrupted', stage: 'prepare' } },
          },
        ]),
      });
    });
    await page.route('http://127.0.0.1:8765/v1/jobs/job-err-1/events', async route => {
      await route.fulfill({
        status: 200,
        headers: { ...corsHeaders, 'content-type': 'text/event-stream' },
        body: 'event: complete\ndata: {"sequence":1,"kind":"complete","message":"interrupted"}\n\n',
      });
    });

    const requestedQueries: URLSearchParams[] = [];
    await installErrorCenterMocks(page, {
      onErrorsRequest: url => requestedQueries.push(url.searchParams),
    });

    await connectSession(page);

    // Atalho Alt+E abre o Histórico operacional com foco na Central de erros
    await page.keyboard.press('Alt+e');
    const center = page.getByTestId('error-center');
    await expect(center).toBeVisible();
    await expect(center).toBeFocused();

    // Resumo e primeira página (2 itens)
    await expect(page.getByTestId('error-center-total')).toHaveText('3');
    await expect(page.getByTestId('error-item-production:prod-err-1')).toBeVisible();
    await expect(page.getByTestId('error-item-workflow_step:step-err-1')).toBeVisible();
    await expect(page.getByTestId('error-action-production:prod-err-1')).toContainText('AÇÃO SUGERIDA');
    await expect(page.getByTestId('error-action-production:prod-err-1')).toContainText(
      'Abra o Diagnóstico técnico da produção',
    );

    // Paginação carrega a segunda página
    await page.getByTestId('error-center-load-more').click();
    await expect(page.getByTestId('error-item-job:job-err-1')).toBeVisible();
    await expect(page.getByTestId('error-center-load-more')).toBeHidden();

    // Auditoria de acessibilidade na lista populada (temas claro e escuro)
    await runAxeInBothThemes(page, 'Central de erros populada');

    // Filtro por chip de causa no resumo
    await page.getByTestId('summary-code-workflow_step_blocked').click();
    await expect(page.getByTestId('summary-code-workflow_step_blocked')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('error-item-workflow_step:step-err-1')).toBeVisible();
    await expect(page.getByTestId('error-item-production:prod-err-1')).toBeHidden();

    // Limpar filtros e filtrar por selects e data
    await page.getByTestId('error-center-clear-filters').click();
    await page.getByTestId('error-filter-kind').selectOption('job');
    await expect(page.getByTestId('error-item-job:job-err-1')).toBeVisible();
    await expect(page.getByTestId('error-item-production:prod-err-1')).toBeHidden();

    await page.getByTestId('error-filter-since').fill('2026-11-14');
    await page.getByTestId('error-filter-until').fill('2026-11-15');
    await expect.poll(() => requestedQueries.some(q => q.get('since') === '2026-11-14T00:00:00Z')).toBe(true);

    // Link para o item (job -> Produção com tarefa selecionada)
    await page.getByTestId('error-open-job:job-err-1').click();
    await expect(page.getByRole('heading', { level: 1, name: /Da ideia à próxima entrega\./ })).toBeVisible();
    await expect(page.locator('.job-item.selected')).toContainText('canal01 · Interrompido');

    // Volta via Alt+E e abre workflow
    await page.keyboard.press('Alt+e');
    await page.getByTestId('error-open-workflow_step:step-err-1').click();
    await expect(page.getByRole('heading', { level: 1, name: /Da ideia à próxima entrega\./ })).toBeVisible();
  });

  test('estados vazio e de erro com nova tentativa funcionam e passam no axe', async ({ page }) => {
    const state = newBackendState();
    await installBackend(page, state);
    await installErrorCenterMocks(page, {
      items: [],
      failNextErrorsRequest: true,
    });

    await connectSession(page);
    await page.locator('.sidebar').getByRole('button', { name: 'Histórico operacional', exact: true }).click();

    // Primeiro carregamento falha -> estado de erro visível
    await expect(page.getByTestId('error-center-error')).toBeVisible();
    await runAxeInBothThemes(page, 'Central de erros em erro');

    // Tentar novamente carrega estado vazio
    await page.getByTestId('error-center-error').getByRole('button', { name: 'Tentar novamente' }).click();
    await expect(page.getByTestId('error-center-empty')).toBeVisible();
    await expect(page.getByTestId('error-center-empty')).toContainText('Nenhuma falha registrada');
    await runAxeInBothThemes(page, 'Central de erros vazia');
  });
});
