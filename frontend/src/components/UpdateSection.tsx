import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { RefObject } from 'react';
import {
  canRetryUpdateError,
  checkUpdates,
  createDisabledUpdateState,
  formatUpdateBytes,
  formatUpdateErrorMessage,
  getUpdateState,
  installUpdate,
  isUpdateBusyPhase,
  listenUpdateState,
  setUpdateAutoCheck,
} from '../services/updates';
import type { UpdateState } from '../services/updates';
import { StatusMessage } from './StatusMessage';

export type UpdateSectionProps = {
  /** Estado controlado opcional (quando gerenciado no nível do App). */
  state?: UpdateState;
  /** Callback para propagar atualizações de estado ao pai. */
  onStateChange?: (next: UpdateState) => void;
};

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

type UpdateConfirmDialogProps = {
  open: boolean;
  version: string;
  currentVersion: string;
  onCancel: () => void;
  onConfirm: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
};

function UpdateConfirmDialog({
  open,
  version,
  currentVersion,
  onCancel,
  onConfirm,
  triggerRef,
}: UpdateConfirmDialogProps) {
  const titleId = useId();
  const descId = useId();
  const dialogRef = useRef<HTMLElement | null>(null);
  const cancelButtonRef = useRef<HTMLButtonElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = requestAnimationFrame(() => {
      cancelButtonRef.current?.focus();
    });
    return () => {
      cancelButtonFrame(frame);
      const restoreTarget = triggerRef.current ?? previousFocusRef.current;
      if (restoreTarget && document.contains(restoreTarget)) {
        restoreTarget.focus();
      }
    };
  }, [open, triggerRef]);

  if (!open) return null;

  function handleKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onCancel();
      return;
    }

    if (event.key === 'Tab' && dialogRef.current) {
      const focusable = getFocusableElements(dialogRef.current);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  return (
    <div
      className="shortcuts-backdrop"
      role="presentation"
      onMouseDown={event => {
        if (event.target === event.currentTarget) {
          onCancel();
        }
      }}
    >
      <section
        ref={dialogRef}
        className="data-deletion-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        data-testid="update-confirm-dialog"
        onKeyDown={handleKeyDown}
      >
        <div className="data-deletion-modal-header">
          <div>
            <p className="eyebrow accent">CONFIRMAÇÃO DE ATUALIZAÇÃO</p>
            <h2 id={titleId}>Instalar versão {version}?</h2>
          </div>
          <button
            ref={cancelButtonRef}
            type="button"
            className="button"
            onClick={onCancel}
            aria-label="Fechar confirmação de atualização"
          >
            Fechar
          </button>
        </div>
        <div className="data-deletion-modal-body" id={descId}>
          <p>
            Você está prestes a atualizar o Hórus da versão <strong>{currentVersion}</strong> para a
            versão <strong>{version}</strong>. Antes de confirmar, veja o que será executado:
          </p>
          <ul className="data-deletion-list">
            <li className="data-deletion-list-item">
              <strong>1. Backup dos dados locais</strong>
              <span className="muted">
                Uma cópia de segurança dos dados locais será criada antes de qualquer alteração.
              </span>
            </li>
            <li className="data-deletion-list-item">
              <strong>2. Download do pacote</strong>
              <span className="muted">
                O instalador da versão {version} será baixado do canal configurado.
              </span>
            </li>
            <li className="data-deletion-list-item">
              <strong>3. Verificação da assinatura digital</strong>
              <span className="muted">
                A integridade e a autenticidade do pacote serão validadas antes da execução.
              </span>
            </li>
            <li className="data-deletion-list-item">
              <strong>4. Reinício do aplicativo</strong>
              <span className="muted">
                O aplicativo e o backend local serão encerrados e reiniciados para concluir a instalação.
              </span>
            </li>
          </ul>
        </div>
        <div className="data-deletion-modal-footer">
          <button type="button" className="button" onClick={onCancel}>
            Cancelar
          </button>
          <button
            type="button"
            className="button primary"
            data-testid="update-confirm-install-button"
            onClick={onConfirm}
          >
            Confirmar e instalar
          </button>
        </div>
      </section>
    </div>
  );
}

function cancelButtonFrame(frame: number) {
  cancelAnimationFrame(frame);
}

function formatDateTime(iso: string | null): string {
  if (!iso) return 'Nunca verificada';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('pt-BR');
}

function getBlockedMessage(reason: UpdateState['blocked_reason']): string {
  if (reason === 'backend_unavailable') {
    return 'A atualização não pode iniciar enquanto o backend local estiver indisponível.';
  }
  return 'A atualização aguarda o fim de renderizações, transcrições ou outras tarefas em andamento.';
}

/**
 * Seção "Atualizações" em Ajustes. Segue o contrato Tauri de atualização sem
 * iniciar downloads ou instalações sem confirmação explícita do usuário.
 */
export function UpdateSection({ state: controlledState, onStateChange }: UpdateSectionProps) {
  const [internalState, setInternalState] = useState<UpdateState>(() =>
    createDisabledUpdateState(),
  );
  const [pendingCommand, setPendingCommand] = useState(false);
  const [commandError, setCommandError] = useState('');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const installButtonRef = useRef<HTMLButtonElement | null>(null);
  const autoCheckLabelId = useId();
  const sectionTitleId = useId();

  const activeState = controlledState ?? internalState;

  const commitState = useCallback(
    (next: UpdateState) => {
      if (onStateChange) {
        onStateChange(next);
      }
      if (controlledState === undefined) {
        setInternalState(next);
      }
    },
    [controlledState, onStateChange],
  );

  useEffect(() => {
    if (controlledState !== undefined) return;
    let active = true;
    let unlisten: (() => void) | undefined;

    void getUpdateState()
      .then(initial => {
        if (active) setInternalState(initial);
      })
      .catch(() => {
        /* Mantém estado desabilitado se o canal não estiver disponível. */
      });

    void listenUpdateState(next => {
      if (active) setInternalState(next);
    }).then(fn => {
      if (active) {
        unlisten = fn;
      } else {
        fn();
      }
    });

    return () => {
      active = false;
      unlisten?.();
    };
  }, [controlledState]);

  const isDisabled = !activeState.configured || activeState.phase === 'disabled';
  const busy = pendingCommand || isUpdateBusyPhase(activeState.phase);

  async function handleToggleAutoCheck() {
    if (isDisabled || busy) return;
    setPendingCommand(true);
    setCommandError('');
    try {
      const next = await setUpdateAutoCheck(!activeState.auto_check);
      commitState(next);
    } catch (reason) {
      setCommandError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPendingCommand(false);
    }
  }

  async function handleCheckNow() {
    if (isDisabled || busy) return;
    setPendingCommand(true);
    setCommandError('');
    try {
      const next = await checkUpdates();
      commitState(next);
    } catch (reason) {
      setCommandError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPendingCommand(false);
    }
  }

  async function handleConfirmInstall() {
    const targetVersion = activeState.available?.version;
    if (!targetVersion || isDisabled || busy) return;
    setConfirmOpen(false);
    setPendingCommand(true);
    setCommandError('');
    try {
      const next = await installUpdate(targetVersion);
      commitState(next);
    } catch (reason) {
      setCommandError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPendingCommand(false);
    }
  }

  const downloadPercent =
    activeState.progress && activeState.progress.total && activeState.progress.total > 0
      ? Math.min(100, Math.round((activeState.progress.downloaded / activeState.progress.total) * 100))
      : null;

  return (
    <section
      id="settings-updates"
      className="card"
      aria-labelledby={sectionTitleId}
      data-testid="settings-updates-section"
    >
      <p className="eyebrow">ATUALIZAÇÕES</p>
      <h2 id={sectionTitleId}>
        {isDisabled ? 'Atualizações desativadas neste build.' : 'Atualizações do aplicativo.'}
      </h2>

      {isDisabled ? (
        <>
          <p className="muted" data-testid="update-disabled-message">
            Este build não possui um canal de atualização configurado. Nenhuma verificação ou
            download automático será realizado.
          </p>
          {activeState.current_version && activeState.current_version !== '0.0.0' && (
            <p className="muted" data-testid="update-current-version">
              Versão instalada: <strong className="mono">{activeState.current_version}</strong>
            </p>
          )}
        </>
      ) : (
        <>
          <div className="update-meta-grid">
            <div className="fact-item">
              <p className="eyebrow">VERSÃO INSTALADA</p>
              <p className="mono" data-testid="update-current-version">
                {activeState.current_version}
              </p>
            </div>
            <div className="fact-item">
              <p className="eyebrow">ÚLTIMA VERIFICAÇÃO</p>
              <p data-testid="update-last-check">{formatDateTime(activeState.last_check_at)}</p>
            </div>
          </div>

          <div className="update-preference-row">
            <div className="update-preference-info">
              <span id={autoCheckLabelId} className="update-preference-title">
                Verificar atualizações ao iniciar
              </span>
              <span className="muted">
                Consulta o canal ao abrir o aplicativo. Downloads e instalações sempre dependem de
                sua confirmação.
              </span>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={activeState.auto_check}
              aria-labelledby={autoCheckLabelId}
              className={`button update-switch ${activeState.auto_check ? 'active' : ''}`}
              disabled={busy}
              data-testid="update-auto-check-switch"
              onClick={() => void handleToggleAutoCheck()}
            >
              {activeState.auto_check ? 'ATIVADO' : 'DESATIVADO'}
            </button>
          </div>

          <div className="row" style={{ gap: 'var(--Space3)', flexWrap: 'wrap' }}>
            <button
              type="button"
              className="button"
              disabled={busy}
              data-testid="update-check-button"
              onClick={() => void handleCheckNow()}
            >
              {activeState.phase === 'checking' ? 'VERIFICANDO…' : 'VERIFICAR AGORA'}
            </button>
          </div>

          <div className="update-live-region" aria-live="polite" aria-atomic="true">
            {activeState.phase === 'checking' && (
              <StatusMessage state="loading" ariaLive="off" testId="update-status-checking">
                Verificando atualizações disponíveis…
              </StatusMessage>
            )}

            {activeState.phase === 'up_to_date' && (
              <StatusMessage state="success" ariaLive="off" testId="update-status-up-to-date">
                Você já está na versão mais recente.
              </StatusMessage>
            )}

            {activeState.phase === 'backing_up' && (
              <div className="card sunken" role="status" data-testid="update-status-backing-up">
                <p className="loading">
                  Criando backup dos dados locais antes da atualização…
                </p>
                {activeState.backup_path_label && (
                  <p className="muted">Backup: {activeState.backup_path_label}</p>
                )}
              </div>
            )}

            {activeState.phase === 'downloading' && (
              <div className="card sunken" role="status" data-testid="update-status-downloading">
                <p className="loading">
                  Baixando atualização
                  {activeState.available ? ` (${activeState.available.version})` : ''}…
                </p>
                <div className="progress-container">
                  <div
                    className={`progress ${downloadPercent === null ? 'indeterminate' : ''}`}
                    role="progressbar"
                    aria-label="Progresso do download da atualização"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={downloadPercent ?? undefined}
                  >
                    <span style={{ width: `${downloadPercent ?? 30}%` }} />
                  </div>
                  <span className="progress-value mono">
                    {downloadPercent !== null ? `${downloadPercent}%` : '…'}
                  </span>
                </div>
                {activeState.progress && (
                  <p className="muted">
                    {formatUpdateBytes(activeState.progress.downloaded)}
                    {activeState.progress.total !== null
                      ? ` de ${formatUpdateBytes(activeState.progress.total)}`
                      : ''}
                  </p>
                )}
              </div>
            )}

            {activeState.phase === 'installing' && (
              <div className="card sunken" role="status" data-testid="update-status-installing">
                <p className="loading">
                  Verificando assinatura e instalando atualização. O aplicativo será reiniciado…
                </p>
                {activeState.backup_path_label && (
                  <p className="muted">Backup preservado: {activeState.backup_path_label}</p>
                )}
              </div>
            )}
          </div>

          {(activeState.phase === 'available' || activeState.phase === 'blocked') && (
            <div className="card sunken" data-testid="update-available-card">
              {activeState.available ? (
                <>
                  <div className="row spread">
                    <div>
                      <p className="eyebrow accent">NOVA VERSÃO DISPONÍVEL</p>
                      <h3>Versão {activeState.available.version}</h3>
                    </div>
                    {activeState.available.pub_date && (
                      <span className="tag">
                        {formatDateTime(activeState.available.pub_date)}
                      </span>
                    )}
                  </div>
                  <p className="muted" style={{ whiteSpace: 'pre-line' }}>
                    {activeState.available.notes || 'Sem notas de versão informadas.'}
                  </p>
                </>
              ) : null}

              {activeState.phase === 'blocked' && (
                <div
                  className="data-deletion-notice"
                  role="status"
                  aria-live="polite"
                  data-testid="update-blocked-notice"
                >
                  <strong>Atualização aguardando conclusão de tarefa</strong>
                  <p className="muted">{getBlockedMessage(activeState.blocked_reason)}</p>
                </div>
              )}

              <div className="row" style={{ gap: 'var(--Space3)', flexWrap: 'wrap' }}>
                <button
                  ref={installButtonRef}
                  type="button"
                  className="button primary"
                  disabled={activeState.phase === 'blocked' || busy || !activeState.available}
                  data-testid="update-install-button"
                  onClick={() => setConfirmOpen(true)}
                >
                  BAIXAR E INSTALAR
                </button>
              </div>
            </div>
          )}

          {activeState.phase === 'error' && activeState.error && (
            <div className="card sunken" data-testid="update-error-card">
              <StatusMessage state="error" testId="update-error-message">
                {formatUpdateErrorMessage(activeState.error)}
              </StatusMessage>
              {activeState.error.message &&
                activeState.error.message !== formatUpdateErrorMessage(activeState.error) && (
                  <p className="muted" data-testid="update-error-detail">
                    Detalhe técnico ({activeState.error.code}): {activeState.error.message}
                  </p>
                )}
              {canRetryUpdateError(activeState.error.code) && (
                <div className="row">
                  <button
                    type="button"
                    className="button"
                    disabled={busy}
                    data-testid="update-retry-button"
                    onClick={() => void handleCheckNow()}
                  >
                    TENTAR NOVAMENTE
                  </button>
                </div>
              )}
            </div>
          )}

          {commandError && (
            <StatusMessage state="error" testId="update-command-error">
              {commandError}
            </StatusMessage>
          )}
        </>
      )}

      {activeState.available && (
        <UpdateConfirmDialog
          open={confirmOpen && activeState.phase === 'available'}
          version={activeState.available.version}
          currentVersion={activeState.current_version}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void handleConfirmInstall()}
          triggerRef={installButtonRef}
        />
      )}
    </section>
  );
}
