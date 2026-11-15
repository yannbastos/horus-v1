import { useEffect, useId, useRef, useState } from 'react';
import { StatusMessage } from './StatusMessage';

export interface HumanCheckpointDialogProps {
  open: boolean;
  busy?: boolean;
  error?: string;
  what: string;
  estimatedCostUsd: string;
  limitUsd: string;
  validityLabel: string;
  confirmLabel?: string;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
}

/**
 * Diálogo acessível de confirmação explícita para checkpoints humanos (HOR-107).
 * Exibe escopo (o quê), custo estimado, limite em USD, validade e regra de uso único.
 */
export function HumanCheckpointDialog({
  open,
  busy = false,
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
  const checkboxRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) {
      setConfirmed(false);
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      checkboxRef.current?.focus();
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) {
        event.preventDefault();
        onCancel();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('keydown', onKeyDown);
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
        className="card modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
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

        {error && (
          <StatusMessage state="error" testId="checkpoint-dialog-error">
            {error}
          </StatusMessage>
        )}

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

        <div className="actions">
          <button type="button" className="button" disabled={busy} onClick={onCancel}>
            Cancelar
          </button>
          <button
            type="button"
            className="button primary"
            disabled={!confirmed || busy}
            onClick={() => void onConfirm()}
          >
            {busy ? 'Autorizando…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
