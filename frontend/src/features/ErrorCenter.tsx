import { useCallback, useEffect, useRef, useState } from 'react';
import { StatusMessage } from '../components/StatusMessage';
import { nativeDesktop } from '../platform/desktop';
import type { Connection } from '../services/api';
import {
  ERROR_KIND_LABELS,
  formatErrorKind,
  formatErrorStage,
  formatErrorStatus,
  getErrorsSummary,
  listErrors,
} from '../services/errorCenter';
import type {
  ErrorCenterItem,
  ErrorCenterSummaryResponse,
} from '../services/errorCenter';

const PAGE_LIMIT = 20;

function statusTagClass(status: string): string {
  if (status === 'failed') return 'tag bad';
  if (status === 'blocked' || status === 'interrupted') return 'tag warn';
  return 'tag';
}

function formatDateTime(iso: string): string {
  if (!iso) return '—';
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

function formatDuration(seconds: number | null | undefined): string | null {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return null;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins} min ${secs} s`;
}

function getNavigateLabel(refKind: string): string {
  if (refKind === 'production') return 'Abrir produção';
  if (refKind === 'workflow') return 'Abrir workflow';
  if (refKind === 'job') return 'Abrir tarefa';
  return 'Abrir item';
}

function toSinceIso(dateValue: string): string | undefined {
  return dateValue ? `${dateValue}T00:00:00Z` : undefined;
}

function toUntilIso(dateValue: string): string | undefined {
  return dateValue ? `${dateValue}T23:59:59Z` : undefined;
}

export default function ErrorCenter({
  connection,
  onNavigateItem,
  focusNonce = 0,
}: {
  connection: Connection | null;
  onNavigateItem?: (item: ErrorCenterItem) => void;
  focusNonce?: number;
}) {
  const sectionRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (focusNonce > 0) {
      sectionRef.current?.focus();
    }
  }, [focusNonce]);
  const [summary, setSummary] = useState<ErrorCenterSummaryResponse | null>(null);
  const [items, setItems] = useState<ErrorCenterItem[]>([]);
  const [nextAfter, setNextAfter] = useState<string | null>(null);

  const [kindFilter, setKindFilter] = useState('');
  const [stageFilter, setStageFilter] = useState('');
  const [codeFilter, setCodeFilter] = useState('');
  const [sinceDate, setSinceDate] = useState('');
  const [untilDate, setUntilDate] = useState('');

  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const [refreshNonce, setRefreshNonce] = useState(0);

  const hasActiveFilters = Boolean(
    kindFilter || stageFilter || codeFilter || sinceDate || untilDate,
  );

  const clearFilters = useCallback(() => {
    setKindFilter('');
    setStageFilter('');
    setCodeFilter('');
    setSinceDate('');
    setUntilDate('');
  }, []);

  useEffect(() => {
    if (!connection) {
      setSummary(null);
      setItems([]);
      setNextAfter(null);
      setError('');
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError('');

    const since = toSinceIso(sinceDate);
    const until = toUntilIso(untilDate);

    void Promise.all([
      getErrorsSummary(connection, { since, until, signal: controller.signal }),
      listErrors(connection, {
        limit: PAGE_LIMIT,
        kind: kindFilter || undefined,
        stage: stageFilter || undefined,
        code: codeFilter || undefined,
        since,
        until,
        signal: controller.signal,
      }),
    ])
      .then(([summaryRes, listRes]) => {
        if (controller.signal.aborted) return;
        setSummary(summaryRes);
        setItems(listRes.items);
        setNextAfter(listRes.next_after);
        setAnnouncement(
          `${listRes.items.length} ${listRes.items.length === 1 ? 'falha exibida' : 'falhas exibidas'} de ${summaryRes.total} no período.`,
        );
      })
      .catch(reason => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [connection, kindFilter, stageFilter, codeFilter, sinceDate, untilDate, refreshNonce]);

  const handleLoadMore = useCallback(async () => {
    if (!connection || !nextAfter || loadingMore) return;
    setLoadingMore(true);
    setError('');
    try {
      const res = await listErrors(connection, {
        limit: PAGE_LIMIT,
        after: nextAfter,
        kind: kindFilter || undefined,
        stage: stageFilter || undefined,
        code: codeFilter || undefined,
        since: toSinceIso(sinceDate),
        until: toUntilIso(untilDate),
      });
      setItems(prev => [...prev, ...res.items]);
      setNextAfter(res.next_after);
      setAnnouncement(`Mais ${res.items.length} falhas carregadas.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoadingMore(false);
    }
  }, [connection, nextAfter, loadingMore, kindFilter, stageFilter, codeFilter, sinceDate, untilDate]);

  if (!connection) {
    return (
      <section ref={sectionRef} id="error-center" className="error-center" aria-labelledby="error-center-heading" tabIndex={-1}>
        <div className="error-center-header">
          <div>
            <p className="eyebrow accent">DIAGNÓSTICO AGREGADO</p>
            <h2 id="error-center-heading">Central de erros</h2>
          </div>
        </div>
        <StatusMessage state="empty" role="status" testId="error-center-disconnected">
          {nativeDesktop
            ? 'Backend local indisponível. Use RECONECTAR BACKEND em Ajustes para consultar a Central de erros.'
            : 'Conecte o backend local em Ajustes para consultar a Central de erros.'}
        </StatusMessage>
      </section>
    );
  }

  const stageOptions = Array.from(
    new Set([
      ...(summary?.by_stage.map(entry => entry.stage) ?? []),
      ...(stageFilter ? [stageFilter] : []),
    ]),
  );

  const codeOptions = summary?.by_code ?? [];

  return (
    <section
      ref={sectionRef}
      id="error-center"
      className="error-center"
      aria-labelledby="error-center-heading"
      data-testid="error-center"
      tabIndex={-1}
    >
      <div className="error-center-header">
        <div>
          <p className="eyebrow accent">DIAGNÓSTICO AGREGADO</p>
          <h2 id="error-center-heading">Central de erros</h2>
          <p className="muted">
            Falhas, bloqueios e interrupções registrados em produções, workflows e tarefas locais, com ação sugerida.
          </p>
        </div>
        <div className="actions">
          <button
            type="button"
            className="button"
            onClick={() => setRefreshNonce(n => n + 1)}
            disabled={loading}
            data-testid="error-center-refresh"
          >
            Atualizar
          </button>
          {hasActiveFilters && (
            <button
              type="button"
              className="button"
              onClick={clearFilters}
              data-testid="error-center-clear-filters"
            >
              Limpar filtros
            </button>
          )}
        </div>
      </div>

      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>

      {summary && (
        <div className="error-center-summary" aria-label="Resumo de falhas por causa e etapa" data-testid="error-center-summary">
          <article className="card error-center-kpi">
            <p className="eyebrow">TOTAL NO PERÍODO</p>
            <p className="error-center-total" data-testid="error-center-total">{summary.total}</p>
            <p className="muted">Ocorrências registradas</p>
          </article>

          <article className="card error-center-breakdown">
            <p className="eyebrow">POR CAUSA</p>
            {summary.by_code.length === 0 ? (
              <p className="muted">Nenhuma causa registrada.</p>
            ) : (
              <div className="error-center-chips" role="group" aria-label="Filtrar por causa">
                {summary.by_code.map(entry => {
                  const active = codeFilter === entry.code;
                  return (
                    <button
                      key={entry.code}
                      type="button"
                      className={`error-center-chip${active ? ' active' : ''}`}
                      aria-pressed={active}
                      onClick={() => setCodeFilter(prev => (prev === entry.code ? '' : entry.code))}
                      data-testid={`summary-code-${entry.code}`}
                    >
                      <span>{entry.title}</span>
                      <span className="tag">{entry.count}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </article>

          <article className="card error-center-breakdown">
            <p className="eyebrow">POR ETAPA E TIPO</p>
            {summary.by_stage.length === 0 && summary.by_kind.length === 0 ? (
              <p className="muted">Nenhuma etapa registrada.</p>
            ) : (
              <div className="error-center-stage-groups">
                {summary.by_stage.length > 0 && (
                  <div className="error-center-chips" role="group" aria-label="Filtrar por etapa">
                    {summary.by_stage.map(entry => {
                      const active = stageFilter === entry.stage;
                      return (
                        <button
                          key={entry.stage}
                          type="button"
                          className={`error-center-chip${active ? ' active' : ''}`}
                          aria-pressed={active}
                          onClick={() => setStageFilter(prev => (prev === entry.stage ? '' : entry.stage))}
                          data-testid={`summary-stage-${entry.stage}`}
                        >
                          <span>{formatErrorStage(entry.stage)}</span>
                          <span className="tag">{entry.count}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
                {summary.by_kind.length > 0 && (
                  <div className="error-center-chips" role="group" aria-label="Filtrar por tipo">
                    {summary.by_kind.map(entry => {
                      const active = kindFilter === entry.kind;
                      return (
                        <button
                          key={entry.kind}
                          type="button"
                          className={`error-center-chip${active ? ' active' : ''}`}
                          aria-pressed={active}
                          onClick={() => setKindFilter(prev => (prev === entry.kind ? '' : entry.kind))}
                          data-testid={`summary-kind-${entry.kind}`}
                        >
                          <span>{formatErrorKind(entry.kind)}</span>
                          <span className="tag">{entry.count}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </article>
        </div>
      )}

      <div className="card error-center-filters" role="search" aria-label="Filtros da Central de erros">
        <div className="error-center-filter-grid">
          <div className="error-center-field">
            <label htmlFor="error-filter-kind">Tipo</label>
            <select
              id="error-filter-kind"
              value={kindFilter}
              onChange={e => setKindFilter(e.target.value)}
              data-testid="error-filter-kind"
            >
              <option value="">Todos os tipos</option>
              {Object.entries(ERROR_KIND_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>

          <div className="error-center-field">
            <label htmlFor="error-filter-stage">Etapa</label>
            <select
              id="error-filter-stage"
              value={stageFilter}
              onChange={e => setStageFilter(e.target.value)}
              data-testid="error-filter-stage"
            >
              <option value="">Todas as etapas</option>
              {stageOptions.map(stage => (
                <option key={stage} value={stage}>{formatErrorStage(stage)}</option>
              ))}
            </select>
          </div>

          <div className="error-center-field">
            <label htmlFor="error-filter-code">Causa</label>
            <select
              id="error-filter-code"
              value={codeFilter}
              onChange={e => setCodeFilter(e.target.value)}
              data-testid="error-filter-code"
            >
              <option value="">Todas as causas</option>
              {codeOptions.map(entry => (
                <option key={entry.code} value={entry.code}>{entry.title}</option>
              ))}
              {codeFilter && !codeOptions.some(entry => entry.code === codeFilter) && (
                <option value={codeFilter}>{codeFilter}</option>
              )}
            </select>
          </div>

          <div className="error-center-field">
            <label htmlFor="error-filter-since">De</label>
            <input
              id="error-filter-since"
              type="date"
              value={sinceDate}
              onChange={e => setSinceDate(e.target.value)}
              data-testid="error-filter-since"
            />
          </div>

          <div className="error-center-field">
            <label htmlFor="error-filter-until">Até</label>
            <input
              id="error-filter-until"
              type="date"
              value={untilDate}
              onChange={e => setUntilDate(e.target.value)}
              data-testid="error-filter-until"
            />
          </div>
        </div>
      </div>

      {loading && (
        <StatusMessage state="loading" testId="error-center-loading">
          Carregando falhas e resumo…
        </StatusMessage>
      )}

      {error && (
        <div className="card" data-testid="error-center-error">
          <StatusMessage state="error">{error}</StatusMessage>
          <div className="actions">
            <button type="button" className="button" onClick={() => setRefreshNonce(n => n + 1)}>
              Tentar novamente
            </button>
          </div>
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <div className="empty-state" data-testid="error-center-empty">
          <span className="tag good">
            {hasActiveFilters ? 'FILTRO SEM RESULTADOS' : 'OPERAÇÃO ESTÁVEL'}
          </span>
          <h3>
            {hasActiveFilters
              ? 'Nenhuma falha encontrada para os filtros aplicados.'
              : 'Nenhuma falha registrada'}
          </h3>
          <p className="muted">
            {hasActiveFilters
              ? 'Ajuste ou limpe os filtros para visualizar outras ocorrências.'
              : 'As execuções recentes não registraram falhas, bloqueios ou interrupções.'}
          </p>
          {hasActiveFilters && (
            <div className="actions">
              <button type="button" className="button" onClick={clearFilters}>
                Limpar filtros
              </button>
            </div>
          )}
        </div>
      )}

      {items.length > 0 && (
        <div className="error-center-list" aria-label="Lista de falhas" data-testid="error-center-list">
          {items.map(item => {
            const duration = formatDuration(item.duration_seconds);
            return (
              <article
                key={item.id}
                className="card error-center-item"
                data-testid={`error-item-${item.id}`}
              >
                <div className="error-center-item-top">
                  <div className="error-center-item-badges">
                    <span className={statusTagClass(item.status)}>
                      {formatErrorStatus(item.status).toUpperCase()}
                    </span>
                    <span className="tag">{formatErrorKind(item.kind)}</span>
                    <span className="tag">{formatErrorStage(item.stage)}</span>
                  </div>
                  <time className="muted error-center-time" dateTime={item.at}>
                    {formatDateTime(item.at)}
                  </time>
                </div>

                <h3>{item.title}</h3>
                <p>{item.message_pt}</p>

                <div className="error-center-action-callout" data-testid={`error-action-${item.id}`}>
                  <p className="eyebrow accent">AÇÃO SUGERIDA</p>
                  <p>{item.suggested_action_pt}</p>
                </div>

                <div className="error-center-item-footer">
                  <div className="error-center-meta muted">
                    <code>{item.code}</code>
                    {duration && <span>· {duration}</span>}
                    {typeof item.exit_code === 'number' && (
                      <span>· Código de saída: {item.exit_code}</span>
                    )}
                  </div>
                  {onNavigateItem && (
                    <button
                      type="button"
                      className="button primary"
                      onClick={() => onNavigateItem(item)}
                      data-testid={`error-open-${item.id}`}
                    >
                      {getNavigateLabel(item.ref.kind)}
                    </button>
                  )}
                </div>
              </article>
            );
          })}

          {nextAfter && (
            <div className="actions error-center-pagination">
              <button
                type="button"
                className="button"
                onClick={() => void handleLoadMore()}
                disabled={loadingMore}
                data-testid="error-center-load-more"
              >
                {loadingMore ? 'Carregando mais…' : 'Carregar mais falhas'}
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
