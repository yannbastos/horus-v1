import { expect, test } from '@playwright/test';
import config from '../playwright.config';

const CORS_HEADERS = () => ({
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
});

async function mockApi(page: import('@playwright/test').Page) {
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
    await route.fulfill({ status: 200, headers, json: [] });
  });
}

test.describe('HOR-148 — Ajustes sem rolagem duplicada nem área branca', () => {
  for (const viewport of [
    { width: 1040, height: 680 },
    { width: 1320, height: 840 },
  ]) {
    test(`sem rolagem de documento nas telas principais e com rolagem interna em Ajustes (${viewport.width}x${viewport.height})`, async ({
      page,
    }) => {
      await mockApi(page);
      await page.setViewportSize(viewport);
      await page.goto('/');

      const pages = [
        'Visão geral',
        'Radar',
        'Biblioteca',
        'Produção',
        'Central de Produção',
        'Canais',
        'Histórico operacional',
        'Transcrição',
        'Ajustes',
      ];

      for (const name of pages) {
        await page.locator('.sidebar').getByRole('button', { name, exact: true }).click();
        if (name === 'Ajustes') {
          await expect(page.getByTestId('shortcuts-settings')).toBeVisible();
        }
        const docMetrics = await page.evaluate(() => ({
          scrollHeight: document.scrollingElement!.scrollHeight,
          clientHeight: document.scrollingElement!.clientHeight,
          horusHeight: document.querySelector('.horus')!.getBoundingClientRect().height,
          innerHeight: window.innerHeight,
        }));
        expect(docMetrics.scrollHeight, `Tela ${name} não deve rolar no documento`).toBeLessThanOrEqual(
          docMetrics.clientHeight,
        );
        expect(docMetrics.horusHeight, `Shell em ${name} deve ocupar a altura da janela`).toBeCloseTo(
          docMetrics.innerHeight,
          0,
        );
      }

      // Conteúdo longo de Ajustes continua alcançável pela rolagem interna de <main>
      const updatesSection = page.locator('#settings-updates');
      await expect(updatesSection).toBeAttached();
      await updatesSection.scrollIntoViewIfNeeded();
      await expect(updatesSection).toBeInViewport();

      const afterScroll = await page.evaluate(() => {
        const main = document.querySelector('main')!;
        return {
          docScrollTop: document.scrollingElement!.scrollTop,
          docScrollHeight: document.scrollingElement!.scrollHeight,
          docClientHeight: document.scrollingElement!.clientHeight,
          mainScrollHeight: main.scrollHeight,
          mainClientHeight: main.clientHeight,
          mainScrollTop: main.scrollTop,
        };
      });
      expect(afterScroll.docScrollTop).toBe(0);
      expect(afterScroll.docScrollHeight).toBeLessThanOrEqual(afterScroll.docClientHeight);
      expect(afterScroll.mainScrollHeight).toBeGreaterThan(afterScroll.mainClientHeight);
      expect(afterScroll.mainScrollTop).toBeGreaterThan(0);

      // Aba Privacidade e Segurança também não gera rolagem de documento
      await page.getByRole('tab', { name: 'PRIVACIDADE E SEGURANÇA' }).click();
      await expect(page.getByRole('heading', { level: 1, name: 'Privacidade e Segurança' })).toBeVisible();
      const privacyMetrics = await page.evaluate(() => ({
        scrollHeight: document.scrollingElement!.scrollHeight,
        clientHeight: document.scrollingElement!.clientHeight,
      }));
      expect(privacyMetrics.scrollHeight).toBeLessThanOrEqual(privacyMetrics.clientHeight);
    });
  }

  test('ao redimensionar/maximizar após carregar Ajustes, shell acompanha a janela sem área branca nem rolagem externa', async ({
    page,
  }) => {
    await mockApi(page);
    await page.setViewportSize({ width: 1320, height: 840 });
    await page.goto('/');
    await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
    await expect(page.getByTestId('shortcuts-settings')).toBeVisible();
    await expect(page.locator('#settings-updates')).toBeVisible();

    // Simula maximização da janela após carregamento inicial
    await page.setViewportSize({ width: 1920, height: 1020 });

    // Tenta rolar o documento diretamente e rolar até Atualizações
    await page.evaluate(() => {
      document.scrollingElement!.scrollTop = 400;
      document.getElementById('settings-updates')?.scrollIntoView({ block: 'end' });
    });

    const maximized = await page.evaluate(() => {
      const horus = document.querySelector('.horus')!.getBoundingClientRect();
      const footer = document.querySelector('footer')!.getBoundingClientRect();
      const main = document.querySelector('main')!;
      return {
        innerHeight: window.innerHeight,
        horusHeight: horus.height,
        horusBottom: horus.bottom,
        footerBottom: footer.bottom,
        docScrollHeight: document.scrollingElement!.scrollHeight,
        docClientHeight: document.scrollingElement!.clientHeight,
        docScrollTop: document.scrollingElement!.scrollTop,
        mainScrollHeight: main.scrollHeight,
        mainClientHeight: main.clientHeight,
        mainScrollTop: main.scrollTop,
      };
    });

    expect(maximized.horusHeight).toBeCloseTo(maximized.innerHeight, 0);
    expect(maximized.horusBottom).toBeCloseTo(maximized.innerHeight, 0);
    expect(maximized.footerBottom).toBeCloseTo(maximized.innerHeight, 0);
    expect(maximized.docScrollHeight).toBeLessThanOrEqual(maximized.docClientHeight);
    expect(maximized.docScrollTop).toBe(0);
    expect(maximized.mainScrollHeight).toBeGreaterThan(maximized.mainClientHeight);
    expect(maximized.mainScrollTop).toBeGreaterThan(0);
    await expect(page.locator('#settings-updates')).toBeInViewport();
  });
});
