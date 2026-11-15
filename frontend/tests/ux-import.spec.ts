import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import config from '../playwright.config';

function createValidWavBuffer(durationSeconds = 1): Buffer {
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
  'Access-Control-Allow-Headers': 'authorization,content-type,idempotency-key',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
});

async function connectToLibrary(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill('browser-test-session-token-0123456789');
  await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();
  await page.getByRole('button', { name: 'Biblioteca', exact: true }).click();
}

const IMPORT_ID = 'imp-2026-001';

const SAMPLE_SUMMARY = {
  id: IMPORT_ID,
  production_id: 'prod-orig-42',
  title: 'Episódio Piloto — IA Local',
  imported_at: '2026-11-14T20:15:00Z',
  exported_at: '2026-11-14T19:00:00Z',
  horus_version: '0.1.0',
  source_package_name: 'pacote-piloto.zip',
  publishable: false,
};

const SAMPLE_DETAIL = {
  ...SAMPLE_SUMMARY,
  manifest: {
    schema_version: 1,
    horus_version: '0.1.0',
    database_schema_version: 12,
    id: IMPORT_ID,
    production_id: 'prod-orig-42',
    created_at: '2026-11-14T19:00:00Z',
    publishable: false,
    metadata_redacted: true,
    files: [
      { name: 'video.mp4', sha256: 'abc123', size_bytes: 20480 },
      { name: 'subtitles.srt', sha256: 'def456', size_bytes: 512 },
    ],
  },
  script: {
    title: 'Episódio Piloto — IA Local',
    sections: [
      { heading: 'Abertura', narration: 'Bem-vindo ao episódio piloto produzido localmente.' },
      { heading: 'Encerramento', narration: 'Obrigado por acompanhar.' },
    ],
  },
  qc: {
    status: 'pass',
    reason: 'Todos os checks de áudio e legendas aprovados na produção original.',
    checks: [{ id: 'subtitles_vs_words', status: 'pass', detail: 'Desvio máximo de 40 ms.' }],
  },
  licenses: {
    publishable: false,
    items: [
      {
        id: 'asset-1',
        title: 'Trilha ambiente CC0',
        license: 'CC0-1.0',
        source_url: 'https://example.org/cc0-track',
        status: 'approved',
      },
    ],
    history: [{ action: 'approve', at: '2026-11-14T18:50:00Z' }],
  },
  review: {
    production_review: { decision: 'approve', reviewed_at: '2026-11-14T18:55:00Z' },
    script_review: { status: 'approved' },
    script_version: 2,
    publishable: false,
  },
};

test.describe('HOR-099 — Importação de projetos (.zip) somente consulta', () => {
  test('degrada silenciosamente quando /v1/imports retorna 404', async ({ page }) => {
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
        await route.fulfill({ status: 200, headers, json: [] });
        return;
      }
      await route.fulfill({ status: 404, headers, json: { detail: 'Not found' } });
    });

    await connectToLibrary(page);
    await expect(page.getByRole('heading', { name: 'Traga a mídia da sua próxima produção.' })).toBeVisible();
    await expect(page.getByTestId('imported-projects-section')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Importar pacote' })).toHaveCount(0);
  });

  test('importa pacote .zip com Idempotency-Key, exibe selo somente consulta, player + legendas e histórico acessível', async ({
    page,
  }) => {
    const items: (typeof SAMPLE_SUMMARY)[] = [];
    let capturedIdempotencyKey: string | null = null;
    let capturedBody: { path?: string } | null = null;
    const wav = createValidWavBuffer(1);

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
        await route.fulfill({ status: 200, headers, json: [] });
        return;
      }
      if (url.pathname === '/v1/imports' && method === 'GET') {
        await route.fulfill({ status: 200, headers, json: items });
        return;
      }
      if (url.pathname === '/v1/imports' && method === 'POST') {
        capturedIdempotencyKey = route.request().headers()['idempotency-key'] ?? null;
        capturedBody = route.request().postDataJSON() as { path?: string };
        items.push(SAMPLE_SUMMARY);
        await route.fulfill({ status: 201, headers, json: SAMPLE_SUMMARY });
        return;
      }
      if (url.pathname === `/v1/imports/${IMPORT_ID}` && method === 'GET') {
        await route.fulfill({ status: 200, headers, json: SAMPLE_DETAIL });
        return;
      }
      if (url.pathname === `/v1/imports/${IMPORT_ID}/video` && method === 'GET') {
        await route.fulfill({ status: 200, headers: { ...headers, 'Content-Type': 'audio/wav' }, body: wav });
        return;
      }
      if (url.pathname === `/v1/imports/${IMPORT_ID}/subtitles` && method === 'GET') {
        await route.fulfill({
          status: 200,
          headers: { ...headers, 'Content-Type': 'text/plain; charset=utf-8' },
          body: '1\n00:00:00,000 --> 00:00:01,000\nLegenda histórica de teste\n',
        });
        return;
      }
      await route.fulfill({ status: 404, headers, json: { detail: 'Not found' } });
    });

    await connectToLibrary(page);

    const section = page.getByTestId('imported-projects-section');
    await expect(section).toBeVisible();
    await expect(section).toContainText('Nenhum projeto importado ainda.');

    await section.getByRole('button', { name: 'Importar pacote' }).click();
    await section.getByLabel('Caminho do pacote (.zip)').fill('C:\\exports\\pacote-piloto.zip');
    await section.getByRole('button', { name: 'Confirmar importação' }).click();

    await expect(page.getByTestId('import-success-status')).toContainText(
      'Pacote "Episódio Piloto — IA Local" importado em modo somente consulta.'
    );
    expect(capturedIdempotencyKey).toBeTruthy();
    expect(capturedBody).toEqual({ path: 'C:\\exports\\pacote-piloto.zip' });

    const list = section.getByRole('list', { name: 'Lista de projetos importados' });
    await expect(list).toContainText('Episódio Piloto — IA Local');
    await expect(list).toContainText('Importado — somente consulta');

    const detail = page.getByTestId('imported-project-detail');
    await expect(detail).toBeVisible();
    await expect(page.getByTestId('imported-readonly-notice')).toContainText(
      'não pode ser aprovado, reexportado nem publicado'
    );
    await expect(detail.getByRole('button', { name: /aprovar|reexportar|exportar|publicar/i })).toHaveCount(0);

    await expect(detail.getByRole('region', { name: /Player de mídia:/i })).toBeVisible();
    await expect(detail).toContainText('Legendas originais do pacote');
    await expect(detail).toContainText('Bem-vindo ao episódio piloto produzido localmente.');
    await expect(detail).toContainText('APROVADO NO QC');
    await expect(detail).toContainText('Trilha ambiente CC0');
    await expect(detail).toContainText('CC0-1.0');
    await expect(detail).toContainText('Decisão da produção: approve');

    const a11yResults = await new AxeBuilder({ page }).include('[data-testid="imported-projects-section"]').analyze();
    expect(a11yResults.violations).toEqual([]);
  });

  test('exibe erros em português para 422 (pacote inválido) e 409 (já importado com abertura do existente)', async ({
    page,
  }) => {
    let mode: 'invalid' | 'conflict' = 'invalid';

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
        await route.fulfill({ status: 200, headers, json: [] });
        return;
      }
      if (url.pathname === '/v1/imports' && method === 'GET') {
        await route.fulfill({ status: 200, headers, json: [SAMPLE_SUMMARY] });
        return;
      }
      if (url.pathname === '/v1/imports' && method === 'POST') {
        if (mode === 'invalid') {
          await route.fulfill({
            status: 422,
            headers,
            json: { detail: 'Manifesto ausente ou checksum divergente no pacote.' },
          });
          return;
        }
        await route.fulfill({
          status: 409,
          headers,
          json: { detail: 'Este pacote já foi importado anteriormente.', id: IMPORT_ID },
        });
        return;
      }
      if (url.pathname === `/v1/imports/${IMPORT_ID}` && method === 'GET') {
        await route.fulfill({ status: 200, headers, json: SAMPLE_DETAIL });
        return;
      }
      if (url.pathname === `/v1/imports/${IMPORT_ID}/video`) {
        await route.fulfill({ status: 404, headers, json: { detail: 'Not found' } });
        return;
      }
      await route.fulfill({ status: 404, headers, json: { detail: 'Not found' } });
    });

    await connectToLibrary(page);
    const section = page.getByTestId('imported-projects-section');

    await section.getByRole('button', { name: 'Importar pacote' }).click();
    await section.getByLabel('Caminho do pacote (.zip)').fill('C:\\exports\\corrompido.zip');
    await section.getByRole('button', { name: 'Confirmar importação' }).click();

    await expect(page.getByTestId('import-error-status')).toContainText(
      'Pacote inválido: Manifesto ausente ou checksum divergente no pacote.'
    );

    mode = 'conflict';
    await section.getByLabel('Caminho do pacote (.zip)').fill('C:\\exports\\pacote-piloto.zip');
    await section.getByRole('button', { name: 'Confirmar importação' }).click();

    await expect(page.getByTestId('import-error-status')).toContainText(
      'Este pacote já foi importado anteriormente.'
    );
    await section.getByRole('button', { name: 'Abrir projeto já importado' }).click();
    await expect(page.getByTestId('imported-project-detail')).toBeVisible();
  });
});
