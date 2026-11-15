import { useCallback, useEffect, useState } from 'react';
import { ApiError, api } from './api';
import type { Connection } from './api';

export type NotificationKind =
  | 'production.succeeded'
  | 'production.failed'
  | 'production.interrupted'
  | 'script.awaiting_review'
  | 'workflow.awaiting_approval'
  | 'workflow.awaiting_authorization';

export type NotificationSeverity = 'success' | 'error' | 'warning' | 'info';

export type NotificationRefKind = 'production' | 'script' | 'workflow';

export type NotificationItem = {
  id: string;
  kind: NotificationKind | string;
  severity: NotificationSeverity;
  title: string;
  message: string;
  at: string;
  ref: {
    kind: NotificationRefKind | string;
    id: string;
  };
  pending: boolean;
  read: boolean;
};

export type NotificationsListResponse = {
  items: NotificationItem[];
  unread_count: number;
  next_after: string | null;
};

export type UnreadCountResponse = {
  unread_count: number;
};

export type ReadStatePayload =
  | { ids: string[]; read: boolean }
  | { all: true; read: boolean };

export type ListNotificationsParams = {
  limit?: number;
  after?: string | null;
  unread?: boolean;
  kind?: string;
  signal?: AbortSignal;
};

const DEFAULT_POLL_INTERVAL_MS = 10000;
const MAX_BACKOFF_MS = 60000;

export function isNotificationsNotFoundError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

export function getNotificationsPollIntervalMs(): number {
  if (typeof window !== 'undefined') {
    const custom = (window as { __HORUS_NOTIFICATIONS_POLL_MS__?: unknown })
      .__HORUS_NOTIFICATIONS_POLL_MS__;
    if (typeof custom === 'number' && Number.isFinite(custom) && custom >= 50) {
      return custom;
    }
  }
  return DEFAULT_POLL_INTERVAL_MS;
}

export async function listNotifications(
  connection: Connection,
  params: ListNotificationsParams = {},
): Promise<NotificationsListResponse> {
  const query = new URLSearchParams();
  query.set('limit', String(params.limit ?? 50));
  if (params.after) query.set('after', params.after);
  if (typeof params.unread === 'boolean') query.set('unread', String(params.unread));
  if (params.kind) query.set('kind', params.kind);
  return api<NotificationsListResponse>(
    connection,
    `/v1/notifications?${query.toString()}`,
    { signal: params.signal },
  );
}

export async function getUnreadNotificationCount(
  connection: Connection,
  signal?: AbortSignal,
): Promise<UnreadCountResponse> {
  return api<UnreadCountResponse>(connection, '/v1/notifications/unread-count', { signal });
}

export async function updateNotificationReadState(
  connection: Connection,
  payload: ReadStatePayload,
  signal?: AbortSignal,
): Promise<UnreadCountResponse> {
  return api<UnreadCountResponse>(connection, '/v1/notifications/read-state', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  });
}

export type NotificationsBellState = {
  /** null enquanto não verificado; true se disponível; false se endpoint retornou 404. */
  available: boolean | null;
  unreadCount: number;
  setUnreadCount: (count: number) => void;
  refreshCount: () => Promise<void>;
};

/**
 * Polling leve de `/v1/notifications/unread-count` com backoff exponencial em erros transitórios
 * e degradação silenciosa quando o endpoint retorna 404.
 */
export function useNotificationsBell(connection: Connection | null): NotificationsBellState {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [unreadCount, setUnreadCount] = useState(0);

  const refreshCount = useCallback(async () => {
    if (!connection) return;
    try {
      const response = await getUnreadNotificationCount(connection);
      setAvailable(true);
      setUnreadCount(typeof response.unread_count === 'number' ? response.unread_count : 0);
    } catch (reason) {
      if (isNotificationsNotFoundError(reason)) {
        setAvailable(false);
        setUnreadCount(0);
      }
    }
  }, [connection]);

  useEffect(() => {
    setAvailable(null);
    setUnreadCount(0);
    if (!connection) return;

    let cancelled = false;
    let controller = new AbortController();
    let timer: number | undefined;
    let consecutiveErrors = 0;
    let endpointDisabled = false;

    const clearTimer = () => {
      if (timer !== undefined) {
        window.clearTimeout(timer);
        timer = undefined;
      }
    };

    const scheduleNext = () => {
      clearTimer();
      if (cancelled || endpointDisabled) return;
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        return;
      }
      const base = getNotificationsPollIntervalMs();
      const delay = Math.min(base * Math.pow(2, consecutiveErrors), MAX_BACKOFF_MS);
      timer = window.setTimeout(() => {
        void poll();
      }, delay);
    };

    const poll = async () => {
      if (cancelled || endpointDisabled) return;
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        return;
      }
      controller.abort();
      controller = new AbortController();
      try {
        const response = await getUnreadNotificationCount(connection, controller.signal);
        if (cancelled) return;
        consecutiveErrors = 0;
        setAvailable(true);
        setUnreadCount(typeof response.unread_count === 'number' ? response.unread_count : 0);
        scheduleNext();
      } catch (reason) {
        if (cancelled || controller.signal.aborted) return;
        if (isNotificationsNotFoundError(reason)) {
          endpointDisabled = true;
          setAvailable(false);
          setUnreadCount(0);
          clearTimer();
          return;
        }
        consecutiveErrors += 1;
        scheduleNext();
      }
    };

    const handleVisibilityChange = () => {
      if (cancelled || endpointDisabled) return;
      if (document.visibilityState === 'visible') {
        clearTimer();
        void poll();
      } else {
        clearTimer();
      }
    };

    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', handleVisibilityChange);
    }

    void poll();

    return () => {
      cancelled = true;
      clearTimer();
      controller.abort();
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', handleVisibilityChange);
      }
    };
  }, [connection]);

  return {
    available,
    unreadCount,
    setUnreadCount,
    refreshCount,
  };
}
