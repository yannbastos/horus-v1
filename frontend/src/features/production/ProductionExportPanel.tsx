import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../services/api';
import type { Connection } from '../../services/api';
import { StatusMessage } from '../../components/StatusMessage';
import {
  computeComposedDescriptionLength,
  createProductionExport,
  createProductionThumbnail,
  downloadProductionExportZip,
  formatExportError,
  formatPublishingError,
  getProductionMetadata,
  listProductionExports,
  MAX_METADATA_DESCRIPTION_LENGTH,
  MAX_METADATA_TITLE_LENGTH,
  saveProductionMetadata,
} from '../../services/exports';
import type {
  ProductionExportItem,
  ProductionMetadataDraft,
  ProductionThumbnailResult,
} from '../../services/exports';
import { fetchProductionArtifactBlob, formatTimecode } from '../../services/production';
import type { ProductionRecord } from '../../services/production';

function formatExportBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB (${bytes} B)`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB (${bytes} B)`;
}

function formatExportDate(iso: string): string {
  if (!iso) return 'Data não informada';
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString('pt-BR');
}

function deriveInitialThumbnailFromArtifacts(
  production: ProductionRecord
): ProductionThumbnailResult | null {
  const thumbArtifacts = production.artifacts.filter(item => item.kind === 'thumbnail');
  if (thumbArtifacts.length === 0) return null;
  const latestPair = thumbArtifacts.slice(-2);
  return {
    timecode_seconds: 0,
    video_sha256: production.review?.artifact_sha256 ?? '',
    width: 1080,
    height: 1920,
    artifacts: latestPair,
    created_at: production.updated_at,
    publishable: false,
    created: false,
  };
}

export default function ProductionExportPanel({
  connection,
  production,
  currentTimeSeconds = 0,
  onCaptureCurrentVideoTime,
}: {
  connection: Connection | null;
  production: ProductionRecord;
  currentTimeSeconds?: number;
  onCaptureCurrentVideoTime?: () => number;
}) {
  const isApproved =
    production.status === 'succeeded' && production.review?.decision === 'approve';

  // Estado de pacotes ZIP exportados
  const [items, setItems] = useState<ProductionExportItem[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [exportState, setExportState] = useState<'idle' | 'generating' | 'ready' | 'error'>('idle');
  const [lastRepeated, setLastRepeated] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [needsReexport, setNeedsReexport] = useState(false);

  // Estado de Thumbnail (1080x1920)
  const [timecodeInput, setTimecodeInput] = useState<string>('0');
  const [thumbnailGenerating, setThumbnailGenerating] = useState(false);
  const [thumbnailError, setThumbnailError] = useState('');
  const [thumbnailResult, setThumbnailResult] = useState<ProductionThumbnailResult | null>(() =>
    deriveInitialThumbnailFromArtifacts(production)
  );
  const [thumbnailPreviewUrl, setThumbnailPreviewUrl] = useState<string | null>(null);
  const [thumbnailPreviewLoading, setThumbnailPreviewLoading] = useState(false);
  const [thumbnailPreviewError, setThumbnailPreviewError] = useState('');

  // Estado de Metadados para publicação manual
  const [metadataLoading, setMetadataLoading] = useState(false);
  const [metadataSaving, setMetadataSaving] = useState(false);
  const [metadataError, setMetadataError] = useState('');
  const [metadataDraft, setMetadataDraft] = useState<ProductionMetadataDraft | null>(null);
  const [metadataTitle, setMetadataTitle] = useState('');
  const [metadataDescription, setMetadataDescription] = useState('');
  const [savedTitle, setSavedTitle] = useState('');
  const [savedDescription, setSavedDescription] = useState('');
  const [metadataSavedOnce, setMetadataSavedOnce] = useState(false);

  const keyByProductionRef = useRef<Map<string, string>>(new Map());
  const thumbKeyByTimecodeRef = useRef<Map<string, string>>(new Map());
  const inFlightRef = useRef(false);
  const thumbInFlightRef = useRef(false);
  const metadataInFlightRef = useRef(false);
  const activeThumbBlobUrlRef = useRef<string | null>(null);

  function getStableKey(productionId: string): string {
    const existing = keyByProductionRef.current.get(productionId);
    if (existing) return existing;
    const generated = crypto.randomUUID();
    keyByProductionRef.current.set(productionId, generated);
    return generated;
  }

  function rotateKey(productionId: string): void {
    keyByProductionRef.current.set(productionId, crypto.randomUUID());
  }

  function getThumbnailKey(productionId: string, timecodeSeconds: number): string {
    const scope = `${productionId}:${timecodeSeconds}`;
    const existing = thumbKeyByTimecodeRef.current.get(scope);
    if (existing) return existing;
    const generated = crypto.randomUUID();
    thumbKeyByTimecodeRef.current.set(scope, generated);
    return generated;
  }

  function rotateThumbnailKey(productionId: string, timecodeSeconds: number): void {
    const scope = `${productionId}:${timecodeSeconds}`;
    thumbKeyByTimecodeRef.current.set(scope, crypto.randomUUID());
  }

  function revokeThumbBlobUrl(): void {
    if (activeThumbBlobUrlRef.current) {
      URL.revokeObjectURL(activeThumbBlobUrlRef.current);
      activeThumbBlobUrlRef.current = null;
    }
  }

  useEffect(() => {
    return () => {
      revokeThumbBlobUrl();
    };
  }, []);

  useEffect(() => {
    setExportState('idle');
    setLastRepeated(false);
    setErrorMessage('');
    setDownloadingId(null);
    setNeedsReexport(false);
    setThumbnailGenerating(false);
    setThumbnailError('');
    setThumbnailResult(deriveInitialThumbnailFromArtifacts(production));
    setMetadataError('');
    setMetadataSavedOnce(false);

    if (!connection || !isApproved) {
      setItems([]);
      setLoadingList(false);
      setMetadataLoading(false);
      setMetadataDraft(null);
      setMetadataTitle('');
      setMetadataDescription('');
      setSavedTitle('');
      setSavedDescription('');
      return;
    }

    const controller = new AbortController();
    setLoadingList(true);
    void listProductionExports(connection, production.id, controller.signal)
      .then(loaded => {
        if (!controller.signal.aborted) {
          setItems(loaded);
        }
      })
      .catch(reason => {
        if (!controller.signal.aborted) {
          setErrorMessage(formatExportError(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoadingList(false);
        }
      });

    setMetadataLoading(true);
    void getProductionMetadata(connection, production.id, controller.signal)
      .then(loaded => {
        if (controller.signal.aborted) return;
        if (loaded) {
          setMetadataDraft(loaded);
          setMetadataTitle(loaded.title);
          setMetadataDescription(loaded.editableDescription);
          setSavedTitle(loaded.title);
          setSavedDescription(loaded.editableDescription);
        }
      })
      .catch(reason => {
        if (!controller.signal.aborted) {
          setMetadataError(formatPublishingError(reason, 'metadata'));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setMetadataLoading(false);
        }
      });

    return () => controller.abort();
  }, [connection, production.id, isApproved]);

  const previewThumbnailArtifact =
    thumbnailResult?.artifacts.find(a => a.relative_path.toLowerCase().endsWith('.png')) ??
    thumbnailResult?.artifacts[0] ??
    null;

  useEffect(() => {
    if (!connection || !isApproved || !previewThumbnailArtifact) {
      revokeThumbBlobUrl();
      setThumbnailPreviewUrl(null);
      setThumbnailPreviewLoading(false);
      setThumbnailPreviewError('');
      return;
    }

    const controller = new AbortController();
    setThumbnailPreviewLoading(true);
    setThumbnailPreviewError('');

    void fetchProductionArtifactBlob(
      connection,
      production.id,
      previewThumbnailArtifact.id,
      controller.signal
    )
      .then(({ blob }) => {
        if (controller.signal.aborted) return;
        revokeThumbBlobUrl();
        const url = URL.createObjectURL(blob);
        activeThumbBlobUrlRef.current = url;
        setThumbnailPreviewUrl(url);
      })
      .catch(reason => {
        if (!controller.signal.aborted) {
          setThumbnailPreviewError(
            reason instanceof Error ? reason.message : 'Não foi possível carregar a prévia.'
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setThumbnailPreviewLoading(false);
        }
      });

    return () => controller.abort();
  }, [connection, production.id, isApproved, previewThumbnailArtifact?.id]);

  function handleUseCurrentFrame() {
    const rawTime = onCaptureCurrentVideoTime ? onCaptureCurrentVideoTime() : currentTimeSeconds;
    const safeTime = Number.isFinite(rawTime) && rawTime >= 0 ? Math.round(rawTime * 1000) / 1000 : 0;
    setTimecodeInput(String(safeTime));
    setThumbnailError('');
  }

  async function handleGenerateThumbnail() {
    if (!connection || !isApproved || thumbInFlightRef.current) return;
    setThumbnailError('');

    const trimmed = timecodeInput.trim();
    const parsed = trimmed === '' ? Number.NaN : Number(trimmed);
    if (!Number.isFinite(parsed) || parsed < 0) {
      setThumbnailError('Informe um timecode válido em segundos (>= 0).');
      return;
    }
    const normalizedSeconds = Math.round(parsed * 1000) / 1000;
    const targetProductionId = production.id;
    const key = getThumbnailKey(targetProductionId, normalizedSeconds);

    thumbInFlightRef.current = true;
    setThumbnailGenerating(true);

    try {
      const result = await createProductionThumbnail(
        connection,
        targetProductionId,
        normalizedSeconds,
        key
      );
      setThumbnailResult(result);
      setNeedsReexport(true);
      rotateKey(targetProductionId);
    } catch (reason) {
      if (reason instanceof ApiError && (reason.status === 409 || reason.status === 422)) {
        rotateThumbnailKey(targetProductionId, normalizedSeconds);
      }
      setThumbnailError(formatPublishingError(reason, 'thumbnail'));
    } finally {
      thumbInFlightRef.current = false;
      setThumbnailGenerating(false);
    }
  }

  async function handleSaveMetadata() {
    if (!connection || !isApproved || metadataInFlightRef.current) return;
    setMetadataError('');
    const targetProductionId = production.id;

    metadataInFlightRef.current = true;
    setMetadataSaving(true);

    try {
      const saved = await saveProductionMetadata(connection, targetProductionId, {
        title: metadataTitle,
        description: metadataDescription,
      });
      setMetadataDraft(saved);
      setMetadataTitle(saved.title);
      setMetadataDescription(saved.editableDescription);
      setSavedTitle(saved.title);
      setSavedDescription(saved.editableDescription);
      setMetadataSavedOnce(true);
      setNeedsReexport(true);
      rotateKey(targetProductionId);
    } catch (reason) {
      setMetadataError(formatPublishingError(reason, 'metadata'));
    } finally {
      metadataInFlightRef.current = false;
      setMetadataSaving(false);
    }
  }

  async function handleExport() {
    if (!connection || !isApproved || inFlightRef.current) return;
    inFlightRef.current = true;
    setExportState('generating');
    setErrorMessage('');
    const targetProductionId = production.id;
    const key = getStableKey(targetProductionId);

    try {
      const result = await createProductionExport(connection, targetProductionId, key);
      setLastRepeated(!result.created);
      setExportState('ready');
      setNeedsReexport(false);
      setItems(prev => {
        const exists = prev.some(entry => entry.id === result.item.id);
        return exists
          ? prev.map(entry => (entry.id === result.item.id ? result.item : entry))
          : [...prev, result.item];
      });
    } catch (reason) {
      if (reason instanceof ApiError && (reason.status === 409 || reason.status === 422)) {
        rotateKey(targetProductionId);
      }
      setExportState('error');
      setErrorMessage(formatExportError(reason));
    } finally {
      inFlightRef.current = false;
    }
  }

  async function handleDownload(item: ProductionExportItem) {
    if (!connection || downloadingId) return;
    setDownloadingId(item.id);
    setErrorMessage('');
    try {
      const blob = await downloadProductionExportZip(connection, production.id, item.id);
      const blobUrl = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement('a');
        anchor.href = blobUrl;
        anchor.download = `horus-pacote-${production.id.slice(0, 8)}-${item.id.slice(0, 8)}.zip`;
        anchor.style.display = 'none';
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
      } finally {
        window.setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
      }
    } catch (reason) {
      setExportState('error');
      setErrorMessage(formatExportError(reason));
    } finally {
      setDownloadingId(null);
    }
  }

  const disclosures = metadataDraft?.disclosures ?? [];
  const sources = metadataDraft?.sources ?? [];
  const composedDescriptionLength = computeComposedDescriptionLength(
    metadataDescription,
    disclosures
  );
  const hasUnsavedMetadata =
    metadataTitle !== savedTitle || metadataDescription !== savedDescription;

  return (
    <section
      className="fact-item"
      role="region"
      aria-labelledby="production-export-title"
      data-testid="production-export-panel"
      style={{ background: 'var(--Background)', display: 'grid', gap: 12 }}
    >
      <div className="row spread">
        <div>
          <span className="eyebrow accent">EXPORTAÇÃO AUDITÁVEL LOCAL</span>
          <h4 id="production-export-title" style={{ margin: '4px 0 0' }}>
            Pacote para publicação manual
          </h4>
        </div>
        <span className="tag demo">publishable: false</span>
      </div>

      <p className="muted">
        Não publica nada; prepara arquivos para você publicar e auditar (<code>publishable: false</code>).
      </p>

      {!isApproved ? (
        <StatusMessage state="empty" role="status" testId="export-requires-approval">
          Disponível somente após aprovação na revisão humana desta produção.
        </StatusMessage>
      ) : (
        <>
          {/* (1) Thumbnail 1080x1920 */}
          <div
            className="info-block"
            role="region"
            aria-label="Thumbnail para publicação manual"
            data-testid="publishing-thumbnail-section"
            style={{ display: 'grid', gap: 10 }}
          >
            <div className="row spread">
              <span className="eyebrow accent">1. THUMBNAIL VERTICAL (1080×1920)</span>
              <span className="tag">PNG + JPG · 1080×1920</span>
            </div>
            <small className="muted">
              Escolha o momento pelo player de vídeo ou informe os segundos para extrair a capa vertical
              auditável (<code>thumbnail.png</code> e <code>thumbnail.jpg</code>).
            </small>

            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 10,
                alignItems: 'flex-end',
              }}
            >
              <label style={{ width: 190, flexShrink: 0 }}>
                Timecode da thumbnail (s)
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  aria-label="Timecode da thumbnail (s)"
                  data-testid="thumbnail-timecode-input"
                  value={timecodeInput}
                  onChange={e => setTimecodeInput(e.target.value)}
                />
              </label>
              <div className="actions">
                <button
                  type="button"
                  className="button"
                  data-testid="thumbnail-use-current-frame"
                  onClick={handleUseCurrentFrame}
                >
                  Usar o quadro atual
                </button>
                <button
                  type="button"
                  className="button primary"
                  data-testid="thumbnail-generate-button"
                  disabled={!connection || thumbnailGenerating}
                  onClick={() => void handleGenerateThumbnail()}
                >
                  {thumbnailGenerating
                    ? 'Gerando thumbnail…'
                    : thumbnailResult
                      ? 'Gerar thumbnail novamente'
                      : 'Gerar thumbnail'}
                </button>
              </div>
            </div>

            {thumbnailError && (
              <StatusMessage state="error" testId="thumbnail-error-alert">
                {thumbnailError}
              </StatusMessage>
            )}

            {thumbnailResult && (
              <div
                data-testid="thumbnail-result"
                style={{
                  display: 'flex',
                  gap: 14,
                  flexWrap: 'wrap',
                  alignItems: 'flex-start',
                  padding: 10,
                  border: '1px solid var(--Line)',
                  background: 'var(--Sunken)',
                }}
              >
                <div style={{ flexShrink: 0 }}>
                  {thumbnailPreviewUrl ? (
                    <img
                      src={thumbnailPreviewUrl}
                      alt={`Prévia da thumbnail ${thumbnailResult.width}×${thumbnailResult.height} em ${thumbnailResult.timecode_seconds}s`}
                      data-testid="thumbnail-preview-image"
                      style={{
                        width: 108,
                        height: 192,
                        objectFit: 'cover',
                        border: '1px solid var(--Line)',
                        display: 'block',
                        background: 'var(--Background)',
                      }}
                    />
                  ) : thumbnailPreviewLoading ? (
                    <span className="muted" data-testid="thumbnail-preview-loading">
                      Carregando prévia da thumbnail…
                    </span>
                  ) : thumbnailPreviewError ? (
                    <span className="muted" data-testid="thumbnail-preview-error">
                      {thumbnailPreviewError}
                    </span>
                  ) : null}
                </div>

                <div
                  data-testid="thumbnail-metadata"
                  style={{ display: 'grid', gap: 6, flex: '1 1 240px', minWidth: 0 }}
                >
                  <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
                    <span className="tag good">
                      Dimensões: {thumbnailResult.width}×{thumbnailResult.height}
                    </span>
                    <span className="tag">
                      Timecode: {formatTimecode(thumbnailResult.timecode_seconds)} (
                      {thumbnailResult.timecode_seconds}s)
                    </span>
                  </div>
                  <small className="muted">
                    Artefatos gerados (você pode escolher outro momento acima e gerar novamente):
                  </small>
                  <ul
                    data-testid="thumbnail-artifacts-list"
                    style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 4 }}
                  >
                    {thumbnailResult.artifacts.map(art => (
                      <li
                        key={art.id}
                        data-testid={`thumbnail-artifact-${art.id}`}
                        style={{ fontSize: 12, overflowWrap: 'anywhere' }}
                      >
                        <strong>{art.relative_path}</strong> ({formatExportBytes(art.size_bytes)}) ·
                        SHA-256: <code title={art.sha256}>{art.sha256}</code>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </div>

          {/* (2) Metadados para publicação manual */}
          <div
            className="info-block"
            role="region"
            aria-label="Metadados para publicação manual"
            data-testid="publishing-metadata-section"
            style={{ display: 'grid', gap: 10 }}
          >
            <div className="row spread">
              <span className="eyebrow accent">
                2. METADADOS PARA PUBLICAÇÃO MANUAL (RASCUNHO DETERMINÍSTICO)
              </span>
              <div className="row">
                {hasUnsavedMetadata ? (
                  <span className="tag warn" data-testid="metadata-unsaved-badge">
                    Alterações não salvas
                  </span>
                ) : metadataSavedOnce ? (
                  <span className="tag good" data-testid="metadata-saved-badge">
                    Metadados salvos
                  </span>
                ) : (
                  <span className="tag" data-testid="metadata-draft-badge">
                    Rascunho determinístico
                  </span>
                )}
              </div>
            </div>

            {metadataLoading ? (
              <StatusMessage state="empty" testId="metadata-loading">
                Carregando rascunho de metadados…
              </StatusMessage>
            ) : (
              <>
                <label htmlFor={`metadata-title-${production.id}`} style={{ display: 'grid', gap: 4 }}>
                  <span className="row spread">
                    <span>Título para publicação</span>
                    <small className="muted" data-testid="metadata-title-counter">
                      {metadataTitle.length}/{MAX_METADATA_TITLE_LENGTH}
                    </small>
                  </span>
                  <input
                    id={`metadata-title-${production.id}`}
                    type="text"
                    maxLength={MAX_METADATA_TITLE_LENGTH}
                    aria-label="Título para publicação"
                    data-testid="metadata-title-input"
                    placeholder="Informe o título para publicação manual (até 100 caracteres)…"
                    value={metadataTitle}
                    onChange={e => setMetadataTitle(e.target.value)}
                  />
                </label>

                <label
                  htmlFor={`metadata-description-${production.id}`}
                  style={{ display: 'grid', gap: 4 }}
                >
                  <span className="row spread">
                    <span>Descrição para publicação</span>
                    <small
                      className={
                        composedDescriptionLength > MAX_METADATA_DESCRIPTION_LENGTH ? 'error' : 'muted'
                      }
                      data-testid="metadata-description-counter"
                    >
                      {metadataDescription.length}/{MAX_METADATA_DESCRIPTION_LENGTH}
                      {disclosures.length > 0
                        ? ` (${composedDescriptionLength}/${MAX_METADATA_DESCRIPTION_LENGTH} com divulgações obrigatórias)`
                        : ''}
                    </small>
                  </span>
                  <textarea
                    id={`metadata-description-${production.id}`}
                    rows={4}
                    maxLength={MAX_METADATA_DESCRIPTION_LENGTH}
                    aria-label="Descrição para publicação"
                    data-testid="metadata-description-input"
                    placeholder="Edite o texto da descrição; as divulgações obrigatórias abaixo são anexadas automaticamente…"
                    value={metadataDescription}
                    onChange={e => setMetadataDescription(e.target.value)}
                  />
                </label>

                <div
                  data-testid="metadata-sources-block"
                  style={{
                    padding: 10,
                    border: '1px solid var(--Line)',
                    background: 'var(--Sunken)',
                    display: 'grid',
                    gap: 4,
                  }}
                >
                  <span className="eyebrow">FONTES CITADAS (FIXAS, NÃO REMOVÍVEIS)</span>
                  {sources.length === 0 ? (
                    <small className="muted" data-testid="metadata-sources-empty">
                      Nenhuma fonte citada nas seções aprovadas deste roteiro.
                    </small>
                  ) : (
                    <ul
                      data-testid="metadata-sources-list"
                      style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 4, fontSize: 12 }}
                    >
                      {sources.map(src => (
                        <li key={src.id} style={{ overflowWrap: 'anywhere' }}>
                          <strong>{src.title || src.id}</strong> (<code>{src.id}</code>)
                          {src.url ? (
                            <>
                              {' '}
                              — <code>{src.url}</code>
                            </>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div
                  data-testid="metadata-disclosures-block"
                  style={{
                    padding: 10,
                    border: '1px solid var(--Line)',
                    background: 'var(--Sunken)',
                    display: 'grid',
                    gap: 4,
                  }}
                >
                  <span className="eyebrow warn">
                    DIVULGAÇÕES OBRIGATÓRIAS (FIXAS, NÃO REMOVÍVEIS)
                  </span>
                  <small className="muted">
                    Este rodapé obrigatório é preservado automaticamente na descrição salva e no{' '}
                    <code>metadata.json</code>:
                  </small>
                  {disclosures.length === 0 ? (
                    <small className="muted">
                      As divulgações obrigatórias de narração, licenças e{' '}
                      <code>publishable=false</code> são incluídas pelo backend ao salvar.
                    </small>
                  ) : (
                    <ul
                      data-testid="metadata-disclosures-list"
                      style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 4, fontSize: 12 }}
                    >
                      {disclosures.map((item, idx) => (
                        <li key={idx}>{item}</li>
                      ))}
                    </ul>
                  )}
                </div>

                {metadataError && (
                  <StatusMessage state="error" testId="metadata-error-alert">
                    {metadataError}
                  </StatusMessage>
                )}

                <div className="actions">
                  <button
                    type="button"
                    className="button primary"
                    data-testid="metadata-save-button"
                    disabled={!connection || metadataSaving}
                    onClick={() => void handleSaveMetadata()}
                  >
                    {metadataSaving ? 'Salvando metadados…' : 'Salvar metadados'}
                  </button>
                </div>
              </>
            )}
          </div>

          {/* (3) Lembrete claro + Botão Exportar pacote */}
          <div
            className="fact-item"
            role="note"
            aria-label="Lembrete para re-exportar o pacote"
            data-testid="publishing-reexport-reminder"
            data-needs-reexport={needsReexport ? 'true' : 'false'}
            style={{
              borderColor: needsReexport ? 'var(--Warn)' : 'var(--Line)',
              background: 'var(--Sunken)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
              flexWrap: 'wrap',
            }}
          >
            <div style={{ display: 'grid', gap: 4 }}>
              <span className={`eyebrow ${needsReexport ? 'warn' : ''}`}>
                3. GERAR OU ATUALIZAR PACOTE ZIP
              </span>
              <span>
                <strong>
                  {needsReexport
                    ? 'Alterações preparadas — exporte de novo para incluir a thumbnail/metadados no pacote.'
                    : 'Lembrete: exporte de novo para incluir a thumbnail/metadados no pacote.'}
                </strong>
              </span>
            </div>
            <button
              type="button"
              className="button primary"
              disabled={!connection || exportState === 'generating'}
              onClick={() => void handleExport()}
            >
              {exportState === 'generating' ? 'Gerando pacote…' : 'Exportar pacote'}
            </button>
          </div>

          {exportState === 'generating' && (
            <StatusMessage state="empty" role="status" ariaLive="polite">
              Gerando pacote de exportação…
            </StatusMessage>
          )}
          {exportState === 'ready' && (
            <StatusMessage state="success" tone="good" ariaLive="polite" testId="export-ready-status">
              {lastRepeated
                ? 'Pacote pronto (mesma chave de idempotência reutilizada, sem duplicar arquivo).'
                : 'Pacote pronto para download.'}
            </StatusMessage>
          )}
          {errorMessage && (
            <StatusMessage state="error" testId="export-error-alert">
              {errorMessage}
            </StatusMessage>
          )}

          {loadingList ? (
            <p className="muted" role="status">
              Carregando pacotes exportados…
            </p>
          ) : items.length === 0 ? (
            <p className="muted" data-testid="exports-empty">
              Nenhum pacote exportado para esta produção.
            </p>
          ) : (
            <ul
              data-testid="exports-list"
              style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 8 }}
            >
              {items.map(item => (
                <li
                  key={item.id}
                  data-testid={`export-item-${item.id}`}
                  style={{
                    padding: 10,
                    border: '1px solid var(--Line)',
                    background: 'var(--Sunken)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 12,
                    flexWrap: 'wrap',
                  }}
                >
                  <div style={{ display: 'grid', gap: 4, minWidth: 0 }}>
                    <strong style={{ overflowWrap: 'anywhere' }}>{item.relative_path}</strong>
                    <span className="muted" style={{ fontSize: 12 }}>
                      Tamanho: {formatExportBytes(item.size_bytes)} · SHA-256:{' '}
                      <code title={item.sha256}>{item.sha256.slice(0, 12)}…</code> · Data:{' '}
                      {formatExportDate(item.created_at)}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="button"
                    disabled={downloadingId === item.id}
                    onClick={() => void handleDownload(item)}
                  >
                    {downloadingId === item.id ? 'Baixando…' : 'Baixar ZIP'}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
