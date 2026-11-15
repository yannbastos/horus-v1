import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../../services/api';
import type { Connection } from '../../services/api';
import {
  fetchImportedSubtitleCues,
  fetchImportedVideoBlobUrl,
  getImportedProjectDetail,
  ImportConflictError,
  importProjectPackage,
  listImportedProjects,
} from '../../services/imports';
import type {
  ImportedProjectDetail,
  ImportedProjectSummary,
} from '../../services/imports';
import { nativeDesktop, selectZipPackage } from '../../platform/desktop';
import { StatusMessage } from '../../components/StatusMessage';
import { MediaPlayer, formatMediaSize } from '../../components/MediaPlayer';
import type { SubtitleCue } from '../../components/MediaPlayer';

function formatTimestamp(iso: string): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('pt-BR');
}

function getQcTagClass(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized === 'pass' || normalized === 'passed' || normalized === 'ok') return 'tag good';
  if (normalized === 'warn' || normalized === 'warning') return 'tag warn';
  if (normalized === 'fail' || normalized === 'failed' || normalized === 'error') return 'tag bad';
  return 'tag';
}

function getQcStatusLabel(status: string): string {
  switch (status.toLowerCase()) {
    case 'pass':
    case 'passed':
    case 'ok':
      return 'APROVADO NO QC';
    case 'warn':
    case 'warning':
      return 'ATENÇÃO NO QC';
    case 'fail':
    case 'failed':
    case 'error':
      return 'REPROVADO NO QC';
    case 'not_run':
      return 'QC NÃO EXECUTADO';
    default:
      return status.toUpperCase();
  }
}

export interface ImportedProjectsSectionProps {
  connection: Connection | null;
  pickZip?: () => Promise<string | null>;
}

/**
 * Seção de importação e consulta histórica de pacotes .zip na Biblioteca.
 * Degrada silenciosamente (oculta a ação e a lista) quando o backend responde 404 para /v1/imports.
 */
export function ImportedProjectsSection({ connection, pickZip }: ImportedProjectsSectionProps) {
  const [endpointAvailable, setEndpointAvailable] = useState<boolean | null>(null);
  const [imports, setImports] = useState<ImportedProjectSummary[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState('');

  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState('');
  const [importSuccess, setImportSuccess] = useState('');
  const [conflictExistingId, setConflictExistingId] = useState<string | null>(null);
  const [showPathPrompt, setShowPathPrompt] = useState(false);
  const [manualZipPath, setManualZipPath] = useState('');

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ImportedProjectDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');

  const [videoBlobUrl, setVideoBlobUrl] = useState<string | null>(null);
  const [subtitleCues, setSubtitleCues] = useState<SubtitleCue[]>([]);
  const [mediaLoading, setMediaLoading] = useState(false);
  const [mediaError, setMediaError] = useState('');
  const [showPlayer, setShowPlayer] = useState(true);

  const activeVideoBlobRef = useRef<string | null>(null);
  const detailControllerRef = useRef<AbortController | null>(null);

  const revokeVideoBlob = useCallback(() => {
    if (activeVideoBlobRef.current) {
      URL.revokeObjectURL(activeVideoBlobRef.current);
      activeVideoBlobRef.current = null;
    }
    setVideoBlobUrl(null);
  }, []);

  const loadImports = useCallback(async (conn: Connection) => {
    setListLoading(true);
    setListError('');
    try {
      const list = await listImportedProjects(conn);
      setEndpointAvailable(true);
      setImports(list);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 404) {
        setEndpointAvailable(false);
        setImports([]);
      } else {
        setEndpointAvailable(true);
        setListError(
          reason instanceof Error ? reason.message : 'Não foi possível carregar os projetos importados.'
        );
      }
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!connection) {
      setEndpointAvailable(null);
      setImports([]);
      setSelectedId(null);
      setDetail(null);
      revokeVideoBlob();
      return;
    }
    void loadImports(connection);
    return () => {
      detailControllerRef.current?.abort();
      revokeVideoBlob();
    };
  }, [connection, loadImports, revokeVideoBlob]);

  const openDetail = useCallback(
    async (importId: string) => {
      if (!connection) return;
      detailControllerRef.current?.abort();
      const controller = new AbortController();
      detailControllerRef.current = controller;

      setSelectedId(importId);
      setDetail(null);
      setDetailLoading(true);
      setDetailError('');
      setMediaLoading(false);
      setMediaError('');
      setSubtitleCues([]);
      setShowPlayer(true);
      revokeVideoBlob();

      try {
        const loaded = await getImportedProjectDetail(connection, importId, controller.signal);
        if (controller.signal.aborted) return;
        setDetail(loaded);
        setDetailLoading(false);

        setMediaLoading(true);
        try {
          const [blobUrl, cues] = await Promise.all([
            fetchImportedVideoBlobUrl(connection, loaded.video_path, controller.signal),
            fetchImportedSubtitleCues(connection, loaded.subtitles_path, controller.signal).catch(
              () => [] as SubtitleCue[]
            ),
          ]);
          if (controller.signal.aborted) {
            URL.revokeObjectURL(blobUrl);
            return;
          }
          activeVideoBlobRef.current = blobUrl;
          setVideoBlobUrl(blobUrl);
          setSubtitleCues(cues);
        } catch (mediaReason) {
          if (!controller.signal.aborted) {
            setMediaError(
              mediaReason instanceof Error
                ? mediaReason.message
                : 'Não foi possível carregar o vídeo e as legendas deste pacote.'
            );
          }
        } finally {
          if (!controller.signal.aborted) {
            setMediaLoading(false);
          }
        }
      } catch (reason) {
        if (!controller.signal.aborted) {
          setDetailError(
            reason instanceof Error
              ? reason.message
              : 'Não foi possível abrir os detalhes do projeto importado.'
          );
          setDetailLoading(false);
        }
      }
    },
    [connection, revokeVideoBlob]
  );

  async function submitImportPath(path: string) {
    if (!connection || importing) return;
    const trimmed = path.trim();
    if (!trimmed) {
      setImportError('Informe o caminho de um pacote .zip para importar.');
      return;
    }

    setImporting(true);
    setImportError('');
    setImportSuccess('');
    setConflictExistingId(null);

    try {
      const created = await importProjectPackage(connection, trimmed);
      setShowPathPrompt(false);
      setManualZipPath('');
      setImportSuccess(`Pacote "${created.title}" importado em modo somente consulta.`);
      await loadImports(connection);
      if (created.id) {
        await openDetail(created.id);
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 404) {
        setEndpointAvailable(false);
        setShowPathPrompt(false);
        return;
      }
      if (reason instanceof ImportConflictError) {
        setImportError(reason.message);
        setConflictExistingId(reason.existingId);
      } else {
        setImportError(
          reason instanceof Error ? reason.message : 'Não foi possível importar o pacote .zip.'
        );
      }
    } finally {
      setImporting(false);
    }
  }

  async function handleImportAction() {
    if (!connection || importing) return;
    setImportError('');
    setImportSuccess('');
    setConflictExistingId(null);

    if (pickZip || nativeDesktop) {
      try {
        const chooser = pickZip ?? selectZipPackage;
        const selected = await chooser();
        if (!selected) return;
        await submitImportPath(selected);
      } catch (reason) {
        setImportError(
          reason instanceof Error ? reason.message : 'Não foi possível abrir o seletor de pacote .zip.'
        );
      }
      return;
    }

    setShowPathPrompt(prev => !prev);
  }

  if (!connection || endpointAvailable === false || endpointAvailable === null) {
    return null;
  }

  return (
    <section
      className="card"
      aria-label="Projetos importados"
      data-testid="imported-projects-section"
      style={{ marginTop: 'var(--Space5)', display: 'flex', flexDirection: 'column', gap: 'var(--Space4)' }}
    >
      <div className="row spread" style={{ flexWrap: 'wrap', gap: 'var(--Space3)' }}>
        <div>
          <p className="eyebrow">HISTÓRICO DE PACOTES EXPORTADOS</p>
          <h2>Projetos importados</h2>
          <p className="muted">
            Consulte pacotes .zip exportados anteriormente com vídeo, legendas, roteiro, QC e licenças originais.
          </p>
        </div>
        <div className="actions">
          <button
            type="button"
            className="button primary"
            disabled={importing}
            onClick={() => void handleImportAction()}
          >
            {importing ? 'Importando…' : 'Importar pacote'}
          </button>
          <button
            type="button"
            className="button"
            disabled={listLoading || importing}
            onClick={() => void loadImports(connection)}
          >
            Atualizar importados
          </button>
        </div>
      </div>

      {showPathPrompt && !nativeDesktop && !pickZip && (
        <form
          className="card sunken"
          onSubmit={event => {
            event.preventDefault();
            void submitImportPath(manualZipPath);
          }}
          style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space3)' }}
        >
          <label>
            Caminho do pacote (.zip)
            <input
              type="text"
              aria-label="Caminho do pacote (.zip)"
              placeholder="Ex.: C:\\exports\\pacote-projeto.zip"
              value={manualZipPath}
              disabled={importing}
              onChange={event => setManualZipPath(event.target.value)}
            />
          </label>
          <div className="actions">
            <button type="submit" className="button primary" disabled={importing}>
              Confirmar importação
            </button>
            <button
              type="button"
              className="button"
              disabled={importing}
              onClick={() => {
                setShowPathPrompt(false);
                setManualZipPath('');
              }}
            >
              Cancelar
            </button>
          </div>
        </form>
      )}

      {importing && (
        <StatusMessage state="loading" testId="import-loading-status">
          Importando e validando pacote .zip…
        </StatusMessage>
      )}

      {importError && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space2)' }}>
          <StatusMessage state="error" testId="import-error-status">
            {importError}
          </StatusMessage>
          {conflictExistingId && (
            <div className="actions">
              <button
                type="button"
                className="button"
                onClick={() => void openDetail(conflictExistingId)}
              >
                Abrir projeto já importado
              </button>
            </div>
          )}
        </div>
      )}

      {importSuccess && (
        <StatusMessage state="success" tone="good" testId="import-success-status">
          {importSuccess}
        </StatusMessage>
      )}

      {listLoading && (
        <StatusMessage state="loading">Carregando projetos importados…</StatusMessage>
      )}

      {listError && <StatusMessage state="error">{listError}</StatusMessage>}

      {!listLoading && !listError && imports.length === 0 && (
        <div className="card sunken">
          <StatusMessage state="empty" role="status">
            Nenhum projeto importado ainda. Use &quot;Importar pacote&quot; para consultar um arquivo .zip exportado.
          </StatusMessage>
        </div>
      )}

      {imports.length > 0 && (
        <ul
          className="media-list imported-projects-list"
          aria-label="Lista de projetos importados"
          style={{ listStyle: 'none', margin: 0, padding: 0 }}
        >
          {imports.map(item => {
            const isSelected = selectedId === item.id;
            return (
              <li key={item.id} style={{ display: 'flex' }}>
                <button
                  type="button"
                  className={`card media-item ${isSelected ? 'selected' : ''}`}
                  aria-current={isSelected ? 'true' : undefined}
                  style={{ width: '100%' }}
                  onClick={() => void openDetail(item.id)}
                >
                  <div className="row spread" style={{ gap: 'var(--Space2)', flexWrap: 'wrap' }}>
                    <strong>{item.title}</strong>
                    <span className="tag warn">Importado — somente consulta</span>
                  </div>
                  <span className="muted">
                    Importado em: {formatTimestamp(item.imported_at)}
                    {item.production_id ? ` · Produção original: ${item.production_id}` : ''}
                    {item.horus_version ? ` · Horus ${item.horus_version}` : ''}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {detailLoading && (
        <StatusMessage state="loading">Carregando detalhes históricos do projeto importado…</StatusMessage>
      )}

      {detailError && <StatusMessage state="error">{detailError}</StatusMessage>}

      {detail && (
        <article
          className="card sunken"
          aria-label={`Detalhes do projeto importado ${detail.title}`}
          data-testid="imported-project-detail"
          style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space4)' }}
        >
          <div className="row spread" style={{ flexWrap: 'wrap', gap: 'var(--Space3)' }}>
            <div>
              <div className="row" style={{ gap: 'var(--Space2)', flexWrap: 'wrap' }}>
                <span className="tag warn">Importado — somente consulta</span>
                {detail.manifest.horus_version && (
                  <span className="tag">Horus {detail.manifest.horus_version}</span>
                )}
              </div>
              <h3 style={{ marginTop: 'var(--Space2)' }}>{detail.title}</h3>
            </div>
            <button
              type="button"
              className="button"
              onClick={() => {
                detailControllerRef.current?.abort();
                setSelectedId(null);
                setDetail(null);
                revokeVideoBlob();
              }}
            >
              Fechar detalhes
            </button>
          </div>

          <div
            className="fact-item"
            role="note"
            aria-label="Aviso de modo somente consulta"
            data-testid="imported-readonly-notice"
          >
            <p className="eyebrow">MODO SOMENTE CONSULTA</p>
            <p className="muted">
              Este projeto é um registro histórico importado: não pode ser aprovado, reexportado nem publicado.
            </p>
          </div>

          {mediaLoading && (
            <StatusMessage state="loading">Carregando vídeo e legendas do pacote…</StatusMessage>
          )}

          {mediaError && <StatusMessage state="error">{mediaError}</StatusMessage>}

          {videoBlobUrl && showPlayer && (
            <MediaPlayer
              item={{
                id: detail.id,
                name: `${detail.title}.mp4`,
                url: videoBlobUrl,
                origin: 'registered',
              }}
              initialSubtitleCues={subtitleCues}
              initialSubtitleLabel="Legendas originais do pacote"
              onClose={() => setShowPlayer(false)}
              onError={msg => setMediaError(msg)}
            />
          )}

          {videoBlobUrl && !showPlayer && (
            <div className="actions">
              <button type="button" className="button" onClick={() => setShowPlayer(true)}>
                Reabrir player de vídeo
              </button>
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 'var(--Space4)' }}>
            <section className="card" aria-label="Roteiro original (histórico)">
              <p className="eyebrow">ROTEIRO ORIGINAL (HISTÓRICO)</p>
              <h4>{detail.script.title}</h4>
              {detail.script.sections.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space2)', marginTop: 'var(--Space2)' }}>
                  {detail.script.sections.map((section, idx) => (
                    <div key={`${section.heading}-${idx}`} className="fact-item">
                      <strong>{section.heading}</strong>
                      <p className="muted">{section.narration}</p>
                    </div>
                  ))}
                </div>
              ) : detail.script.raw_text ? (
                <pre style={{ whiteSpace: 'pre-wrap', marginTop: 'var(--Space2)' }}>{detail.script.raw_text}</pre>
              ) : (
                <p className="muted">Roteiro original não incluído neste pacote.</p>
              )}
            </section>

            <section className="card" aria-label="QC original (histórico)">
              <p className="eyebrow">QC ORIGINAL (HISTÓRICO)</p>
              <div className="row" style={{ gap: 'var(--Space2)', marginTop: 'var(--Space2)', flexWrap: 'wrap' }}>
                <span className={getQcTagClass(detail.qc.status)}>{getQcStatusLabel(detail.qc.status)}</span>
              </div>
              {detail.qc.reason && (
                <p className="muted" style={{ marginTop: 'var(--Space2)' }}>{detail.qc.reason}</p>
              )}
              {detail.qc.checks.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space2)', marginTop: 'var(--Space2)' }}>
                  {detail.qc.checks.map(check => (
                    <div key={check.id} className="fact-item">
                      <div className="row spread">
                        <strong>{check.id}</strong>
                        <span className={getQcTagClass(check.status)}>{check.status.toUpperCase()}</span>
                      </div>
                      {check.detail && <p className="muted">{check.detail}</p>}
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="card" aria-label="Licenças originais (histórico)">
              <p className="eyebrow">LICENÇAS E PROVENIÊNCIA (HISTÓRICO)</p>
              <p className="muted" style={{ marginTop: 'var(--Space2)' }}>
                Registro de licenças original do pacote (publicação bloqueada neste modo).
              </p>
              {detail.licenses.items.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space2)', marginTop: 'var(--Space2)' }}>
                  {detail.licenses.items.map(lic => (
                    <div key={lic.id} className="fact-item">
                      <div className="row spread" style={{ gap: 'var(--Space2)', flexWrap: 'wrap' }}>
                        <strong>{lic.title}</strong>
                        <span className="tag">{lic.license}</span>
                      </div>
                      <span className="muted">
                        Status original: {lic.status}
                        {lic.source_url ? ` · ${lic.source_url}` : ''}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted" style={{ marginTop: 'var(--Space2)' }}>
                  {detail.licenses.history_count > 0
                    ? `${detail.licenses.history_count} registro(s) de revisão de licença preservados no pacote.`
                    : 'Nenhum item de licença detalhado registrado neste pacote.'}
                </p>
              )}
            </section>

            <section className="card" aria-label="Revisão e manifesto originais (histórico)">
              <p className="eyebrow">REVISÃO E MANIFESTO (HISTÓRICO)</p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space2)', marginTop: 'var(--Space2)' }}>
                <div className="fact-item">
                  <span className="eyebrow">REVISÃO ORIGINAL</span>
                  <span className="muted">
                    Decisão da produção: {detail.review.production_decision}
                    {detail.review.production_reviewed_at
                      ? ` · ${formatTimestamp(detail.review.production_reviewed_at)}`
                      : ''}
                  </span>
                  {detail.review.script_status && (
                    <span className="muted">
                      Roteiro: {detail.review.script_status}
                      {detail.review.script_version != null ? ` (v${detail.review.script_version})` : ''}
                    </span>
                  )}
                </div>
                <div className="fact-item">
                  <span className="eyebrow">MANIFESTO DO PACOTE</span>
                  <span className="muted">
                    Produção original: {detail.manifest.production_id || '—'}
                    {detail.manifest.created_at ? ` · Exportado em: ${formatTimestamp(detail.manifest.created_at)}` : ''}
                  </span>
                  <span className="muted">
                    Schema v{detail.manifest.schema_version ?? '—'} · Metadados sensíveis redigidos:{' '}
                    {detail.manifest.metadata_redacted ? 'Sim' : 'Não'}
                  </span>
                  {detail.manifest.files.length > 0 && (
                    <span className="muted">
                      {detail.manifest.files.length} arquivo(s):{' '}
                      {detail.manifest.files
                        .map(f => `${f.name} (${formatMediaSize(f.size_bytes)})`)
                        .join(', ')}
                    </span>
                  )}
                </div>
              </div>
            </section>
          </div>
        </article>
      )}
    </section>
  );
}
