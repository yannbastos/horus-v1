import { useCallback, useEffect, useState } from 'react';
import type { Connection } from '../services/api';
import { ApiError } from '../services/api';
import type { CheckpointStatus, HumanCheckpoint } from '../services/checkpoints';
import {
  formatCheckpointAction,
  formatSecurityGateError,
  formatUsdLimit,
  getCheckpoints,
  getCheckpointStatus,
} from '../services/checkpoints';
import { StatusMessage } from '../components/StatusMessage';

type StatusFilter = 'all' | CheckpointStatus;

const STATUS_META: Record<CheckpointStatus, { label: string; tagClass: string }> = {
  active: { label: 'Ativo', tagClass: 'tag good' },
  used: { label: 'Usado', tagClass: 'tag' },
  expired: { label: 'Expirado', tagClass: 'tag bad' },
};

const FILTER_OPTIONS: { id: StatusFilter; label: string }[] = [
  { id: 'all', label: 'Todos' },
  { id: 'active', label: 'Ativos' },
  { id: 'used', label: 'Usados' },
  { id: 'expired', label: 'Expirados' },
];

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString('pt-BR');
}

function describeCheckpointLoadError(reason: unknown): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) {
      return 'Sessão não autorizada para consultar os checkpoints. Reconecte o backend em Ajustes → Geral.';
    }
    if (reason.status === 404) {
      return 'Rota de checkpoints não encontrada no backend atual. Atualize o backend.';
    }
    if (reason.status === 409) {
      return formatSecurityGateError(reason);
    }
    return reason.message;
  }
  return reason instanceof Error
    ? `Não foi possível carregar os checkpoints: ${reason.message}`
    : 'Não foi possível carregar os checkpoints.';
}

export default function CheckpointsHistory({
  connection,
  refreshKey = 0,
}: {
  connection: Connection;
  refreshKey?: number;
}) {
  const [items, setItems] = useState<HumanCheckpoint[]>([]);
  const [nextAfter, setNextAfter] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<StatusFilter>('all');

  const loadInitial = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError('');
      try {
        const page = await getCheckpoints(connection, { limit: 20 }, signal);
        if (signal?.aborted) return;
        setItems(Array.isArray(page.items) ? page.items : []);
        setNextAfter(page.next_after ?? null);
      } catch (reason) {
        if (signal?.aborted) return;
        setItems([]);
        setNextAfter(null);
        setError(describeCheckpointLoadError(reason));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [connection]
  );

  useEffect(() => {
    const controller = new AbortController();
    void loadInitial(controller.signal);
    return () => controller.abort();
  }, [loadInitial, refreshKey]);

  async function handleLoadMore() {
    if (nextAfter === null || loadingMore) return;
    setLoadingMore(true);
    setError('');
    try {
      const page = await getCheckpoints(connection, { limit: 20, after: nextAfter });
      const incoming = Array.isArray(page.items) ? page.items : [];
      setItems(prev => [...prev, ...incoming]);
      setNextAfter(page.next_after ?? null);
    } catch (reason) {
      setError(describeCheckpointLoadError(reason));
    } finally {
      setLoadingMore(false);
    }
  }

  const ordered = [...items].sort((a, b) => b.seq - a.seq);
  const visible = ordered.filter(item => {
    if (filter === 'all') return true;
    return getCheckpointStatus(item) === filter;
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div className="row spread" style={{ flexWrap: 'wrap', gap: '8px' }}>
        <div className="row" role="group" aria-label="Filtrar checkpoints por estado" style={{ flexWrap: 'wrap', gap: '6px' }}>
          {FILTER_OPTIONS.map(option => (
            <button
              key={option.id}
              type="button"
              className={`button ${filter === option.id ? 'primary' : ''}`}
              aria-pressed={filter === option.id}
              onClick={() => setFilter(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="button"
          disabled={loading}
          onClick={() => void loadInitial()}
        >
          {loading ? 'Atualizando…' : 'Atualizar checkpoints'}
        </button>
      </div>

      {loading && items.length === 0 && (
        <StatusMessage state="loading" testId="checkpoints-loading">
          Carregando checkpoints humanos…
        </StatusMessage>
      )}

      {error && (
        <StatusMessage state="error" testId="checkpoints-error">
          {error}
        </StatusMessage>
      )}

      {!loading && !error && visible.length === 0 && (
        <StatusMessage state="empty" role="status" testId="checkpoints-empty">
          {items.length === 0
            ? 'Nenhum checkpoint humano registrado ainda.'
            : 'Nenhum checkpoint corresponde ao filtro selecionado.'}
        </StatusMessage>
      )}

      {visible.length > 0 && (
        <div className="data-table-container">
          <table className="data-table" aria-label="Checkpoints humanos recentes">
            <thead>
              <tr>
                <th>Ação e descrição</th>
                <th>Estado</th>
                <th>Limite (USD)</th>
                <th>Aprovação</th>
                <th>Validade / consumo</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(cp => {
                const status = getCheckpointStatus(cp);
                const meta = STATUS_META[status];
                return (
                  <tr key={cp.id} data-checkpoint-status={status}>
                    <td>
                      <strong>{formatCheckpointAction(cp.action)}</strong>
                      <div className="muted small">{cp.description}</div>
                    </td>
                    <td>
                      <span className={meta.tagClass}>{meta.label}</span>
                    </td>
                    <td>{formatUsdLimit(cp.limit_amount, cp.currency)}</td>
                    <td>
                      <div>{cp.approved_by === 'human' ? 'Humano' : cp.approved_by} ({cp.operator_id})</div>
                      <div className="muted small">{formatDateTime(cp.approved_at)}</div>
                    </td>
                    <td>
                      <div>Expira: {formatDateTime(cp.expires_at)}</div>
                      {cp.consumed_at && (
                        <div className="muted small">
                          Usado: {formatDateTime(cp.consumed_at)}
                          {cp.consumed_ref ? ` · ref ${cp.consumed_ref}` : ''}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {nextAfter !== null && (
        <div className="actions">
          <button
            type="button"
            className="button"
            disabled={loadingMore}
            onClick={() => void handleLoadMore()}
          >
            {loadingMore ? 'Carregando…' : 'Carregar mais checkpoints'}
          </button>
        </div>
      )}
    </div>
  );
}
