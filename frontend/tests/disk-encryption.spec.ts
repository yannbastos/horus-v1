import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import config from '../playwright.config';

const corsHeaders = () => ({
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type,idempotency-key',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
});

const SAMPLE_GUIDANCE = [
  'O que é: a criptografia de disco (BitLocker no Windows Pro/Enterprise/Education; "criptografia de dispositivo" no Windows Home e em alguns computadores) embaralha todo o volume, de modo que quem pegar o disco ou o computador não consegue ler os arquivos sem a chave.',
  'Por que importa: o Hórus guarda roteiros, mídia, comentários, o banco de dados e o arquivo de conexão do Hermes na pasta de dados, sem criptografia própria.',
  'Como ativar: Windows Pro/Enterprise/Education: Configurações > Privacidade e segurança > Criptografia de dispositivo, ou Painel de Controle > Sistema e Segurança > BitLocker. Windows Home não tem BitLocker: use Configurações > Privacidade e segurança > Criptografia de dispositivo. Guarde a chave de recuperação fora do computador.',
].join('\n');

const baseSecurityStatus = {
  schema_version: 'security.status.v1',
  checked_at: '2026-11-15T12:00:00Z',
  real_execution_allowed: false,
  blockers: [],
  processing: {
    local_only: true,
    workflow_executor_default: 'simulated',
    radar_executor: 'hermes_simulated',
    external_connections: [],
  },
  hermes: {
    profile: 'horus',
    configured: false,
    tools: {
      restricted: true,
      effective: [],
      dangerous: [],
    },
    memory: {
      isolated: true,
    },
  },
  radar: {
    retention_days: 28,
  },
  private_mode: {
    available: true,
    enabled: true,
    reason: null,
  },
  audit: {
    available: true,
    reason: null,
  },
  credentials: {
    items: [],
  },
};

async function setupRoutes(
  page: Page,
  diagnosticsPayload: Record<string, unknown> | null,
  diagnosticsStatus = 200
) {
  const headers = corsHeaders();
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const pathUrl = new URL(request.url()).pathname;
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (pathUrl === '/health') {
      await route.fulfill({ status: 200, headers, json: { status: 'ready' } });
      return;
    }
    if (pathUrl === '/v1/hermes') {
      await route.fulfill({
        status: 200,
        headers,
        json: { state: 'ready', connected: true, cli_available: true, configured: true, features: [] },
      });
      return;
    }
    if (pathUrl === '/v1/diagnostics') {
      if (diagnosticsStatus >= 400) {
        await route.fulfill({
          status: diagnosticsStatus,
          headers,
          json: { detail: 'Não encontrado' },
        });
        return;
      }
      await route.fulfill({
        status: 200,
        headers,
        json: diagnosticsPayload ?? { checks: [], notice: 'Ambiente de teste.' },
      });
      return;
    }
    if (pathUrl === '/v1/security/status') {
      await route.fulfill({ status: 200, headers, json: baseSecurityStatus });
      return;
    }
    await route.fulfill({ status: 200, headers, json: {} });
  });
}

async function openPrivacySecurity(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill('test-session-token-0123456789');
  await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();
  await expect(page.getByText('Backend local conectado.')).toBeVisible();
  await page.getByRole('tab', { name: 'PRIVACIDADE E SEGURANÇA' }).click();
  await expect(page.getByTestId('disk-encryption-section')).toBeVisible();
}

test.describe('HOR-119 — Aviso de criptografia do disco na interface', () => {
  test('estado encrypted: exibe volume protegido, sem aviso na prontidão e passa no axe', async ({
    page,
  }) => {
    await setupRoutes(page, {
      checks: [],
      notice: 'Ambiente de teste.',
      disk_encryption: {
        status: 'encrypted',
        protection: 'on',
        method: null,
        volume: 'C:',
        detail: 'Volume C: com criptografia de disco ligada.',
        guidance: SAMPLE_GUIDANCE,
      },
    });

    await openPrivacySecurity(page);

    const card = page.getByRole('region', { name: 'Criptografia do Volume' });
    await expect(card).toContainText('Volume C:');
    await expect(card).toContainText('ATIVO');
    await expect(page.getByTestId('disk-encryption-status')).toContainText(
      'Volume C: com criptografia de disco ligada.'
    );
    await expect(page.getByTestId('disk-encryption-guidance-details')).toBeVisible();

    const axePrivacy = await new AxeBuilder({ page })
      .include('[aria-label="Criptografia do Volume"]')
      .analyze();
    expect(axePrivacy.violations).toEqual([]);

    await page.getByRole('button', { name: 'Visão geral', exact: true }).click();
    await expect(page.getByTestId('first-run-readiness')).toBeVisible();
    await expect(page.getByTestId('readiness-disk-encryption-notice')).toHaveCount(0);
  });

  test('estado not_encrypted: exibe orientação visível, não bloqueia e mostra aviso discreto na prontidão', async ({
    page,
  }) => {
    await setupRoutes(page, {
      checks: [],
      notice: 'Ambiente de teste.',
      production: {
        ready: true,
        missing: [],
        checks: [
          {
            id: 'ffmpeg',
            name: 'FFmpeg do piloto',
            available: true,
            detail: 'Disponível.',
            required_for: ['draft', 'final'],
          },
        ],
      },
      disk_encryption: {
        status: 'not_encrypted',
        protection: 'off',
        method: null,
        volume: 'D:',
        detail: 'Volume D: com proteção de criptografia desligada (sem criptografia ou suspensa).',
        guidance: SAMPLE_GUIDANCE,
      },
    });

    await openPrivacySecurity(page);

    const card = page.getByRole('region', { name: 'Criptografia do Volume' });
    await expect(card).toContainText('Volume D:');
    await expect(card).toContainText('SEM CRIPTOGRAFIA');
    await expect(card).not.toContainText('BLOQUEADO');
    await expect(page.getByTestId('disk-encryption-status')).toContainText(
      'Volume D: com proteção de criptografia desligada'
    );
    await expect(page.getByTestId('disk-encryption-nonblocking')).toContainText(
      'não impede nenhuma operação ou produção'
    );
    await expect(page.getByTestId('disk-encryption-guidance')).toContainText('BitLocker');
    await expect(page.getByTestId('disk-encryption-guidance')).toContainText('Windows Home');

    const axePrivacy = await new AxeBuilder({ page })
      .include('[aria-label="Criptografia do Volume"]')
      .analyze();
    expect(axePrivacy.violations).toEqual([]);

    await page.getByRole('button', { name: 'Visão geral', exact: true }).click();
    const readiness = page.getByTestId('first-run-readiness');
    await expect(readiness).toBeVisible();
    await expect(page.getByTestId('readiness-disk-encryption-notice')).toContainText(
      'o volume D: está sem criptografia de disco'
    );
    await expect(page.getByTestId('readiness-disk-encryption-notice')).toContainText(
      'não bloqueia nenhuma produção'
    );

    const axeReadiness = await new AxeBuilder({ page })
      .include('[data-testid="first-run-readiness"]')
      .analyze();
    expect(axeReadiness.violations).toEqual([]);
  });

  test('estado unverified: exibe não verificado, orientação expansível e passa no axe', async ({
    page,
  }) => {
    await setupRoutes(page, {
      checks: [],
      notice: 'Ambiente de teste.',
      disk_encryption: {
        status: 'unverified',
        protection: 'unknown',
        method: null,
        volume: 'C:',
        detail: 'Não verificado no volume C:: o sistema não informou o estado de forma conclusiva.',
        guidance: SAMPLE_GUIDANCE,
      },
    });

    await openPrivacySecurity(page);

    const card = page.getByRole('region', { name: 'Criptografia do Volume' });
    await expect(card).toContainText('Volume C:');
    await expect(card).toContainText('NÃO VERIFICADO');
    await expect(page.getByTestId('disk-encryption-status')).toContainText(
      'o sistema não informou o estado de forma conclusiva'
    );
    const details = page.getByTestId('disk-encryption-guidance-details');
    await expect(details).toBeVisible();
    await details.locator('summary').click();
    await expect(details).toContainText('BitLocker');

    const axePrivacy = await new AxeBuilder({ page })
      .include('[aria-label="Criptografia do Volume"]')
      .analyze();
    expect(axePrivacy.violations).toEqual([]);
  });

  test('chave ausente em /v1/diagnostics: degrada sem quebrar e passa no axe', async ({ page }) => {
    await setupRoutes(page, {
      checks: [],
      notice: 'Backend antigo sem disk_encryption.',
    });

    await openPrivacySecurity(page);

    const card = page.getByRole('region', { name: 'Criptografia do Volume' });
    await expect(card).toContainText('NÃO VERIFICADO');
    await expect(page.getByTestId('disk-encryption-status')).toContainText(
      'não informado por este backend. Nenhuma função do Hórus é bloqueada.'
    );

    const axePrivacy = await new AxeBuilder({ page })
      .include('[aria-label="Criptografia do Volume"]')
      .analyze();
    expect(axePrivacy.violations).toEqual([]);
  });
});
