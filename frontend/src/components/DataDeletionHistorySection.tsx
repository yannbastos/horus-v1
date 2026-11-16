import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Connection } from '../services/api';
import {
  formatDeletionBytes,
  listDataDeletionAudit,
  restoreDataDeletion,
} from '../services/dataDeletion';
import type { DataDeletionAuditEvent } from '../services/dataDeletion';
import { StatusCard } from './SecurityWidgets';
import { StatusMessage } from './StatusMessage';

const ACTION_LABELS: Record<string, string> = {
  started: 'Exclusão iniciada',
  completed: 'Exclusão concluída (em quarentena)',
  failed: 'Falha na exclusão',
  restored: 'Restaurado da quarentena',
};

const ACTION_TAG_CLASS: Record<string, string> = {
  started: 'tag',
  completed: 'tag warn',
  failed: 'tag bad',
  restored: 'tag good',
};

const SCOPE_LABELS: Record<string, string> = {
  production: 'Produção',
  import: 'Projeto importado',
};

function formatAuditDate(iso: string): string {
  if (!iso) return '—';
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return iso;
  return new Date(parsed).toLocaleString('pt-BR');
}

export interface DataDeletionHistorySectionProps {
  connection: Connection | null;
}

/**
 * Histórico de exclusões locais em Ajustes → Privacidade e Segurança,
 * com verificação da cadeia de auditoria e ação "Restaurar" da quarentena.
 */
export function DataDeletionHistorySection({ connection }: DataDeletionHistorySectionProps) {
  const [events, setEvents] = useState<DataDeletionAuditEvent[]>([]);
  const [chainOk, setChainOk] = useState(true);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [restoringAuditId, setRestoringAuditId] = useState<string | null>(null);
  const [restoreError, setRestoreError] = useState('');
  const [restoreSuccess, setRestoreSuccess] = useState('');

  const loadAudit = useCallback(
    async (conn: Connection, signal?: AbortSignal) => {
      setLoading(true);
      setLoadError('');
      try {
        const page = await listDataDeletionAudit(conn, { limit: 50, signal });
        if (!signal?.aborted) {
          setChainOk(page.chain_ok);
          setEvents(page.items);
          setNextBefore(page.next_before);
        }
      } catch (reason) {
        if (!signal?.aborted) {
          setLoadError(
            reason instanceof Error
              ? reason.message
              : 'Não foi possível carregar o histórico de exclusões.'
          );
        }
      } finally {
        if (!signal?.aborted) {
          setLoading(false);
        }
      }
    },
    []
  );

  useEffect(() => {
    if (!connection) {
      setEvents([]);
      setChainOk(true);
      setNextBefore(null);
      setLoadError('');
      return;
    }
    const controller = new AbortController();
    void loadAudit(connection, controller.signal);
    return () => controller.abort();
  }, [connection, loadAudit]);

  const restorableAuditIds = useMemo(() => {
    const actionsByAudit = new Map<string, Set<string>>();
    for (const ev of events) {
      const set = actionsByAudit.get(ev.audit_id) ?? new Set<string>();
      set.add(ev.action);
      actionsByAudit.set(ev.audit_id, set);
    }
    const restorable = new Set<string>();
    for (const [auditId, actions] of actionsByAudit.entries()) {
      if (actions.has('completed') && !actions.has('restored') && !actions.has('failed')) {
        restorable.add(auditId);
      }
    }
    return restorable;
  }, [events]);

  const startedMetaByAudit = useMemo(() => {
    const map = new Map<string, DataDeletionAuditEvent>();
    for (const ev of events) {
      if (ev.action === 'started') {
        map.set(ev.audit_id, ev);
      }
    }
    return map;
  }, [events]);

  async function handleLoadMore() {
    if (!connection || nextBefore === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listDataDeletionAudit(connection, { limit: 50, before: nextBefore });
      setChainOk(page.chain_ok);
      setEvents(prev => [...prev, ...page.items]);
      setNextBefore(page.next_before);
    } catch (reason) {
      setLoadError(
        reason instanceof Error ? reason.message : 'Não foi possível carregar mais eventos.'
      );
    } finally {
      setLoadingMore(false);
    }
  }

  async function handleRestore(auditId: string) {
    if (!connection || restoringAuditId) return;
    setRestoringAuditId(auditId);
    setRestoreError('');
    setRestoreSuccess('');
    try {
      const result = await restoreDataDeletion(connection, auditId);
      setRestoreSuccess(
        `Arquivos de ${SCOPE_LABELS[result.scope] || result.scope} (${result.id.slice(
          0,
          8
        )}) restaurados da quarentena com sucesso.`
      );
      await loadAudit(connection);
    } catch (reason) {
      setRestoreError(
        reason instanceof Error
          ? reason.message
          : 'Não foi possível restaurar os arquivos da quarentena.'
      );
    } finally {
      setRestoringAuditId(null);
    }
  }

  return (
    <StatusCard
      eyebrow="EXCLUSÃO SEGURA DE DADOS"
      title="Histórico de exclusões locais e quarentena"
      subtitle="Registro encadeado de exclusões em quarentena recuperável. Você pode restaurar arquivos movidos para a quarentena."
      ariaLabel="Histórico de exclusões locais e quarentena"
      badge={
        connection ? (
          <button
            type="button"
            className="button"
            disabled={loading || Boolean(restoringAuditId)}
            onClick={() => void loadAudit(connection)}
          >
            Atualizar histórico
          </button>
        ) : undefined
      }
    >
      {!chainOk && (
        <StatusMessage state="error" testId="data-deletion-chain-warning">
          A cadeia de integridade do histórico de exclusões falhou. Novas exclusões e restaurações
          ficam bloqueadas até verificação.
        </StatusMessage>
      )}

      {restoreError && (
        <StatusMessage state="error" testId="data-deletion-restore-error">
          {restoreError}
        </StatusMessage>
      )}

      {restoreSuccess && (
        <StatusMessage state="success" tone="good" testId="data-deletion-restore-success">
          {restoreSuccess}
        </StatusMessage>
      )}

      {loading && (
        <StatusMessage state="loading" testId="data-deletion-audit-loading">
          Carregando histórico de exclusões…
        </StatusMessage>
      )}

      {!loading && loadError && (
        <StatusMessage state="error" testId="data-deletion-audit-error">
          {loadError}
        </StatusMessage>
      )}

      {!loading && !loadError && events.length === 0 && (
        <StatusMessage state="empty" testId="data-deletion-audit-empty">
          Nenhuma exclusão local registrada até o momento.
        </StatusMessage>
      )}

      {events.length > 0 && (
        <ul className="data-deletion-list" data-testid="data-deletion-audit-list">
          {events.map(ev => {
            const isRestorable =
              chainOk && ev.action === 'completed' && restorableAuditIds.has(ev.audit_id);
            const started = startedMetaByAudit.get(ev.audit_id);
            const items = ev.items ?? started?.items;
            const backup = ev.backup ?? started?.backup;
            const totalFiles = items?.reduce((acc, item) => acc + item.files, 0) ?? 0;
            const totalBytes = items?.reduce((acc, item) => acc + item.bytes, 0) ?? 0;

            return (
              <li
                key={ev.id}
                className="data-deletion-list-item"
                data-testid={`deletion-audit-event-${ev.audit_id}-${ev.action}`}
              >
                <div className="row spread" style={{ gap: 'var(--Space3)', flexWrap: 'wrap' }}>
                  <div className="row" style={{ gap: 'var(--Space2)', flexWrap: 'wrap' }}>
                    <span className={ACTION_TAG_CLASS[ev.action] || 'tag'}>
                      {ACTION_LABELS[ev.action] || ev.action}
                    </span>
                    <strong>
                      {SCOPE_LABELS[ev.scope] || ev.scope} · {ev.target_id.slice(0, 8)}
                    </strong>
                  </div>
                  <div className="row" style={{ gap: 'var(--Space2)', flexWrap: 'wrap' }}>
                    <small className="muted">{formatAuditDate(ev.at)}</small>
                    {isRestorable && (
                      <button
                        type="button"
                        className="button"
                        disabled={Boolean(restoringAuditId)}
                        data-testid={`restore-deletion-${ev.audit_id}`}
                        onClick={() => void handleRestore(ev.audit_id)}
                      >
                        {restoringAuditId === ev.audit_id ? 'Restaurando…' : 'Restaurar'}
                      </button>
                    )}
                  </div>
                </div>
                <small className="muted">
                  ID completo: <code>{ev.target_id}</code> · Auditoria: <code>{ev.audit_id.slice(0, 8)}</code>
                  {items && items.length > 0
                    ? ` · ${totalFiles} arquivo(s) (${formatDeletionBytes(totalBytes)})`
                    : ''}
                </small>
                {backup?.name && (
                  <small className="muted">
                    Backup pré-exclusão: <code>{backup.name}</code> ({formatDeletionBytes(backup.bytes)})
                  </small>
                )}
                {ev.error && (
                  <small className="error">Motivo da falha: {ev.error}</small>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {nextBefore !== null && (
        <div className="actions" style={{ marginTop: 'var(--Space3)' }}>
          <button
            type="button"
            className="button"
            disabled={loadingMore}
            onClick={() => void handleLoadMore()}
          >
            {loadingMore ? 'Carregando…' : 'Carregar eventos anteriores'}
          </button>
        </div>
      )}
    </StatusCard>
  );
}
