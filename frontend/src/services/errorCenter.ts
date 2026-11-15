import { api } from './api';
import type { Connection } from './api';

export type ErrorCenterKind = 'production' | 'workflow_step' | 'job' | 'workflow';
export type ErrorCenterStatus = 'failed' | 'blocked' | 'interrupted';

export type ErrorCenterRef = {
  kind: 'production' | 'workflow' | 'job' | string;
  id: string;
  workflow_id?: string | null;
};

export type ErrorCenterItem = {
  id: string;
  kind: ErrorCenterKind | string;
  status: ErrorCenterStatus | string;
  code: string;
  stage: string;
  at: string;
  title: string;
  message_pt: string;
  suggested_action_pt: string;
  ref: ErrorCenterRef;
  duration_seconds?: number | null;
  exit_code?: number | null;
};

export type ErrorCenterListResponse = {
  items: ErrorCenterItem[];
  next_after: string | null;
};

export type ErrorCenterSummaryCode = {
  code: string;
  count: number;
  title: string;
  suggested_action_pt: string;
};

export type ErrorCenterSummaryStage = {
  stage: string;
  count: number;
};

export type ErrorCenterSummaryKind = {
  kind: string;
  count: number;
};

export type ErrorCenterSummaryResponse = {
  total: number;
  by_code: ErrorCenterSummaryCode[];
  by_stage: ErrorCenterSummaryStage[];
  by_kind: ErrorCenterSummaryKind[];
};

export type ListErrorsParams = {
  limit?: number;
  after?: string | null;
  stage?: string;
  code?: string;
  kind?: string;
  since?: string;
  until?: string;
  signal?: AbortSignal;
};

export type GetErrorsSummaryParams = {
  since?: string;
  until?: string;
  signal?: AbortSignal;
};

export const ERROR_KIND_LABELS: Record<string, string> = {
  production: 'Produção',
  workflow_step: 'Etapa da análise',
  job: 'Tarefa',
  workflow: 'Análise',
};

export const ERROR_STATUS_LABELS: Record<string, string> = {
  failed: 'Falhou',
  blocked: 'Bloqueado',
  interrupted: 'Interrompido',
};

export const ERROR_STAGE_LABELS: Record<string, string> = {
  prepare: 'Preparação',
  render: 'Render',
  transcription: 'Transcrição',
  download: 'Download',
  planning: 'Planejamento',
  research: 'Pesquisa',
  analysis: 'Análise',
  proposal: 'Proposta',
  workflow: 'Análise',
  narration: 'Narração',
  subtitles: 'Legendas',
  qc: 'Controle de qualidade',
  unknown: 'Desconhecida',
};

export function formatErrorKind(kind: string): string {
  return ERROR_KIND_LABELS[kind] ?? kind;
}

export function formatErrorStatus(status: string): string {
  return ERROR_STATUS_LABELS[status] ?? status;
}

export function formatErrorStage(stage: string): string {
  return ERROR_STAGE_LABELS[stage] ?? stage;
}

export async function listErrors(
  connection: Connection,
  params: ListErrorsParams = {},
): Promise<ErrorCenterListResponse> {
  const query = new URLSearchParams();
  query.set('limit', String(params.limit ?? 25));
  if (params.after) query.set('after', params.after);
  if (params.stage) query.set('stage', params.stage);
  if (params.code) query.set('code', params.code);
  if (params.kind) query.set('kind', params.kind);
  if (params.since) query.set('since', params.since);
  if (params.until) query.set('until', params.until);
  return api<ErrorCenterListResponse>(connection, `/v1/errors?${query.toString()}`, {
    signal: params.signal,
  });
}

export async function getErrorsSummary(
  connection: Connection,
  params: GetErrorsSummaryParams = {},
): Promise<ErrorCenterSummaryResponse> {
  const query = new URLSearchParams();
  if (params.since) query.set('since', params.since);
  if (params.until) query.set('until', params.until);
  const suffix = query.toString();
  return api<ErrorCenterSummaryResponse>(
    connection,
    suffix ? `/v1/errors/summary?${suffix}` : '/v1/errors/summary',
    { signal: params.signal },
  );
}
