import { useMemo } from 'react';
import type {
  ProposalArtifactContent,
  WorkflowApproval,
  WorkflowArtifact,
} from '../../services/api';
import { StatusMessage } from '../../components/StatusMessage';

export type ProposalCompareItem = {
  artifact: WorkflowArtifact;
  content: ProposalArtifactContent;
  approval: WorkflowApproval | null;
};

const DECISION_LABELS: Record<string, string> = {
  approve: 'Aprovado',
  reject: 'Rejeitado',
  request_changes: 'Alterações solicitadas',
};

const FORMAT_LABELS: Record<string, string> = {
  short: 'Vídeo Curto (Shorts)',
  long: 'Vídeo Longo',
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString('pt-BR');
}

function shortId(id: string | null | undefined): string {
  if (!id) return '—';
  return id.length > 14 ? `${id.slice(0, 12)}…` : id;
}

function formatDecision(approval: WorkflowApproval | null): string {
  if (!approval) return 'Sem decisão registrada';
  const label = DECISION_LABELS[approval.decision] || approval.decision;
  return approval.feedback ? `${label} — ${approval.feedback}` : label;
}

function formatEvidence(content: ProposalArtifactContent): string {
  if (!content.evidence) return 'Não informado';
  return `${content.evidence.sources} fontes · ${content.evidence.facts} fatos · ${content.evidence.hypotheses} hipóteses`;
}

function formatList(items: string[] | undefined): string {
  if (!items || items.length === 0) return 'Nenhum';
  return items.join(' | ');
}

export default function ProposalComparePanel({
  proposals,
  baseArtifactId,
  targetArtifactId,
  onChangeBaseId,
  onChangeTargetId,
  onClose,
}: {
  proposals: ProposalCompareItem[];
  baseArtifactId: string;
  targetArtifactId: string;
  onChangeBaseId: (artifactId: string) => void;
  onChangeTargetId: (artifactId: string) => void;
  onClose: () => void;
}) {
  const orderedAsc = useMemo(
    () => [...proposals].sort((a, b) => a.artifact.revision - b.artifact.revision),
    [proposals]
  );

  const baseItem = useMemo(
    () => orderedAsc.find(p => p.artifact.id === baseArtifactId) ?? orderedAsc[0] ?? null,
    [orderedAsc, baseArtifactId]
  );

  const targetItem = useMemo(
    () =>
      orderedAsc.find(p => p.artifact.id === targetArtifactId) ??
      orderedAsc[orderedAsc.length - 1] ??
      null,
    [orderedAsc, targetArtifactId]
  );

  if (!baseItem || !targetItem) {
    return null;
  }

  const sameRevision = baseItem.artifact.id === targetItem.artifact.id;

  const rows: Array<{ label: string; valA: string; valB: string }> = [
    {
      label: 'Revisão e data',
      valA: `Rev. ${baseItem.artifact.revision} (${formatDate(baseItem.artifact.created_at)})`,
      valB: `Rev. ${targetItem.artifact.revision} (${formatDate(targetItem.artifact.created_at)})`,
    },
    {
      label: 'Job / Tentativa',
      valA: `${shortId(baseItem.artifact.job_id)} / ${shortId(baseItem.artifact.attempt_id)}`,
      valB: `${shortId(targetItem.artifact.job_id)} / ${shortId(targetItem.artifact.attempt_id)}`,
    },
    {
      label: 'Decisão editorial',
      valA: formatDecision(baseItem.approval),
      valB: formatDecision(targetItem.approval),
    },
    {
      label: 'Título',
      valA: baseItem.content.title || '—',
      valB: targetItem.content.title || '—',
    },
    {
      label: 'Premissa',
      valA: baseItem.content.recommendation?.premise || '—',
      valB: targetItem.content.recommendation?.premise || '—',
    },
    {
      label: 'Ângulo',
      valA: baseItem.content.recommendation?.angle || '—',
      valB: targetItem.content.recommendation?.angle || '—',
    },
    {
      label: 'Formato',
      valA:
        FORMAT_LABELS[baseItem.content.recommendation?.format || ''] ||
        baseItem.content.recommendation?.format ||
        '—',
      valB:
        FORMAT_LABELS[targetItem.content.recommendation?.format || ''] ||
        targetItem.content.recommendation?.format ||
        '—',
    },
    {
      label: 'Originalidade',
      valA: baseItem.content.recommendation?.originality || '—',
      valB: targetItem.content.recommendation?.originality || '—',
    },
    {
      label: 'Opções de título',
      valA: formatList(baseItem.content.recommendation?.title_options),
      valB: formatList(targetItem.content.recommendation?.title_options),
    },
    {
      label: 'Riscos apontados',
      valA: formatList(baseItem.content.recommendation?.risks),
      valB: formatList(targetItem.content.recommendation?.risks),
    },
    {
      label: 'Evidências',
      valA: formatEvidence(baseItem.content),
      valB: formatEvidence(targetItem.content),
    },
    {
      label: 'Hash do artefato',
      valA: shortId(baseItem.artifact.content_hash),
      valB: shortId(targetItem.artifact.content_hash),
    },
  ];

  return (
    <article
      className="card sunken"
      role="region"
      aria-label="Comparação de revisões da proposta"
      style={{ gap: 'var(--Space5)' }}
    >
      <div className="row spread">
        <div>
          <p className="eyebrow accent">COMPARAÇÃO DE PROPOSTAS (SOMENTE LEITURA)</p>
          <h3>
            Revisão {baseItem.artifact.revision} vs Revisão {targetItem.artifact.revision}
          </h3>
        </div>
        <button type="button" className="button" onClick={onClose}>
          Fechar comparação de propostas
        </button>
      </div>

      <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--Space4)' }}>
        <label style={{ minWidth: '180px', flex: '1 1 180px' }}>
          Revisão base
          <select
            aria-label="Revisão base da proposta"
            value={baseItem.artifact.id}
            onChange={e => onChangeBaseId(e.target.value)}
          >
            {orderedAsc.map(item => (
              <option key={item.artifact.id} value={item.artifact.id}>
                Rev. {item.artifact.revision} — {item.content.title || item.artifact.id}
              </option>
            ))}
          </select>
        </label>

        <label style={{ minWidth: '180px', flex: '1 1 180px' }}>
          Revisão comparada
          <select
            aria-label="Revisão comparada da proposta"
            value={targetItem.artifact.id}
            onChange={e => onChangeTargetId(e.target.value)}
          >
            {orderedAsc.map(item => (
              <option key={item.artifact.id} value={item.artifact.id}>
                Rev. {item.artifact.revision} — {item.content.title || item.artifact.id}
              </option>
            ))}
          </select>
        </label>
      </div>

      {sameRevision ? (
        <StatusMessage state="empty" role="status">
          Selecione duas revisões diferentes para comparar as alterações da proposta.
        </StatusMessage>
      ) : (
        <div className="data-table-container" tabIndex={0}>
          <table className="data-table" aria-label="Tabela comparativa de revisões da proposta">
            <thead>
              <tr>
                <th scope="col">Campo</th>
                <th scope="col">Base (Rev. {baseItem.artifact.revision})</th>
                <th scope="col">Comparada (Rev. {targetItem.artifact.revision})</th>
                <th scope="col">Diferença</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const changed = row.valA !== row.valB;
                return (
                  <tr key={row.label} className={changed ? 'compare-row-changed' : undefined}>
                    <th scope="row">{row.label}</th>
                    <td>{row.valA}</td>
                    <td>{row.valB}</td>
                    <td>
                      <span className={`tag ${changed ? 'warn' : ''}`}>
                        {changed ? 'Alterado' : 'Igual'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}
