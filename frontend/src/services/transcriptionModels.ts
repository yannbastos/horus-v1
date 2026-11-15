import { api, ApiError } from './api';
import type { Connection, Job } from './api';

export type WhisperModelLicense = {
  identifier: string;
  status: string;
  model_card_present: boolean;
};

export type WhisperModelSource = {
  kind: string;
  name: string;
};

export type WhisperModelInfo = {
  model: string;
  installed: boolean;
  managed: boolean;
  size_bytes: number | null;
  license: WhisperModelLicense | null;
  source: WhisperModelSource | null;
  removable: boolean;
};

export type WhisperModelsResponse = {
  models: WhisperModelInfo[];
};

export type WhisperCatalogModel = {
  model: string;
  size_bytes: number;
  source: {
    host: string;
    url: string;
  };
  license: {
    identifier: string;
    url: string;
  };
  sha256?: string | null;
  destination_label: string;
};

export type WhisperCatalogResponse = {
  models: WhisperCatalogModel[];
};

export type DownloadWhisperModelResponse = {
  job_id: string;
};

export type ImportWhisperModelRequest = {
  model: string;
  source_directory: string;
  confirm_license: true;
};

export type CudaRuntimeDiagnostics = {
  configured: boolean;
  available: boolean;
  source: string | null;
  detail: string | null;
  missing: string[];
  cuda_major: number | null;
  cudnn_major: number | null;
};

export async function fetchWhisperModels(
  connection: Connection,
  signal?: AbortSignal
): Promise<WhisperModelInfo[]> {
  const response = await api<Partial<WhisperModelsResponse>>(connection, '/v1/transcription/models', { signal });
  if (!response || !Array.isArray(response.models)) {
    return [];
  }
  return response.models;
}

export async function importWhisperModel(
  connection: Connection,
  payload: ImportWhisperModelRequest
): Promise<WhisperModelInfo> {
  return api<WhisperModelInfo>(connection, '/v1/transcription/models/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function deleteWhisperModel(
  connection: Connection,
  model: string
): Promise<void> {
  await api<unknown>(connection, `/v1/transcription/models/${encodeURIComponent(model)}?confirm=true`, {
    method: 'DELETE',
  });
}

export async function fetchWhisperModelCatalog(
  connection: Connection,
  signal?: AbortSignal
): Promise<WhisperCatalogModel[] | null> {
  try {
    const response = await api<Partial<WhisperCatalogResponse>>(
      connection,
      '/v1/transcription/models/catalog',
      { signal }
    );
    if (!response || !Array.isArray(response.models)) {
      return [];
    }
    return response.models;
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 404) {
      return null;
    }
    throw reason;
  }
}

export async function startWhisperModelDownload(
  connection: Connection,
  model: string,
  idempotencyKey: string
): Promise<DownloadWhisperModelResponse> {
  return api<DownloadWhisperModelResponse>(
    connection,
    `/v1/transcription/models/${encodeURIComponent(model)}/download`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ confirm: true }),
    }
  );
}

export async function fetchWhisperDownloadJob(
  connection: Connection,
  jobId: string,
  signal?: AbortSignal
): Promise<Job> {
  return api<Job>(connection, `/v1/jobs/${encodeURIComponent(jobId)}`, { signal });
}

export async function cancelWhisperDownloadJob(
  connection: Connection,
  jobId: string
): Promise<unknown> {
  return api<unknown>(connection, `/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  });
}

export function safeExternalUrl(raw: string | null | undefined): string | null {
  if (!raw || typeof raw !== 'string') return null;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol === 'https:' || parsed.protocol === 'http:') {
      return parsed.toString();
    }
    return null;
  } catch {
    return null;
  }
}

export function readCudaRuntime(raw: unknown): CudaRuntimeDiagnostics | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const record = raw as Record<string, unknown>;
  if (typeof record.configured !== 'boolean' && typeof record.available !== 'boolean') return null;
  return {
    configured: Boolean(record.configured),
    available: Boolean(record.available),
    source: typeof record.source === 'string' && record.source.trim() ? record.source : null,
    detail: typeof record.detail === 'string' && record.detail.trim() ? record.detail : null,
    missing: Array.isArray(record.missing)
      ? record.missing.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
      : [],
    cuda_major: typeof record.cuda_major === 'number' ? record.cuda_major : null,
    cudnn_major: typeof record.cudnn_major === 'number' ? record.cudnn_major : null,
  };
}

export function formatModelSize(sizeBytes: number | null | undefined): string {
  if (typeof sizeBytes !== 'number' || !Number.isFinite(sizeBytes) || sizeBytes <= 0) {
    return '—';
  }
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = sizeBytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const formatted = unitIndex >= 2
    ? value.toLocaleString('pt-BR', { maximumFractionDigits: 1 })
    : Math.round(value).toLocaleString('pt-BR');
  return `${formatted} ${units[unitIndex]}`;
}

export function formatModelActionError(
  action: 'import' | 'delete' | 'list' | 'download' | 'cancel',
  reason: unknown
): string {
  const detail = reason instanceof Error ? reason.message : String(reason);
  if (reason instanceof ApiError) {
    if (action === 'import') {
      if (reason.status === 409) {
        return `Este modelo já está instalado (${detail}). Remova o modelo atual antes de importar novamente.`;
      }
      if (reason.status === 400 || reason.status === 422) {
        return `Pasta de modelo inválida (${detail}). Selecione uma pasta com os arquivos completos do modelo Whisper (config.json, model.bin, tokenizer.json).`;
      }
    }
    if (action === 'delete' && reason.status === 409) {
      return `Não foi possível remover o modelo (${detail}). Modelos de legado externo não podem ser removidos por aqui, e modelos em uso exigem aguardar a conclusão da transcrição.`;
    }
    if (action === 'download') {
      if (reason.status === 409) {
        return `Este modelo já está instalado ou possui um download em andamento (${detail}). Atualize a lista de modelos antes de tentar novamente.`;
      }
      if (reason.status === 400 || reason.status === 422) {
        return `Não foi possível iniciar o download (${detail}). Confirme os dados da prévia e tente novamente.`;
      }
      if (reason.status === 404) {
        return `Modelo não encontrado no catálogo oficial (${detail}). Atualize os modelos ou use a importação de pasta local.`;
      }
      if (reason.status === 502 || reason.status === 503 || reason.status === 504) {
        return `Falha de conexão ao baixar o modelo (${detail}). Verifique sua rede e tente novamente ou importe de uma pasta local.`;
      }
    }
    if (action === 'cancel' && (reason.status === 404 || reason.status === 409)) {
      return `Não foi possível cancelar o download (${detail}). A tarefa pode já ter sido finalizada.`;
    }
  }
  if (action === 'download') {
    return `${detail || 'Falha durante o download do modelo.'} Verifique sua conexão, o espaço em disco ou use a importação manual de pasta.`;
  }
  return detail || 'Não foi possível concluir a operação. Verifique a conexão com o backend local.';
}
