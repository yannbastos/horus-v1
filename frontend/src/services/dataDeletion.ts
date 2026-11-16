import { api } from './api';
import type { Connection } from './api';

export type DataDeletionScope = 'production' | 'import';

export type DataDeletionLocalState = {
  state: 'present' | 'deleted' | 'incomplete';
  audit_id: string | null;
  changed_at: string | null;
};

export type DataDeletionItem = {
  kind: string;
  path: string;
  exists: boolean;
  files: number;
  bytes: number;
};

export type DataDeletionDbKept = {
  table: string;
  rows: number;
  reason: string;
};

export type DataDeletionDependency = {
  kind: string;
  id: string;
  version?: number;
  note: string;
};

export type DataDeletionBlocker = {
  code: string;
  message: string;
};

export type DataDeletionPreview = {
  plan_id: string;
  intent_token: string;
  expires_in_seconds: number;
  scope: DataDeletionScope;
  id: string;
  confirmation_phrase: string;
  items: DataDeletionItem[];
  totals: {
    files: number;
    bytes: number;
  };
  db_kept: DataDeletionDbKept[];
  dependencies: DataDeletionDependency[];
  blockers: DataDeletionBlocker[];
  can_execute: boolean;
  notice: string;
};

export type DataDeletionBackupInfo = {
  name: string;
  sha256: string;
  bytes: number;
  verified: boolean;
  location: string;
};

export type DataDeletionExecuteResult = {
  audit_id: string;
  scope: DataDeletionScope;
  id: string;
  items: {
    path: string;
    files: number;
    bytes: number;
    digest: string;
  }[];
  backup: DataDeletionBackupInfo;
  quarantine: string;
  recoverable: boolean;
  totals: {
    files: number;
    bytes: number;
  };
};

export type DataDeletionAuditEvent = {
  seq: number;
  id: string;
  at: string;
  action: 'started' | 'completed' | 'failed' | 'restored' | string;
  audit_id: string;
  scope: DataDeletionScope | string;
  target_id: string;
  items?: {
    path: string;
    files: number;
    bytes: number;
    digest: string;
  }[];
  backup?: DataDeletionBackupInfo;
  error?: string;
  restored_all?: boolean;
  prev_hash: string;
  hash: string;
};

export type DataDeletionAuditPage = {
  chain_ok: boolean;
  items: DataDeletionAuditEvent[];
  next_before: number | null;
};

export type DataDeletionRestoreResult = {
  audit_id: string;
  scope: DataDeletionScope | string;
  id: string;
  restored: string[];
};

export async function previewDataDeletion(
  conn: Connection,
  scope: DataDeletionScope,
  id: string,
  signal?: AbortSignal
): Promise<DataDeletionPreview> {
  return api<DataDeletionPreview>(conn, '/v1/data-deletion/preview', {
    method: 'POST',
    body: JSON.stringify({ scope, id }),
    signal,
  });
}

export async function executeDataDeletion(
  conn: Connection,
  payload: {
    plan_id: string;
    intent_token: string;
    phrase: string;
  }
): Promise<DataDeletionExecuteResult> {
  return api<DataDeletionExecuteResult>(conn, '/v1/data-deletion/execute', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function listDataDeletionAudit(
  conn: Connection,
  params?: { limit?: number; before?: number; signal?: AbortSignal }
): Promise<DataDeletionAuditPage> {
  const query = new URLSearchParams();
  if (typeof params?.limit === 'number') query.set('limit', String(params.limit));
  if (typeof params?.before === 'number') query.set('before', String(params.before));
  const suffix = query.toString() ? `?${query.toString()}` : '';
  const raw = await api<Partial<DataDeletionAuditPage>>(conn, `/v1/data-deletion/audit${suffix}`, {
    signal: params?.signal,
  });
  return {
    chain_ok: raw?.chain_ok !== false,
    items: Array.isArray(raw?.items) ? raw.items : [],
    next_before: typeof raw?.next_before === 'number' ? raw.next_before : null,
  };
}

export async function restoreDataDeletion(
  conn: Connection,
  auditId: string
): Promise<DataDeletionRestoreResult> {
  return api<DataDeletionRestoreResult>(
    conn,
    `/v1/data-deletion/${encodeURIComponent(auditId)}/restore`,
    {
      method: 'POST',
    }
  );
}

export function formatDeletionBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1).replace('.', ',')} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1).replace('.', ',')} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(2).replace('.', ',')} GB`;
}
