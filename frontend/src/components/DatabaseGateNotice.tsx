import { describeDatabaseStatus } from '../services/database';
import type { DatabaseStatus } from '../services/database';

export interface DatabaseGateNoticeProps {
  status: DatabaseStatus;
  variant?: 'banner' | 'card';
  testing?: boolean;
  onRetry?: () => void;
  onOpenSettings?: () => void;
  contextLabel?: string;
}

export function DatabaseGateNotice({
  status,
  variant = 'card',
  testing = false,
  onRetry,
  onOpenSettings,
  contextLabel,
}: DatabaseGateNoticeProps) {
  if (status.state === 'connected') return null;

  const guidance = describeDatabaseStatus(status);
  const tagClass =
    guidance.tone === 'bad'
      ? 'tag bad'
      : guidance.tone === 'warn'
        ? 'tag warn'
        : 'tag';

  if (variant === 'banner') {
    const noticeTone = guidance.tone === 'bad' ? 'failed' : 'interrupted';
    return (
      <div
        className={`production-notice ${noticeTone}`}
        role={guidance.tone === 'bad' ? 'alert' : 'status'}
        aria-live="polite"
        aria-label="Estado do banco de dados"
        data-testid="database-global-banner"
      >
        <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
          <span className={tagClass}>{guidance.eyebrow}</span>
          <span>
            <strong>{guidance.title}</strong> {guidance.summary}
          </span>
        </div>
        <div className="actions">
          {onRetry && status.state !== 'not_configured' && (
            <button
              type="button"
              className="button"
              disabled={testing}
              aria-busy={testing}
              data-testid="database-banner-retry"
              onClick={onRetry}
            >
              {testing ? 'VERIFICANDO…' : 'Tentar novamente'}
            </button>
          )}
          {onOpenSettings && (
            <button
              type="button"
              className="button primary"
              data-testid="database-banner-open-settings"
              onClick={onOpenSettings}
            >
              {status.state === 'not_configured' ? 'Configurar banco de dados' : 'Abrir Banco de dados'}
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <section
      className="card sunken"
      role={guidance.tone === 'bad' ? 'alert' : 'region'}
      aria-label={contextLabel ? `Banco de dados indisponível em ${contextLabel}` : 'Banco de dados indisponível'}
      data-testid="database-gate-card"
    >
      <div className="row spread">
        <div>
          <p className="eyebrow warn">{guidance.eyebrow}</p>
          <h2>{guidance.title}</h2>
        </div>
        <span className={tagClass}>
          {status.last_error_code ?? status.state.toUpperCase()}
        </span>
      </div>
      <p className="muted">
        {contextLabel
          ? `${contextLabel}: ${guidance.summary}`
          : guidance.summary}
      </p>
      {guidance.hints.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 'var(--Space7)', display: 'grid', gap: 'var(--Space2)' }}>
          {guidance.hints.map(hint => (
            <li key={hint} className="muted">
              {hint}
            </li>
          ))}
        </ul>
      )}
      <div className="actions">
        {onRetry && status.state !== 'not_configured' && (
          <button
            type="button"
            className="button primary"
            disabled={testing}
            aria-busy={testing}
            data-testid="database-gate-retry"
            onClick={onRetry}
          >
            {testing ? 'VERIFICANDO…' : 'Tentar novamente'}
          </button>
        )}
        {onOpenSettings && (
          <button
            type="button"
            className={`button ${status.state === 'not_configured' ? 'primary' : ''}`}
            data-testid="database-gate-open-settings"
            onClick={onOpenSettings}
          >
            {status.state === 'not_configured' ? 'Configurar banco de dados' : 'Configurar em Ajustes'}
          </button>
        )}
      </div>
    </section>
  );
}
