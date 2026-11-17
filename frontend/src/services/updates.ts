import { invoke, isTauri } from '@tauri-apps/api/core';

export type UpdatePhase =
  | 'disabled'
  | 'idle'
  | 'checking'
  | 'up_to_date'
  | 'available'
  | 'blocked'
  | 'backing_up'
  | 'downloading'
  | 'installing'
  | 'restarting'
  | 'error';

export type UpdateAvailableInfo = {
  version: string;
  notes: string;
  pub_date: string | null;
};

export type UpdateBlockedReason = 'job_running' | 'backend_unavailable';

export type UpdateProgress = {
  phase?: string | null;
  downloaded?: number | null;
  total: number | null;
};

export type UpdateErrorCode =
  | 'network'
  | 'manifest_invalid'
  | 'startup_failed'
  | 'signature_invalid'
  | 'downgrade_refused'
  | 'backup_failed'
  | 'install_failed'
  | 'not_configured'
  | 'configuration_invalid'
  | 'enrollment_invalid'
  | 'enrollment_required'
  | 'credential_unavailable'
  | 'token_rejected'
  | 'token_revoked'
  | 'unauthorized'
  | 'incompatible_version'
  | 'version_incompatible'
  | 'blocked_by_production';

export type UpdateError = {
  code: UpdateErrorCode | string;
  message: string;
};

export interface UpdateState {
  phase: UpdatePhase;
  current_version: string;
  installed_version?: string | null;
  auto_check: boolean;
  configured: boolean;
  channel?: string | null;
  enrolled?: boolean;
  last_check_at: string | null;
  available: UpdateAvailableInfo | null;
  available_version?: string | null;
  notes?: string | null;
  blocked_reason: UpdateBlockedReason | null;
  progress: UpdateProgress | null;
  backup_path_label: string | null;
  just_updated?: boolean;
  updated_from_version?: string | null;
  error: UpdateError | null;
  last_error?: UpdateError | null;
}

const VALID_PHASES = new Set<UpdatePhase>([
  'disabled',
  'idle',
  'checking',
  'up_to_date',
  'available',
  'blocked',
  'backing_up',
  'downloading',
  'installing',
  'restarting',
  'error',
]);

export function normalizeUpdateState(raw: unknown): UpdateState {
  if (!raw || typeof raw !== 'object') {
    return createDisabledUpdateState();
  }
  const obj = raw as Record<string, unknown>;
  const configured = typeof obj.configured === 'boolean' ? obj.configured : false;
  const rawPhase = typeof obj.phase === 'string' ? (obj.phase as UpdatePhase) : null;
  const phase: UpdatePhase =
    rawPhase && VALID_PHASES.has(rawPhase) ? rawPhase : configured ? 'idle' : 'disabled';

  const currentVersion =
    typeof obj.installed_version === 'string' && obj.installed_version
      ? obj.installed_version
      : typeof obj.current_version === 'string' && obj.current_version
        ? obj.current_version
        : '0.0.0';

  let available: UpdateAvailableInfo | null = null;
  if (obj.available && typeof obj.available === 'object') {
    const av = obj.available as Record<string, unknown>;
    if (typeof av.version === 'string' && av.version) {
      available = {
        version: av.version,
        notes: typeof av.notes === 'string' ? av.notes : '',
        pub_date: typeof av.pub_date === 'string' ? av.pub_date : null,
      };
    }
  } else if (typeof obj.available_version === 'string' && obj.available_version) {
    available = {
      version: obj.available_version,
      notes: typeof obj.notes === 'string' ? obj.notes : '',
      pub_date: null,
    };
  }

  let progress: UpdateProgress | null = null;
  if (obj.progress && typeof obj.progress === 'object') {
    const p = obj.progress as Record<string, unknown>;
    progress = {
      phase: typeof p.phase === 'string' ? p.phase : null,
      downloaded: typeof p.downloaded === 'number' && Number.isFinite(p.downloaded) ? p.downloaded : null,
      total: typeof p.total === 'number' && Number.isFinite(p.total) ? p.total : null,
    };
  }

  const rawError =
    obj.error && typeof obj.error === 'object'
      ? (obj.error as Record<string, unknown>)
      : obj.last_error && typeof obj.last_error === 'object'
        ? (obj.last_error as Record<string, unknown>)
        : null;
  const error: UpdateError | null =
    rawError && typeof rawError.code === 'string'
      ? {
          code: rawError.code,
          message: typeof rawError.message === 'string' ? rawError.message : '',
        }
      : null;

  return {
    phase,
    current_version: currentVersion,
    auto_check: typeof obj.auto_check === 'boolean' ? obj.auto_check : false,
    configured,
    channel: typeof obj.channel === 'string' && obj.channel.trim() ? obj.channel.trim() : null,
    enrolled: typeof obj.enrolled === 'boolean' ? obj.enrolled : undefined,
    last_check_at: typeof obj.last_check_at === 'string' ? obj.last_check_at : null,
    available,
    blocked_reason:
      obj.blocked_reason === 'job_running' || obj.blocked_reason === 'backend_unavailable'
        ? obj.blocked_reason
        : null,
    progress,
    backup_path_label: typeof obj.backup_path_label === 'string' ? obj.backup_path_label : null,
    just_updated: typeof obj.just_updated === 'boolean' ? obj.just_updated : false,
    updated_from_version:
      typeof obj.updated_from_version === 'string' ? obj.updated_from_version : null,
    error,
  };
}

export const UPDATE_STATE_EVENT = 'update://state';

export function createDisabledUpdateState(currentVersion = '0.0.0'): UpdateState {
  return {
    phase: 'disabled',
    current_version: currentVersion,
    auto_check: false,
    configured: false,
    last_check_at: null,
    available: null,
    blocked_reason: null,
    progress: null,
    backup_path_label: null,
    error: null,
  };
}

export async function getUpdateState(): Promise<UpdateState> {
  if (!isTauri()) return createDisabledUpdateState();
  return normalizeUpdateState(await invoke<unknown>('update_get_state'));
}

export async function setUpdateAutoCheck(enabled: boolean): Promise<UpdateState> {
  if (!isTauri()) return createDisabledUpdateState();
  return normalizeUpdateState(await invoke<unknown>('update_set_auto_check', { enabled }));
}

export async function checkUpdates(): Promise<UpdateState> {
  if (!isTauri()) return createDisabledUpdateState();
  return normalizeUpdateState(await invoke<unknown>('update_check'));
}

export async function installUpdate(confirmedVersion: string): Promise<UpdateState> {
  if (!isTauri()) return createDisabledUpdateState();
  return normalizeUpdateState(
    await invoke<unknown>('update_install', { confirmed_version: confirmedVersion }),
  );
}

export async function enrollUpdateToken(token: string): Promise<UpdateState> {
  if (!isTauri()) return createDisabledUpdateState();
  const normalized = token.trim();
  if (!normalized) {
    throw new Error('empty_token');
  }
  return normalizeUpdateState(await invoke<unknown>('update_enroll', { token: normalized }));
}

export async function clearUpdateEnrollment(): Promise<UpdateState> {
  if (!isTauri()) return createDisabledUpdateState();
  return normalizeUpdateState(await invoke<unknown>('update_clear_enrollment'));
}

export async function listenUpdateState(
  handler: (state: UpdateState) => void,
): Promise<() => void> {
  if (!isTauri()) return () => undefined;
  const { listen } = await import('@tauri-apps/api/event');
  return listen<unknown>(UPDATE_STATE_EVENT, event => {
    handler(normalizeUpdateState(event.payload));
  });
}

export function isUpdateBusyPhase(phase: UpdatePhase): boolean {
  return (
    phase === 'checking' ||
    phase === 'backing_up' ||
    phase === 'downloading' ||
    phase === 'installing' ||
    phase === 'restarting'
  );
}

const ERROR_MESSAGES_BY_CODE: Record<string, string> = {
  network:
    'Não foi possível consultar ou baixar a atualização por erro de rede. Verifique sua conexão e tente novamente.',
  signature_invalid:
    'A assinatura digital do pacote de atualização é inválida. A instalação foi recusada por segurança. Não execute o pacote; aguarde um pacote corrigido.',
  downgrade_refused:
    'A versão informada pelo canal é igual ou inferior à instalada e foi recusada por segurança.',
  backup_failed:
    'Não foi possível criar o backup dos dados locais antes da atualização. Nenhuma alteração foi feita.',
  manifest_invalid:
    'O manifesto da atualização é inválido ou incompatível. Nenhuma instalação foi iniciada. Aguarde a correção do canal antes de verificar novamente.',
  startup_failed:
    'A inicialização após a atualização não foi validada: a versão, o backend ou a saúde dos dados não foram confirmados. Preserve os dados e siga a recuperação manual abaixo.',
  install_failed:
    'A instalação da atualização falhou. Preserve os dados e consulte a recuperação manual abaixo antes de tentar novamente.',
  not_configured:
    'Este build não possui um canal de atualização configurado.',
  configuration_invalid:
    'A configuração do canal de atualização deste build é inválida. Nenhuma verificação ou instalação foi iniciada.',
  enrollment_invalid:
    'O formato do token de instalação é inválido. Informe 64 caracteres hexadecimais ou 43 caracteres base64url sem padding.',
  enrollment_required:
    'Este canal exige um token de instalação válido. Cole o token em Acesso ao canal antes de verificar atualizações.',
  credential_unavailable:
    'Não foi possível acessar o cofre de credenciais do sistema para ler ou salvar o token de instalação.',
  token_rejected:
    'O token de acesso ao canal foi recusado. Verifique o token informado ou solicite um novo token ao administrador.',
  unauthorized:
    'O token de acesso ao canal foi recusado. Verifique o token informado ou solicite um novo token ao administrador.',
  token_invalid:
    'O token de acesso ao canal foi recusado. Verifique o token informado ou solicite um novo token ao administrador.',
  token_revoked:
    'O token de acesso ao canal foi revogado. Solicite um novo token ao administrador e atualize o acesso ao canal abaixo.',
  incompatible_version:
    'A versão disponível no canal é incompatível com esta instalação. Nenhuma alteração foi feita.',
  version_incompatible:
    'A versão disponível no canal é incompatível com esta instalação. Nenhuma alteração foi feita.',
  blocked_by_production:
    'A atualização foi adiada porque há uma produção ou operação crítica em andamento. Aguarde a conclusão antes de instalar.',
};

export function formatUpdateErrorMessage(error: UpdateError): string {
  const message = ERROR_MESSAGES_BY_CODE[error.code];
  return typeof message === 'string' ? message : 'Não foi possível concluir a atualização. Preserve os dados e procure suporte.';
}

// Compatibilidade com recibos antigos: somente versões numéricas, nunca texto bruto.
export function formatUpdateErrorDetail(error: UpdateError): string | null {
  if (error.code !== 'downgrade_refused') return null;
  const versions = /^manifest version ([0-9]+\.[0-9]+\.[0-9]+) <= current ([0-9]+\.[0-9]+\.[0-9]+)$/.exec(error.message);
  return versions ? `${versions[1]} <= current ${versions[2]}` : null;
}

export function canRetryUpdateError(code: string): boolean {
  return code === 'network' || code === 'backup_failed' || code === 'install_failed';
}

export function formatUpdateBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B';
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[unitIndex]}`;
}
