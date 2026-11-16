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
  | 'error';

export type UpdateAvailableInfo = {
  version: string;
  notes: string;
  pub_date: string | null;
};

export type UpdateBlockedReason = 'job_running' | 'backend_unavailable';

export type UpdateProgress = {
  downloaded: number;
  total: number | null;
};

export type UpdateErrorCode =
  | 'network'
  | 'signature_invalid'
  | 'downgrade_refused'
  | 'backup_failed'
  | 'install_failed'
  | 'not_configured';

export type UpdateError = {
  code: UpdateErrorCode | string;
  message: string;
};

export interface UpdateState {
  phase: UpdatePhase;
  current_version: string;
  auto_check: boolean;
  configured: boolean;
  last_check_at: string | null;
  available: UpdateAvailableInfo | null;
  blocked_reason: UpdateBlockedReason | null;
  progress: UpdateProgress | null;
  backup_path_label: string | null;
  error: UpdateError | null;
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
  return invoke<UpdateState>('update_get_state');
}

export async function setUpdateAutoCheck(enabled: boolean): Promise<UpdateState> {
  if (!isTauri()) return createDisabledUpdateState();
  return invoke<UpdateState>('update_set_auto_check', { enabled });
}

export async function checkUpdates(): Promise<UpdateState> {
  if (!isTauri()) return createDisabledUpdateState();
  return invoke<UpdateState>('update_check');
}

export async function installUpdate(confirmedVersion: string): Promise<UpdateState> {
  if (!isTauri()) return createDisabledUpdateState();
  return invoke<UpdateState>('update_install', { confirmed_version: confirmedVersion });
}

export async function listenUpdateState(
  handler: (state: UpdateState) => void,
): Promise<() => void> {
  if (!isTauri()) return () => undefined;
  const { listen } = await import('@tauri-apps/api/event');
  return listen<UpdateState>(UPDATE_STATE_EVENT, event => {
    handler(event.payload);
  });
}

export function isUpdateBusyPhase(phase: UpdatePhase): boolean {
  return (
    phase === 'checking' ||
    phase === 'backing_up' ||
    phase === 'downloading' ||
    phase === 'installing'
  );
}

const ERROR_MESSAGES_BY_CODE: Record<string, string> = {
  network:
    'Não foi possível consultar ou baixar a atualização por erro de rede. Verifique sua conexão e tente novamente.',
  signature_invalid:
    'A assinatura digital do pacote de atualização é inválida. A instalação foi recusada por segurança.',
  downgrade_refused:
    'A versão informada pelo canal é igual ou inferior à instalada e foi recusada por segurança.',
  backup_failed:
    'Não foi possível criar o backup dos dados locais antes da atualização. Nenhuma alteração foi feita.',
  install_failed:
    'A instalação da atualização falhou. Seus dados locais e o backup foram preservados.',
  not_configured:
    'Este build não possui um canal de atualização configurado.',
};

export function formatUpdateErrorMessage(error: UpdateError): string {
  return ERROR_MESSAGES_BY_CODE[error.code] ?? (error.message || 'Erro desconhecido ao atualizar.');
}

export function canRetryUpdateError(code: string): boolean {
  return code !== 'signature_invalid' && code !== 'downgrade_refused' && code !== 'not_configured';
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
