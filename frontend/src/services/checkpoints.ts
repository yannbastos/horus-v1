import type { Connection, SecurityStatusResponse } from './api';
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

/**
 * Formata uma string decimal positiva em USD para pt-BR sem converter para float.
 */
export function formatDecimalUsdPtBr(raw: string): string | null {
  const trimmed = raw.trim();
  const match = /^[+]?(\d+)(?:\.(\d+))?$/.exec(trimmed);
  if (!match) return null;

  const rawInt = match[1].replace(/^0+(?=\d)/, '') || '0';
  const intFormatted = rawInt.replace(/\B(?=(\d{3})+(?!\d))/g, '.');

  let frac = match[2] ?? '';
  while (frac.length > 2 && frac.endsWith('0')) {
    frac = frac.slice(0, -1);
  }
  while (frac.length < 2) {
    frac += '0';
  }

  return `US$ ${intFormatted},${frac} (USD)`;
}

function formatSingleDurationPtBr(seconds: number): string {
  if (seconds >= 60 && seconds % 60 === 0) {
    const mins = seconds / 60;
    return `${mins} ${mins === 1 ? 'minuto' : 'minutos'} (${seconds} segundos)`;
  }
  if (seconds >= 60) {
    const mins = Math.floor(seconds / 60);
    const rem = seconds % 60;
    return `${mins} ${mins === 1 ? 'minuto' : 'minutos'} e ${rem} ${rem === 1 ? 'segundo' : 'segundos'} (${seconds} segundos)`;
  }
  return `${seconds} ${seconds === 1 ? 'segundo' : 'segundos'}`;
}

export function formatCheckpointValidityPtBr(
  defaultTtlSeconds: number | null | undefined,
  maxTtlSeconds?: number | null | undefined
): string {
  if (typeof defaultTtlSeconds !== 'number' || !Number.isFinite(defaultTtlSeconds) || defaultTtlSeconds <= 0) {
    return 'Não informado';
  }
  const normalizedDefault = Math.trunc(defaultTtlSeconds);
  const base = formatSingleDurationPtBr(normalizedDefault);
  if (
    typeof maxTtlSeconds === 'number' &&
    Number.isFinite(maxTtlSeconds) &&
    maxTtlSeconds > 0 &&
    Math.trunc(maxTtlSeconds) !== normalizedDefault
  ) {
    return `${base} · máximo: ${formatSingleDurationPtBr(Math.trunc(maxTtlSeconds))}`;
  }
  return base;
}

export type ResolvedCheckpointLimits = {
  status: 'configured' | 'unconfigured' | 'unknown';
  reservationPerRunUsd: string | null;
  estimatedCostLabel: string;
  limitLabel: string;
  validityLabel: string;
  defaultTtlSeconds: number | null;
  maxTtlSeconds: number | null;
};

/**
 * Extrai e formata os limites reais de checkpoint informados por GET /v1/security/status,
 * degradando com segurança quando o backend é antigo e não expõe os campos novos.
 */
export function resolveCheckpointLimits(
  securityStatus: SecurityStatusResponse | null | undefined
): ResolvedCheckpointLimits {
  const costLimits = securityStatus?.hermes?.cost_limits;
  const checkpointLimits = securityStatus?.hermes?.checkpoint_limits;

  const defaultTtlSeconds =
    typeof checkpointLimits?.default_ttl_seconds === 'number' &&
    Number.isFinite(checkpointLimits.default_ttl_seconds) &&
    checkpointLimits.default_ttl_seconds > 0
      ? Math.trunc(checkpointLimits.default_ttl_seconds)
      : null;

  const maxTtlSeconds =
    typeof checkpointLimits?.max_ttl_seconds === 'number' &&
    Number.isFinite(checkpointLimits.max_ttl_seconds) &&
    checkpointLimits.max_ttl_seconds > 0
      ? Math.trunc(checkpointLimits.max_ttl_seconds)
      : null;

  const validityLabel = formatCheckpointValidityPtBr(defaultTtlSeconds, maxTtlSeconds);

  if (
    !costLimits ||
    typeof costLimits !== 'object' ||
    !('reservation_per_run_usd' in costLimits) ||
    costLimits.reservation_per_run_usd === undefined
  ) {
    return {
      status: 'unknown',
      reservationPerRunUsd: null,
      estimatedCostLabel: 'Não informado',
      limitLabel: 'Não informado',
      validityLabel,
      defaultTtlSeconds,
      maxTtlSeconds,
    };
  }

  const rawUsd = costLimits.reservation_per_run_usd;
  if (typeof rawUsd === 'string') {
    const formatted = formatDecimalUsdPtBr(rawUsd);
    if (formatted) {
      return {
        status: 'configured',
        reservationPerRunUsd: rawUsd.trim(),
        estimatedCostLabel: `Até ${formatted}`,
        limitLabel: formatted,
        validityLabel,
        defaultTtlSeconds,
        maxTtlSeconds,
      };
    }
  }

  return {
    status: 'unconfigured',
    reservationPerRunUsd: null,
    estimatedCostLabel: 'Não configurado',
    limitLabel: 'Não configurado',
    validityLabel,
    defaultTtlSeconds,
    maxTtlSeconds,
  };
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
