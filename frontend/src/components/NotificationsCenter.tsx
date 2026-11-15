import { useCallback, useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { Connection } from '../services/api';
import {
  isNotificationsNotFoundError,
  listNotifications,
  updateNotificationReadState,
} from '../services/notifications';
import type { NotificationItem, NotificationSeverity } from '../services/notifications';
import { StatusMessage } from './StatusMessage';

export const NOTIFICATION_KIND_LABELS: Record<string, string> = {
  'production.succeeded': 'Produção concluída',
  'production.failed': 'Falha na produção',
  'production.interrupted': 'Produção interrompida',
  'script.awaiting_review': 'Roteiro aguardando revisão',
  'workflow.awaiting_approval': 'Workflow aguardando aprovação',
  'workflow.awaiting_authorization': 'Workflow aguardando autorização',
};

const NOTIFICATION_KIND_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Todos os tipos' },
  { value: 'production.succeeded', label: NOTIFICATION_KIND_LABELS['production.succeeded'] },
  { value: 'production.failed', label: NOTIFICATION_KIND_LABELS['production.failed'] },
  { value: 'production.interrupted', label: NOTIFICATION_KIND_LABELS['production.interrupted'] },
  { value: 'script.awaiting_review', label: NOTIFICATION_KIND_LABELS['script.awaiting_review'] },
  { value: 'workflow.awaiting_approval', label: NOTIFICATION_KIND_LABELS['workflow.awaiting_approval'] },
  { value: 'workflow.awaiting_authorization', label: NOTIFICATION_KIND_LABELS['workflow.awaiting_authorization'] },
];

function severityTagClass(severity: NotificationSeverity): string {
  if (severity === 'success') return 'tag good';
  if (severity === 'error') return 'tag bad';
  if (severity === 'warning') return 'tag warn';
  return 'tag';
}

function formatNotificationTime(iso: string): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function getNavigateActionLabel(refKind: string): string {
  if (refKind === 'production') return 'Abrir produção';
  if (refKind === 'script') return 'Abrir roteiro';
  if (refKind === 'workflow') return 'Abrir workflow';
  return 'Abrir item';
}

export function NotificationBellButton({
  unreadCount,
  open,
  onToggle,
  buttonRef,
}: {
  unreadCount: number;
  open: boolean;
  onToggle: () => void;
  buttonRef?: RefObject<HTMLButtonElement | null>;
}) {
  return (
    <button
      ref={buttonRef}
      type="button"
      className={`notifications-bell-button${open ? ' active' : ''}`}
      aria-expanded={open}
      aria-controls="notifications-panel"
      data-testid="notifications-bell"
      onClick={onToggle}
    >
      <span className="notifications-bell-label">
        <svg
          width="15"
          height="15"
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden="true"
        >
          <path
            d="M8 2a4 4 0 0 0-4 4v2.6L2.7 10.7a.5.5 0 0 0 .43.75h9.74a.5.5 0 0 0 .43-.75L12 8.6V6a4 4 0 0 0-4-4Z"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinejoin="round"
          />
          <path
            d="M6.5 12.2a1.6 1.6 0 0 0 3 0"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinecap="round"
          />
        </svg>
        <span>Notificações</span>
      </span>
      {unreadCount > 0 && (
        <>
          <span className="notifications-badge" data-testid="notifications-unread-badge">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
          <span className="sr-only">
            {` não ${unreadCount === 1 ? 'lida' : 'lidas'}`}
          </span>
        </>
      )}
    </button>
  );
}

export type NotificationsPanelViewProps = {
  items: NotificationItem[];
  unreadCount: number;
  loading: boolean;
  loadingMore: boolean;
  error: string;
  busy: boolean;
  unreadOnly: boolean;
  kindFilter: string;
  nextAfter: string | null;
  liveAnnouncement: string;
  onToggleUnreadOnly: () => void;
  onChangeKindFilter: (kind: string) => void;
  onMarkAllRead: () => void;
  onToggleItemRead: (item: NotificationItem) => void;
  onNavigateItem: (item: NotificationItem) => void;
  onLoadMore: () => void;
  onRetry: () => void;
  onClose: () => void;
  closeButtonRef?: RefObject<HTMLButtonElement | null>;
};

export function NotificationsPanelView({
  items,
  unreadCount,
  loading,
  loadingMore,
  error,
  busy,
  unreadOnly,
  kindFilter,
  nextAfter,
  liveAnnouncement,
  onToggleUnreadOnly,
  onChangeKindFilter,
  onMarkAllRead,
  onToggleItemRead,
  onNavigateItem,
  onLoadMore,
  onRetry,
  onClose,
  closeButtonRef,
}: NotificationsPanelViewProps) {
  const hasFilters = unreadOnly || kindFilter !== '';

  return (
    <div
      className="notifications-backdrop"
      data-testid="notifications-backdrop"
      onClick={event => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        id="notifications-panel"
        className="notifications-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="notifications-panel-title"
        data-testid="notifications-panel"
        onKeyDown={event => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            onClose();
          }
        }}
      >
        <header className="notifications-panel-header">
          <div className="notifications-panel-heading">
            <div>
              <p className="eyebrow accent">CENTRAL DE NOTIFICAÇÕES</p>
              <div className="row" style={{ gap: 8, marginTop: 4 }}>
                <h2 id="notifications-panel-title">Notificações</h2>
                {unreadCount > 0 && (
                  <span className="tag warn" data-testid="notifications-panel-unread-tag">
                    {unreadCount} não {unreadCount === 1 ? 'lida' : 'lidas'}
                  </span>
                )}
              </div>
            </div>
            <div className="actions">
              <button
                type="button"
                className="button"
                disabled={unreadCount === 0 || busy}
                onClick={onMarkAllRead}
              >
                Marcar todas como lidas
              </button>
              <button
                ref={closeButtonRef}
                type="button"
                className="button"
                aria-label="Fechar painel de notificações"
                onClick={onClose}
              >
                Fechar
              </button>
            </div>
          </div>

          <div className="notifications-filters" role="group" aria-label="Filtros de notificações">
            <button
              type="button"
              className={`button${unreadOnly ? ' active' : ''}`}
              aria-pressed={unreadOnly}
              onClick={onToggleUnreadOnly}
            >
              Somente não lidas
            </button>
            <label className="notifications-kind-filter">
              <span className="sr-only">Filtrar por tipo</span>
              <select
                aria-label="Filtrar por tipo"
                value={kindFilter}
                onChange={event => onChangeKindFilter(event.target.value)}
              >
                {NOTIFICATION_KIND_OPTIONS.map(option => (
                  <option key={option.value || 'all'} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </header>

        <div
          className="sr-only"
          aria-live="polite"
          aria-atomic="true"
          data-testid="notifications-live-region"
        >
          {liveAnnouncement}
        </div>

        <div className="notifications-panel-body">
          {loading && items.length === 0 && (
            <StatusMessage state="loading" testId="notifications-loading">
              Carregando notificações…
            </StatusMessage>
          )}

          {error && (
            <div className="notifications-error-box">
              <StatusMessage state="error" testId="notifications-error">
                {error}
              </StatusMessage>
              <button type="button" className="button" onClick={onRetry}>
                Tentar novamente
              </button>
            </div>
          )}

          {!loading && !error && items.length === 0 && (
            <StatusMessage state="empty" testId="notifications-empty">
              {hasFilters
                ? 'Nenhuma notificação corresponde aos filtros.'
                : 'Nenhuma notificação no momento.'}
            </StatusMessage>
          )}

          {items.length > 0 && (
            <ul className="notifications-list" aria-label="Lista de notificações">
              {items.map(item => {
                const kindLabel = NOTIFICATION_KIND_LABELS[item.kind] ?? item.kind;
                return (
                  <li
                    key={item.id}
                    className={`notification-item${!item.read ? ' unread' : ''}${item.pending ? ' pending' : ''}`}
                    data-testid={`notification-item-${item.id}`}
                    data-pending={item.pending ? 'true' : 'false'}
                    data-read={item.read ? 'true' : 'false'}
                  >
                    <div className="notification-item-meta">
                      <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                        <span className={severityTagClass(item.severity)}>{kindLabel}</span>
                        {item.pending && <span className="tag warn">PENDENTE</span>}
                        {!item.read && <span className="tag">NÃO LIDA</span>}
                      </div>
                      <time className="muted notification-time" dateTime={item.at}>
                        {formatNotificationTime(item.at)}
                      </time>
                    </div>

                    <div className="notification-item-content">
                      <h3>{item.title}</h3>
                      <p className="muted">{item.message}</p>
                    </div>

                    <div className="actions">
                      <button
                        type="button"
                        className="button primary"
                        onClick={() => onNavigateItem(item)}
                      >
                        {getNavigateActionLabel(item.ref.kind)}
                      </button>
                      <button
                        type="button"
                        className="button"
                        disabled={busy}
                        onClick={() => onToggleItemRead(item)}
                      >
                        {item.read ? 'Marcar como não lida' : 'Marcar como lida'}
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          {nextAfter && !error && (
            <div className="notifications-load-more">
              <button
                type="button"
                className="button"
                disabled={loadingMore}
                onClick={onLoadMore}
              >
                {loadingMore ? 'Carregando…' : 'Carregar mais'}
              </button>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

export function NotificationsPanel({
  connection,
  open,
  unreadCount,
  onUnreadCountChange,
  onUnavailable,
  onNavigateItem,
  onClose,
  triggerRef,
}: {
  connection: Connection | null;
  open: boolean;
  unreadCount: number;
  onUnreadCountChange: (count: number) => void;
  onUnavailable: () => void;
  onNavigateItem: (item: NotificationItem) => void;
  onClose: () => void;
  triggerRef?: RefObject<HTMLButtonElement | null>;
}) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [kindFilter, setKindFilter] = useState('');
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [liveAnnouncement, setLiveAnnouncement] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const wasOpenRef = useRef(false);

  const handleClose = useCallback(() => {
    onClose();
    requestAnimationFrame(() => {
      triggerRef?.current?.focus();
    });
  }, [onClose, triggerRef]);

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      requestAnimationFrame(() => {
        closeButtonRef.current?.focus();
      });
    }
    wasOpenRef.current = open;
  }, [open]);

  useEffect(() => {
    if (!open || !connection) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void listNotifications(connection, {
      limit: 50,
      unread: unreadOnly ? true : undefined,
      kind: kindFilter || undefined,
      signal: controller.signal,
    })
      .then(response => {
        if (controller.signal.aborted) return;
        setItems(response.items);
        setNextAfter(response.next_after ?? null);
        if (typeof response.unread_count === 'number') {
          onUnreadCountChange(response.unread_count);
        }
      })
      .catch(reason => {
        if (controller.signal.aborted) return;
        if (isNotificationsNotFoundError(reason)) {
          onUnavailable();
          onClose();
          return;
        }
        setError(reason instanceof Error ? reason.message : 'Não foi possível carregar as notificações.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [open, connection, unreadOnly, kindFilter, reloadKey, onUnreadCountChange, onUnavailable, onClose]);

  async function handleLoadMore() {
    if (!connection || !nextAfter || loadingMore) return;
    setLoadingMore(true);
    setError('');
    try {
      const response = await listNotifications(connection, {
        limit: 50,
        after: nextAfter,
        unread: unreadOnly ? true : undefined,
        kind: kindFilter || undefined,
      });
      setItems(prev => [...prev, ...response.items]);
      setNextAfter(response.next_after ?? null);
      if (typeof response.unread_count === 'number') {
        onUnreadCountChange(response.unread_count);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Não foi possível carregar mais notificações.');
    } finally {
      setLoadingMore(false);
    }
  }

  async function handleMarkAllRead() {
    if (!connection || busy) return;
    setBusy(true);
    setError('');
    try {
      const response = await updateNotificationReadState(connection, { all: true, read: true });
      setItems(prev => (unreadOnly ? [] : prev.map(item => ({ ...item, read: true }))));
      onUnreadCountChange(typeof response.unread_count === 'number' ? response.unread_count : 0);
      setLiveAnnouncement('Todas as notificações marcadas como lidas.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Não foi possível atualizar as notificações.');
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleItemRead(item: NotificationItem) {
    if (!connection || busy) return;
    const nextRead = !item.read;
    setBusy(true);
    setError('');
    try {
      const response = await updateNotificationReadState(connection, {
        ids: [item.id],
        read: nextRead,
      });
      setItems(prev => {
        if (unreadOnly && nextRead) {
          return prev.filter(entry => entry.id !== item.id);
        }
        return prev.map(entry => (entry.id === item.id ? { ...entry, read: nextRead } : entry));
      });
      if (typeof response.unread_count === 'number') {
        onUnreadCountChange(response.unread_count);
      }
      setLiveAnnouncement(
        nextRead ? 'Notificação marcada como lida.' : 'Notificação marcada como não lida.',
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Não foi possível atualizar a notificação.');
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;

  return (
    <NotificationsPanelView
      items={items}
      unreadCount={unreadCount}
      loading={loading}
      loadingMore={loadingMore}
      error={error}
      busy={busy}
      unreadOnly={unreadOnly}
      kindFilter={kindFilter}
      nextAfter={nextAfter}
      liveAnnouncement={liveAnnouncement}
      onToggleUnreadOnly={() => setUnreadOnly(prev => !prev)}
      onChangeKindFilter={setKindFilter}
      onMarkAllRead={() => {
        void handleMarkAllRead();
      }}
      onToggleItemRead={item => {
        void handleToggleItemRead(item);
      }}
      onNavigateItem={onNavigateItem}
      onLoadMore={() => {
        void handleLoadMore();
      }}
      onRetry={() => setReloadKey(prev => prev + 1)}
      onClose={handleClose}
      closeButtonRef={closeButtonRef}
    />
  );
}
