import { api, ApiError } from './api';
import type { Connection } from './api';

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

export function formatModelActionError(action: 'import' | 'delete' | 'list', reason: unknown): string {
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
  }
  return detail || 'Não foi possível concluir a operação. Verifique a conexão com o backend local.';
}
