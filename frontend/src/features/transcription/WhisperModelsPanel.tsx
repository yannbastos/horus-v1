import { useCallback, useEffect, useState } from 'react';
import { StatusMessage } from '../../components/StatusMessage';
import { nativeDesktop, selectDirectory } from '../../platform/desktop';
import type { Connection } from '../../services/api';
import {
  deleteWhisperModel,
  fetchWhisperModels,
  formatModelActionError,
  formatModelSize,
  importWhisperModel,
} from '../../services/transcriptionModels';
import type { WhisperModelInfo, WhisperModelLicense } from '../../services/transcriptionModels';

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

export default function WhisperModelsPanel({
  connection,
  pickDirectory,
  initialModels,
}: WhisperModelsPanelProps) {
  const [models, setModels] = useState<WhisperModelInfo[]>(initialModels ?? []);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [actionSuccess, setActionSuccess] = useState('');
  const [revision, setRevision] = useState(0);

  const [importingModel, setImportingModel] = useState<string | null>(null);
  const [sourceDirectory, setSourceDirectory] = useState('');
  const [confirmLicense, setConfirmLicense] = useState(false);

  const [confirmDeleteModel, setConfirmDeleteModel] = useState<string | null>(null);
  const [deleteStepConfirmed, setDeleteStepConfirmed] = useState(false);

  const [busyModel, setBusyModel] = useState<string | null>(null);

  const loadModels = useCallback(async (signal?: AbortSignal) => {
    if (!connection) return;
    setLoading(true);
    setLoadError('');
    try {
      const items = await fetchWhisperModels(connection, signal);
      if (!signal?.aborted) {
        setModels(items);
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
    setImportingModel(modelName);
    setSourceDirectory('');
    setConfirmLicense(false);
    setActionError('');
    setActionSuccess('');
  }

  function closeImport() {
    setImportingModel(null);
    setSourceDirectory('');
    setConfirmLicense(false);
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
    setConfirmDeleteModel(modelName);
    setDeleteStepConfirmed(false);
    setActionError('');
    setActionSuccess('');
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
        A transcrição usa somente modelos Whisper locais. Nenhum modelo é baixado pela rede.
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
            const isDeleting = confirmDeleteModel === item.model;
            const isBusy = busyModel === item.model;
            const isLegacyExternal = item.installed && !item.managed;

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
                    {!item.installed && (
                      <button
                        type="button"
                        className="button"
                        disabled={!connection || busyModel !== null || isImporting}
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
                        disabled={!connection || busyModel !== null || isDeleting}
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
