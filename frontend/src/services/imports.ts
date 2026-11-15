import { ApiError } from './api';
import type { Connection } from './api';
import { parseSubtitleCues } from '../components/MediaPlayer';
import type { SubtitleCue } from '../components/MediaPlayer';

export type ImportedProjectSummary = {
  id: string;
  production_id: string;
  title: string;
  imported_at: string;
  exported_at: string;
  horus_version: string;
  source_package_name: string;
  publishable: false;
};

export type ImportedManifestFile = {
  name: string;
  sha256: string;
  size_bytes: number;
};

export type ImportedManifest = {
  schema_version: number | null;
  horus_version: string;
  database_schema_version: number | null;
  id: string;
  production_id: string;
  created_at: string;
  publishable: false;
  metadata_redacted: boolean;
  files: ImportedManifestFile[];
};

export type ImportedScriptSection = {
  heading: string;
  narration: string;
};

export type ImportedScript = {
  title: string;
  sections: ImportedScriptSection[];
  raw_text: string;
};

export type ImportedQcReport = {
  status: string;
  reason: string;
  checks: { id: string; status: string; detail: string }[];
};

export type ImportedLicenseItem = {
  id: string;
  title: string;
  license: string;
  source_url: string;
  status: string;
};

export type ImportedLicensesHistory = {
  publishable: false;
  items: ImportedLicenseItem[];
  history_count: number;
  raw: unknown;
};

export type ImportedReviewHistory = {
  production_decision: string;
  production_reviewed_at: string;
  production_notes: string;
  script_status: string;
  script_version: number | null;
  publishable: false;
};

export type ImportedProjectDetail = ImportedProjectSummary & {
  manifest: ImportedManifest;
  script: ImportedScript;
  qc: ImportedQcReport;
  licenses: ImportedLicensesHistory;
  review: ImportedReviewHistory;
  video_path: string;
  subtitles_path: string;
};

export class ImportConflictError extends ApiError {
  existingId: string | null;

  constructor(message: string, existingId: string | null) {
    super(message, 409);
    this.existingId = existingId;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readString(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function assertLoopbackUrl(baseUrl: string, path: string): string {
  const parsedBase = new URL(baseUrl);
  const host = parsedBase.hostname.toLowerCase();
  if (!['127.0.0.1', 'localhost', '::1', '[::1]'].includes(host)) {
    throw new Error('Operação recusada: apenas loopback local é permitido.');
  }
  return new URL(path, parsedBase).toString();
}

function extractErrorDetail(body: unknown, fallback: string): string {
  if (!isRecord(body)) return fallback;
  if (typeof body.detail === 'string' && body.detail.trim()) {
    return body.detail.trim();
  }
  if (Array.isArray(body.detail)) {
    const messages = body.detail
      .map(item => (isRecord(item) && typeof item.msg === 'string' ? item.msg.trim() : ''))
      .filter(Boolean);
    if (messages.length > 0) {
      return messages.join('; ');
    }
  }
  if (typeof body.message === 'string' && body.message.trim()) {
    return body.message.trim();
  }
  if (typeof body.reason === 'string' && body.reason.trim()) {
    return body.reason.trim();
  }
  return fallback;
}

function extractConflictId(body: unknown): string | null {
  if (!isRecord(body)) return null;
  const direct =
    readString(body.existing_id) ||
    readString(body.import_id) ||
    readString(body.id);
  if (direct) return direct;
  if (isRecord(body.existing)) {
    return readString(body.existing.id) || null;
  }
  if (isRecord(body.detail)) {
    return (
      readString(body.detail.existing_id) ||
      readString(body.detail.import_id) ||
      readString(body.detail.id) ||
      null
    );
  }
  return null;
}

function parseImportSummary(raw: unknown): ImportedProjectSummary {
  const record = isRecord(raw) ? raw : {};
  const manifest = isRecord(record.manifest) ? record.manifest : {};
  const script = isRecord(record.script) ? record.script : {};
  const scriptDoc = isRecord(script.document) ? script.document : script;

  const id = readString(record.id) || readString(manifest.id);
  const productionId = readString(record.production_id) || readString(manifest.production_id);
  const title =
    readString(record.title) ||
    readString(record.script_title) ||
    readString(scriptDoc.title) ||
    (id ? `Projeto importado (${id.slice(0, 8)})` : 'Projeto importado');

  return {
    id,
    production_id: productionId,
    title,
    imported_at: readString(record.imported_at) || readString(record.created_at),
    exported_at: readString(record.exported_at) || readString(manifest.created_at),
    horus_version: readString(record.horus_version) || readString(manifest.horus_version),
    source_package_name:
      readString(record.source_package_name) ||
      readString(record.package_name) ||
      readString(record.source_path) ||
      readString(record.relative_path),
    publishable: false,
  };
}

function parseManifest(raw: unknown, fallbackId: string, fallbackProductionId: string): ImportedManifest {
  const obj = isRecord(raw) ? raw : {};
  const rawFiles = Array.isArray(obj.files) ? obj.files : [];
  const files: ImportedManifestFile[] = rawFiles
    .filter(isRecord)
    .map(file => ({
      name: readString(file.name, 'arquivo'),
      sha256: readString(file.sha256),
      size_bytes: readNumber(file.size_bytes) ?? 0,
    }));

  return {
    schema_version: readNumber(obj.schema_version),
    horus_version: readString(obj.horus_version),
    database_schema_version: readNumber(obj.database_schema_version),
    id: readString(obj.id, fallbackId),
    production_id: readString(obj.production_id, fallbackProductionId),
    created_at: readString(obj.created_at),
    publishable: false,
    metadata_redacted: obj.metadata_redacted === true,
    files,
  };
}

function parseScript(raw: unknown, fallbackTitle: string): ImportedScript {
  const obj = isRecord(raw) ? raw : {};
  const doc = isRecord(obj.document) ? obj.document : obj;
  const rawSections = Array.isArray(doc.sections) ? doc.sections : [];
  const sections: ImportedScriptSection[] = rawSections
    .filter(isRecord)
    .map((sec, index) => ({
      heading: readString(sec.heading, `Seção ${index + 1}`),
      narration: readString(sec.narration) || readString(sec.text),
    }));

  return {
    title: readString(doc.title, fallbackTitle),
    sections,
    raw_text: readString(obj.raw_text) || readString(obj.text),
  };
}

function parseQc(raw: unknown): ImportedQcReport {
  const obj = isRecord(raw) ? raw : {};
  const rawChecks = Array.isArray(obj.checks) ? obj.checks : [];
  const checks = rawChecks
    .filter(isRecord)
    .map((check, index) => ({
      id: readString(check.id, `check_${index + 1}`),
      status: readString(check.status, 'unknown'),
      detail: readString(check.detail) || readString(check.reason),
    }));

  return {
    status: readString(obj.status, 'not_run'),
    reason: readString(obj.reason),
    checks,
  };
}

function parseLicenses(raw: unknown): ImportedLicensesHistory {
  const obj = isRecord(raw) ? raw : {};
  const effective = isRecord(obj.effective_provenance)
    ? obj.effective_provenance
    : isRecord(obj.original_provenance)
      ? obj.original_provenance
      : null;

  const rawItems = Array.isArray(obj.items)
    ? obj.items
    : effective && Array.isArray(effective.assets)
      ? effective.assets
      : effective && Array.isArray(effective.items)
        ? effective.items
        : [];

  const items: ImportedLicenseItem[] = rawItems.filter(isRecord).map((item, index) => ({
    id: readString(item.id) || readString(item.asset_id, `asset-${index + 1}`),
    title: readString(item.title) || readString(item.name) || readString(item.source_title, `Recurso ${index + 1}`),
    license: readString(item.license) || readString(item.license_name, 'Não informada'),
    source_url: readString(item.source_url) || readString(item.url),
    status: readString(item.status) || readString(item.decision, 'registrada'),
  }));

  const history = Array.isArray(obj.history) ? obj.history : [];

  return {
    publishable: false,
    items,
    history_count: history.length,
    raw,
  };
}

function parseReview(raw: unknown): ImportedReviewHistory {
  const obj = isRecord(raw) ? raw : {};
  const prodReview = isRecord(obj.production_review) ? obj.production_review : obj;
  const scriptReview = isRecord(obj.script_review) ? obj.script_review : {};

  return {
    production_decision:
      readString(prodReview.decision) ||
      readString(obj.decision, 'não registrada'),
    production_reviewed_at:
      readString(prodReview.reviewed_at) ||
      readString(obj.reviewed_at),
    production_notes:
      readString(prodReview.notes) ||
      readString(prodReview.comment),
    script_status: readString(scriptReview.status) || readString(scriptReview.decision),
    script_version: readNumber(obj.script_version),
    publishable: false,
  };
}

function parseImportDetail(raw: unknown): ImportedProjectDetail {
  const obj = isRecord(raw) ? raw : {};
  const summary = parseImportSummary(obj);
  const manifest = parseManifest(obj.manifest, summary.id, summary.production_id);
  const script = parseScript(obj.script, summary.title);
  const qc = parseQc(obj.qc ?? obj.quality_report);
  const licenses = parseLicenses(obj.licenses);
  const review = parseReview(obj.review);

  const encodedId = encodeURIComponent(summary.id);
  return {
    ...summary,
    title: script.title || summary.title,
    manifest,
    script,
    qc,
    licenses,
    review,
    video_path: readString(obj.video_path, `/v1/imports/${encodedId}/video`),
    subtitles_path: readString(obj.subtitles_path, `/v1/imports/${encodedId}/subtitles`),
  };
}

/**
 * Lista os projetos importados somente para consulta.
 * Lança ApiError(404) quando o backend ainda não expõe o endpoint /v1/imports.
 */
export async function listImportedProjects(
  connection: Connection,
  signal?: AbortSignal
): Promise<ImportedProjectSummary[]> {
  const url = assertLoopbackUrl(connection.baseUrl, '/v1/imports');
  const response = await fetch(url, {
    method: 'GET',
    headers: { Authorization: `Bearer ${connection.token}` },
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      extractErrorDetail(body, `Não foi possível listar projetos importados (${response.status}).`),
      response.status
    );
  }
  const payload = await response.json();
  const rawList = Array.isArray(payload)
    ? payload
    : isRecord(payload) && Array.isArray(payload.items)
      ? payload.items
      : [];
  return rawList.map(parseImportSummary).filter(item => item.id.length > 0);
}

/**
 * Importa um pacote .zip local via POST /v1/imports com Idempotency-Key.
 */
export async function importProjectPackage(
  connection: Connection,
  packagePath: string,
  idempotencyKey = crypto.randomUUID(),
  signal?: AbortSignal
): Promise<ImportedProjectSummary> {
  const url = assertLoopbackUrl(connection.baseUrl, '/v1/imports');
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${connection.token}`,
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ path: packagePath }),
    signal,
  });

  const body = await response.json().catch(() => null);
  if (response.status === 409) {
    const existingId = extractConflictId(body);
    const detail = extractErrorDetail(body, 'Este pacote já foi importado anteriormente.');
    throw new ImportConflictError(detail, existingId);
  }
  if (response.status === 400 || response.status === 422) {
    const reason = extractErrorDetail(body, 'Estrutura ou manifesto do pacote .zip inválido.');
    throw new ApiError(
      reason.toLowerCase().includes('inválid') ? reason : `Pacote inválido: ${reason}`,
      response.status
    );
  }
  if (!response.ok) {
    throw new ApiError(
      extractErrorDetail(body, `Não foi possível importar o pacote (${response.status}).`),
      response.status
    );
  }

  const rawItem = isRecord(body) && isRecord(body.item) ? body.item : body;
  return parseImportSummary(rawItem);
}

/**
 * Carrega os detalhes históricos (manifesto, roteiro, QC, licenças e revisão) de um projeto importado.
 */
export async function getImportedProjectDetail(
  connection: Connection,
  importId: string,
  signal?: AbortSignal
): Promise<ImportedProjectDetail> {
  const url = assertLoopbackUrl(connection.baseUrl, `/v1/imports/${encodeURIComponent(importId)}`);
  const response = await fetch(url, {
    method: 'GET',
    headers: { Authorization: `Bearer ${connection.token}` },
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      extractErrorDetail(body, `Não foi possível abrir o projeto importado (${response.status}).`),
      response.status
    );
  }
  const payload = await response.json();
  return parseImportDetail(payload);
}

/**
 * Baixa o stream de vídeo do projeto importado com autenticação Bearer e devolve Blob URL local.
 */
export async function fetchImportedVideoBlobUrl(
  connection: Connection,
  videoPath: string,
  signal?: AbortSignal
): Promise<string> {
  const url = assertLoopbackUrl(connection.baseUrl, videoPath);
  const response = await fetch(url, {
    method: 'GET',
    headers: { Authorization: `Bearer ${connection.token}` },
    signal,
  });
  if (!response.ok) {
    throw new ApiError(`Não foi possível carregar o vídeo importado (${response.status}).`, response.status);
  }
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

/**
 * Baixa o stream de legendas do projeto importado e converte em cues para o MediaPlayer.
 */
export async function fetchImportedSubtitleCues(
  connection: Connection,
  subtitlesPath: string,
  signal?: AbortSignal
): Promise<SubtitleCue[]> {
  const url = assertLoopbackUrl(connection.baseUrl, subtitlesPath);
  const response = await fetch(url, {
    method: 'GET',
    headers: { Authorization: `Bearer ${connection.token}` },
    signal,
  });
  if (response.status === 404) {
    return [];
  }
  if (!response.ok) {
    throw new ApiError(`Não foi possível carregar as legendas (${response.status}).`, response.status);
  }
  const rawText = await response.text();
  return parseSubtitleCues(rawText);
}
