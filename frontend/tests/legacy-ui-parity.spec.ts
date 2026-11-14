import config from '../playwright.config';
import { expect, test } from '@playwright/test';

const headers = {
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type,idempotency-key',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
};

test('ThemeTests: contraste WCAG >= 4.5:1 em superfícies e botão primário nos dois temas', async ({ page }) => {
  await page.goto('/');

  const textTokens = [
    '--Ink',
    '--InkStrong',
    '--Muted',
    '--Faint',
    '--AccentInk',
    '--Good',
    '--Warn',
    '--Bad',
  ];
  const surfaceTokens = ['--Background', '--Sunken'];

  for (const theme of ['dark', 'light'] as const) {
    await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
    await page.getByRole('button', { name: theme === 'dark' ? 'ESCURO' : 'CLARO', exact: true }).click();
    await expect(page.locator('.horus')).toHaveAttribute('data-theme', theme);

    const results = await page.evaluate(
      ({ textTokens, surfaceTokens }) => {
        const root = document.querySelector('.horus')!;
        const style = getComputedStyle(root);

        function parseHex(raw: string): [number, number, number] {
          const hex = raw.trim().replace(/^#/, '');
          return [
            parseInt(hex.slice(0, 2), 16),
            parseInt(hex.slice(2, 4), 16),
            parseInt(hex.slice(4, 6), 16),
          ];
        }

        function linear(channel: number): number {
          const value = channel / 255;
          return value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
        }

        function luminance([r, g, b]: [number, number, number]): number {
          return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
        }

        function contrastRatio(first: [number, number, number], second: [number, number, number]): number {
          const l1 = luminance(first);
          const l2 = luminance(second);
          return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        }

        const pairs: { pair: string; ratio: number }[] = [];
        for (const surface of surfaceTokens) {
          const surfaceRgb = parseHex(style.getPropertyValue(surface));
          for (const text of textTokens) {
            const textRgb = parseHex(style.getPropertyValue(text));
            pairs.push({
              pair: `${text} on ${surface}`,
              ratio: contrastRatio(textRgb, surfaceRgb),
            });
          }
        }

        const invertFg = parseHex(style.getPropertyValue('--InvertForeground'));
        const invertBg = parseHex(style.getPropertyValue('--InvertBackground'));
        const primaryRatio = contrastRatio(invertFg, invertBg);
        const computedBg = style.backgroundColor;
        const expectedBgRgb = parseHex(style.getPropertyValue('--Background'));

        return {
          pairs,
          primaryRatio,
          computedBg,
          expectedBg: `rgb(${expectedBgRgb[0]}, ${expectedBgRgb[1]}, ${expectedBgRgb[2]})`,
        };
      },
      { textTokens, surfaceTokens },
    );

    for (const item of results.pairs) {
      expect(item.ratio, `${theme}: ${item.pair} (${item.ratio.toFixed(2)}:1)`).toBeGreaterThanOrEqual(4.5);
    }
    expect(results.primaryRatio, `${theme}: primary button contrast`).toBeGreaterThanOrEqual(4.5);
    expect(results.computedBg).toBe(results.expectedBg);
  }
});

test('ThemeTests: fontes locais Archivo Expanded, Inter e Geist Mono carregam sem fallback', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);

  const fontReport = await page.evaluate(async () => {
    await Promise.all([
      document.fonts.load('600 24px Archivo'),
      document.fonts.load('400 14px Inter'),
      document.fonts.load('500 11px Geist'),
    ]);
    const loadedFamilies = Array.from(document.fonts)
      .filter(face => face.status === 'loaded')
      .map(face => face.family.replace(/['"]/g, ''));

    return {
      loadedFamilies,
      archivoReady: document.fonts.check('600 24px Archivo'),
      interReady: document.fonts.check('400 14px Inter'),
      geistReady: document.fonts.check('500 11px Geist'),
      headingFamily: getComputedStyle(document.querySelector('h1')!).fontFamily,
      bodyFamily: getComputedStyle(document.querySelector('.horus')!).fontFamily,
      labelFamily: getComputedStyle(document.querySelector('.eyebrow')!).fontFamily,
    };
  });

  expect(fontReport.archivoReady).toBe(true);
  expect(fontReport.interReady).toBe(true);
  expect(fontReport.geistReady).toBe(true);
  expect(fontReport.loadedFamilies).toEqual(expect.arrayContaining(['Archivo', 'Inter', 'Geist']));
  expect(fontReport.headingFamily).toContain('Archivo');
  expect(fontReport.bodyFamily).toContain('Inter');
  expect(fontReport.labelFamily).toContain('Geist');
});

test('MainWindowTests & ExampleScreensTests: identidade, tema SISTEMA (prefers-color-scheme) e navegação exclusiva entre as 8 seções', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/');

  // MainWindowTests: título, nome do app e status de desenvolvimento
  await expect(page).toHaveTitle('HORUS');
  await expect(page.locator('.brand-name')).toContainText('HORUS');
  await expect(page.locator('.topbar .tag')).toHaveText('APLICATIVO EM DESENVOLVIMENTO');

  // ExampleScreensTests: opções de tema CLARO, ESCURO e SISTEMA seguindo prefers-color-scheme
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  const lightBtn = page.getByRole('button', { name: 'CLARO', exact: true });
  const darkBtn = page.getByRole('button', { name: 'ESCURO', exact: true });
  const systemBtn = page.getByRole('button', { name: 'SISTEMA', exact: true });

  await lightBtn.click();
  await expect(page.locator('.horus')).toHaveAttribute('data-theme', 'light');
  await expect(lightBtn).toHaveAttribute('aria-pressed', 'true');

  await darkBtn.click();
  await expect(page.locator('.horus')).toHaveAttribute('data-theme', 'dark');
  await expect(darkBtn).toHaveAttribute('aria-pressed', 'true');

  await systemBtn.click();
  await expect(systemBtn).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.horus')).toHaveAttribute('data-theme', 'dark');

  await page.emulateMedia({ colorScheme: 'light' });
  await expect(page.locator('.horus')).toHaveAttribute('data-theme', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('.horus')).toHaveAttribute('data-theme', 'dark');

  // ExampleScreensTests: cada item da barra lateral mostra apenas a tela correspondente
  const screens: [string, RegExp][] = [
    ['Visão geral', /Um novo olhar para criar\./],
    ['Radar', /Um olhar para as oportunidades\./],
    ['Biblioteca', /Sua mídia, em um lugar\./],
    ['Produção', /Da ideia à próxima entrega\./],
    ['Canais', /Uma identidade para cada história\./],
    ['Histórico operacional', /Cada produção deixa um registro\./],
    ['Transcrição', /Áudio e vídeo/],
    ['Ajustes', /Aparência/],
  ];

  for (const [label, heading] of screens) {
    const navBtn = page.locator('.sidebar').getByRole('button', { name: label, exact: true });
    await navBtn.click();
    await expect(navBtn).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible();
    if (label === 'Transcrição') {
      await expect(page.locator('.transcription')).not.toHaveAttribute('hidden', '');
    } else {
      const trans = page.locator('.transcription');
      if (await trans.count()) {
        await expect(trans).toHaveAttribute('hidden', '');
      }
    }
  }
});

test('WorkspaceLayoutTests: ações da Visão Geral abrem Produção e Radar e mantêm ação primária dentro da janela em 1040×680 e 1320×840', async ({ page }) => {
  await page.goto('/');

  // Ação EXPLORAR RADAR abre Radar
  await page.getByRole('button', { name: 'EXPLORAR RADAR', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Um olhar para as oportunidades.' })).toBeVisible();

  // Volta para Visão Geral e testa ABRIR PRODUÇÃO
  await page.getByRole('button', { name: 'Visão geral', exact: true }).click();
  await page.getByRole('button', { name: 'ABRIR PRODUÇÃO', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Da ideia à próxima entrega.' })).toBeVisible();

  const combinations: [number, number, 'dark' | 'light'][] = [
    [1040, 680, 'dark'],
    [1040, 680, 'light'],
    [1320, 840, 'dark'],
    [1320, 840, 'light'],
  ];

  for (const [width, height, theme] of combinations) {
    await page.setViewportSize({ width, height });
    await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
    await page.getByRole('button', { name: theme === 'dark' ? 'ESCURO' : 'CLARO', exact: true }).click();
    await page.getByRole('button', { name: 'Visão geral', exact: true }).click();

    const button = page.getByRole('button', { name: 'ABRIR PRODUÇÃO', exact: true });
    await expect(button).toBeVisible();
    const box = await button.boundingBox();
    expect(box, `${theme} ${width}x${height}`).not.toBeNull();
    expect(box!.width).toBeGreaterThan(0);
    expect(box!.height).toBeGreaterThan(0);
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    expect(box!.y + box!.height).toBeLessThanOrEqual(height);
  }
});

test('TranscriptionViewModelTests & YouTubeDownloadTests: etapas visuais (01/02/03), validação de URLs do YouTube e cancelamento de download', async ({ page }) => {
  let downloadCancelled = false;
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (path === '/v1/jobs' && request.method() === 'POST') {
      const payload = request.postDataJSON();
      const id = payload.operation === 'youtube.download' ? 'dl-1' : 'tr-1';
      await route.fulfill({
        status: 202,
        headers,
        json: { id, status: 'queued', attempt: 1, payload, error: null, result: null, created_at: '2026-11-14T12:00:00Z', updated_at: '2026-11-14T12:00:00Z' },
      });
      return;
    }
    if (path === '/v1/jobs/tr-1/events') {
      const frames = [
        { sequence: 1, kind: 'status', message: 'Transcrevendo...' },
        { sequence: 2, kind: 'progress', message: '100' },
        {
          kind: 'complete',
          job: {
            id: 'tr-1',
            status: 'succeeded',
            attempt: 1,
            payload: { operation: 'transcription.run', path: 'C:/midia/aula.mp4', model: 'small', language: 'auto' },
            error: null,
            result: { text: 'Texto transcrito.\n' },
          },
        },
      ];
      await route.fulfill({ status: 200, headers, body: frames.map(f => JSON.stringify(f)).join('\n') + '\n' });
      return;
    }
    if (path === '/v1/jobs/dl-1/cancel') {
      downloadCancelled = true;
      await route.fulfill({ status: 200, headers, json: { id: 'dl-1', status: 'cancelled' } });
      return;
    }
    if (path === '/v1/jobs/dl-1') {
      await route.fulfill({
        status: 200,
        headers,
        json: {
          id: 'dl-1',
          status: downloadCancelled ? 'cancelled' : 'running',
          attempt: 1,
          payload: { operation: 'youtube.download', url: 'https://youtu.be/BaW_jenozKc', directory: 'C:/videos' },
          error: null,
          result: null,
        },
      });
      return;
    }
    if (path === '/v1/jobs/dl-1/events') {
      const frames = [
        { sequence: 1, kind: 'stdout', message: '[download] 15% of 12.00MiB' },
        {
          kind: 'complete',
          job: {
            id: 'dl-1',
            status: 'cancelled',
            attempt: 1,
            payload: { operation: 'youtube.download', url: 'https://youtu.be/BaW_jenozKc', directory: 'C:/videos' },
            error: null,
            result: null,
          },
        },
      ];
      await route.fulfill({ status: 200, headers, body: frames.map(f => JSON.stringify(f)).join('\n') + '\n' });
      return;
    }
    const body =
      path === '/health'
        ? { status: 'ready', database_id: 'legacy-ui-db' }
        : path === '/v1/hermes'
          ? { state: 'not_checked', connected: false, cli_available: false, configured: false, message: '', checked_at: null, version: null, features: [] }
          : path === '/v1/transcription'
            ? { engine: true, downloader: true, ffmpeg: true }
            : path === '/v1/transcription/jobs'
              ? []
              : { checks: [], notice: '' };
    await route.fulfill({ status: 200, headers, json: body });
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill('browser-test-session-token-0123456789');
  await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();
  await page.getByRole('button', { name: 'Transcrição', exact: true }).click();

  // YouTubeDownloadTests.ValidatesSingleYouTubeVideo
  const urlInput = page.getByLabel('Link do YouTube');
  const dlButton = page.getByRole('button', { name: 'BAIXAR E ADICIONAR À FILA', exact: true });

  const urlCases: [string, boolean][] = [
    ['https://www.youtube.com/watch?v=BaW_jenozKc', true],
    ['https://youtu.be/BaW_jenozKc', true],
    ['https://m.youtube.com/shorts/BaW_jenozKc', true],
    ['https://www.youtube.com/live/BaW_jenozKc', true],
    ['https://www.youtube.com/embed/BaW_jenozKc', true],
    ['https://music.youtube.com/watch?v=BaW_jenozKc', true],
    ['https://youtube.com.evil.org/watch?v=BaW_jenozKc', false],
    ['file:///C:/test', false],
    ['--exec calc', false],
    ['https://youtube.com/playlist?list=test', false],
    ['http://youtu.be/BaW_jenozKc', false],
    ['https://user:pass@youtu.be/BaW_jenozKc', false],
  ];

  for (const [candidate, valid] of urlCases) {
    await urlInput.fill(candidate);
    if (valid) {
      await expect(dlButton, candidate).toBeEnabled();
    } else {
      await expect(dlButton, candidate).toBeDisabled();
    }
  }

  // Cancelamento de download do YouTube preserva mensagem legada em português
  await urlInput.fill('https://youtu.be/BaW_jenozKc');
  await page.getByLabel('Pasta de destino do download').fill('C:/videos');
  await dlButton.click();
  await expect(page.locator('.status-bar p')).toHaveText(
    'Download cancelado. Arquivos parciais podem ser retomados na mesma pasta.',
  );

  // TranscriptionViewModelTests.CompletionAndSaveDriveTheVisibleSteps
  const steps = page.getByRole('list', { name: 'Etapas da transcrição' }).locator('li');
  await expect(steps.nth(0)).not.toHaveClass(/done/);
  await expect(steps.nth(1)).not.toHaveClass(/done/);
  await expect(steps.nth(2)).not.toHaveClass(/ready/);

  await page.getByLabel('Caminho local do arquivo').fill('C:/midia/aula.mp4');
  await page.getByRole('button', { name: 'ADICIONAR À FILA', exact: true }).click();
  await page.getByRole('button', { name: 'TRANSCREVER', exact: true }).click();

  await expect(steps.nth(0)).toHaveClass(/done/);
  await expect(steps.nth(1)).not.toHaveClass(/done/);
  await expect(steps.nth(2)).not.toHaveClass(/ready/);

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'SALVAR .TXT', exact: true }).click();
  await downloadPromise;

  await expect(steps.nth(0)).toHaveClass(/done/);
  await expect(steps.nth(1)).toHaveClass(/done/);
  await expect(steps.nth(2)).toHaveClass(/ready/);

  await page.getByRole('textbox', { name: 'Transcrição', exact: true }).fill('Texto editado.');
  await expect(steps.nth(1)).not.toHaveClass(/done/);
  await expect(steps.nth(2)).not.toHaveClass(/ready/);
});
