import { api, ApiError } from './api';
import type { Connection } from './api';
import type { ProductionArtifact } from './production';

export const MANDATORY_DISCLOSURES_SEPARATOR = '\n\nDivulgações obrigatórias:\n';
export const MAX_METADATA_TITLE_LENGTH = 100;
export const MAX_METADATA_DESCRIPTION_LENGTH = 5000;

export type ProductionExportItem = {
  id: string;
  production_id: string;
  created_at: string;
  relative_path: string;
  size_bytes: number;
  sha256: string;
  publishable: boolean;
};

export type CreateExportResult = {
  item: ProductionExportItem;
  created: boolean;
};

export type ProductionThumbnailResult = {
  timecode_seconds: number;
  video_sha256: string;
  width: number;
  height: number;
  artifacts: ProductionArtifact[];
  created_at: string;
  publishable: boolean;
  created: boolean;
};

export type ProductionMetadataSource = {
  id: string;
  url: string | null;
  title: string | null;
};

export type ProductionMetadataDraft = {
  title: string;
  description: string;
  editableDescription: string;
  sources: ProductionMetadataSource[];
  disclosures: string[];
  licenses_pending: boolean;
  publishable: boolean;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function extractEditableDescription(description: string): string {
  const idx = description.indexOf(MANDATORY_DISCLOSURES_SEPARATOR);
  return idx >= 0 ? description.slice(0, idx) : description;
}

export function computeComposedDescriptionLength(
  editableDescription: string,
  disclosures: string[]
): number {
  if (disclosures.length === 0) {
    return editableDescription.length;
  }
  const footer = MANDATORY_DISCLOSURES_SEPARATOR + disclosures.join('\n');
  return editableDescription.length + footer.length;
}

function extractApiErrorDetail(
  body: { detail?: unknown; message?: unknown } | null,
  fallback: string
): string {
  if (typeof body?.detail === 'string' && body.detail.trim()) {
    return body.detail.trim();
  }
  if (Array.isArray(body?.detail)) {
    const messages = body.detail
      .map(item => (isRecord(item) && typeof item.msg === 'string' ? item.msg.trim() : ''))
      .filter(Boolean);
    if (messages.length > 0) {
      return messages.join('; ');
    }
  }
  if (typeof body?.message === 'string' && body.message.trim()) {
    return body.message.trim();
  }
  return fallback;
}

function parseExportItem(raw: unknown): ProductionExportItem {
  if (!isRecord(raw)) {
    throw new Error('Resposta de exportação inválida.');
  }
  const id = typeof raw.id === 'string' ? raw.id : '';
  const productionId = typeof raw.production_id === 'string' ? raw.production_id : '';
  const createdAt = typeof raw.created_at === 'string' ? raw.created_at : '';
  const relativePath = typeof raw.relative_path === 'string' ? raw.relative_path : '';
  const sizeBytes = typeof raw.size_bytes === 'number' && Number.isFinite(raw.size_bytes) ? raw.size_bytes : 0;
  const sha256 = typeof raw.sha256 === 'string' ? raw.sha256 : '';
  if (!id || !productionId || !sha256) {
    throw new Error('Metadados de exportação incompletos.');
  }
  return {
    id,
    production_id: productionId,
    created_at: createdAt,
    relative_path: relativePath,
    size_bytes: sizeBytes,
    sha256,
    publishable: raw.publishable === true,
  };
}

function assertLoopbackUrl(baseUrl: string, path: string): string {
  const parsedBase = new URL(baseUrl);
  const host = parsedBase.hostname.toLowerCase();
  if (!['127.0.0.1', 'localhost', '::1', '[::1]'].includes(host)) {
    throw new Error('Operação recusada: apenas loopback local é permitido.');
  }
  const target = new URL(path, parsedBase.origin);
  if (target.origin !== parsedBase.origin) {
    throw new Error('Endereço externo não permitido.');
  }
  return target.toString();
}

function assertSafeIdentifier(value: string, label: string): void {
  if (!value || /[/:\\]|\.\./.test(value)) {
    throw new Error(`${label} inválido.`);
  }
}

export function formatExportError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return error.message;
    }
    if (error.status === 401) {
      return `Sessão não autorizada (401): ${error.message}`;
    }
    if (error.status === 422) {
      return `Pedido de exportação inválido (422): ${error.message}`;
    }
    return `Erro ao exportar pacote (${error.status}): ${error.message}`;
  }
  return error instanceof Error ? error.message : String(error);
}

export function formatPublishingError(
  error: unknown,
  context: 'thumbnail' | 'metadata'
): string {
  const actionLabel = context === 'thumbnail' ? 'gerar thumbnail' : 'salvar metadados';
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return error.message;
    }
    if (error.status === 401) {
      return `Sessão não autorizada (401): ${error.message}`;
    }
    if (error.status === 422) {
      return `Dados inválidos ao ${actionLabel} (422): ${error.message}`;
    }
    return `Erro ao ${actionLabel} (${error.status}): ${error.message}`;
  }
  return error instanceof Error ? error.message : String(error);
}

function parseThumbnailArtifact(raw: unknown): ProductionArtifact {
  if (!isRecord(raw)) {
    throw new Error('Artefato de thumbnail inválido.');
  }
  const id = typeof raw.id === 'string' ? raw.id : '';
  const relativePath = typeof raw.relative_path === 'string' ? raw.relative_path : '';
  const sha256 = typeof raw.sha256 === 'string' ? raw.sha256 : '';
  const sizeBytes =
    typeof raw.size_bytes === 'number' && Number.isFinite(raw.size_bytes) ? raw.size_bytes : 0;
  if (!id || !relativePath || !sha256) {
    throw new Error('Metadados de artefato de thumbnail incompletos.');
  }
  return {
    id,
    kind: 'thumbnail',
    relative_path: relativePath,
    sha256,
    size_bytes: sizeBytes,
  };
}

function parseThumbnailResult(raw: unknown, created: boolean): ProductionThumbnailResult {
  if (!isRecord(raw)) {
    throw new Error('Resposta de thumbnail inválida.');
  }
  const timecodeSeconds =
    typeof raw.timecode_seconds === 'number' && Number.isFinite(raw.timecode_seconds)
      ? raw.timecode_seconds
      : 0;
  const videoSha256 = typeof raw.video_sha256 === 'string' ? raw.video_sha256 : '';
  const width = typeof raw.width === 'number' && Number.isFinite(raw.width) ? raw.width : 1080;
  const height = typeof raw.height === 'number' && Number.isFinite(raw.height) ? raw.height : 1920;
  const createdAt = typeof raw.created_at === 'string' ? raw.created_at : '';
  const rawArtifacts = Array.isArray(raw.artifacts) ? raw.artifacts : [];
  const artifacts = rawArtifacts.map(parseThumbnailArtifact);
  if (artifacts.length === 0) {
    throw new Error('Resposta de thumbnail sem artefatos gerados.');
  }
  return {
    timecode_seconds: timecodeSeconds,
    video_sha256: videoSha256,
    width,
    height,
    artifacts,
    created_at: createdAt,
    publishable: raw.publishable === true,
    created,
  };
}

function parseMetadataDraft(raw: unknown): ProductionMetadataDraft {
  if (!isRecord(raw)) {
    throw new Error('Resposta de metadados de publicação inválida.');
  }
  const title = typeof raw.title === 'string' ? raw.title : '';
  const description = typeof raw.description === 'string' ? raw.description : '';
  const rawSources = Array.isArray(raw.sources) ? raw.sources : [];
  const sources: ProductionMetadataSource[] = rawSources
    .filter(isRecord)
    .map(item => ({
      id: typeof item.id === 'string' ? item.id : '',
      url: typeof item.url === 'string' && item.url.trim() ? item.url.trim() : null,
      title: typeof item.title === 'string' && item.title.trim() ? item.title.trim() : null,
    }))
    .filter(item => Boolean(item.id));
  const rawDisclosures = Array.isArray(raw.disclosures) ? raw.disclosures : [];
  const disclosures = rawDisclosures
    .filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
  return {
    title,
    description,
    editableDescription: extractEditableDescription(description),
    sources,
    disclosures,
    licenses_pending: raw.licenses_pending !== false,
    publishable: raw.publishable === true,
  };
}

export async function listProductionExports(
  connection: Connection,
  productionId: string,
  signal?: AbortSignal
): Promise<ProductionExportItem[]> {
  assertSafeIdentifier(productionId, 'Identificador da produção');
  try {
    const response = await api<{ items?: unknown[] } | unknown[]>(
      connection,
      `/v1/productions/${encodeURIComponent(productionId)}/exports`,
      { signal }
    );
    const rawItems = Array.isArray(response)
      ? response
      : isRecord(response) && Array.isArray(response.items)
        ? response.items
        : [];
    return rawItems.map(parseExportItem);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return [];
    }
    throw error;
  }
}

export async function createProductionExport(
  connection: Connection,
  productionId: string,
  idempotencyKey: string,
  signal?: AbortSignal
): Promise<CreateExportResult> {
  assertSafeIdentifier(productionId, 'Identificador da produção');
  const url = assertLoopbackUrl(
    connection.baseUrl,
    `/v1/productions/${encodeURIComponent(productionId)}/export`
  );
  const response = await fetch(url, {
    method: 'POST',
    signal,
    headers: {
      Authorization: `Bearer ${connection.token}`,
      'Idempotency-Key': idempotencyKey,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
      message?: unknown;
    } | null;
    const detail =
      typeof body?.detail === 'string' && body.detail.trim()
        ? body.detail.trim()
        : typeof body?.message === 'string' && body.message.trim()
          ? body.message.trim()
          : `Não foi possível exportar o pacote (${response.status}).`;
    throw new ApiError(detail, response.status);
  }
  const raw = await response.json();
  return {
    item: parseExportItem(raw),
    created: response.status === 201,
  };
}

export async function downloadProductionExportZip(
  connection: Connection,
  productionId: string,
  exportId: string,
  signal?: AbortSignal
): Promise<Blob> {
  assertSafeIdentifier(productionId, 'Identificador da produção');
  assertSafeIdentifier(exportId, 'Identificador da exportação');
  const url = assertLoopbackUrl(
    connection.baseUrl,
    `/v1/productions/${encodeURIComponent(productionId)}/exports/${encodeURIComponent(exportId)}/file`
  );
  const response = await fetch(url, {
    method: 'GET',
    signal,
    headers: {
      Authorization: `Bearer ${connection.token}`,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail =
      typeof body?.detail === 'string' && body.detail.trim()
        ? body.detail.trim()
        : `Não foi possível baixar o pacote ZIP (${response.status}).`;
    throw new ApiError(detail, response.status);
  }
  return response.blob();
}

export async function createProductionThumbnail(
  connection: Connection,
  productionId: string,
  timecodeSeconds: number,
  idempotencyKey: string,
  signal?: AbortSignal
): Promise<ProductionThumbnailResult> {
  assertSafeIdentifier(productionId, 'Identificador da produção');
  if (typeof timecodeSeconds !== 'number' || !Number.isFinite(timecodeSeconds) || timecodeSeconds < 0) {
    throw new Error('Informe um timecode válido em segundos (>= 0).');
  }
  const url = assertLoopbackUrl(
    connection.baseUrl,
    `/v1/productions/${encodeURIComponent(productionId)}/thumbnail`
  );
  const response = await fetch(url, {
    method: 'POST',
    signal,
    headers: {
      Authorization: `Bearer ${connection.token}`,
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ timecode_seconds: timecodeSeconds }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
      message?: unknown;
    } | null;
    const detail = extractApiErrorDetail(
      body,
      `Não foi possível gerar a thumbnail (${response.status}).`
    );
    throw new ApiError(detail, response.status);
  }
  const raw = await response.json();
  return parseThumbnailResult(raw, response.status === 201);
}

export async function getProductionMetadata(
  connection: Connection,
  productionId: string,
  signal?: AbortSignal
): Promise<ProductionMetadataDraft | null> {
  assertSafeIdentifier(productionId, 'Identificador da produção');
  const url = assertLoopbackUrl(
    connection.baseUrl,
    `/v1/productions/${encodeURIComponent(productionId)}/metadata`
  );
  const response = await fetch(url, {
    method: 'GET',
    signal,
    headers: {
      Authorization: `Bearer ${connection.token}`,
    },
  });
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
      message?: unknown;
    } | null;
    const detail = extractApiErrorDetail(
      body,
      `Não foi possível carregar os metadados (${response.status}).`
    );
    throw new ApiError(detail, response.status);
  }
  const raw = await response.json();
  return parseMetadataDraft(raw);
}

export async function saveProductionMetadata(
  connection: Connection,
  productionId: string,
  payload: { title: string; description: string },
  signal?: AbortSignal
): Promise<ProductionMetadataDraft> {
  assertSafeIdentifier(productionId, 'Identificador da produção');
  const url = assertLoopbackUrl(
    connection.baseUrl,
    `/v1/productions/${encodeURIComponent(productionId)}/metadata`
  );
  const response = await fetch(url, {
    method: 'PUT',
    signal,
    headers: {
      Authorization: `Bearer ${connection.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      title: payload.title,
      description: payload.description,
    }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
      message?: unknown;
    } | null;
    const detail = extractApiErrorDetail(
      body,
      `Não foi possível salvar os metadados (${response.status}).`
    );
    throw new ApiError(detail, response.status);
  }
  const raw = await response.json();
  return parseMetadataDraft(raw);
}
