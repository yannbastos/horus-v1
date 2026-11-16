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
});
