import { useEffect, useId, useMemo, useState } from 'react';
import type { Connection, SecurityTransmissionItem } from '../services/api';
import { ApiError, getSecurityTransmissions } from '../services/api';
import { StatusMessage } from '../components/StatusMessage';

const PAGE_SIZE = 20;

type PurposeFilter = 'all' | 'analysis' | 'script' | 'authorize' | 'diagnostic';
type DecisionFilter = 'all' | 'allowed' | 'refused';

const STAGE_LABELS: Record<string, string> = {
  analysis: 'Análise editorial',
  script: 'Geração de roteiro',
  authorize: 'Autorização de envio',
  diagnostic: 'Teste de conexão',
};

const KIND_LABELS: Record<string, string> = {
  submit: 'Envio',
  outcome: 'Resultado',
  gate_refusal: 'Bloqueio preventivo',
  probe: 'Verificação de saúde',
};

const RESULT_DESCRIPTIONS: Record<string, string> = {
  refused_gate: 'Bloqueada pela barreira de segurança',
  attempted: 'Tentativa de envio registrada',
  sent: 'Enviada ao destino',
  completed: 'Concluída pelo destino',
  failed: 'Falhou no destino',
  error: 'Erro de comunicação',
  unknown: 'Desfecho ainda pendente',
};

function formatDate(value: string | null): string {
  if (!value || value.trim() === '') return 'Data não informada';
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return 'Data inválida';
  return new Date(parsed).toLocaleString('pt-BR');
}

function formatBytes(bytes: number | null): string {
  if (typeof bytes !== 'number' || Number.isNaN(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1).replace('.', ',')} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`;
}

function isRefused(item: SecurityTransmissionItem): boolean {
  return item.result === 'refused_gate' || item.kind === 'gate_refusal';
}

function formatDestination(item: SecurityTransmissionItem): { primary: string; secondary: string | null } {
  const dest = item.destination?.trim();
  const provider = item.provider?.trim();
  if (dest) {
    const sub = provider && provider !== dest ? provider : null;
    return { primary: dest, secondary: sub };
  }
  if (provider) {
    return { primary: provider, secondary: null };
  }
  return { primary: 'Local (sem envio externo)', secondary: null };
}

function formatPurpose(item: SecurityTransmissionItem): { primary: string; secondary: string | null } {
  const stageKey = item.stage?.trim() ?? '';
  const kindKey = item.kind?.trim() ?? '';
  const primary = STAGE_LABELS[stageKey] ?? (stageKey || 'Operação não informada');
  const secondary = KIND_LABELS[kindKey] ?? (kindKey || null);
  return { primary, secondary };
}

function formatDecision(item: SecurityTransmissionItem): {
  refused: boolean;
  badge: string;
  description: string;
} {
  const refused = isRefused(item);
  const resultKey = item.result?.trim() ?? '';
  const description =
    RESULT_DESCRIPTIONS[resultKey] ??
    (refused ? 'Bloqueada pela barreira de segurança' : 'Transmissão registrada');
  return {
    refused,
    badge: refused ? 'RECUSADA' : 'PERMITIDA',
    description,
  };
}

function describeTransmissionsError(reason: unknown): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) {
      return 'Token de sessão inválido ou expirado (401). Reconecte o backend na aba GERAL.';
    }
    if (reason.status === 404) {
      return 'A rota de registro de transmissões não está disponível neste backend (404).';
    }
    if (reason.status >= 500) {
      return `O backend falhou ao consultar o registro de transmissões (${reason.status}).`;
    }
    return `Não foi possível carregar o registro (${reason.status}).`;
  }
  if (reason instanceof Error && reason.name === 'AbortError') {
    return 'Tempo esgotado ao carregar o registro de transmissões.';
  }
  return 'Sem conexão com o backend local ao consultar o registro de transmissões.';
}

/**
 * Visualização paginada e filtrável do registro append-only de transmissões (HOR-115).
 * Exibe apenas metadados (quando, destino, finalidade, decisão e tamanho), nunca conteúdo.
 */
export default function TransmissionsAudit({
  connection,
  refreshKey = 0,
}: {
  connection: Connection;
  refreshKey?: number;
}) {
  const destinationInputId = useId();
  const purposeSelectId = useId();
  const decisionSelectId = useId();

  const [afterStack, setAfterStack] = useState<number[]>([0]);
  const [nextAfter, setNextAfter] = useState<number | null>(null);
  const [items, setItems] = useState<SecurityTransmissionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localRevision, setLocalRevision] = useState(0);

  const [destinationFilter, setDestinationFilter] = useState('');
  const [purposeFilter, setPurposeFilter] = useState<PurposeFilter>('all');
  const [decisionFilter, setDecisionFilter] = useState<DecisionFilter>('all');

  const currentAfter = afterStack[afterStack.length - 1] ?? 0;

  useEffect(() => {
    setAfterStack([0]);
  }, [connection, refreshKey]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    void getSecurityTransmissions(connection, { limit: PAGE_SIZE, after: currentAfter }, controller.signal)
      .then(res => {
        if (controller.signal.aborted) return;
        const safeItems = Array.isArray(res?.items) ? res.items : [];
        const safeNext = typeof res?.next_after === 'number' ? res.next_after : null;
        setItems(safeItems);
        setNextAfter(safeNext);
      })
      .catch(reason => {
        if (controller.signal.aborted) return;
        setItems([]);
        setNextAfter(null);
        setError(describeTransmissionsError(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [connection, currentAfter, localRevision, refreshKey]);

  const filteredItems = useMemo(() => {
    const query = destinationFilter.trim().toLowerCase();
    return items.filter(item => {
      if (query) {
        const haystack = `${item.destination ?? ''} ${item.provider ?? ''}`.toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      if (purposeFilter !== 'all' && item.stage !== purposeFilter) {
        return false;
      }
      if (decisionFilter === 'refused' && !isRefused(item)) return false;
      if (decisionFilter === 'allowed' && isRefused(item)) return false;
      return true;
    });
  }, [items, destinationFilter, purposeFilter, decisionFilter]);

  const hasActiveFilters =
    destinationFilter.trim() !== '' || purposeFilter !== 'all' || decisionFilter !== 'all';

  function handleClearFilters() {
    setDestinationFilter('');
    setPurposeFilter('all');
    setDecisionFilter('all');
  }

  function handlePrevPage() {
    if (afterStack.length <= 1 || loading) return;
    setAfterStack(prev => prev.slice(0, -1));
  }

  function handleNextPage() {
    if (nextAfter === null || loading) return;
    setAfterStack(prev => [...prev, nextAfter]);
  }

  function handleReload() {
    setLocalRevision(r => r + 1);
  }

  return (
    <div className="transmissions-audit" data-testid="transmissions-audit">
      <div className="transmissions-toolbar">
        <div className="transmissions-filters" role="group" aria-label="Filtros do registro de transmissões">
          <div className="transmissions-filter-field">
            <label htmlFor={destinationInputId} className="eyebrow">
              Destino
            </label>
            <input
              id={destinationInputId}
              type="search"
              value={destinationFilter}
              onChange={e => setDestinationFilter(e.target.value)}
              placeholder="Ex.: 127.0.0.1 ou hermes"
            />
          </div>

          <div className="transmissions-filter-field">
            <label htmlFor={purposeSelectId} className="eyebrow">
              Finalidade
            </label>
            <select
              id={purposeSelectId}
              value={purposeFilter}
              onChange={e => setPurposeFilter(e.target.value as PurposeFilter)}
            >
              <option value="all">Todas</option>
              <option value="analysis">Análise editorial</option>
              <option value="script">Geração de roteiro</option>
              <option value="authorize">Autorização de envio</option>
              <option value="diagnostic">Teste de conexão</option>
            </select>
          </div>

          <div className="transmissions-filter-field">
            <label htmlFor={decisionSelectId} className="eyebrow">
              Decisão
            </label>
            <select
              id={decisionSelectId}
              value={decisionFilter}
              onChange={e => setDecisionFilter(e.target.value as DecisionFilter)}
            >
              <option value="all">Todas</option>
              <option value="allowed">Permitida</option>
              <option value="refused">Recusada</option>
            </select>
          </div>
        </div>

        <div className="transmissions-actions">
          {hasActiveFilters && (
            <button type="button" className="button" onClick={handleClearFilters}>
              LIMPAR FILTROS
            </button>
          )}
          <button type="button" className="button" disabled={loading} onClick={handleReload}>
            {loading ? 'ATUALIZANDO…' : 'ATUALIZAR REGISTRO'}
          </button>
        </div>
      </div>

      {loading && (
        <StatusMessage state="loading" ariaLive="polite" testId="transmissions-loading">
          Carregando registro de transmissões…
        </StatusMessage>
      )}

      {!loading && error && (
        <div className="transmissions-error-box">
          <StatusMessage state="error" testId="transmissions-error">
            {error}
          </StatusMessage>
          <div>
            <button type="button" className="button" onClick={handleReload}>
              TENTAR NOVAMENTE
            </button>
          </div>
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <StatusMessage state="empty" role="status" testId="transmissions-empty">
          Nenhuma transmissão registrada até o momento. Envios ao Hermes e recusas da barreira de
          segurança aparecerão aqui.
        </StatusMessage>
      )}

      {!loading && !error && items.length > 0 && filteredItems.length === 0 && (
        <StatusMessage state="empty" role="status" testId="transmissions-filter-empty">
          Nenhum registro nesta página corresponde aos filtros selecionados.
        </StatusMessage>
      )}

      {!loading && !error && filteredItems.length > 0 && (
        <div className="data-table-container" tabIndex={0}>
          <table className="data-table" aria-label="Registro de transmissões">
            <thead>
              <tr>
                <th scope="col">Quando</th>
                <th scope="col">Destino</th>
                <th scope="col">Finalidade</th>
                <th scope="col">Decisão</th>
                <th scope="col">Tamanho</th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.map(item => {
                const destination = formatDestination(item);
                const purpose = formatPurpose(item);
                const decision = formatDecision(item);
                return (
                  <tr key={item.id} data-testid={`transmission-row-${item.id}`}>
                    <td>{formatDate(item.at)}</td>
                    <td>
                      <div>{destination.primary}</div>
                      {destination.secondary && (
                        <div className="muted small transmissions-cell-sub">{destination.secondary}</div>
                      )}
                    </td>
                    <td>
                      <div>{purpose.primary}</div>
                      {purpose.secondary && (
                        <div className="muted small transmissions-cell-sub">{purpose.secondary}</div>
                      )}
                    </td>
                    <td>
                      <div className="transmissions-decision-cell">
                        <span className={`tag ${decision.refused ? 'bad' : 'good'}`}>
                          {decision.badge}
                        </span>
                        <span className="muted small">{decision.description}</span>
                      </div>
                    </td>
                    <td>{formatBytes(item.envelope_bytes)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!error && (items.length > 0 || afterStack.length > 1) && (
        <nav className="transmissions-pagination" aria-label="Paginação do registro de transmissões">
          <span className="muted small" data-testid="transmissions-page-info">
            Página {afterStack.length} · Exibindo {filteredItems.length} de {items.length}{' '}
            {items.length === 1 ? 'registro' : 'registros'}
          </span>
          <div className="row">
            <button
              type="button"
              className="button"
              disabled={afterStack.length <= 1 || loading}
              onClick={handlePrevPage}
            >
              PÁGINA ANTERIOR
            </button>
            <button
              type="button"
              className="button"
              disabled={nextAfter === null || loading}
              onClick={handleNextPage}
            >
              PRÓXIMA PÁGINA
            </button>
          </div>
        </nav>
      )}
    </div>
  );
}
