import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { RefObject } from 'react';
import {
  canRetryUpdateError,
  checkUpdates,
  clearUpdateEnrollment,
  createDisabledUpdateState,
  enrollUpdateToken,
  formatUpdateBytes,
  formatUpdateErrorMessage,
  formatUpdateErrorDetail,
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
          <p className="muted" data-testid="update-confirm-deferral-warning">
            Durante uma produção ou operação crítica em andamento, a instalação é adiada ou bloqueada para proteger seus dados.
          </p>
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
            Instalar e reiniciar
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
  const [tokenInput, setTokenInput] = useState('');
  const installButtonRef = useRef<HTMLButtonElement | null>(null);
  const autoCheckLabelId = useId();
  const enrollInputId = useId();
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
    } catch {
      setCommandError('Não foi possível executar a ação de atualização. Preserve os dados e tente novamente mais tarde.');
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
    } catch {
      setCommandError('Não foi possível executar a ação de atualização. Preserve os dados e tente novamente mais tarde.');
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
    } catch {
      setCommandError('Não foi possível executar a ação de atualização. Preserve os dados e tente novamente mais tarde.');
    } finally {
      setPendingCommand(false);
    }
  }

  async function handleEnrollSubmit(event: React.FormEvent) {
    event.preventDefault();
    const candidate = tokenInput.trim();
    if (!candidate || isDisabled || busy) return;
    setTokenInput('');
    setPendingCommand(true);
    setCommandError('');
    try {
      const next = await enrollUpdateToken(candidate);
      commitState(next);
    } catch {
      setCommandError('Não foi possível salvar o token de acesso ao canal. Tente novamente.');
    } finally {
      setPendingCommand(false);
    }
  }

  async function handleClearEnrollment() {
    if (isDisabled || busy) return;
    setTokenInput('');
    setPendingCommand(true);
    setCommandError('');
    try {
      const next = await clearUpdateEnrollment();
      commitState(next);
    } catch {
      setCommandError('Não foi possível remover o token de acesso ao canal. Tente novamente.');
    } finally {
      setPendingCommand(false);
    }
  }

  const downloadPercent =
    activeState.progress &&
    typeof activeState.progress.downloaded === 'number' &&
    activeState.progress.total &&
    activeState.progress.total > 0
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
            {activeState.channel && (
              <div className="fact-item">
                <p className="eyebrow">CANAL</p>
                <p className="mono" data-testid="update-channel">
                  {activeState.channel}
                </p>
              </div>
            )}
            <div className="fact-item">
              <p className="eyebrow">ÚLTIMA VERIFICAÇÃO</p>
              <p data-testid="update-last-check">{formatDateTime(activeState.last_check_at)}</p>
            </div>
          </div>

          {activeState.just_updated && !busy && activeState.phase !== 'error' && (
            <StatusMessage state="success" testId="update-post-update-notice">
              Atualização concluída para a versão {activeState.current_version}
              {activeState.updated_from_version
                ? ` (anterior: ${activeState.updated_from_version})`
                : ''}
              .
            </StatusMessage>
          )}

          {typeof activeState.enrolled === 'boolean' && (
            <div className="card sunken" data-testid="update-channel-access">
              <div className="row spread">
                <div>
                  <p className="eyebrow">ACESSO AO CANAL</p>
                  <p className="muted">
                    {activeState.enrolled
                      ? 'Token de instalação armazenado com segurança no cofre do sistema.'
                      : 'Cole o token de instalação emitido para acessar este canal de atualizações.'}
                  </p>
                </div>
                <span className="tag" data-testid="update-enrollment-status">
                  {activeState.enrolled ? 'Inscrito' : 'Não inscrito'}
                </span>
              </div>
              {activeState.enrolled ? (
                <div className="row" style={{ gap: 'var(--Space3)', flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    className="button"
                    disabled={busy}
                    data-testid="update-clear-enrollment-button"
                    onClick={() => void handleClearEnrollment()}
                  >
                    REMOVER TOKEN
                  </button>
                </div>
              ) : (
                <form
                  className="row"
                  style={{ gap: 'var(--Space3)', flexWrap: 'wrap', alignItems: 'flex-end' }}
                  onSubmit={event => void handleEnrollSubmit(event)}
                >
                  <label htmlFor={enrollInputId} style={{ flex: '1 1 240px', minWidth: 0 }}>
                    <span>Token de instalação</span>
                    <input
                      id={enrollInputId}
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      value={tokenInput}
                      disabled={busy}
                      placeholder="Cole o token de acesso ao canal"
                      data-testid="update-enroll-token-input"
                      onChange={event => setTokenInput(event.target.value)}
                    />
                  </label>
                  <button
                    type="submit"
                    className="button"
                    disabled={busy || tokenInput.trim().length === 0}
                    data-testid="update-enroll-submit-button"
                  >
                    SALVAR TOKEN
                  </button>
                </form>
              )}
            </div>
          )}

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
                {activeState.progress && typeof activeState.progress.downloaded === 'number' ? (
                  <p className="muted">
                    {formatUpdateBytes(activeState.progress.downloaded)}
                    {activeState.progress.total !== null
                      ? ` de ${formatUpdateBytes(activeState.progress.total)}`
                      : ''}
                  </p>
                ) : activeState.progress?.phase ? (
                  <p className="muted">Fase: {activeState.progress.phase}</p>
                ) : null}
              </div>
            )}

            {activeState.phase === 'installing' && (
              <div className="card sunken" role="status" data-testid="update-status-installing">
                <p className="loading">
                  Verificando assinatura e instalando atualização. O aplicativo será reiniciado…
                </p>
                <div className="progress-container">
                  <div
                    className="progress indeterminate"
                    role="progressbar"
                    aria-label="Progresso da instalação da atualização"
                    aria-valuemin={0}
                    aria-valuemax={100}
                  >
                    <span style={{ width: '60%' }} />
                  </div>
                  <span className="progress-value mono">…</span>
                </div>
                {activeState.backup_path_label && (
                  <p className="muted">Backup preservado: {activeState.backup_path_label}</p>
                )}
              </div>
            )}

            {activeState.phase === 'restarting' && (
              <div className="card sunken" role="status" data-testid="update-status-restarting">
                <p className="loading">
                  Reiniciando o aplicativo para concluir a atualização…
                </p>
                <div className="progress-container">
                  <div
                    className="progress indeterminate"
                    role="progressbar"
                    aria-label="Progresso do reinício do aplicativo"
                    aria-valuemin={0}
                    aria-valuemax={100}
                  >
                    <span style={{ width: '85%' }} />
                  </div>
                  <span className="progress-value mono">…</span>
                </div>
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
                      <h3 data-testid="update-available-version">
                        Versão {activeState.available.version}
                      </h3>
                    </div>
                    {activeState.available.pub_date && (
                      <span className="tag">
                        {formatDateTime(activeState.available.pub_date)}
                      </span>
                    )}
                  </div>
                  <p className="muted" style={{ whiteSpace: 'pre-line' }} data-testid="update-available-notes">
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

              <p className="muted" data-testid="update-deferral-note">
                A instalação é adiada ou bloqueada durante produção ou operação crítica em andamento.
              </p>

              <div className="row" style={{ gap: 'var(--Space3)', flexWrap: 'wrap' }}>
                <button
                  ref={installButtonRef}
                  type="button"
                  className="button primary"
                  disabled={activeState.phase === 'blocked' || busy || !activeState.available}
                  data-testid="update-install-button"
                  onClick={() => setConfirmOpen(true)}
                >
                  INSTALAR E REINICIAR
                </button>
              </div>
            </div>
          )}

          {activeState.phase === 'error' && activeState.error && (
            <div className="card sunken" data-testid="update-error-card">
              <StatusMessage state="error" testId="update-error-message">
                {formatUpdateErrorMessage(activeState.error)}
              </StatusMessage>
              {formatUpdateErrorDetail(activeState.error) && (
                <p className="muted" data-testid="update-error-detail">
                  {formatUpdateErrorDetail(activeState.error)}
                </p>
              )}
              {['startup_failed', 'install_failed'].includes(activeState.error.code) && (
                <div className="muted" data-testid="update-recovery-guidance">
                  <p>Recuperação manual: feche o Hórus e preserve o banco, os arquivos WAL/SHM e o instalador atual. Não exclua dados nem marque a opção de apagar dados no desinstalador.</p>
                  <p>O backup verificado criado antes da instalação fica em <code>update-backups/pre-update-*.zip</code>, ao lado da pasta de dados. No local padrão: <code>%LOCALAPPDATA%/com.horus.audiovisual/update-backups/</code>. Se a pasta de dados foi personalizada, procure ao lado dela. A existência do backup precisa ser conferida.</p>
                  <p>Instaladores baixados e validados ficam em <code>workspace/updater/installers/&lt;versão&gt;/setup.exe</code> e <code>setup.exe.sig</code>, dentro da mesma pasta padrão (ou em <code>updater/installers/</code> na pasta de dados personalizada). A primeira instalação não guarda seu próprio instalador; se a versão anterior não estiver em cache, obtenha o instalador original confiável.</p>
                  <p>Reinstale a versão anterior manualmente somente se ela for compatível com o schema dos dados. Caso contrário, restaure o backup verificado em uma pasta nova, preservando o original. O ZIP não inclui mídia, modelos, WebView ou segredos; preserve esses recursos separadamente. Não há downgrade automático. Roteiro completo: <code>docs/adr-atualizador.md</code>, seção “Instalação Windows e recuperação”, no repositório do Hórus, acessível mesmo se o app não abrir.</p>
                </div>
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
