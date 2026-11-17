import type { Connection } from './api';
import { ApiError, api } from './api';

export type CheckpointAction = 'hermes_run' | 'hermes_script' | 'paid_narration' | 'publish';
export type CheckpointStatus = 'active' | 'used' | 'expired';

export type HumanCheckpoint = {
  seq: number;
  id: string;
  action: CheckpointAction | string;
  subject: string;
  description: string;
  approved_by: string;
  operator_id: string;
  approved_at: string;
  expires_at: string;
  limit_amount: string | null;
  currency: string | null;
  consumed_at: string | null;
  consumed_ref: string | null;
};

export type CheckpointPage = {
  items: HumanCheckpoint[];
  next_after: number | null;
};

export type CreateCheckpointPayload = {
  action: 'paid_narration' | 'publish';
  subject: string;
  description: string;
  limit_amount: string | null;
  currency: string | null;
  ttl_seconds?: number;
  approved_by: 'human';
};

const ACTION_LABELS: Record<string, string> = {
  hermes_run: 'Execução cognitiva (Hermes)',
  hermes_script: 'Roteiro via Hermes',
  paid_narration: 'Narração paga',
  publish: 'Publicação externa',
};

export function formatCheckpointAction(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

export function getCheckpointStatus(checkpoint: HumanCheckpoint, nowMs = Date.now()): CheckpointStatus {
  if (checkpoint.consumed_at) return 'used';
  const expiresMs = Date.parse(checkpoint.expires_at);
  if (!Number.isNaN(expiresMs) && expiresMs <= nowMs) return 'expired';
  return 'active';
}

export function formatUsdLimit(amount: string | null | undefined, currency: string | null | undefined): string {
  if (!amount || !currency) return 'Sem limite monetário';
  const numeric = Number(amount);
  if (!Number.isFinite(numeric)) return `${amount} ${currency}`;
  if (currency === 'USD') {
    return `US$ ${numeric.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })} (USD)`;
  }
  return `${numeric.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })} ${currency}`;
}

export async function getCheckpoints(
  connection: Connection,
  params: { limit?: number; after?: number } = {},
  signal?: AbortSignal
): Promise<CheckpointPage> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set('limit', String(params.limit));
  if (params.after !== undefined) search.set('after', String(params.after));
  const qs = search.toString();
  return api<CheckpointPage>(connection, `/v1/checkpoints${qs ? `?${qs}` : ''}`, { signal });
}

export async function createCheckpoint(
  connection: Connection,
  payload: CreateCheckpointPayload,
  idempotencyKey = crypto.randomUUID(),
  signal?: AbortSignal
): Promise<HumanCheckpoint> {
  return api<HumanCheckpoint>(connection, '/v1/checkpoints', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
    signal,
  });
}

/**
 * Traduz recusas 409 do SecurityGate (e códigos de CheckpointRefused) para linguagem simples.
 */
export function formatSecurityGateError(reason: unknown): string {
  if (reason instanceof ApiError && reason.status === 409) {
    const rawMsg = reason.message.replace(/^security_gate:\s*/i, '').trim();
    const lower = rawMsg.toLowerCase();

    if (
      reason.code === 'checkpoint_missing' ||
      lower.includes('checkpoint_missing') ||
      lower.includes('exige aprovação humana registrada')
    ) {
      return 'Ação bloqueada: falta uma aprovação humana (checkpoint) válida para este item. Confirme o diálogo de aprovação antes de prosseguir.';
    }
    if (
      reason.code === 'checkpoint_consumed' ||
      lower.includes('checkpoint_consumed') ||
      lower.includes('já foi usado')
    ) {
      return 'Ação bloqueada: esta aprovação humana já foi usada (uso único). Crie uma nova aprovação para executar novamente.';
    }
    if (
      reason.code === 'checkpoint_expired' ||
      lower.includes('checkpoint_expired') ||
      lower.includes('expirou')
    ) {
      return 'Ação bloqueada: o prazo de validade da aprovação humana expirou. Confirme uma nova aprovação para continuar.';
    }
    if (
      reason.code === 'checkpoint_limit_exceeded' ||
      lower.includes('checkpoint_limit_exceeded') ||
      lower.includes('excede o limite aprovado')
    ) {
      return 'Ação bloqueada: o custo estimado ultrapassa o limite em USD aprovado para este checkpoint. Revise o orçamento ou faça uma nova aprovação.';
    }
    if (reason.code === 'security_gate' || reason.message.toLowerCase().includes('security_gate')) {
      return rawMsg
        ? `Ação bloqueada pela trava de segurança: ${rawMsg} Confira os detalhes em Ajustes → Privacidade e Segurança.`
        : 'Ação bloqueada pela trava de segurança do Hórus. Confira os bloqueios em Ajustes → Privacidade e Segurança.';
    }
    return rawMsg || 'Não foi possível concluir a operação por conflito de estado (409).';
  }

  return reason instanceof Error ? reason.message : String(reason);
}
