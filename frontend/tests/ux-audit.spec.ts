import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import config from '../playwright.config';

const CORS_HEADERS = () => ({
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
});

const VIEWPORTS = [
  { width: 1040, height: 680 },
  { width: 1320, height: 840 },
];

const THEMES = ['dark', 'light'] as const;

test.describe('Auditoria de UX — layout, foco e acessibilidade', () => {
  for (const theme of THEMES) {
    for (const viewport of VIEWPORTS) {
      test(`sem corte na sidebar, sem overflow e axe limpo (${theme} ${viewport.width}x${viewport.height})`, async ({
        page,
      }) => {
        await page.setViewportSize(viewport);
        await page.addInitScript(({ t }) => {
          localStorage.setItem('horus.theme', t);
        }, { t: theme });

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
              json: {
                state: 'not_checked',
                connected: false,
                cli_available: true,
                configured: false,
                message: 'Não verificado.',
                checked_at: null,
                version: null,
                features: [],
              },
            });
            return;
          }
          if (url.pathname === '/v1/diagnostics') {
            await route.fulfill({ status: 200, headers, json: { checks: [], notice: 'OK' } });
            return;
          }
          if (url.pathname === '/v1/jobs') {
            await route.fulfill({ status: 200, headers, json: [] });
            return;
          }
          await route.fulfill({ status: 200, headers, json: [] });
        });

        await page.goto('/');

        // 1. Sidebar nav não corta itens em 1040x680
        const navOverflow = await page.evaluate(() => {
          const nav = document.querySelector('.sidebar nav');
          return nav ? nav.scrollHeight > nav.clientHeight + 1 : false;
        });
        expect(navOverflow).toBe(false);

        // 2. Central de Produção possui h1
        await page.getByRole('button', { name: 'Central de Produção', exact: true }).click();
        await expect(
          page.getByRole('heading', { level: 1, name: 'Cada vídeo, sua etapa e o próximo passo humano' })
        ).toBeVisible();
        const pcAxe = await new AxeBuilder({ page }).analyze();
        expect(pcAxe.violations).toEqual([]);

        // 3. Transcrição tem landmarks únicos e foco visível em input
        await page.getByRole('button', { name: 'Transcrição', exact: true }).click();
        const pathInput = page.getByLabel('Caminho local do arquivo');
        await expect(pathInput).toBeVisible();
        await pathInput.focus();
        const txAxe = await new AxeBuilder({ page }).analyze();
        expect(txAxe.violations).toEqual([]);

        // 4. Ajustes mantém abas alinhadas e sem overflow horizontal
        await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
        await expect(page.getByRole('tab', { name: 'PRIVACIDADE E SEGURANÇA' })).toBeVisible();
        const hasHorizontalOverflow = await page.evaluate(
          () => document.documentElement.scrollWidth > window.innerWidth + 1
        );
        expect(hasHorizontalOverflow).toBe(false);

        // 5. Produção não repete bloco genérico de prévia abaixo da operação
        await page.locator('.sidebar').getByRole('button', { name: 'Produção', exact: true }).click();
        await expect(page.getByText('Análises editoriais e aprovações')).toBeVisible();
        await expect(page.getByText('PRÉVIA DE LAYOUT · EM PREPARAÇÃO')).toHaveCount(0);

        // 6. Canais e Histórico operacional não repetem o placeholder antigo.
        await page.locator('.sidebar').getByRole('button', { name: 'Canais', exact: true }).click();
        await expect(page.getByTestId('legacy-factory-advanced')).toBeVisible();
        await expect(page.getByText('PRÉVIA DE LAYOUT · EM PREPARAÇÃO')).toHaveCount(0);
        await page.locator('.sidebar').getByRole('button', { name: 'Histórico operacional', exact: true }).click();
        await expect(page.getByRole('heading', { level: 2, name: 'Central de erros' })).toBeVisible();
        await expect(page.getByTestId('error-center-disconnected')).toBeVisible();
        await expect(page.getByText('PRÉVIA DE LAYOUT · EM PREPARAÇÃO')).toHaveCount(0);
        const historyAxe = await new AxeBuilder({ page }).analyze();
        expect(historyAxe.violations).toEqual([]);
      });
    }
  }

  test('HOR-146: landmarks únicos, rotulagem atualizada e atalho para Ajustes quando desconectado', async ({
    page,
  }) => {
    const workflowId = '11111111000000000000000000000000';
    await page.route('http://127.0.0.1:8765/**', async route => {
      const url = new URL(route.request().url());
      const headers = CORS_HEADERS();
      if (route.request().method() === 'OPTIONS') {
        return route.fulfill({ status: 204, headers });
      }
      if (url.pathname === '/health') return route.fulfill({ status: 200, headers, json: { status: 'ready' } });
      if (url.pathname === '/v1/hermes') {
        return route.fulfill({
          status: 200,
          headers,
          json: { state: 'not_checked', connected: false, cli_available: true, configured: false, features: [] },
        });
      }
      if (url.pathname === '/v1/diagnostics') {
        return route.fulfill({ status: 200, headers, json: { checks: [], notice: 'OK' } });
      }
      if (url.pathname === '/v1/workflows') {
        return route.fulfill({
          status: 200,
          headers,
          json: [
            {
              id: workflowId,
              status: 'approved',
              revision: 1,
              created_at: '2026-11-16T10:00:00Z',
              updated_at: '2026-11-16T10:00:00Z',
            },
          ],
        });
      }
      if (url.pathname === `/v1/workflows/${workflowId}`) {
        return route.fulfill({
          status: 200,
          headers,
          json: {
            id: workflowId,
            status: 'approved',
            revision: 1,
            created_at: '2026-11-16T10:00:00Z',
            updated_at: '2026-11-16T10:00:00Z',
            current_artifact_id: 'aaaa1111',
            artifacts: [],
            approvals: [],
          },
        });
      }
      if (url.pathname === `/v1/workflows/${workflowId}/scripts`) {
        return route.fulfill({ status: 200, headers, json: { items: [] } });
      }
      if (url.pathname === '/v1/radar/datasets') {
        return route.fulfill({
          status: 200,
          headers,
          json: [
            {
              snapshot_id: 'snap-1',
              revision: 1,
              origin: 'operator_fixture',
              videos: 1,
              eligible_videos: 1,
              records: 1,
              collected_at: '2026-11-16T10:00:00Z',
              expires_at: '2026-11-23T10:00:00Z',
            },
          ],
        });
      }
      if (url.pathname === '/v1/radar/datasets/snap-1/1') {
        return route.fulfill({
          status: 200,
          headers,
          json: {
            dataset: {
              snapshot_id: 'snap-1',
              revision: 1,
              origin: 'operator_fixture',
              status: 'ready',
              collected_at: '2026-11-16T10:00:00Z',
              expires_at: '2026-11-23T10:00:00Z',
              records: [],
            },
            eligibility: {},
          },
        });
      }
      return route.fulfill({ status: 200, headers, json: [] });
    });

    await page.goto('/');

    // 1. Visão geral com rotulagem atualizada (sem "PLANEJAMENTO · EM PREPARAÇÃO" / "PRODUÇÃO · EM PREPARAÇÃO")
    await expect(page.getByText('PLANEJAMENTO · ROTEIRO E REVISÃO')).toBeVisible();
    await expect(page.getByText('PRODUÇÃO · PILOTO LOCAL')).toBeVisible();
    await expect(page.getByText('PLANEJAMENTO · EM PREPARAÇÃO')).toHaveCount(0);
    await expect(page.getByText('PRODUÇÃO · EM PREPARAÇÃO')).toHaveCount(0);

    // 2. Central de Produção desconectada oferece atalho direto para Ajustes
    await page.getByRole('button', { name: 'Central de Produção', exact: true }).click();
    const openSettingsBtn = page.locator('.page').getByRole('button', { name: 'ABRIR AJUSTES', exact: true });
    await expect(openSettingsBtn).toBeVisible();
    await openSettingsBtn.click();
    await expect(page.getByRole('heading', { level: 1, name: 'Aparência' })).toBeVisible();

    // Conecta sessão local para testar landmarks únicos em Produção e Radar e estado sem roteiro
    await page.getByLabel('Token da sessão').fill('horus-test-token-1234567890abcdef');
    await page.getByRole('button', { name: 'CONECTAR BACKEND' }).click();
    await expect(page.getByText('Backend local conectado.')).toBeVisible();

    // 3. Central de Produção conectada exibe orientação atualizada do passo "Escrever roteiro"
    await page.getByRole('button', { name: 'Central de Produção', exact: true }).click();
    await expect(
      page.getByText('Escreva seu roteiro manual do zero (ou a partir de uma proposta aprovada).'),
    ).toBeVisible();

    // 4. Produção com workflow aprovado e sem roteiro: apenas 1 <main> e sem tag SIMULADO prematura
    await page.locator('.sidebar').getByRole('button', { name: 'Produção', exact: true }).click();
    await expect(page.getByText('Roteiro estruturado e revisão humana')).toBeVisible();
    expect(await page.locator('main').count()).toBe(1);
    await expect(page.locator('.tag.simulated', { hasText: 'SIMULADO' })).toHaveCount(0);

    // 5. Radar com dataset carregado: apenas 1 <main>
    await page.locator('.sidebar').getByRole('button', { name: 'Radar', exact: true }).click();
    await expect(page.getByText('SNAPSHOT SELECIONADO')).toBeVisible();
    expect(await page.locator('main').count()).toBe(1);
  });
});
