import { useEffect, useId, useRef, useState } from 'react';
import { StatusMessage } from './StatusMessage';

export interface HumanCheckpointDialogProps {
  open: boolean;
  busy?: boolean;
  loadingLimits?: boolean;
  limitStatus?: 'configured' | 'unconfigured' | 'unknown';
  error?: string;
  what: string;
  estimatedCostUsd: string;
  limitUsd: string;
  validityLabel: string;
  confirmLabel?: string;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
}

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  const selector = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(', ');
  return Array.from(container.querySelectorAll<HTMLElement>(selector)).filter(
    el => !el.hasAttribute('hidden') && el.offsetParent !== null,
  );
}

/**
 * Diálogo acessível de confirmação explícita para checkpoints humanos (HOR-107).
 * Exibe escopo (o quê), custo estimado, limite em USD, validade e regra de uso único.
 */
export function HumanCheckpointDialog({
  open,
  busy = false,
  loadingLimits = false,
  limitStatus = 'configured',
  error,
  what,
  estimatedCostUsd,
  limitUsd,
  validityLabel,
  confirmLabel = 'Confirmar checkpoint e autorizar',
  onCancel,
  onConfirm,
}: HumanCheckpointDialogProps) {
  const [confirmed, setConfirmed] = useState(false);
  const titleId = useId();
  const descId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const checkboxRef = useRef<HTMLInputElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const canConfirm = limitStatus === 'configured' && !loadingLimits;

  useEffect(() => {
    if (!open) {
      setConfirmed(false);
      return;
    }
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => {
      if (checkboxRef.current) {
        checkboxRef.current.focus();
      } else if (dialogRef.current) {
        const focusable = getFocusableElements(dialogRef.current);
        (focusable[0] ?? dialogRef.current).focus();
      }
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) {
        event.preventDefault();
        event.stopPropagation();
        onCancel();
        return;
      }
      if (event.key === 'Tab' && dialogRef.current) {
        const focusable = getFocusableElements(dialogRef.current);
        if (focusable.length === 0) {
          event.preventDefault();
          dialogRef.current.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;
        if (event.shiftKey) {
          if (active === first || !dialogRef.current.contains(active)) {
            event.preventDefault();
            last.focus();
          }
        } else if (active === last || !dialogRef.current.contains(active)) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('keydown', onKeyDown);
      const restoreTarget = previousFocusRef.current;
      if (restoreTarget && document.contains(restoreTarget)) {
        restoreTarget.focus();
      }
    };
  }, [open, busy, onCancel]);

  if (!open) return null;

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={event => {
        if (event.target === event.currentTarget && !busy) {
          onCancel();
        }
      }}
    >
      <div
        ref={dialogRef}
        className="card modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        tabIndex={-1}
        data-testid="human-checkpoint-dialog"
      >
        <div className="row spread">
          <div>
            <p className="eyebrow warn">CHECKPOINT HUMANO OBRIGATÓRIO</p>
            <h2 id={titleId}>Confirmar aprovação para ação no Hermes</h2>
          </div>
          <span className="tag warn">USO ÚNICO</span>
        </div>

        <p id={descId} className="muted">
          Revise os limites abaixo antes de confirmar. Esta aprovação vale para uma única tentativa e será bloqueada se
          expirar ou ultrapassar o limite em USD.
        </p>

        <dl className="checkpoint-summary-list">
          <div className="fact-item">
            <dt className="eyebrow">O QUÊ</dt>
            <dd>{what}</dd>
          </div>
          <div className="fact-item">
            <dt className="eyebrow">CUSTO ESTIMADO E LIMITE EM USD</dt>
            <dd>
              Estimado: <strong>{estimatedCostUsd}</strong> · Limite máximo: <strong>{limitUsd}</strong>
            </dd>
          </div>
          <div className="fact-item">
            <dt className="eyebrow">VALIDADE</dt>
            <dd>{validityLabel}</dd>
          </div>
          <div className="fact-item">
            <dt className="eyebrow">USO ÚNICO</dt>
            <dd>Uso único: esta aprovação será consumida na execução e não poderá ser reutilizada.</dd>
          </div>
        </dl>

        {loadingLimits && !canConfirm && (
          <StatusMessage state="loading" testId="checkpoint-limits-loading">
            Consultando limites reais configurados no backend…
          </StatusMessage>
        )}

        {!loadingLimits && limitStatus === 'unconfigured' && (
          <StatusMessage state="error" testId="checkpoint-limit-blocked">
            Limite de reserva por execução não configurado no backend. A execução real permanece bloqueada pelo
            backend até que um limite em USD seja configurado.
          </StatusMessage>
        )}

        {!loadingLimits && limitStatus === 'unknown' && (
          <StatusMessage state="error" testId="checkpoint-limit-blocked">
            Limite de reserva por execução não informado pelo backend atual. A execução real permanece bloqueada sem
            confirmação dos limites do servidor.
          </StatusMessage>
        )}

        {error && (
          <StatusMessage state="error" testId="checkpoint-dialog-error">
            {error}
          </StatusMessage>
        )}

        {canConfirm && (
          <label className="checkpoint-confirm-check">
            <input
              ref={checkboxRef}
              type="checkbox"
              checked={confirmed}
              disabled={busy}
              onChange={event => setConfirmed(event.target.checked)}
            />
            <span>
              Confirmo que revisei o escopo, o custo estimado, o limite em USD, a validade e o uso único deste
              checkpoint.
            </span>
          </label>
        )}

        <div className="actions">
          <button type="button" className="button" disabled={busy} onClick={onCancel}>
            {canConfirm ? 'Cancelar' : 'Fechar'}
          </button>
          {canConfirm && (
            <button
              type="button"
              className="button primary"
              disabled={!confirmed || busy}
              onClick={() => void onConfirm()}
            >
              {busy ? 'Autorizando…' : confirmLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
