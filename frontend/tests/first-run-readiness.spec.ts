import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import config from '../playwright.config';

const CORS_HEADERS = () => ({
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
});

function buildDiagnostics(present: {
  whisper: boolean;
  kokoro: boolean;
  ytDlp: boolean;
  ffmpeg: boolean;
}) {
  return {
    checks: [
      {
        id: 'ffmpeg',
        name: 'ffmpeg',
        available: present.ffmpeg,
        detail: present.ffmpeg ? 'Localizado.' : 'Não localizado.',
      },
      {
        id: 'faster_whisper',
        name: 'Whisper Python',
        available: present.whisper,
        detail: present.whisper ? 'Disponível.' : 'Não instalado.',
      },
      {
        id: 'yt-dlp',
        name: 'Downloader disponível',
        available: present.ytDlp,
        detail: present.ytDlp ? 'Disponível.' : 'Não localizado.',
      },
    ],
    production: {
      ready: present.whisper && present.kokoro && present.ffmpeg,
      missing: [
        ...(!present.ffmpeg ? ['ffmpeg'] : []),
        ...(!present.whisper ? ['alignment_engine'] : []),
        ...(!present.kokoro ? ['kokoro'] : []),
      ],
      checks: [
        {
          id: 'ffmpeg',
          name: 'FFmpeg do piloto',
          available: present.ffmpeg,
          detail: present.ffmpeg ? 'Presença local verificada.' : 'Indisponível.',
          required_for: ['draft', 'final'],
        },
        {
          id: 'alignment_engine',
          name: 'Motor de alinhamento',
          available: present.whisper,
          detail: present.whisper ? 'Presença local verificada.' : 'Indisponível.',
          required_for: ['draft', 'final'],
        },
        {
          id: 'kokoro',
          name: 'Kokoro local',
          available: present.kokoro,
          detail: present.kokoro ? 'Presença local verificada.' : 'Indisponível.',
          required_for: ['kokoro'],
        },
      ],
    },
    notice: 'Verificação de presença apenas.',
  };
}

test.describe('HOR-100 — Prontidão da primeira execução (componentes opcionais)', () => {
  test('exibe o que já funciona, o que falta, permite selecionar pasta/arquivo local e revalida', async ({
    page,
  }) => {
    let mode: 'missing' | 'present' | 'error' = 'missing';

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
        if (mode === 'error') {
          await route.fulfill({
            status: 500,
            headers,
            json: { detail: 'Falha ao consultar diagnóstico local.' },
          });
          return;
        }
        await route.fulfill({
          status: 200,
          headers,
          json:
            mode === 'missing'
              ? buildDiagnostics({ whisper: false, kokoro: false, ytDlp: false, ffmpeg: false })
              : buildDiagnostics({ whisper: true, kokoro: true, ytDlp: true, ffmpeg: true }),
        });
        return;
      }
      await route.fulfill({ status: 200, headers, json: [] });
    });

    await page.goto('/');
    await expect(page.getByTestId('first-run-disconnected')).toBeVisible();

    await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
    await page.getByLabel('Token da sessão').fill('browser-test-session-token-0123456789');
    await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();

    await page.getByRole('button', { name: 'Visão geral', exact: true }).click();
    const panel = page.getByTestId('first-run-readiness');
    await expect(panel).toBeVisible();
    await expect(page.getByTestId('first-run-summary')).toContainText(
      '4 de 4 componentes opcionais ainda não instalados'
    );

    for (const id of ['whisper', 'kokoro', 'yt-dlp', 'ffmpeg']) {
      await expect(page.getByTestId(`optional-component-${id}`)).toContainText('OPCIONAL AUSENTE');
    }

    // Seleciona pasta/arquivo localmente sem download automático
    await page.getByTestId('select-component-ffmpeg').click();
    await page.getByLabel('Caminho local (HORUS_FFMPEG)').fill('C:\\Ferramentas\\ffmpeg\\bin\\ffmpeg.exe');
    await page.getByRole('button', { name: 'SALVAR CAMINHO', exact: true }).click();
    await expect(page.getByTestId('saved-path-ffmpeg')).toContainText(
      'C:\\Ferramentas\\ffmpeg\\bin\\ffmpeg.exe'
    );

    // Simula erro no /v1/diagnostics
    mode = 'error';
    await panel.getByRole('button', { name: 'VERIFICAR NOVAMENTE', exact: true }).click();
    await expect(page.getByTestId('first-run-error')).toContainText(
      'Não foi possível consultar o diagnóstico local'
    );

    // Simula todos os 4 componentes presentes
    mode = 'present';
    await panel.getByRole('button', { name: 'VERIFICAR NOVAMENTE', exact: true }).click();
    await expect(page.getByTestId('first-run-summary')).toContainText('Tudo pronto');
    for (const id of ['whisper', 'kokoro', 'yt-dlp', 'ffmpeg']) {
      await expect(page.getByTestId(`optional-component-${id}`)).toContainText('DISPONÍVEL');
    }

    const axe = await new AxeBuilder({ page }).include('[data-testid="first-run-readiness"]').analyze();
    expect(axe.violations).toEqual([]);
  });
});
