import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '../../services/api';
import type { Connection } from '../../services/api';
import type {
  ReviewComment,
  ReviewCommentStatus,
  ReviewCommentTrailAction,
} from '../../services/production';
import {
  createProductionComment,
  formatProductionError,
  formatTimecode,
  listProductionComments,
  reopenProductionComment,
  resolveProductionComment,
} from '../../services/production';
import { StatusMessage } from '../../components/StatusMessage';

const CONTROL_CHARS_TEXT = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/;
const CONTROL_CHARS_AUTHOR = /[\x00-\x1f\x7f]/;

const TRAIL_ACTION_LABEL: Record<ReviewCommentTrailAction, string> = {
  created: 'Criado',
  resolved: 'Resolvido',
  reopened: 'Reaberto',
};

function shortHash(sha256: string): string {
  return sha256.length > 12 ? `${sha256.slice(0, 12)}…` : sha256;
}

function formatCommentDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export interface ReviewCommentsPanelProps {
  connection: Connection;
  productionId: string;
  currentVersionSha256: string;
  currentTimeSeconds: number;
  onSeekTimecode: (seconds: number, reason?: string) => void;
  onCaptureCurrentVideoTime: () => number;
}

export default function ReviewCommentsPanel({
  connection,
  productionId,
  currentVersionSha256,
  currentTimeSeconds,
  onSeekTimecode,
  onCaptureCurrentVideoTime,
}: ReviewCommentsPanelProps) {
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [counts, setCounts] = useState<{ open: number; resolved: number }>({
    open: 0,
    resolved: 0,
  });
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [knownVersions, setKnownVersions] = useState<string[]>([currentVersionSha256]);

  const [statusFilter, setStatusFilter] = useState<'all' | ReviewCommentStatus>('all');
  const [versionFilter, setVersionFilter] = useState<'all' | string>('all');

  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState('');

  const [draftTimeSeconds, setDraftTimeSeconds] = useState<string>(() =>
    String(Math.round(Math.max(0, currentTimeSeconds) * 1000) / 1000)
  );
  const [draftAuthor, setDraftAuthor] = useState('Operador');
  const [draftText, setDraftText] = useState('');
  const [createIdempotencyKey, setCreateIdempotencyKey] = useState(() => crypto.randomUUID());
  const [submittingCreate, setSubmittingCreate] = useState(false);
  const [actionError, setActionError] = useState('');
  const [actionSuccess, setActionSuccess] = useState('');

  const [activeDecisionId, setActiveDecisionId] = useState<string | null>(null);
  const [decisionNote, setDecisionNote] = useState('');
  const [decisionIdempotencyKey, setDecisionIdempotencyKey] = useState(() => crypto.randomUUID());
  const [submittingDecisionId, setSubmittingDecisionId] = useState<string | null>(null);

  useEffect(() => {
    const rounded = Math.round(Math.max(0, currentTimeSeconds) * 1000) / 1000;
    setDraftTimeSeconds(String(rounded));
  }, [currentTimeSeconds]);

  useEffect(() => {
    setKnownVersions(prev =>
      prev.includes(currentVersionSha256) ? prev : [currentVersionSha256, ...prev]
    );
  }, [currentVersionSha256]);

  const mergeKnownVersions = useCallback(
    (items: ReviewComment[]) => {
      setKnownVersions(prev => {
        const next = new Set([currentVersionSha256, ...prev]);
        for (const item of items) {
          if (item.artifact_sha256) next.add(item.artifact_sha256);
        }
        return Array.from(next);
      });
    },
    [currentVersionSha256]
  );

  const fetchComments = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setLoadError('');
      try {
        const page = await listProductionComments(
          connection,
          productionId,
          {
            status: statusFilter === 'all' ? undefined : statusFilter,
            artifact_sha256: versionFilter === 'all' ? undefined : versionFilter,
            limit: 50,
          },
          signal
        );
        if (signal?.aborted) return;
        setComments(page.items);
        setCounts(page.counts);
        setNextAfter(page.next_after);
        mergeKnownVersions(page.items);
      } catch (reason) {
        if (signal?.aborted) return;
        if (reason instanceof ApiError && reason.status === 404) {
          // Backend sem suporte a comentários ou produção ausente (já sinalizada por ProductionRun)
          setComments([]);
          setCounts({ open: 0, resolved: 0 });
          setNextAfter(null);
          mergeKnownVersions([]);
          return;
        }
        setLoadError(formatProductionError(reason));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [connection, productionId, statusFilter, versionFilter, mergeKnownVersions]
  );

  useEffect(() => {
    const controller = new AbortController();
    void fetchComments(controller.signal);
    return () => controller.abort();
  }, [fetchComments]);

  async function handleLoadMore() {
    if (!nextAfter || loadingMore) return;
    setLoadingMore(true);
    setLoadError('');
    try {
      const page = await listProductionComments(connection, productionId, {
        status: statusFilter === 'all' ? undefined : statusFilter,
        artifact_sha256: versionFilter === 'all' ? undefined : versionFilter,
        limit: 50,
        after: nextAfter,
      });
      setComments(prev => [...prev, ...page.items]);
      setCounts(page.counts);
      setNextAfter(page.next_after);
      mergeKnownVersions(page.items);
    } catch (reason) {
      setLoadError(formatProductionError(reason));
    } finally {
      setLoadingMore(false);
    }
  }

  function handleSyncFromVideo() {
    const captured = onCaptureCurrentVideoTime();
    const rounded = Math.round(Math.max(0, captured) * 1000) / 1000;
    setDraftTimeSeconds(String(rounded));
    setActionError('');
  }

  async function handleCreateComment(event: React.FormEvent) {
    event.preventDefault();
    setActionError('');
    setActionSuccess('');

    const rawSec = Number.parseFloat(draftTimeSeconds);
    if (!Number.isFinite(rawSec) || rawSec < 0 || rawSec > 86400) {
      setActionError('Informe um timecode válido entre 0 e 86400 segundos.');
      return;
    }
    const trimmedAuthor = draftAuthor.trim();
    if (
      trimmedAuthor.length < 1 ||
      trimmedAuthor.length > 80 ||
      CONTROL_CHARS_AUTHOR.test(trimmedAuthor)
    ) {
      setActionError('Informe um autor válido (1 a 80 caracteres, sem caracteres de controle).');
      return;
    }
    const trimmedText = draftText.trim();
    if (trimmedText.length < 1 || trimmedText.length > 2000 || CONTROL_CHARS_TEXT.test(trimmedText)) {
      setActionError('O comentário deve ter entre 1 e 2000 caracteres válidos.');
      return;
    }

    setSubmittingCreate(true);
    try {
      const created = await createProductionComment(
        connection,
        productionId,
        {
          artifact_sha256: currentVersionSha256,
          at_seconds: Math.round(rawSec * 1000) / 1000,
          text: trimmedText,
          author: trimmedAuthor,
        },
        createIdempotencyKey
      );
      setDraftText('');
      setCreateIdempotencyKey(crypto.randomUUID());
      setActionSuccess(
        `Comentário registrado em ${formatTimecode(created.at_seconds)} para a versão ${shortHash(
          created.artifact_sha256
        )}.`
      );
      await fetchComments();
    } catch (reason) {
      setActionError(formatProductionError(reason));
    } finally {
      setSubmittingCreate(false);
    }
  }

  function openDecisionForm(commentId: string) {
    setActiveDecisionId(commentId);
    setDecisionNote('');
    setDecisionIdempotencyKey(crypto.randomUUID());
    setActionError('');
    setActionSuccess('');
  }

  async function handleDecision(comment: ReviewComment, action: 'resolve' | 'reopen') {
    setActionError('');
    setActionSuccess('');

    const trimmedAuthor = draftAuthor.trim();
    if (
      trimmedAuthor.length < 1 ||
      trimmedAuthor.length > 80 ||
      CONTROL_CHARS_AUTHOR.test(trimmedAuthor)
    ) {
      setActionError('Informe um autor válido (1 a 80 caracteres) antes de decidir.');
      return;
    }
    const trimmedNote = decisionNote.trim();
    if (trimmedNote.length > 500 || (trimmedNote && CONTROL_CHARS_TEXT.test(trimmedNote))) {
      setActionError('A nota da decisão deve ter no máximo 500 caracteres válidos.');
      return;
    }

    setSubmittingDecisionId(comment.id);
    try {
      const fn = action === 'resolve' ? resolveProductionComment : reopenProductionComment;
      await fn(
        connection,
        productionId,
        comment.id,
        {
          author: trimmedAuthor,
          ...(trimmedNote ? { note: trimmedNote } : {}),
        },
        decisionIdempotencyKey
      );
      setActiveDecisionId(null);
      setDecisionNote('');
      setDecisionIdempotencyKey(crypto.randomUUID());
      setActionSuccess(
        action === 'resolve'
          ? `Comentário em ${formatTimecode(comment.at_seconds)} marcado como resolvido.`
          : `Comentário em ${formatTimecode(comment.at_seconds)} reaberto.`
      );
      await fetchComments();
    } catch (reason) {
      setActionError(formatProductionError(reason));
    } finally {
      setSubmittingDecisionId(null);
    }
  }

  return (
    <div
      className="info-block"
      role="region"
      aria-label="Comentários por timecode e versão"
      data-testid="review-comments-panel"
      style={{ gap: '12px' }}
    >
      <div className="row spread" style={{ flexWrap: 'wrap', gap: '8px' }}>
        <div>
          <span className="eyebrow accent">
            COMENTÁRIOS POR TIMECODE E VERSÃO (HOR-088)
          </span>
          <small className="muted" style={{ display: 'block', marginTop: '2px' }}>
            Versão em revisão: <code>{shortHash(currentVersionSha256)}</code> · Persistência auditável
            com trilha append-only (não altera a aprovação final).
          </small>
        </div>
        <div className="row" style={{ gap: '6px', flexWrap: 'wrap' }}>
          <span className="tag warn">ABERTOS: {counts.open}</span>
          <span className="tag good">RESOLVIDOS: {counts.resolved}</span>
        </div>
      </div>

      <form
        onSubmit={e => void handleCreateComment(e)}
        aria-label="Adicionar comentário por timecode"
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
          padding: '10px',
          background: 'var(--Background)',
          border: '1px solid var(--Line)',
        }}
      >
        <div className="row spread" style={{ flexWrap: 'wrap', gap: '8px' }}>
          <span className="eyebrow">
            NOVO COMENTÁRIO NA VERSÃO EM REVISÃO ({shortHash(currentVersionSha256)})
          </span>
          <button
            className="button sm"
            type="button"
            onClick={handleSyncFromVideo}
          >
            Capturar tempo atual do player
          </button>
        </div>

        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '10px',
            alignItems: 'flex-end',
          }}
        >
          <label style={{ width: '150px', flexShrink: 0 }}>
            Timecode (s)
            <input
              aria-label="Timecode do comentário em segundos"
              type="number"
              min="0"
              max="86400"
              step="0.01"
              value={draftTimeSeconds}
              onChange={e => setDraftTimeSeconds(e.target.value)}
            />
          </label>
          <label style={{ width: '180px', flexShrink: 0 }}>
            Autor / revisor
            <input
              aria-label="Autor do comentário"
              type="text"
              maxLength={80}
              value={draftAuthor}
              onChange={e => setDraftAuthor(e.target.value)}
            />
          </label>
          <label style={{ flex: '1 1 240px', minWidth: 0 }}>
            Comentário (1..2000 caracteres)
            <input
              aria-label="Texto do comentário por timecode"
              type="text"
              maxLength={2000}
              placeholder="Descreva o ajuste ou observação para este instante do vídeo…"
              value={draftText}
              onChange={e => setDraftText(e.target.value)}
            />
          </label>
          <div className="actions">
            <button
              className="button primary"
              type="submit"
              disabled={submittingCreate}
            >
              {submittingCreate ? 'Salvando…' : 'Adicionar comentário'}
            </button>
          </div>
        </div>
      </form>

      {actionError && <StatusMessage state="error">{actionError}</StatusMessage>}
      {actionSuccess && (
        <StatusMessage state="success" tone="good">
          {actionSuccess}
        </StatusMessage>
      )}

      <div
        className="row spread"
        style={{ flexWrap: 'wrap', gap: '10px', alignItems: 'center' }}
      >
        <div className="row" role="group" aria-label="Filtrar comentários por status" style={{ gap: '6px', flexWrap: 'wrap' }}>
          <button
            type="button"
            className={`button sm ${statusFilter === 'all' ? 'active' : ''}`}
            aria-pressed={statusFilter === 'all'}
            onClick={() => setStatusFilter('all')}
          >
            Todos ({counts.open + counts.resolved})
          </button>
          <button
            type="button"
            className={`button sm ${statusFilter === 'open' ? 'active' : ''}`}
            aria-pressed={statusFilter === 'open'}
            onClick={() => setStatusFilter('open')}
          >
            Abertos ({counts.open})
          </button>
          <button
            type="button"
            className={`button sm ${statusFilter === 'resolved' ? 'active' : ''}`}
            aria-pressed={statusFilter === 'resolved'}
            onClick={() => setStatusFilter('resolved')}
          >
            Resolvidos ({counts.resolved})
          </button>
        </div>

        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', flexDirection: 'row' }}>
          <span className="eyebrow" style={{ whiteSpace: 'nowrap' }}>
            Versão:
          </span>
          <select
            aria-label="Filtrar comentários por versão do vídeo"
            value={versionFilter}
            onChange={e => setVersionFilter(e.target.value)}
            style={{ width: 'auto', minWidth: '220px' }}
          >
            <option value="all">Todas as versões</option>
            {knownVersions.map(sha => (
              <option key={sha} value={sha}>
                {sha === currentVersionSha256
                  ? `Versão em revisão (${shortHash(sha)})`
                  : `Outra versão (${shortHash(sha)})`}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading && (
        <StatusMessage state="loading">Carregando comentários de revisão…</StatusMessage>
      )}

      {!loading && loadError && <StatusMessage state="error">{loadError}</StatusMessage>}

      {!loading && !loadError && comments.length === 0 && (
        <StatusMessage state="empty">
          Nenhum comentário encontrado para os filtros selecionados.
        </StatusMessage>
      )}

      {!loading && !loadError && comments.length > 0 && (
        <div
          role="list"
          aria-label="Lista de comentários por timecode"
          style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}
        >
          {comments.map(comment => {
            const isCurrentVersion = comment.artifact_sha256 === currentVersionSha256;
            const isDeciding = activeDecisionId === comment.id;
            const isBusy = submittingDecisionId === comment.id;
            return (
              <div
                key={comment.id}
                role="listitem"
                className="fact-item"
                style={{ background: 'var(--Background)', gap: '8px' }}
                data-comment-id={comment.id}
                data-comment-version={isCurrentVersion ? 'current' : 'other'}
              >
                <div className="row spread" style={{ flexWrap: 'wrap', gap: '8px' }}>
                  <div className="row" style={{ gap: '8px', flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      className="button sm"
                      aria-label={`Ir para comentário em ${formatTimecode(comment.at_seconds)} — ${comment.text}`}
                      onClick={() =>
                        onSeekTimecode(
                          comment.at_seconds,
                          `Comentário ${formatTimecode(comment.at_seconds)}`
                        )
                      }
                    >
                      {formatTimecode(comment.at_seconds)}
                    </button>
                    <span className={`tag ${comment.status === 'open' ? 'warn' : 'good'}`}>
                      {comment.status === 'open' ? 'ABERTO' : 'RESOLVIDO'}
                    </span>
                    <span
                      className={`tag ${isCurrentVersion ? 'good' : 'bad'}`}
                      title={`SHA-256: ${comment.artifact_sha256}`}
                    >
                      {isCurrentVersion
                        ? `Versão em revisão (${shortHash(comment.artifact_sha256)})`
                        : `Outra versão (${shortHash(comment.artifact_sha256)})`}
                    </span>
                  </div>

                  <div>
                    {comment.status === 'open' ? (
                      <button
                        type="button"
                        className="button sm"
                        disabled={isBusy}
                        onClick={() => openDecisionForm(comment.id)}
                      >
                        Resolver
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="button sm"
                        disabled={isBusy}
                        onClick={() => openDecisionForm(comment.id)}
                      >
                        Reabrir
                      </button>
                    )}
                  </div>
                </div>

                {!isCurrentVersion && (
                  <small className="error" role="note">
                    Atenção: este comentário pertence a outra versão do vídeo (SHA-256{' '}
                    <code>{shortHash(comment.artifact_sha256)}</code>), diferente da versão atual em
                    revisão.
                  </small>
                )}

                <p style={{ margin: 0, overflowWrap: 'anywhere' }}>{comment.text}</p>

                <small className="muted">
                  Autor: <strong>{comment.author}</strong> ({comment.author_kind}) ·{' '}
                  {formatCommentDate(comment.created_at)}
                </small>

                {isDeciding && (
                  <div
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px',
                      padding: '8px',
                      background: 'var(--Sunken)',
                      border: '1px solid var(--Line)',
                    }}
                  >
                    <label>
                      Nota da decisão (opcional, até 500 caracteres)
                      <input
                        aria-label={`Nota para ${comment.status === 'open' ? 'resolver' : 'reabrir'} comentário`}
                        type="text"
                        maxLength={500}
                        placeholder="Ex.: legenda ajustada na nova renderização"
                        value={decisionNote}
                        onChange={e => setDecisionNote(e.target.value)}
                      />
                    </label>
                    <div className="actions" style={{ gap: '8px' }}>
                      <button
                        type="button"
                        className="button sm primary"
                        disabled={isBusy}
                        onClick={() =>
                          void handleDecision(
                            comment,
                            comment.status === 'open' ? 'resolve' : 'reopen'
                          )
                        }
                      >
                        {isBusy
                          ? 'Enviando…'
                          : comment.status === 'open'
                            ? 'Confirmar resolução'
                            : 'Confirmar reabertura'}
                      </button>
                      <button
                        type="button"
                        className="button sm"
                        disabled={isBusy}
                        onClick={() => setActiveDecisionId(null)}
                      >
                        Cancelar
                      </button>
                    </div>
                  </div>
                )}

                <div
                  aria-label="Trilha de decisões do comentário"
                  style={{
                    paddingTop: '6px',
                    borderTop: '1px solid var(--Line)',
                  }}
                >
                  <span className="eyebrow" style={{ display: 'block', marginBottom: '4px' }}>
                    TRILHA DE DECISÕES ({comment.trail.length})
                  </span>
                  <ol
                    style={{
                      margin: 0,
                      paddingLeft: '18px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '2px',
                    }}
                  >
                    {comment.trail.map((entry, idx) => (
                      <li key={`${entry.action}-${entry.at}-${idx}`}>
                        <small className="muted">
                          <strong>{TRAIL_ACTION_LABEL[entry.action] ?? entry.action}</strong> por{' '}
                          {entry.author} em {formatCommentDate(entry.at)}
                          {entry.note ? ` — "${entry.note}"` : ''}
                        </small>
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!loading && nextAfter && (
        <div className="actions">
          <button
            type="button"
            className="button sm"
            disabled={loadingMore}
            onClick={() => void handleLoadMore()}
          >
            {loadingMore ? 'Carregando…' : 'Carregar mais comentários'}
          </button>
        </div>
      )}
    </div>
  );
}
