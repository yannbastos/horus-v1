import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import config from '../playwright.config';

const origin = new URL(config.use!.baseURL!).origin;
const cors = {
  'Access-Control-Allow-Origin': origin,
  'Access-Control-Allow-Headers': 'authorization,content-type,idempotency-key',
  'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
};

type ModelRecord = {
  model: string;
  installed: boolean;
  managed: boolean;
  size_bytes: number | null;
  license: { identifier: string; status: string; model_card_present: boolean } | null;
  source: { kind: string; name: string } | null;
  removable: boolean;
};

function makeInitialModels(): ModelRecord[] {
  return [
    {
      model: 'tiny',
      installed: true,
      managed: true,
      size_bytes: 78_643_200,
      license: { identifier: 'MIT', status: 'confirmed', model_card_present: true },
      source: { kind: 'local_import', name: 'whisper-tiny-local' },
      removable: true,
    },
    {
      model: 'base',
      installed: true,
      managed: false,
      size_bytes: 152_043_520,
      license: { identifier: 'MIT', status: 'verified', model_card_present: false },
      source: { kind: 'external_legacy', name: 'cache-externo' },
      removable: false,
    },
    {
      model: 'small',
      installed: false,
      managed: false,
      size_bytes: null,
      license: null,
      source: null,
      removable: false,
    },
  ];
}

type CatalogModelRecord = {
  model: string;
  size_bytes: number;
  source: { host: string; url: string };
  license: { identifier: string; url: string };
  sha256?: string | null;
  destination_label: string;
};

type SetupOptions = {
  models?: ModelRecord[];
  catalogModels?: CatalogModelRecord[] | null;
  cudaRuntime?: Record<string, unknown> | null;
  omitCudaField?: boolean;
  importError?: { status: number; detail: string } | null;
  deleteError?: { status: number; detail: string } | null;
  downloadStartError?: { status: number; detail: string } | null;
  downloadOutcome?: 'succeeded' | 'failed' | 'cancelled';
  downloadFailureMessage?: string;
  holdAfterProgress?: boolean;
};

async function setupMockBackend(page: Page, options: SetupOptions = {}) {
  let models = (options.models ?? makeInitialModels()).map(item => ({ ...item }));
  const calls: { method: string; path: string; body: unknown; headers: Record<string, string> }[] = [];
  let releaseDownloadCompletion: (() => void) | null = null;
  const downloadCompletionGate = options.holdAfterProgress
    ? new Promise<void>(resolve => {
        releaseDownloadCompletion = resolve;
      })
    : Promise.resolve();
  let cancelledViaApi = false;

  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (method === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }

    const raw = request.postData();
    const body = raw ? JSON.parse(raw) : null;
    calls.push({ method, path, body, headers: request.headers() });

    if (path === '/health') {
      return route.fulfill({ headers: cors, json: { status: 'ready', database_id: 'test-db' } });
    }

    if (path === '/v1/diagnostics') {
      const payload: Record<string, unknown> = {
        checks: [],
        notice: 'Diagnóstico simulado.',
      };
      if (!options.omitCudaField) {
        payload.cuda_runtime = options.cudaRuntime ?? {
          configured: true,
          available: true,
          source: 'nvidia_driver',
          detail: 'GPU NVIDIA pronta para aceleração CUDA.',
          missing: [],
          cuda_major: 12,
          cudnn_major: 9,
        };
      }
      return route.fulfill({ headers: cors, json: payload });
    }

    if (path === '/v1/transcription') {
      return route.fulfill({ headers: cors, json: { engine: true, downloader: true, ffmpeg: true } });
    }

    if (path === '/v1/transcription/jobs') {
      return route.fulfill({ headers: cors, json: [] });
    }

    if (path === '/v1/transcription/models' && method === 'GET') {
      return route.fulfill({ headers: cors, json: { models } });
    }

    if (path === '/v1/transcription/models/catalog' && method === 'GET') {
      if (!options.catalogModels) {
        return route.fulfill({ status: 404, headers: cors, json: { detail: 'Not found' } });
      }
      return route.fulfill({ headers: cors, json: { models: options.catalogModels } });
    }

    if (path.startsWith('/v1/transcription/models/') && path.endsWith('/download') && method === 'POST') {
      if (options.downloadStartError) {
        return route.fulfill({
          status: options.downloadStartError.status,
          headers: cors,
          json: { detail: options.downloadStartError.detail },
        });
      }
      return route.fulfill({ status: 202, headers: cors, json: { job_id: 'job-dl-small' } });
    }

    if (path === '/v1/jobs/job-dl-small/events' && method === 'GET') {
      const after = Number(url.searchParams.get('after') ?? '0');
      if (after === 0) {
        const lines = [
          JSON.stringify({ sequence: 1, kind: 'status', message: 'Baixando arquivos do modelo…' }),
          JSON.stringify({ sequence: 2, kind: 'progress', message: '45' }),
        ].join('\n') + '\n';
        return route.fulfill({
          status: 200,
          headers: { ...cors, 'Content-Type': 'application/x-ndjson' },
          body: lines,
        });
      }

      await downloadCompletionGate;
      const finalStatus = cancelledViaApi ? 'cancelled' : (options.downloadOutcome ?? 'succeeded');
      if (finalStatus === 'succeeded') {
        models = models.map(item =>
          item.model === 'small'
            ? {
                model: 'small',
                installed: true,
                managed: true,
                size_bytes: 508_559_360,
                license: { identifier: 'MIT', status: 'verified', model_card_present: true },
                source: { kind: 'catalog_download', name: 'huggingface.co' },
                removable: true,
              }
            : item
        );
      }
      const completeJob = {
        id: 'job-dl-small',
        status: finalStatus,
        attempt: 1,
        created_at: '2026-11-15T09:00:00Z',
        updated_at: '2026-11-15T09:00:10Z',
        payload: { operation: 'transcription.run', path: '', model: 'small', language: 'pt' },
        error: finalStatus === 'failed' ? (options.downloadFailureMessage ?? 'Checksum SHA-256 divergente') : null,
        result: finalStatus === 'succeeded' ? { model: 'small' } : null,
      };
      const lines = [
        JSON.stringify({ sequence: 3, kind: 'status', message: 'Verificando integridade SHA-256…' }),
        JSON.stringify({ sequence: 4, kind: 'complete', message: 'Finalizado', job: completeJob }),
      ].join('\n') + '\n';
      return route.fulfill({
        status: 200,
        headers: { ...cors, 'Content-Type': 'application/x-ndjson' },
        body: lines,
      });
    }

    if (path === '/v1/jobs/job-dl-small/cancel' && method === 'POST') {
      cancelledViaApi = true;
      releaseDownloadCompletion?.();
      return route.fulfill({ status: 200, headers: cors, json: { status: 'cancel_requested' } });
    }

    if (path === '/v1/jobs/job-dl-small' && method === 'GET') {
      const finalStatus = cancelledViaApi ? 'cancelled' : (options.downloadOutcome ?? 'running');
      return route.fulfill({
        status: 200,
        headers: cors,
        json: {
          id: 'job-dl-small',
          status: finalStatus,
          attempt: 1,
          created_at: '2026-11-15T09:00:00Z',
          updated_at: '2026-11-15T09:00:10Z',
          payload: { operation: 'transcription.run', path: '', model: 'small', language: 'pt' },
          error: finalStatus === 'failed' ? (options.downloadFailureMessage ?? 'Checksum SHA-256 divergente') : null,
          result: null,
        },
      });
    }

    if (path === '/v1/transcription/models/import' && method === 'POST') {
      if (options.importError) {
        return route.fulfill({
          status: options.importError.status,
          headers: cors,
          json: { detail: options.importError.detail },
        });
      }
      const imported: ModelRecord = {
        model: String(body?.model ?? 'small'),
        installed: true,
        managed: true,
        size_bytes: 508_559_360,
        license: { identifier: 'MIT', status: 'confirmed', model_card_present: true },
        source: { kind: 'local_import', name: String(body?.source_directory ?? 'local') },
        removable: true,
      };
      models = models.map(item => (item.model === imported.model ? imported : item));
      return route.fulfill({ status: 200, headers: cors, json: imported });
    }

    if (path.startsWith('/v1/transcription/models/') && method === 'DELETE') {
      const targetModel = decodeURIComponent(path.replace('/v1/transcription/models/', ''));
      if (url.searchParams.get('confirm') !== 'true') {
        return route.fulfill({ status: 400, headers: cors, json: { detail: 'confirm=true obrigatório' } });
      }
      if (options.deleteError) {
        return route.fulfill({
          status: options.deleteError.status,
          headers: cors,
          json: { detail: options.deleteError.detail },
        });
      }
      models = models.map(item =>
        item.model === targetModel
          ? {
              model: targetModel,
              installed: false,
              managed: false,
              size_bytes: null,
              license: null,
              source: null,
              removable: false,
            }
          : item
      );
      return route.fulfill({ status: 200, headers: cors, json: { removed: targetModel } });
    }

    return route.fulfill({
      headers: cors,
      json: { connected: false, features: [], configured: false, cli_available: false, checks: [], notice: '' },
    });
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill('browser-test-session-token-0123456789');
  await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();
  await expect(page.getByTestId('whisper-models-list')).toBeVisible();

  return { calls, releaseDownloadCompletion: () => releaseDownloadCompletion?.() };
}

test.describe('Modelos Whisper e Runtime CUDA', () => {
  test('lista modelos instalados/ausentes, importa com confirmação MIT e remove com confirmação dupla (+ axe)', async ({ page }) => {
    const { calls } = await setupMockBackend(page);

    // Verifica modelos na lista
    const tinyItem = page.getByTestId('whisper-model-item-tiny');
    await expect(tinyItem).toContainText('INSTALADO');
    await expect(tinyItem).toContainText('MIT');
    await expect(tinyItem).toContainText('75 MB');

    const baseItem = page.getByTestId('whisper-model-item-base');
    await expect(baseItem).toContainText('LEGADO EXTERNO');
    await expect(page.getByTestId('whisper-remove-btn-base')).toHaveCount(0);

    const smallItem = page.getByTestId('whisper-model-item-small');
    await expect(smallItem).toContainText('AUSENTE');

    // Verifica CUDA presente e disponível
    const cudaCard = page.getByTestId('cuda-runtime-status');
    await expect(cudaCard).toBeVisible();
    await expect(cudaCard).toContainText('DISPONÍVEL');
    await expect(cudaCard).toContainText('CUDA 12 · cuDNN 9');

    // Acessibilidade em Ajustes antes da importação
    const axeSettings = await new AxeBuilder({ page })
      .include('[data-testid="whisper-models-panel"]')
      .include('[data-testid="cuda-runtime-status"]')
      .analyze();
    expect(axeSettings.violations).toEqual([]);

    // Importa modelo 'small' com confirmação de licença MIT
    await page.getByTestId('whisper-import-btn-small').click();
    const importForm = page.getByTestId('whisper-import-form-small');
    await expect(importForm).toBeVisible();

    const confirmImportBtn = page.getByTestId('whisper-confirm-import-btn-small');
    await expect(confirmImportBtn).toBeDisabled();

    await importForm.getByLabel('Pasta local do modelo').fill('C:\\modelos\\whisper-small');
    await expect(confirmImportBtn).toBeDisabled();

    await importForm.getByRole('checkbox').check();
    await expect(confirmImportBtn).toBeEnabled();
    await confirmImportBtn.click();

    await expect(page.getByTestId('whisper-models-action-success')).toContainText('small');
    await expect(smallItem).toContainText('INSTALADO');

    const importCall = calls.find(c => c.path === '/v1/transcription/models/import');
    expect(importCall?.body).toEqual({
      model: 'small',
      source_directory: 'C:\\modelos\\whisper-small',
      confirm_license: true,
    });

    // Remoção com confirmação dupla do modelo 'tiny'
    await page.getByTestId('whisper-remove-btn-tiny').click();
    const deleteConfirm = page.getByTestId('whisper-delete-confirm-tiny');
    await expect(deleteConfirm).toBeVisible();

    const confirmDeleteBtn = page.getByTestId('whisper-confirm-delete-btn-tiny');
    await expect(confirmDeleteBtn).toBeDisabled();

    await deleteConfirm.getByRole('checkbox').check();
    await expect(confirmDeleteBtn).toBeEnabled();
    await confirmDeleteBtn.click();

    await expect(page.getByTestId('whisper-models-action-success')).toContainText('removido');
    await expect(tinyItem).toContainText('AUSENTE');
    expect(calls.some(c => c.method === 'DELETE' && c.path === '/v1/transcription/models/tiny')).toBe(true);
  });

  test('exibe erros acionáveis em português para importação 409, pasta inválida 422 e remoção 409', async ({ page }) => {
    // 409 na importação
    await setupMockBackend(page, {
      importError: { status: 409, detail: 'Model already installed' },
      deleteError: { status: 409, detail: 'Active transcription job using model' },
    });

    await page.getByTestId('whisper-import-btn-small').click();
    const importForm = page.getByTestId('whisper-import-form-small');
    await importForm.getByLabel('Pasta local do modelo').fill('C:\\modelos\\whisper-small');
    await importForm.getByRole('checkbox').check();
    await page.getByTestId('whisper-confirm-import-btn-small').click();

    await expect(page.getByTestId('whisper-models-action-error')).toContainText('já está instalado');

    // 409 na remoção
    await page.getByTestId('whisper-remove-btn-tiny').click();
    await page.getByTestId('whisper-delete-confirm-tiny').getByRole('checkbox').check();
    await page.getByTestId('whisper-confirm-delete-btn-tiny').click();

    await expect(page.getByTestId('whisper-models-action-error')).toContainText('Não foi possível remover');
  });

  test('exibe erro acionável em português para pasta inválida 422 na importação', async ({ page }) => {
    await setupMockBackend(page, {
      importError: { status: 422, detail: 'Missing model.bin in source directory' },
    });

    await page.getByTestId('whisper-import-btn-small').click();
    const importForm = page.getByTestId('whisper-import-form-small');
    await importForm.getByLabel('Pasta local do modelo').fill('C:\\pasta-vazia');
    await importForm.getByRole('checkbox').check();
    await page.getByTestId('whisper-confirm-import-btn-small').click();

    await expect(page.getByTestId('whisper-models-action-error')).toContainText('Pasta de modelo inválida');
  });

  test('exibe runtime CUDA ausente e degrada com elegância quando cuda_runtime falta no diagnóstico', async ({ page }) => {
    // Caso 1: cuda_runtime presente porém indisponível
    await setupMockBackend(page, {
      cudaRuntime: {
        configured: true,
        available: false,
        source: 'auto',
        detail: 'Biblioteca cublas64_12.dll não encontrada.',
        missing: ['cublas64_12.dll', 'cudnn_ops64_9.dll'],
        cuda_major: 12,
        cudnn_major: null,
      },
    });

    const cudaCard = page.getByTestId('cuda-runtime-status');
    await expect(cudaCard).toBeVisible();
    await expect(cudaCard).toContainText('INDISPONÍVEL');
    await expect(cudaCard).toContainText('cublas64_12.dll');

    // Caso 2: campo cuda_runtime ausente no /v1/diagnostics
    await setupMockBackend(page, { omitCudaField: true });
    await expect(page.getByTestId('whisper-models-panel')).toBeVisible();
    await expect(page.getByTestId('cuda-runtime-status')).toHaveCount(0);
  });

  test('na tela de Transcrição, mostra aviso com atalho quando o modelo escolhido está ausente (+ axe)', async ({ page }) => {
    await setupMockBackend(page);

    // Vai para a tela de Transcrição (modelo padrão é 'small', que está AUSENTE)
    await page.getByRole('button', { name: 'Transcrição', exact: true }).click();
    const warningBox = page.getByTestId('transcription-missing-model-warning');
    await expect(warningBox).toBeVisible();
    await expect(warningBox).toContainText('small');
    await expect(warningBox).toContainText('não está instalado localmente');

    // Axe na região do aviso na tela de Transcrição
    const axeTx = await new AxeBuilder({ page })
      .include('[data-testid="transcription-missing-model-warning"]')
      .analyze();
    expect(axeTx.violations).toEqual([]);

    // Ao trocar para um modelo instalado ('tiny'), o aviso desaparece
    await page.getByLabel('Modelo').selectOption('tiny');
    await expect(warningBox).toHaveCount(0);

    // Volta para 'small' e usa o atalho GERENCIAR MODELOS para ir aos Ajustes
    await page.getByLabel('Modelo').selectOption('small');
    await expect(warningBox).toBeVisible();
    await page.getByTestId('transcription-manage-models-shortcut').click();
    await expect(page.getByTestId('whisper-models-panel')).toBeVisible();
  });

  test('esconde opção de baixar modelo quando o catálogo retorna 404, mantendo importação manual', async ({ page }) => {
    await setupMockBackend(page, { catalogModels: null });

    await expect(page.getByTestId('whisper-download-btn-small')).toHaveCount(0);
    await expect(page.getByTestId('whisper-import-btn-small')).toBeVisible();
  });

  test('fluxo completo de download explícito: prévia obrigatória, confirmação, progresso acessível e verificação concluída (+ axe)', async ({ page }) => {
    const catalogSmall: CatalogModelRecord = {
      model: 'small',
      size_bytes: 508_559_360,
      source: { host: 'huggingface.co', url: 'https://huggingface.co/Systran/faster-whisper-small' },
      license: { identifier: 'MIT', url: 'https://opensource.org/licenses/MIT' },
      sha256: 'e3b0c44298fc1c149afbf4c8996fb924',
      destination_label: 'Dados locais do Hórus / whisper / small',
    };

    const { calls, releaseDownloadCompletion } = await setupMockBackend(page, {
      catalogModels: [catalogSmall],
      holdAfterProgress: true,
      downloadOutcome: 'succeeded',
    });

    await page.getByTestId('whisper-download-btn-small').click();
    const preview = page.getByTestId('whisper-download-preview-small');
    await expect(preview).toBeVisible();
    await expect(preview).toContainText('small');
    await expect(preview).toContainText('485 MB');
    await expect(preview).toContainText('huggingface.co');
    await expect(preview).toContainText('Dados locais do Hórus / whisper / small');
    await expect(preview).toContainText('e3b0c44298fc1c149afbf4c8996fb924');

    const licenseLink = preview.getByRole('link', { name: /MIT/i });
    await expect(licenseLink).toHaveAttribute('href', 'https://opensource.org/licenses/MIT');

    const confirmBtn = page.getByTestId('whisper-confirm-download-btn-small');
    await expect(confirmBtn).toBeDisabled();

    const axePreview = await new AxeBuilder({ page })
      .include('[data-testid="whisper-models-panel"]')
      .analyze();
    expect(axePreview.violations).toEqual([]);

    await preview.getByRole('checkbox').check();
    await expect(confirmBtn).toBeEnabled();
    await confirmBtn.click();

    const progressBox = page.getByTestId('whisper-download-progress-small');
    await expect(progressBox).toBeVisible();
    const progressBar = page.getByTestId('whisper-download-progressbar-small');
    await expect(progressBar).toHaveAttribute('role', 'progressbar');
    await expect(progressBar).toHaveAttribute('aria-valuemin', '0');
    await expect(progressBar).toHaveAttribute('aria-valuemax', '100');
    await expect(progressBar).toHaveAttribute('aria-valuenow', '45');
    await expect(page.getByTestId('whisper-download-progress-value-small')).toHaveText('45%');

    const axeProgress = await new AxeBuilder({ page })
      .include('[data-testid="whisper-models-panel"]')
      .analyze();
    expect(axeProgress.violations).toEqual([]);

    releaseDownloadCompletion();

    await expect(page.getByTestId('whisper-models-action-success')).toContainText('verificação concluída');
    await expect(page.getByTestId('whisper-models-action-success')).toContainText('SHA-256 validado');
    await expect(page.getByTestId('whisper-model-item-small')).toContainText('INSTALADO');

    const downloadCall = calls.find(c => c.path === '/v1/transcription/models/small/download');
    expect(downloadCall?.method).toBe('POST');
    expect(downloadCall?.body).toEqual({ confirm: true });
    expect(downloadCall?.headers['idempotency-key']).toBeTruthy();
  });

  test('permite cancelar download em andamento via POST /v1/jobs/{id}/cancel', async ({ page }) => {
    const catalogSmall: CatalogModelRecord = {
      model: 'small',
      size_bytes: 508_559_360,
      source: { host: 'huggingface.co', url: 'https://huggingface.co/Systran/faster-whisper-small' },
      license: { identifier: 'MIT', url: 'https://opensource.org/licenses/MIT' },
      destination_label: 'Dados locais do Hórus / whisper / small',
    };

    const { calls } = await setupMockBackend(page, {
      catalogModels: [catalogSmall],
      holdAfterProgress: true,
    });

    await page.getByTestId('whisper-download-btn-small').click();
    await page.getByTestId('whisper-download-preview-small').getByRole('checkbox').check();
    await page.getByTestId('whisper-confirm-download-btn-small').click();

    await expect(page.getByTestId('whisper-download-progress-small')).toBeVisible();
    await page.getByTestId('whisper-cancel-download-btn-small').click();

    await expect(page.getByTestId('whisper-models-action-notice')).toContainText('cancelado');
    await expect(page.getByTestId('whisper-model-item-small')).toContainText('AUSENTE');
    expect(calls.some(c => c.method === 'POST' && c.path === '/v1/jobs/job-dl-small/cancel')).toBe(true);
  });

  test('exibe erro acionável quando o download falha na verificação ou ao iniciar', async ({ page }) => {
    const catalogSmall: CatalogModelRecord = {
      model: 'small',
      size_bytes: 508_559_360,
      source: { host: 'huggingface.co', url: 'https://huggingface.co/Systran/faster-whisper-small' },
      license: { identifier: 'MIT', url: 'https://opensource.org/licenses/MIT' },
      destination_label: 'Dados locais do Hórus / whisper / small',
    };

    // Caso 1: erro 503 ao iniciar
    await setupMockBackend(page, {
      catalogModels: [catalogSmall],
      downloadStartError: { status: 503, detail: 'Upstream unreachable' },
    });

    await page.getByTestId('whisper-download-btn-small').click();
    await page.getByTestId('whisper-download-preview-small').getByRole('checkbox').check();
    await page.getByTestId('whisper-confirm-download-btn-small').click();
    await expect(page.getByTestId('whisper-models-action-error')).toContainText('Falha de conexão');

    // Caso 2: job falha com erro de checksum
    await setupMockBackend(page, {
      catalogModels: [catalogSmall],
      downloadOutcome: 'failed',
      downloadFailureMessage: 'Checksum SHA-256 divergente',
    });

    await page.getByTestId('whisper-download-btn-small').click();
    await page.getByTestId('whisper-download-preview-small').getByRole('checkbox').check();
    await page.getByTestId('whisper-confirm-download-btn-small').click();
    await expect(page.getByTestId('whisper-models-action-error')).toContainText('Checksum SHA-256 divergente');
    await expect(page.getByTestId('whisper-models-action-error')).toContainText('importação de pasta local');
  });
});
