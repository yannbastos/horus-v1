import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { Connection } from '../services/api';
import {
  executeDataDeletion,
  formatDeletionBytes,
  previewDataDeletion,
} from '../services/dataDeletion';
import type {
  DataDeletionExecuteResult,
  DataDeletionPreview,
  DataDeletionScope,
} from '../services/dataDeletion';
import { StatusMessage } from './StatusMessage';

export type DataDeletionDialogProps = {
  open: boolean;
  connection: Connection | null;
  scope: DataDeletionScope;
  targetId: string;
  targetLabel?: string;
  onClose: () => void;
  onDeleted?: (result: DataDeletionExecuteResult) => void;
  triggerRef?: RefObject<HTMLButtonElement | null>;
};

const ITEM_KIND_LABELS: Record<string, string> = {
  production_folder: 'Pasta da produção',
  exports: 'Pacotes exportados',
  comments: 'Comentários de revisão',
  import_folder: 'Pasta do projeto importado',
};

const TABLE_LABELS: Record<string, string> = {
  productions: 'Registro da produção (productions)',
  jobs: 'Execução do job (jobs)',
  events: 'Eventos do job (events)',
  job_attempts: 'Tentativas do job (job_attempts)',
  production_receipts: 'Recibos de produção (production_receipts)',
  license_reviews: 'Revisões de licença (license_reviews)',
};

const DEPENDENCY_KIND_LABELS: Record<string, string> = {
  script: 'Roteiro',
  workflow: 'Fluxo editorial',
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
    el => !el.hasAttribute('hidden') && el.offsetParent !== null
  );
}

/**
 * Diálogo acessível para prévia, confirmação por frase exata e execução da exclusão segura
 * de arquivos locais (produção ou projeto importado), com backup automático e quarentena recuperável.
 */
export function DataDeletionDialog({
  open,
  connection,
  scope,
  targetId,
  targetLabel,
  onClose,
  onDeleted,
  triggerRef,
}: DataDeletionDialogProps) {
  const titleId = useId();
  const descId = useId();
  const phraseHintId = useId();
  const phraseInputId = useId();

  const dialogRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  const [preview, setPreview] = useState<DataDeletionPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [typedPhrase, setTypedPhrase] = useState('');
  const [executing, setExecuting] = useState(false);
  const [executeError, setExecuteError] = useState('');
  const [reloadTick, setReloadTick] = useState(0);

  const loadPreview = useCallback(
    async (conn: Connection, signal?: AbortSignal) => {
      setLoading(true);
      setLoadError('');
      setExecuteError('');
      setTypedPhrase('');
      setPreview(null);
      try {
        const result = await previewDataDeletion(conn, scope, targetId, signal);
        if (!signal?.aborted) {
          setPreview(result);
        }
      } catch (reason) {
        if (!signal?.aborted) {
          setLoadError(
            reason instanceof Error
              ? reason.message
              : 'Não foi possível gerar a prévia da exclusão.'
          );
        }
      } finally {
        if (!signal?.aborted) {
          setLoading(false);
        }
      }
    },
    [scope, targetId]
  );

  useEffect(() => {
    if (!open || !connection || !targetId) return;
    const controller = new AbortController();
    void loadPreview(connection, controller.signal);
    return () => controller.abort();
  }, [open, connection, targetId, reloadTick, loadPreview]);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });
    return () => {
      cancelAnimationFrame(frame);
      const restoreTarget = triggerRef?.current ?? previousFocusRef.current;
      if (restoreTarget && document.contains(restoreTarget)) {
        restoreTarget.focus();
      }
    };
  }, [open, triggerRef]);

  if (!open) return null;

  const phraseMatches = Boolean(
    preview && preview.can_execute && typedPhrase === preview.confirmation_phrase
  );

  function handleKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key === 'Escape') {
      if (executing) return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
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

  async function handleConfirmDelete() {
    if (!connection || !preview || !phraseMatches || executing) return;
    setExecuting(true);
    setExecuteError('');
    try {
      const result = await executeDataDeletion(connection, {
        plan_id: preview.plan_id,
        intent_token: preview.intent_token,
        phrase: typedPhrase,
      });
      onDeleted?.(result);
      onClose();
    } catch (reason) {
      setExecuteError(
        reason instanceof Error
          ? reason.message
          : 'Não foi possível concluir a exclusão dos arquivos locais.'
      );
    } finally {
      setExecuting(false);
    }
  }

  const scopeLabel = scope === 'production' ? 'Produção' : 'Projeto importado';

  return (
    <div
      className="shortcuts-backdrop"
      data-testid="data-deletion-backdrop"
      onClick={event => {
        if (event.target === event.currentTarget && !executing) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className="data-deletion-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        data-testid="data-deletion-dialog"
        onKeyDown={handleKeyDown}
      >
        <header className="data-deletion-modal-header">
          <div>
            <p className="eyebrow warn">EXCLUSÃO SEGURA DE DADOS LOCAIS</p>
            <h2 id={titleId}>Excluir arquivos locais</h2>
            <p id={descId} className="muted">
              {scopeLabel}: <strong>{targetLabel || targetId}</strong>
            </p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="button"
            disabled={executing}
            onClick={onClose}
            aria-label="Fechar diálogo de exclusão"
          >
            Fechar
          </button>
        </header>

        <div className="data-deletion-modal-body">
          {loading && (
            <StatusMessage state="loading" testId="data-deletion-preview-loading">
              Calculando prévia exata dos arquivos locais…
            </StatusMessage>
          )}

          {loadError && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space3)' }}>
              <StatusMessage state="error" testId="data-deletion-preview-error">
                {loadError}
              </StatusMessage>
              <div className="actions">
                <button
                  type="button"
                  className="button"
                  onClick={() => setReloadTick(t => t + 1)}
                >
                  Tentar novamente
                </button>
              </div>
            </div>
          )}

          {preview && !loading && (
            <>
              <div
                className="data-deletion-notice"
                role="note"
                aria-label="Aviso de backup automático e quarentena recuperável"
                data-testid="data-deletion-backup-notice"
              >
                <span className="eyebrow warn">BACKUP AUTOMÁTICO E QUARENTENA RECUPERÁVEL</span>
                <p>
                  Antes de remover qualquer arquivo, o Hórus cria e verifica um pacote de backup
                  automático (<code>pre-exclusao-*.zip</code>) fora da pasta de dados.
                </p>
                <p className="muted">
                  <strong>Atenção:</strong> o pacote de backup inclui apenas o banco de dados e
                  manifestos JSON — ele <strong>não</strong> contém vídeos, áudios ou exports. Os
                  arquivos removidos da pasta ativa vão para a <strong>quarentena recuperável</strong>{' '}
                  e podem ser restaurados em <strong>Ajustes → Privacidade e Segurança</strong>.
                </p>
              </div>

              <div role="region" aria-label="O que será removido da pasta ativa">
                <div className="row spread" style={{ marginBottom: 'var(--Space2)' }}>
                  <span className="eyebrow">O QUE SAI DA PASTA ATIVA (VAI PARA A QUARENTENA)</span>
                  <span className="tag" data-testid="data-deletion-totals">
                    {preview.totals.files} arquivo(s) · {formatDeletionBytes(preview.totals.bytes)}
                  </span>
                </div>
                <ul className="data-deletion-list" data-testid="data-deletion-items">
                  {preview.items.map(item => (
                    <li key={item.path} className="data-deletion-list-item">
                      <div className="row spread" style={{ gap: 'var(--Space2)', flexWrap: 'wrap' }}>
                        <strong>{ITEM_KIND_LABELS[item.kind] || item.kind}</strong>
                        <span>
                          {item.exists
                            ? `${item.files} arquivo(s) · ${formatDeletionBytes(item.bytes)}`
                            : 'Não existe no disco'}
                        </span>
                      </div>
                      <small className="muted" style={{ fontFamily: 'var(--FontMono)' }}>
                        {item.path}
                      </small>
                    </li>
                  ))}
                </ul>
              </div>

              <div role="region" aria-label="O que permanece no banco de dados">
                <span className="eyebrow" style={{ display: 'block', marginBottom: 'var(--Space2)' }}>
                  O QUE PERMANECE NO BANCO DE DADOS E POR QUÊ
                </span>
                {preview.db_kept.length > 0 ? (
                  <ul className="data-deletion-list" data-testid="data-deletion-db-kept">
                    {preview.db_kept.map(kept => (
                      <li key={kept.table} className="data-deletion-list-item">
                        <div className="row spread" style={{ gap: 'var(--Space2)', flexWrap: 'wrap' }}>
                          <strong>{TABLE_LABELS[kept.table] || kept.table}</strong>
                          <span className="tag">{kept.rows} registro(s)</span>
                        </div>
                        <small className="muted">{kept.reason}</small>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted" data-testid="data-deletion-db-kept-empty">
                    Nenhum registro operacional no banco pertence a este item; apenas o histórico de
                    exclusões registra a operação.
                  </p>
                )}
              </div>

              {preview.dependencies.length > 0 && (
                <div role="region" aria-label="Dependências preservadas">
                  <span className="eyebrow" style={{ display: 'block', marginBottom: 'var(--Space2)' }}>
                    DEPENDÊNCIAS PRESERVADAS (NÃO SÃO APAGADAS)
                  </span>
                  <ul className="data-deletion-list" data-testid="data-deletion-dependencies">
                    {preview.dependencies.map(dep => (
                      <li key={`${dep.kind}-${dep.id}`} className="data-deletion-list-item">
                        <div className="row spread" style={{ gap: 'var(--Space2)', flexWrap: 'wrap' }}>
                          <strong>
                            {DEPENDENCY_KIND_LABELS[dep.kind] || dep.kind} {dep.id}
                            {typeof dep.version === 'number' ? ` (v${dep.version})` : ''}
                          </strong>
                          <span className="tag good">Preservado</span>
                        </div>
                        <small className="muted">{dep.note}</small>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {preview.blockers.length > 0 && (
                <div
                  role="alert"
                  aria-label="Bloqueios para exclusão"
                  data-testid="data-deletion-blockers"
                  style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space2)' }}
                >
                  <StatusMessage state="error">
                    Não é possível excluir agora devido aos seguintes bloqueios:
                  </StatusMessage>
                  <ul className="data-deletion-list">
                    {preview.blockers.map(blocker => (
                      <li key={blocker.code} className="data-deletion-list-item">
                        <strong>{blocker.message}</strong>
                      </li>
                    ))}
                  </ul>
                  <div className="actions">
                    <button
                      type="button"
                      className="button"
                      onClick={() => setReloadTick(t => t + 1)}
                    >
                      Atualizar prévia
                    </button>
                  </div>
                </div>
              )}

              {preview.can_execute && (
                <div className="data-deletion-phrase-box">
                  <p id={phraseHintId}>
                    Para confirmar que você deseja mover esses arquivos para a quarentena, digite
                    exatamente a frase abaixo:
                  </p>
                  <code
                    className="data-deletion-phrase-code"
                    data-testid="data-deletion-phrase"
                    aria-label={`Frase exata de confirmação: ${preview.confirmation_phrase}`}
                  >
                    {preview.confirmation_phrase}
                  </code>
                  <label htmlFor={phraseInputId}>
                    Frase de confirmação
                    <input
                      id={phraseInputId}
                      type="text"
                      autoComplete="off"
                      spellCheck={false}
                      value={typedPhrase}
                      disabled={executing}
                      placeholder={preview.confirmation_phrase}
                      aria-describedby={phraseHintId}
                      data-testid="data-deletion-phrase-input"
                      onChange={event => setTypedPhrase(event.target.value)}
                    />
                  </label>
                </div>
              )}

              {executeError && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space2)' }}>
                  <StatusMessage state="error" testId="data-deletion-execute-error">
                    {executeError}
                  </StatusMessage>
                  <div className="actions">
                    <button
                      type="button"
                      className="button"
                      disabled={executing}
                      onClick={() => setReloadTick(t => t + 1)}
                    >
                      Gerar nova prévia
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <footer className="data-deletion-modal-footer">
          <button
            type="button"
            className="button"
            disabled={executing}
            onClick={onClose}
          >
            Cancelar
          </button>
          {preview?.can_execute && (
            <button
              type="button"
              className="button danger"
              disabled={!phraseMatches || executing}
              data-testid="data-deletion-confirm-btn"
              onClick={() => void handleConfirmDelete()}
            >
              {executing ? 'Excluindo e gerando backup…' : 'Confirmar exclusão'}
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}
