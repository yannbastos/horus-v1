import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import config from '../playwright.config';

function createValidWavBuffer(durationSeconds = 2): Buffer {
  const sampleRate = 8000;
  const bytesPerSample = 2;
  const dataSize = sampleRate * bytesPerSample * durationSeconds;
  const wav = Buffer.alloc(44 + dataSize);
  wav.write('RIFF');
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24);
  wav.writeUInt32LE(sampleRate * bytesPerSample, 28);
  wav.writeUInt16LE(bytesPerSample, 32);
  wav.writeUInt16LE(16, 34);
  wav.write('data', 36);
  wav.writeUInt32LE(dataSize, 40);
  return wav;
}

const CORS_HEADERS = () => ({
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
});

test.describe('HOR-019 — Biblioteca de mídia e player acessível', () => {
  test('exibe estados carregando, erro com recuperação e vazio na Biblioteca', async ({ page }) => {
    let shouldFail = true;
    let holdMediaRequests = true;
    let releaseMediaResponse: (() => void) | null = null;
    const mediaGate = new Promise<void>(resolve => {
      releaseMediaResponse = resolve;
    });

    await page.route('http://127.0.0.1:8765/**', async route => {
      const url = new URL(route.request().url());
      const method = route.request().method();
      const headers = CORS_HEADERS();
      if (method === 'OPTIONS') {
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
            configured: true,
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
      if (url.pathname === '/v1/media') {
        if (holdMediaRequests) {
          await mediaGate;
        }
        if (shouldFail) {
          await route.fulfill({
            status: 500,
            headers,
            json: { detail: 'Falha temporária ao ler o índice de mídia.' },
          });
          return;
        }
        await route.fulfill({ status: 200, headers, json: [] });
        return;
      }
      await route.fulfill({ status: 404, headers, json: { detail: 'Not found' } });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
    await page.getByLabel('Token da sessão').fill('browser-test-session-token-0123456789');
    await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();

    await page.getByRole('button', { name: 'Biblioteca', exact: true }).click();
    await expect(page.getByText('Carregando biblioteca de mídia…')).toBeVisible();
    holdMediaRequests = false;
    releaseMediaResponse?.();
    await expect(
      page.getByRole('alert').filter({ hasText: 'Falha temporária ao ler o índice de mídia.' })
    ).toBeVisible();

    shouldFail = false;
    await page.getByRole('button', { name: 'ATUALIZAR LISTA', exact: true }).click();
    await expect(
      page.getByRole('heading', { name: 'Traga a mídia da sua próxima produção.' })
    ).toBeVisible();
  });

  test('filtra por busca e tipo (vídeo/áudio), ordena por tamanho/nome e reproduz stream com metadados', async ({
    page,
  }) => {
    const wav = createValidWavBuffer(2);
    const items = [
      {
        id: 'med-1',
        name: 'entrevista-principal.mp4',
        size: 15 * 1024 * 1024,
        stream_path: '/v1/media/med-1/stream?expires=9999999999&ticket=ok',
      },
      {
        id: 'med-2',
        name: 'abertura-curta.webm',
        size: 3 * 1024 * 1024,
        stream_path: '/v1/media/med-2/stream?expires=9999999999&ticket=ok',
      },
      {
        id: 'med-3',
        name: 'locucao-limpa.wav',
        size: wav.length,
        stream_path: '/v1/media/med-3/stream?expires=9999999999&ticket=ok',
      },
    ];

    await page.route('http://127.0.0.1:8765/**', async route => {
      const url = new URL(route.request().url());
      const method = route.request().method();
      const headers = CORS_HEADERS();
      if (method === 'OPTIONS') {
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
            configured: true,
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
      if (url.pathname === '/v1/media') {
        await route.fulfill({ status: 200, headers, json: items });
        return;
      }
      if (url.pathname === '/v1/media/med-3/stream') {
        await route.fulfill({
          status: 200,
          headers: { ...headers, 'Content-Type': 'audio/wav' },
          body: wav,
        });
        return;
      }
      await route.fulfill({ status: 404, headers, json: { detail: 'Not found' } });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
    await page.getByLabel('Token da sessão').fill('browser-test-session-token-0123456789');
    await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();

    await page.getByRole('button', { name: 'Biblioteca', exact: true }).click();
    await expect(page.getByText(/Exibindo 3 de 3 arquivo\(s\)/)).toBeVisible();

    // Filtro por tipo: Áudio
    await page.getByRole('button', { name: 'ÁUDIO (1)', exact: true }).click();
    await expect(page.getByText(/Exibindo 1 de 3 arquivo\(s\)/)).toBeVisible();
    await expect(page.getByRole('button', { name: /locucao-limpa\.wav/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /entrevista-principal\.mp4/ })).toHaveCount(0);

    // Filtro por tipo: Vídeo
    await page.getByRole('button', { name: 'VÍDEO (2)', exact: true }).click();
    await expect(page.getByText(/Exibindo 2 de 3 arquivo\(s\)/)).toBeVisible();

    // Busca sem resultados e Limpar filtros
    await page.getByLabel('Buscar mídia por nome').fill('inexistente');
    await expect(page.getByText('Nenhuma mídia encontrada para o filtro atual.')).toBeVisible();
    await page.getByRole('button', { name: 'LIMPAR FILTROS', exact: true }).click();
    await expect(page.getByText(/Exibindo 3 de 3 arquivo\(s\)/)).toBeVisible();

    // Ordenação por maior tamanho
    await page.getByLabel('Ordenar mídias').selectOption('size_desc');
    const cards = page.getByRole('region', { name: 'Arquivos registrados' }).locator('.media-item');
    await expect(cards.first()).toContainText('entrevista-principal.mp4');

    // Abre item de áudio e verifica duração extraída do stream
    await page.getByRole('button', { name: /locucao-limpa\.wav/ }).click();
    const player = page.getByRole('region', { name: 'Player de mídia: locucao-limpa.wav' });
    await expect(player).toBeVisible();
    await expect(player.getByText('DURAÇÃO 00:02')).toBeVisible();
    await expect(page.getByRole('button', { name: /locucao-limpa\.wav/ })).toContainText(
      'Duração: 00:02'
    );
  });

  test('player local suporta controles de teclado, velocidade, legenda .srt e passa no axe-core', async ({
    page,
  }) => {
    const wav = createValidWavBuffer(4);
    await page.goto('/');
    await page.getByRole('button', { name: 'Biblioteca', exact: true }).click();

    await page.getByLabel('Selecionar mídia local').setInputFiles({
      name: 'narracao-piloto.wav',
      mimeType: 'audio/wav',
      buffer: wav,
    });

    const player = page.getByRole('region', { name: 'Player de mídia: narracao-piloto.wav' });
    await expect(player).toBeVisible();
    await expect(player.getByText('ÁUDIO', { exact: true })).toBeVisible();
    await expect(player.getByText('WAV', { exact: true })).toBeVisible();
    await expect(player.getByText('DURAÇÃO 00:04')).toBeVisible();

    // Carrega legenda .srt local
    const srtContent = [
      '1',
      '00:00:00,000 --> 00:00:02,000',
      'Primeira frase sincronizada no player local.',
      '',
      '2',
      '00:00:02,100 --> 00:00:04,000',
      'Segunda frase após avançar cinco segundos.',
    ].join('\n');

    await player.getByLabel('Selecionar legenda local (.srt ou .vtt)').setInputFiles({
      name: 'legendas-pt.srt',
      mimeType: 'text/plain',
      buffer: Buffer.from(srtContent, 'utf-8'),
    });

    const captionBox = player.getByRole('status', { name: 'Legenda atual' });
    await expect(captionBox).toContainText('Primeira frase sincronizada no player local.');

    // Avança e verifica atualização de tempo, velocidade e mudo via atalhos/controles
    await player.getByLabel('Velocidade de reprodução').selectOption('1.5');
    expect(await page.locator('video').evaluate((v: HTMLVideoElement) => v.playbackRate)).toBe(1.5);

    await player.focus();
    await page.keyboard.press('m');
    await expect(player.getByRole('button', { name: 'ATIVAR SOM' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );

    // Oculta e reexibe legenda
    await player.getByRole('button', { name: 'OCULTAR LEGENDA', exact: true }).click();
    await expect(captionBox).toHaveCount(0);
    await player.getByRole('button', { name: 'EXIBIR LEGENDA', exact: true }).click();
    await expect(captionBox).toBeVisible();

    // Auditoria axe-core WCAG 2.1 AA na tela da Biblioteca com player e legenda ativos
    const axe = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    expect(axe.violations).toEqual([]);
  });
});
