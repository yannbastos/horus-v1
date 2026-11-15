import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { StatusMessage } from '../../components/StatusMessage';
import { nativeDesktop, selectDirectory } from '../../platform/desktop';
import { observeJob, terminalStatuses } from '../../services/api';
import type { Connection, Job } from '../../services/api';
import {
  cancelWhisperDownloadJob,
  deleteWhisperModel,
  fetchWhisperDownloadJob,
  fetchWhisperModelCatalog,
  fetchWhisperModels,
  formatModelActionError,
  formatModelSize,
  importWhisperModel,
  safeExternalUrl,
  startWhisperModelDownload,
} from '../../services/transcriptionModels';
import type {
  WhisperCatalogModel,
  WhisperModelInfo,
  WhisperModelLicense,
} from '../../services/transcriptionModels';

const LICENSE_STATUS_LABELS: Record<string, string> = {
  verified: 'Verificada',
  confirmed: 'Confirmada',
  pending: 'Pendente',
  unknown: 'Não verificada',
  missing: 'Ausente',
};

function formatLicense(license: WhisperModelLicense | null): string {
  if (!license) return 'MIT (aguardando importação)';
  const statusLabel = LICENSE_STATUS_LABELS[license.status] ?? license.status;
  const cardLabel = license.model_card_present ? 'model card presente' : 'sem model card';
  return `${license.identifier} · ${statusLabel} · ${cardLabel}`;
}

export interface WhisperModelsPanelProps {
  connection: Connection | null;
  pickDirectory?: () => Promise<string | null>;
  initialModels?: WhisperModelInfo[];
}

type ActiveDownloadState = {
  model: string;
  jobId: string;
  progress: number | null;
  statusMessage: string;
  cancelling: boolean;
};

export default function WhisperModelsPanel({
  connection,
  pickDirectory,
  initialModels,
}: WhisperModelsPanelProps) {
  const [models, setModels] = useState<WhisperModelInfo[]>(initialModels ?? []);
  const [catalog, setCatalog] = useState<WhisperCatalogModel[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [actionSuccess, setActionSuccess] = useState('');
  const [actionNotice, setActionNotice] = useState('');
  const [revision, setRevision] = useState(0);

  const [importingModel, setImportingModel] = useState<string | null>(null);
  const [sourceDirectory, setSourceDirectory] = useState('');
  const [confirmLicense, setConfirmLicense] = useState(false);

  const [previewModel, setPreviewModel] = useState<string | null>(null);
  const [confirmDownload, setConfirmDownload] = useState(false);
  const [activeDownload, setActiveDownload] = useState<ActiveDownloadState | null>(null);

  const [confirmDeleteModel, setConfirmDeleteModel] = useState<string | null>(null);
  const [deleteStepConfirmed, setDeleteStepConfirmed] = useState(false);

  const [busyModel, setBusyModel] = useState<string | null>(null);
  const downloadAbortRef = useRef<AbortController | null>(null);

  const catalogByModel = useMemo(() => {
    const map = new Map<string, WhisperCatalogModel>();
    if (!catalog) return map;
    for (const entry of catalog) {
      map.set(entry.model, entry);
    }
    return map;
  }, [catalog]);

  useEffect(() => {
    return () => {
      downloadAbortRef.current?.abort();
    };
  }, []);

  const loadModels = useCallback(async (signal?: AbortSignal) => {
    if (!connection) return;
    setLoading(true);
    setLoadError('');
    try {
      const [items, catalogItems] = await Promise.all([
        fetchWhisperModels(connection, signal),
        fetchWhisperModelCatalog(connection, signal).catch(() => null),
      ]);
      if (!signal?.aborted) {
        setModels(items);
        setCatalog(catalogItems);
      }
    } catch (reason) {
      if (!signal?.aborted) {
        setLoadError(formatModelActionError('list', reason));
      }
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
      }
    }
  }, [connection]);

  useEffect(() => {
    if (initialModels && revision === 0) return;
    if (!connection) {
      setModels(initialModels ?? []);
      setLoadError('');
      return;
    }
    const controller = new AbortController();
    void loadModels(controller.signal);
    return () => controller.abort();
  }, [connection, initialModels, loadModels, revision]);

  function openImport(modelName: string) {
    setConfirmDeleteModel(null);
    setDeleteStepConfirmed(false);
    setPreviewModel(null);
    setConfirmDownload(false);
    setImportingModel(modelName);
    setSourceDirectory('');
    setConfirmLicense(false);
    setActionError('');
    setActionSuccess('');
    setActionNotice('');
  }

  function closeImport() {
    setImportingModel(null);
    setSourceDirectory('');
    setConfirmLicense(false);
  }

  function openDownloadPreview(modelName: string) {
    closeImport();
    setConfirmDeleteModel(null);
    setDeleteStepConfirmed(false);
    setPreviewModel(modelName);
    setConfirmDownload(false);
    setActionError('');
    setActionSuccess('');
    setActionNotice('');
  }

  function closeDownloadPreview() {
    setPreviewModel(null);
    setConfirmDownload(false);
  }

  async function submitDownload(catalogItem: WhisperCatalogModel) {
    if (!connection || !confirmDownload || busyModel || activeDownload) return;
    setBusyModel(catalogItem.model);
    setActionError('');
    setActionSuccess('');
    setActionNotice('');

    try {
      const idempotencyKey = crypto.randomUUID();
      const { job_id: jobId } = await startWhisperModelDownload(
        connection,
        catalogItem.model,
        idempotencyKey
      );
      closeDownloadPreview();
      setBusyModel(null);

      downloadAbortRef.current?.abort();
      const controller = new AbortController();
      downloadAbortRef.current = controller;

      setActiveDownload({
        model: catalogItem.model,
        jobId,
        progress: 0,
        statusMessage: 'Iniciando download do modelo…',
        cancelling: false,
      });

      let finalJob: Job | null = null;
      try {
        await observeJob(connection, jobId, controller.signal, event => {
          if (event.kind === 'progress') {
            const parsed = Number(event.message);
            const pct = Number.isFinite(parsed) ? Math.max(0, Math.min(100, Math.round(parsed))) : null;
            setActiveDownload(prev => (prev && prev.jobId === jobId ? { ...prev, progress: pct } : prev));
          } else if (event.kind === 'status') {
            setActiveDownload(prev =>
              prev && prev.jobId === jobId ? { ...prev, statusMessage: event.message } : prev
            );
          } else if (event.kind === 'complete' && event.job) {
            finalJob = event.job;
          }
        });
      } catch (streamError) {
        if (controller.signal.aborted) return;
        try {
          const snapshot = await fetchWhisperDownloadJob(connection, jobId, controller.signal);
          if (terminalStatuses.includes(snapshot.status)) {
            finalJob = snapshot;
          } else {
            throw streamError;
          }
        } catch (fallbackError) {
          if (controller.signal.aborted) return;
          setActiveDownload(null);
          setActionError(formatModelActionError('download', fallbackError));
          return;
        }
      }

      if (controller.signal.aborted) return;

      if (!finalJob) {
        try {
          finalJob = await fetchWhisperDownloadJob(connection, jobId, controller.signal);
        } catch (reason) {
          if (!controller.signal.aborted) {
            setActiveDownload(null);
            setActionError(formatModelActionError('download', reason));
          }
          return;
        }
      }

      setActiveDownload(null);

      if (finalJob.status === 'succeeded') {
        await loadModels();
        setActionSuccess(
          `Modelo "${catalogItem.model}" baixado, verificação concluída${catalogItem.sha256 ? ' (SHA-256 validado)' : ''} e pronto para uso local.`
        );
      } else if (finalJob.status === 'cancelled') {
        setActionNotice(`Download do modelo "${catalogItem.model}" cancelado.`);
      } else {
        const reasonText = finalJob.error
          ? `Falha no download do modelo "${catalogItem.model}": ${finalJob.error} Verifique sua conexão ou tente a importação de pasta local.`
          : `Não foi possível concluir o download do modelo "${catalogItem.model}". Verifique sua conexão e tente novamente.`;
        setActionError(reasonText);
      }
    } catch (reason) {
      setBusyModel(null);
      setActiveDownload(null);
      setActionError(formatModelActionError('download', reason));
    }
  }

  async function handleCancelDownload() {
    if (!connection || !activeDownload || activeDownload.cancelling) return;
    const { jobId } = activeDownload;
    setActiveDownload(prev => (prev && prev.jobId === jobId ? { ...prev, cancelling: true } : prev));
    try {
      await cancelWhisperDownloadJob(connection, jobId);
    } catch (reason) {
      setActiveDownload(prev => (prev && prev.jobId === jobId ? { ...prev, cancelling: false } : prev));
      setActionError(formatModelActionError('cancel', reason));
    }
  }

  async function chooseDirectory() {
    setActionError('');
    try {
      const chooser = pickDirectory ?? selectDirectory;
      const selected = await chooser();
      if (selected) {
        setSourceDirectory(selected);
      }
    } catch (reason) {
      setActionError(formatModelActionError('import', reason));
    }
  }

  async function submitImport(modelName: string) {
    if (!connection || !sourceDirectory.trim() || !confirmLicense || busyModel) return;
    setBusyModel(modelName);
    setActionError('');
    setActionSuccess('');
    try {
      await importWhisperModel(connection, {
        model: modelName,
        source_directory: sourceDirectory.trim(),
        confirm_license: true,
      });
      closeImport();
      setActionSuccess(`Modelo "${modelName}" importado e validado com licença MIT.`);
      await loadModels();
    } catch (reason) {
      setActionError(formatModelActionError('import', reason));
    } finally {
      setBusyModel(null);
    }
  }

  function openDelete(modelName: string) {
    closeImport();
    closeDownloadPreview();
    setConfirmDeleteModel(modelName);
    setDeleteStepConfirmed(false);
    setActionError('');
    setActionSuccess('');
    setActionNotice('');
  }

  function closeDelete() {
    setConfirmDeleteModel(null);
    setDeleteStepConfirmed(false);
  }

  async function submitDelete(modelName: string) {
    if (!connection || !deleteStepConfirmed || busyModel) return;
    setBusyModel(modelName);
    setActionError('');
    setActionSuccess('');
    try {
      await deleteWhisperModel(connection, modelName);
      closeDelete();
      setActionSuccess(`Modelo "${modelName}" removido do armazenamento local.`);
      await loadModels();
    } catch (reason) {
      setActionError(formatModelActionError('delete', reason));
    } finally {
      setBusyModel(null);
    }
  }

  const canUseNativePicker = nativeDesktop || Boolean(pickDirectory);

  return (
    <section
      className="card"
      id="transcription-models"
      aria-labelledby="whisper-models-title"
      data-testid="whisper-models-panel"
    >
      <div className="row spread">
        <div>
          <p className="eyebrow">TRANSCRIÇÃO LOCAL · WHISPER</p>
          <h2 id="whisper-models-title">Modelos de transcrição</h2>
        </div>
        {connection && (
          <button
            type="button"
            className="button"
            disabled={loading || busyModel !== null}
            onClick={() => setRevision(value => value + 1)}
          >
            ATUALIZAR MODELOS
          </button>
        )}
      </div>

      <p className="muted">
        {catalog !== null
          ? 'A transcrição usa somente modelos Whisper locais. Importe uma pasta local ou baixe explicitamente um modelo do catálogo após confirmar origem e licença.'
          : 'A transcrição usa somente modelos Whisper locais. Nenhum modelo é baixado pela rede.'}
      </p>

      {!connection && !initialModels && (
        <StatusMessage state="empty" testId="whisper-models-disconnected">
          Conecte o backend local para listar, importar ou remover modelos Whisper.
        </StatusMessage>
      )}

      {loading && (
        <StatusMessage state="loading" ariaLive="polite" testId="whisper-models-loading">
          Carregando modelos Whisper locais…
        </StatusMessage>
      )}

      {loadError && (
        <StatusMessage state="error" testId="whisper-models-error">
          {loadError}
        </StatusMessage>
      )}

      {actionError && (
        <StatusMessage state="error" testId="whisper-models-action-error">
          {actionError}
        </StatusMessage>
      )}

      {actionSuccess && (
        <StatusMessage state="success" tone="good" testId="whisper-models-action-success">
          {actionSuccess}
        </StatusMessage>
      )}

      {actionNotice && (
        <StatusMessage state="empty" testId="whisper-models-action-notice">
          {actionNotice}
        </StatusMessage>
      )}

      {!loading && !loadError && (connection || initialModels) && models.length === 0 && (
        <StatusMessage state="empty" testId="whisper-models-empty">
          Nenhum modelo Whisper encontrado no catálogo local.
        </StatusMessage>
      )}

      {models.length > 0 && (
        <ul
          style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 'var(--Space4)' }}
          aria-label="Lista de modelos Whisper"
          data-testid="whisper-models-list"
        >
          {models.map(item => {
            const isImporting = importingModel === item.model;
            const isPreviewing = previewModel === item.model;
            const isDownloading = activeDownload?.model === item.model;
            const isDeleting = confirmDeleteModel === item.model;
            const isBusy = busyModel === item.model;
            const isLegacyExternal = item.installed && !item.managed;
            const catalogEntry = catalogByModel.get(item.model) ?? null;
            const safeLicenseUrl = catalogEntry ? safeExternalUrl(catalogEntry.license.url) : null;
            const safeSourceUrl = catalogEntry ? safeExternalUrl(catalogEntry.source.url) : null;

            return (
              <li
                key={item.model}
                data-testid={`whisper-model-item-${item.model}`}
                style={{
                  padding: 12,
                  border: '1px solid var(--Line)',
                  background: 'var(--Sunken)',
                  display: 'grid',
                  gap: 8,
                }}
              >
                <div className="row spread" style={{ flexWrap: 'wrap' }}>
                  <div className="row" style={{ flexWrap: 'wrap' }}>
                    <strong>{item.model}</strong>
                    <span className={item.installed ? 'tag good' : 'tag warn'}>
                      {item.installed ? 'INSTALADO' : 'AUSENTE'}
                    </span>
                    {isLegacyExternal && <span className="tag">LEGADO EXTERNO</span>}
                  </div>
                  <div className="actions">
                    {!item.installed && catalogEntry && (
                      <button
                        type="button"
                        className="button primary"
                        disabled={!connection || busyModel !== null || activeDownload !== null || isPreviewing}
                        onClick={() => openDownloadPreview(item.model)}
                        data-testid={`whisper-download-btn-${item.model}`}
                      >
                        BAIXAR MODELO
                      </button>
                    )}
                    {!item.installed && (
                      <button
                        type="button"
                        className="button"
                        disabled={!connection || busyModel !== null || activeDownload !== null || isImporting}
                        onClick={() => openImport(item.model)}
                        data-testid={`whisper-import-btn-${item.model}`}
                      >
                        IMPORTAR DE PASTA
                      </button>
                    )}
                    {item.installed && item.removable && (
                      <button
                        type="button"
                        className="button"
                        disabled={!connection || busyModel !== null || activeDownload !== null || isDeleting}
                        onClick={() => openDelete(item.model)}
                        data-testid={`whisper-remove-btn-${item.model}`}
                      >
                        REMOVER
                      </button>
                    )}
                  </div>
                </div>

                <p className="muted">
                  Tamanho: {formatModelSize(item.size_bytes)} · Licença: {formatLicense(item.license)}
                  {item.source?.name ? ` · Origem: ${item.source.name}` : ''}
                </p>

                {item.installed && !item.removable && (
                  <p className="muted">
                    Este modelo é gerenciado externamente e não pode ser removido pelo aplicativo.
                  </p>
                )}

                {isPreviewing && catalogEntry && (
                  <div
                    className="card"
                    role="region"
                    aria-label={`Prévia de download do modelo ${item.model}`}
                    data-testid={`whisper-download-preview-${item.model}`}
                  >
                    <p className="eyebrow">PRÉVIA OBRIGATÓRIA DE DOWNLOAD · {item.model.toUpperCase()}</p>
                    <div
                      style={{
                        display: 'grid',
                        gap: 6,
                        padding: 10,
                        border: '1px solid var(--Line)',
                        background: 'var(--Surface)',
                      }}
                    >
                      <p>
                        <strong>Modelo:</strong> {catalogEntry.model}
                      </p>
                      <p>
                        <strong>Tamanho:</strong> {formatModelSize(catalogEntry.size_bytes)}
                      </p>
                      <p>
                        <strong>Origem / Host:</strong> {catalogEntry.source.host}
                        {safeSourceUrl && (
                          <>
                            {' '}
                            (
                            <a href={safeSourceUrl} target="_blank" rel="noreferrer noopener">
                              ver origem
                            </a>
                            )
                          </>
                        )}
                      </p>
                      <p>
                        <strong>Licença:</strong>{' '}
                        {safeLicenseUrl ? (
                          <a href={safeLicenseUrl} target="_blank" rel="noreferrer noopener">
                            {catalogEntry.license.identifier} (ver licença)
                          </a>
                        ) : (
                          catalogEntry.license.identifier
                        )}
                      </p>
                      <p>
                        <strong>Destino:</strong> {catalogEntry.destination_label}
                      </p>
                      {catalogEntry.sha256 && (
                        <p className="muted">
                          <strong>SHA-256 esperado:</strong> <code>{catalogEntry.sha256}</code>
                        </p>
                      )}
                    </div>

                    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexDirection: 'row' }}>
                      <input
                        type="checkbox"
                        checked={confirmDownload}
                        disabled={isBusy}
                        onChange={e => setConfirmDownload(e.target.checked)}
                        style={{ width: 'auto', marginTop: 3 }}
                      />
                      <span>
                        Confirmo o download explícito deste modelo a partir de {catalogEntry.source.host} sob licença{' '}
                        {catalogEntry.license.identifier} para o destino local indicado.
                      </span>
                    </label>

                    <div className="actions">
                      <button
                        type="button"
                        className="button primary"
                        disabled={!confirmDownload || isBusy || activeDownload !== null}
                        onClick={() => void submitDownload(catalogEntry)}
                        data-testid={`whisper-confirm-download-btn-${item.model}`}
                      >
                        {isBusy ? 'INICIANDO…' : 'CONFIRMAR DOWNLOAD'}
                      </button>
                      <button
                        type="button"
                        className="button"
                        disabled={isBusy}
                        onClick={closeDownloadPreview}
                        data-testid={`whisper-cancel-preview-btn-${item.model}`}
                      >
                        CANCELAR
                      </button>
                    </div>
                  </div>
                )}

                {isDownloading && activeDownload && (
                  <div
                    className="card"
                    role="region"
                    aria-label={`Progresso do download do modelo ${item.model}`}
                    data-testid={`whisper-download-progress-${item.model}`}
                  >
                    <p className="eyebrow">BAIXANDO MODELO · {item.model.toUpperCase()}</p>
                    <StatusMessage state="loading" ariaLive="polite" testId={`whisper-download-status-${item.model}`}>
                      {activeDownload.statusMessage}
                    </StatusMessage>
                    <div className="progress-container">
                      <div
                        className={`progress${activeDownload.progress === null ? ' indeterminate' : ''}`}
                        role="progressbar"
                        aria-label={`Progresso do download do modelo ${item.model}`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={activeDownload.progress ?? undefined}
                        data-testid={`whisper-download-progressbar-${item.model}`}
                      >
                        <span style={{ width: `${activeDownload.progress ?? 0}%` }} />
                      </div>
                      <span
                        className="eyebrow progress-value"
                        data-testid={`whisper-download-progress-value-${item.model}`}
                      >
                        {activeDownload.progress !== null ? `${activeDownload.progress}%` : '…'}
                      </span>
                    </div>
                    <div className="actions">
                      <button
                        type="button"
                        className="button"
                        disabled={activeDownload.cancelling}
                        onClick={() => void handleCancelDownload()}
                        data-testid={`whisper-cancel-download-btn-${item.model}`}
                      >
                        {activeDownload.cancelling ? 'CANCELANDO…' : 'CANCELAR DOWNLOAD'}
                      </button>
                    </div>
                  </div>
                )}

                {isImporting && (
                  <div
                    className="card"
                    role="region"
                    aria-label={`Importar modelo ${item.model}`}
                    data-testid={`whisper-import-form-${item.model}`}
                  >
                    <p className="eyebrow">IMPORTAR MODELO LOCAL · {item.model.toUpperCase()}</p>

                    {canUseNativePicker ? (
                      <div className="row" style={{ flexWrap: 'wrap' }}>
                        <button
                          type="button"
                          className="button"
                          disabled={isBusy}
                          onClick={() => void chooseDirectory()}
                        >
                          SELECIONAR PASTA
                        </button>
                        <span className="muted">
                          {sourceDirectory || 'Nenhuma pasta selecionada.'}
                        </span>
                      </div>
                    ) : (
                      <label>
                        Pasta local do modelo
                        <input
                          type="text"
                          value={sourceDirectory}
                          disabled={isBusy}
                          onChange={e => setSourceDirectory(e.target.value)}
                          placeholder="C:\modelos\whisper-small"
                          autoComplete="off"
                        />
                      </label>
                    )}

                    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexDirection: 'row' }}>
                      <input
                        type="checkbox"
                        checked={confirmLicense}
                        disabled={isBusy}
                        onChange={e => setConfirmLicense(e.target.checked)}
                        style={{ width: 'auto', marginTop: 3 }}
                      />
                      <span>
                        Confirmo que este modelo Whisper está licenciado sob licença MIT (ou compatível) e foi obtido localmente.
                      </span>
                    </label>

                    <div className="actions">
                      <button
                        type="button"
                        className="button primary"
                        disabled={!sourceDirectory.trim() || !confirmLicense || isBusy}
                        onClick={() => void submitImport(item.model)}
                        data-testid={`whisper-confirm-import-btn-${item.model}`}
                      >
                        {isBusy ? 'IMPORTANDO…' : 'CONFIRMAR IMPORTAÇÃO'}
                      </button>
                      <button
                        type="button"
                        className="button"
                        disabled={isBusy}
                        onClick={closeImport}
                      >
                        CANCELAR
                      </button>
                    </div>
                  </div>
                )}

                {isDeleting && (
                  <div
                    className="card"
                    role="region"
                    aria-label={`Confirmar remoção do modelo ${item.model}`}
                    data-testid={`whisper-delete-confirm-${item.model}`}
                  >
                    <p className="eyebrow">CONFIRMAÇÃO DUPLA · REMOVER {item.model.toUpperCase()}</p>
                    <p className="muted">
                      Esta ação remove os arquivos locais do modelo &ldquo;{item.model}&rdquo; gerenciados pelo Hórus.
                    </p>
                    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexDirection: 'row' }}>
                      <input
                        type="checkbox"
                        checked={deleteStepConfirmed}
                        disabled={isBusy}
                        onChange={e => setDeleteStepConfirmed(e.target.checked)}
                        style={{ width: 'auto', marginTop: 3 }}
                      />
                      <span>Confirmo que desejo remover este modelo localmente.</span>
                    </label>
                    <div className="actions">
                      <button
                        type="button"
                        className="button primary"
                        disabled={!deleteStepConfirmed || isBusy}
                        onClick={() => void submitDelete(item.model)}
                        data-testid={`whisper-confirm-delete-btn-${item.model}`}
                      >
                        {isBusy ? 'REMOVENDO…' : 'CONFIRMAR REMOÇÃO'}
                      </button>
                      <button
                        type="button"
                        className="button"
                        disabled={isBusy}
                        onClick={closeDelete}
                      >
                        CANCELAR
                      </button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
