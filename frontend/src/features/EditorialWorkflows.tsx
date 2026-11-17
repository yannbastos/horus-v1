import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type {
  AnalysisArtifactContent,
  Connection,
  ProposalArtifactContent,
  SecurityStatusResponse,
  WorkflowArtifact,
  WorkflowDetail,
  WorkflowSummary,
} from '../services/api';
import {
  authorizeWorkflow,
  cancelWorkflow,
  createManualWorkflow,
  getSecurityStatus,
  getWorkflow,
  getWorkflows,
  reconcileWorkflow,
  retryWorkflow,
  reviewWorkflow,
} from '../services/api';
import { StatusMessage } from '../components/StatusMessage';
import { nativeDesktop } from '../platform/desktop';
import ProposalComparePanel, { type ProposalCompareItem } from './proposal-compare/ProposalComparePanel';
import ScriptReview, { type AutoOpenEditorRequest } from './ScriptReview';


import { HumanCheckpointDialog } from '../components/HumanCheckpointDialog';
import { formatSecurityGateError, resolveCheckpointLimits } from '../services/checkpoints';

const STATUS_LABELS: Record<string, string> = {
  queued: 'Na fila',
  running: 'Em execução',
  awaiting_authorization: 'Aguardando autorização',
  awaiting_approval: 'Aguardando aprovação',
  approved: 'Aprovado',
  rejected: 'Rejeitado',
  cancel_requested: 'Cancelando',
  cancelled: 'Cancelado',
  failed: 'Falhou',
  interrupted: 'Interrompido',
  unknown: 'Desconhecido',
  waiting: 'Aguardando',
  succeeded: 'Concluído',
  blocked: 'Bloqueado',
};

const STAGE_LABELS: Record<string, string> = {
  planning: 'Planejamento',
  research: 'Pesquisa',
  analysis: 'Análise',
  proposal: 'Proposta',
};

const TERMINAL_STATUSES = ['approved', 'rejected', 'cancelled', 'failed'];

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const date = new Date(iso);
    return isNaN(date.getTime()) ? iso : date.toLocaleString('pt-BR');
  } catch {
    return iso;
  }
}

export type EditorialFocusTarget = {
  kind: string;
  id: string;
  nonce: number;
};

export default function EditorialWorkflows({
  connection,
  onOpenCenter,
  focusTarget,
  databaseBlocked = false,
}: {
  connection: Connection | null;
  onOpenCenter?: () => void;
  focusTarget?: EditorialFocusTarget | null;
  databaseBlocked?: boolean;
}) {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [current, setCurrent] = useState<WorkflowDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [feedbackError, setFeedbackError] = useState('');
  const [proposalCompareState, setProposalCompareState] = useState<{
    baseId: string;
    targetId: string;
  } | null>(null);
  const [listLoaded, setListLoaded] = useState(false);
  const [manualFormOpen, setManualFormOpen] = useState(false);
  const [manualTitle, setManualTitle] = useState('');
  const [manualError, setManualError] = useState('');
  const [creatingManual, setCreatingManual] = useState(false);
  const [autoOpenEditor, setAutoOpenEditor] = useState<AutoOpenEditorRequest | null>(null);
  const [scriptFocusNonce, setScriptFocusNonce] = useState(0);
  const manualTitleInputRef = useRef<HTMLInputElement | null>(null);


  const [checkpointDialogOpen, setCheckpointDialogOpen] = useState(false);
  const [dialogError, setDialogError] = useState('');
  const [securityStatus, setSecurityStatus] = useState<SecurityStatusResponse | null>(null);
  const [securityStatusLoading, setSecurityStatusLoading] = useState(false);

  const checkpointLimits = resolveCheckpointLimits(securityStatus);

  useEffect(() => {
    if (!connection) {
      setSecurityStatus(null);
      setSecurityStatusLoading(false);
      return;
    }
    const controller = new AbortController();
    setSecurityStatusLoading(true);
    void getSecurityStatus(connection, controller.signal)
      .then(status => {
        if (!controller.signal.aborted) {
          setSecurityStatus(status);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setSecurityStatus(null);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setSecurityStatusLoading(false);
        }
      });
    return () => {
      controller.abort();
    };
  }, [connection]);

  // Initial fetch and when connection changes
  async function refreshList(selectFirst = false) {
    if (!connection) {
      setListLoaded(false);
      return;
    }
    try {
      const list = await getWorkflows(connection);
      setWorkflows(list);
      if (selectFirst && list.length > 0 && !selectedId) {
        setSelectedId(list[0].id);
      }
    } catch {
      // Background list polling does not spam alerts
    } finally {
      setListLoaded(true);
    }
  }

  useEffect(() => {
    void refreshList(true);
  }, [connection]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!focusTarget) return;
    if (focusTarget.kind === 'workflow' && focusTarget.id) {
      setSelectedId(focusTarget.id);
      return;
    }
    if (focusTarget.kind === 'guide_step') {
      if (focusTarget.id === 'write_script') {
        setManualFormOpen(true);
        setManualError('');
        requestAnimationFrame(() => {
          manualTitleInputRef.current?.focus();
          manualTitleInputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        return;
      }
      if (
        focusTarget.id === 'approve_script' ||
        focusTarget.id === 'produce_final' ||
        focusTarget.id === 'review_approve'
      ) {
        const approved = workflows.find(wf => wf.status === 'approved');
        if (approved && selectedId !== approved.id) {
          setSelectedId(approved.id);
        }
        setScriptFocusNonce(focusTarget.nonce);
      }
    }
  }, [focusTarget]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleCreateManualWorkflow(event: FormEvent) {
    event.preventDefault();
    if (!connection || creatingManual) return;
    const trimmedTitle = manualTitle.trim();
    if (!trimmedTitle) {
      setManualError('Informe um título para o roteiro.');
      manualTitleInputRef.current?.focus();
      return;
    }
    setCreatingManual(true);
    setManualError('');
    try {
      const created = await createManualWorkflow(connection, { title: trimmedTitle });
      await refreshList(false);
      setSelectedId(created.workflow_id);
      const detail = await getWorkflow(connection, created.workflow_id);
      setCurrent({
        ...detail,
        current_artifact_id: detail.current_artifact_id ?? created.proposal_artifact_id,
      });
      setManualFormOpen(false);
      setManualTitle('');
      setAutoOpenEditor({
        workflowId: created.workflow_id,
        title: trimmedTitle,
        nonce: Date.now(),
      });
    } catch (reason) {
      setManualError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setCreatingManual(false);
    }
  }

  // Polling: updates list and current workflow every 1.5s until terminal or unmount
  useEffect(() => {
    const conn = connection;
    if (!conn) return;
    let timer: number | undefined;
    let active = true;

    async function poll() {
      if (!conn) return;
      try {
        const list = await getWorkflows(conn);
        if (!active) return;
        setWorkflows(list);

        if (selectedId) {
          const detail = await getWorkflow(conn, selectedId);
          if (!active) return;
          setCurrent(detail);
          if (TERMINAL_STATUSES.includes(detail.status)) {
            return; // Terminal state reached: stop polling
          }
        }
      } catch (reason) {
        // Keep polling on transient errors unless unmounted
      }
      if (active) {
        timer = window.setTimeout(poll, 1500);
      }
    }

    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [connection, selectedId]);

  // Load selected workflow detail immediately when selection changes
  useEffect(() => {
    setProposalCompareState(null);
    const conn = connection;
    if (!conn || !selectedId) {
      setCurrent(null);
      return;
    }
    let active = true;
    setLoading(true);
    setError('');
    void getWorkflow(conn, selectedId)
      .then(detail => {
        if (!active) return;
        setCurrent(detail);
      })
      .catch(reason => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [connection, selectedId]);

  async function executeAuthorize(fromDialog = false) {
    if (!connection || !current || !current.pending_authorization) return;
    if (fromDialog && checkpointLimits.status !== 'configured') return;
    setBusy(true);
    setError('');
    setDialogError('');
    try {
      const updated = await authorizeWorkflow(connection, current.id, {
        expected_revision: current.revision,
        job_id: current.pending_authorization.job_id,
      });
      setCurrent(updated);
      setCheckpointDialogOpen(false);
      await refreshList();
    } catch (reason) {
      const friendly = formatSecurityGateError(reason);
      if (fromDialog) {
        setDialogError(friendly);
      } else {
        setError(friendly);
      }
    } finally {
      setBusy(false);
    }
  }

  function handleAuthorize() {
    if (!current || !current.pending_authorization) return;
    if (isSimulated) {
      void executeAuthorize(false);
      return;
    }
    setDialogError('');
    setCheckpointDialogOpen(true);
    if (connection) {
      setSecurityStatusLoading(true);
      void getSecurityStatus(connection)
        .then(status => {
          setSecurityStatus(status);
        })
        .catch(() => {
          setSecurityStatus(null);
        })
        .finally(() => {
          setSecurityStatusLoading(false);
        });
    }
  }

  async function handleCancel() {
    if (!connection || !current) return;
    setBusy(true);
    setError('');
    try {
      const updated = await cancelWorkflow(connection, current.id);
      setCurrent(updated);
      await refreshList();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function handleRetry() {
    if (!connection || !current) return;
    setBusy(true);
    setError('');
    try {
      const updated = await retryWorkflow(connection, current.id, current.revision);
      setCurrent(updated);
      await refreshList();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function handleReconcile() {
    if (!connection || !current) return;
    setBusy(true);
    setError('');
    try {
      const updated = await reconcileWorkflow(connection, current.id, current.revision);
      setCurrent(updated);
      await refreshList();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function handleReview(decision: 'approve' | 'reject' | 'request_changes') {
    if (!connection || !current || !current.current_artifact_id) return;
    if (decision === 'request_changes' && !feedback.trim()) {
      setFeedbackError('Descreva as alterações solicitadas.');
      return;
    }
    setBusy(true);
    setError('');
    setFeedbackError('');
    try {
      const result = await reviewWorkflow(connection, current.id, {
        expected_revision: current.revision,
        artifact_id: current.current_artifact_id,
        decision,
        feedback: decision === 'request_changes' ? feedback.trim() : undefined,
      });
      // Show final state returned by backend (no optimistic state)
      setCurrent(result.workflow);
      if (decision === 'request_changes') {
        setFeedback('');
      }
      await refreshList();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const isSimulated = current?.simulated || current?.executor === 'hermes_simulated';
  const isTerminal = current ? TERMINAL_STATUSES.includes(current.status) : false;
  const canCancel = current && !isTerminal && current.status !== 'cancel_requested';
  const canRetry = current && (current.status === 'failed' || current.status === 'interrupted');
  const hasUnknownRun = current?.cognitive_runs?.some(r => r.status === 'unknown');
  const canReconcile = current && (current.status === 'failed' || current.status === 'interrupted') && hasUnknownRun;

  const proposalArtifact = current?.artifacts?.find(
    (a: WorkflowArtifact) => a.stage === 'proposal' && a.revision === current.revision
  );
  const proposalContent = proposalArtifact?.content as ProposalArtifactContent | undefined;

  const proposalCompareItems: ProposalCompareItem[] = (current?.artifacts ?? [])
    .filter((a: WorkflowArtifact) => a.stage === 'proposal')
    .sort((a, b) => a.revision - b.revision)
    .map(artifact => ({
      artifact,
      content: (artifact.content ?? { stage: 'proposal', title: '' }) as ProposalArtifactContent,
      approval:
        current?.approvals?.find(
          app => app.artifact_id === artifact.id || app.revision === artifact.revision
        ) ?? null,
    }));

  const analysisArtifact = current?.artifacts?.find(
    (a: WorkflowArtifact) => a.stage === 'analysis' && a.revision === current.revision
  );
  const analysisContent = analysisArtifact?.content as AnalysisArtifactContent | undefined;
  const analysisResult = analysisContent?.result;

  return (
    <section className="workspace" aria-label="Análises editoriais e aprovação humana">
      <div className="card">
        <div className="row spread">
          <div>
            <p className="eyebrow accent">PIPELINE EDITORIAL</p>
            <h2>Análises editoriais e aprovações</h2>
          </div>
          <div className="row" style={{ gap: '8px', flexWrap: 'wrap' }}>
            {connection && workflows.length > 0 && !manualFormOpen && (
              <button
                type="button"
                className="button"
                data-testid="new-manual-script-button"
                disabled={databaseBlocked}
                onClick={() => {
                  setManualFormOpen(true);
                  setManualError('');
                  requestAnimationFrame(() => {
                    manualTitleInputRef.current?.focus();
                  });
                }}
              >
                Novo roteiro do zero
              </button>
            )}
            <span className="tag">MÓDULO EDITORIAL</span>
          </div>
        </div>
        <p className="muted">
          Fluxo principal do primeiro vídeo (local_pilot): escreva seu roteiro do zero ou acompanhe análises
          editoriais com aprovação humana.
        </p>

        {error && (
          <StatusMessage state="error">
            {error}
          </StatusMessage>
        )}
      </div>

      {!connection && (
        <StatusMessage state="empty" role="status">
          {nativeDesktop
            ? 'Backend local indisponível. Use RECONECTAR BACKEND em Ajustes para escrever roteiros do zero e acompanhar aprovações.'
            : 'Conecte o backend local em Ajustes para escrever roteiros do zero e acompanhar aprovações.'}
        </StatusMessage>
      )}

      {connection && manualFormOpen && (
        <form
          className="card sunken"
          aria-label="Criar roteiro manual do zero"
          data-testid="manual-script-create-form"
          onSubmit={event => void handleCreateManualWorkflow(event)}
        >
          <p className="eyebrow accent">ROTEIRO MANUAL (SEM PROPOSTA PRÉVIA)</p>
          <h3>Escrever roteiro do zero</h3>
          <p className="muted">
            Informe apenas o título do vídeo para iniciar o fluxo manual e abrir o editor de roteiro por seções.
          </p>
          <label>
            Título do roteiro
            <input
              ref={manualTitleInputRef}
              type="text"
              value={manualTitle}
              onChange={event => {
                setManualTitle(event.target.value);
                if (manualError) setManualError('');
              }}
              placeholder="Ex.: Como gravar seu primeiro vídeo local"
              maxLength={200}
              disabled={creatingManual}
            />
          </label>
          {manualError && <StatusMessage state="error">{manualError}</StatusMessage>}
          <div className="actions">
            <button
              type="submit"
              className="button primary"
              disabled={creatingManual || !manualTitle.trim() || databaseBlocked}
            >
              {creatingManual ? 'Criando…' : 'Criar e abrir editor'}
            </button>
            <button
              type="button"
              className="button"
              disabled={creatingManual}
              onClick={() => {
                setManualFormOpen(false);
                setManualError('');
              }}
            >
              Cancelar
            </button>
          </div>
        </form>
      )}

      {connection && listLoaded && workflows.length === 0 && !manualFormOpen && (
        <article className="card sunken" data-testid="editorial-empty-state">
          <p className="eyebrow accent">PRIMEIRO VÍDEO · ROTEIRO MANUAL</p>
          <h3>Nenhum roteiro criado ainda.</h3>
          <p className="muted">
            Você pode escrever seu roteiro do zero agora mesmo, sem precisar de análise ou proposta prévia de IA.
          </p>
          <div className="actions">
            <button
              type="button"
              className="button primary"
              disabled={databaseBlocked}
              onClick={() => {
                setManualFormOpen(true);
                setManualError('');
                requestAnimationFrame(() => {
                  manualTitleInputRef.current?.focus();
                });
              }}
            >
              Escrever roteiro do zero
            </button>
          </div>
        </article>
      )}

      {workflows.length > 0 && (
        <div className="editorial-grid">
          <aside className="editorial-list" aria-label="Lista de análises editoriais">
            <p className="eyebrow accent">ANÁLISES RECENTES</p>
            {workflows.map(wf => {
              const isSelected = wf.id === selectedId;
              return (
                <button
                  key={wf.id}
                  className={`editorial-item ${isSelected ? 'selected' : ''}`}
                  onClick={() => {
                    setSelectedId(wf.id);
                    setError('');
                  }}
                >
                  <div className="row spread">
                    <span className="eyebrow accent">{wf.id.slice(0, 12)}…</span>
                    <span className="tag">{STATUS_LABELS[wf.status] || wf.status}</span>
                  </div>
                  <small className="muted">
                    Revisão {wf.revision} · {formatDate(wf.created_at)}
                  </small>
                </button>
              );
            })}
          </aside>

          <div className="editorial-detail" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space7)' }}>
            {loading && <StatusMessage state="loading">Carregando análise…</StatusMessage>}

            {current && !loading && (
              <>
                <article className="card">
                  <div className="row spread">
                    <div>
                      <p className="eyebrow accent">FLUXO EDITORIAL</p>
                      <h3>Workflow {current.id}</h3>
                    </div>
                    <div className="row">
                      {isSimulated && <span className="tag simulated">SIMULADO</span>}
                      <span className="tag">{STATUS_LABELS[current.status] || current.status}</span>
                    </div>
                  </div>

                  <p className="muted">
                    Revisão: <strong>{current.revision}</strong> · Executor: <strong>{current.executor}</strong> ·
                    Criado em: {formatDate(current.created_at)}
                  </p>

                  {current.request?.objective && (
                    <div>
                      <p className="eyebrow">OBJETIVO</p>
                      <p style={{ marginTop: '4px' }}>{current.request.objective}</p>
                    </div>
                  )}

                  <div>
                    <p className="eyebrow">ETAPAS DO FLUXO</p>
                    <div className="steps-horizontal">
                      {(['planning', 'research', 'analysis', 'proposal'] as const).map(stageKey => {
                        const stepJob = current.steps?.find(
                          s => s.step_key === stageKey && s.workflow_revision === current.revision
                        );
                        const stepStatus = stepJob ? stepJob.status : 'waiting';
                        const isDone = stepStatus === 'succeeded';
                        const isPendingAuth = stepStatus === 'awaiting_authorization';
                        return (
                          <span
                            key={stageKey}
                            className={`tag ${isDone ? 'good' : isPendingAuth ? 'warn' : ''}`}
                          >
                            {STAGE_LABELS[stageKey]}: {STATUS_LABELS[stepStatus] || stepStatus}
                          </span>
                        );
                      })}
                    </div>
                  </div>

                  <div className="actions">
                    {canCancel && (
                      <button
                        className="button"
                        disabled={busy || current.status === 'cancel_requested'}
                        onClick={() => void handleCancel()}
                      >
                        Cancelar
                      </button>
                    )}
                    {canRetry && (
                      <button className="button" disabled={busy} onClick={() => void handleRetry()}>
                        Tentar novamente
                      </button>
                    )}
                    {canReconcile && (
                      <button className="button" disabled={busy} onClick={() => void handleReconcile()}>
                        Conciliar
                      </button>
                    )}
                  </div>
                </article>

                {current.pending_authorization && (
                  <article className="card sunken" role="region" aria-label="Autorização necessária">
                    <div className="row spread">
                      <p className="eyebrow warn">AUTORIZAÇÃO EXPLÍCITA NECESSÁRIA</p>
                      <span className="tag warn">{isSimulated ? 'ETAPA COGNITIVA' : 'CHECKPOINT HUMANO'}</span>
                    </div>
                    <p>
                      {isSimulated
                        ? 'A etapa de análise editorial cognitiva requer autorização explícita para prosseguir. Esta execução é simulada e não gera custo.'
                        : 'A etapa de análise editorial no Hermes exige aprovação humana (checkpoint) com limite em USD, validade e uso único antes de prosseguir.'}
                    </p>
                    <div className="actions">
                      <button className="button primary" disabled={busy} onClick={() => handleAuthorize()}>
                        Autorizar uma execução
                      </button>
                    </div>
                  </article>
                )}

                {proposalContent && (
                  <article className="card" role="region" aria-label="Proposta editorial">
                    <div className="row spread">
                      <p className="eyebrow accent">PROPOSTA EDITORIAL</p>
                      <div className="row" style={{ flexWrap: 'wrap' }}>
                        {proposalCompareItems.length >= 2 && (
                          <button
                            type="button"
                            className={`button sm ${proposalCompareState ? 'active' : ''}`}
                            onClick={() =>
                              setProposalCompareState(prev =>
                                prev
                                  ? null
                                  : {
                                      baseId: proposalCompareItems[proposalCompareItems.length - 2].artifact.id,
                                      targetId: proposalCompareItems[proposalCompareItems.length - 1].artifact.id,
                                    }
                              )
                            }
                          >
                            {proposalCompareState
                              ? 'Ocultar comparação de propostas'
                              : `Comparar revisões (${proposalCompareItems.length})`}
                          </button>
                        )}
                        {proposalContent.result_status && (
                          <span className="tag good">Status: {proposalContent.result_status}</span>
                        )}
                      </div>
                    </div>

                    <h2>{proposalContent.title}</h2>

                    {proposalContent.recommendation ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                        {proposalContent.recommendation.title_options && (
                          <div>
                            <p className="eyebrow">OPÇÕES DE TÍTULO</p>
                            <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
                              {proposalContent.recommendation.title_options.map((t, idx) => (
                                <li key={idx}><strong>{t}</strong></li>
                              ))}
                            </ul>
                          </div>
                        )}

                        <div className="proposal-grid">
                          <div>
                            <p className="eyebrow">PREMISSA</p>
                            <p>{proposalContent.recommendation.premise}</p>
                          </div>
                          <div>
                            <p className="eyebrow">ÂNGULO</p>
                            <p>{proposalContent.recommendation.angle}</p>
                          </div>
                          <div>
                            <p className="eyebrow">FORMATO</p>
                            <p>{proposalContent.recommendation.format === 'short' ? 'Vídeo Curto (Shorts)' : 'Vídeo Longo'}</p>
                          </div>
                          <div>
                            <p className="eyebrow">ORIGINALIDADE</p>
                            <p>{proposalContent.recommendation.originality}</p>
                          </div>
                        </div>

                        {proposalContent.recommendation.risks && proposalContent.recommendation.risks.length > 0 && (
                          <div>
                            <p className="eyebrow warn">RISCOS</p>
                            <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
                              {proposalContent.recommendation.risks.map((r, idx) => (
                                <li key={idx}>{r}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {proposalContent.recommendation.licensing_notes && (
                          <div>
                            <p className="eyebrow">NOTAS DE LICENCIAMENTO</p>
                            <p className="muted">{proposalContent.recommendation.licensing_notes}</p>
                          </div>
                        )}

                        {proposalContent.opportunities && proposalContent.opportunities.length > 0 && (
                          <div>
                            <p className="eyebrow">OPORTUNIDADES IDENTIFICADAS</p>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '6px' }}>
                              {proposalContent.opportunities.map(op => (
                                <div key={op.id} className="fact-item">
                                  <strong>{op.summary}</strong>
                                  <small className="muted">Enquadramento Brasil: {op.br_fit_rationale}</small>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {proposalContent.unknowns && proposalContent.unknowns.length > 0 && (
                          <div>
                            <p className="eyebrow">PONTOS DESCONHECIDOS</p>
                            <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
                              {proposalContent.unknowns.map((u, idx) => (
                                <li key={idx} className="muted">{u}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {proposalContent.evidence && (
                          <div>
                            <p className="eyebrow">CONTAGEM DE EVIDÊNCIAS</p>
                            <div className="evidence-metrics" style={{ marginTop: '4px' }}>
                              <span className="tag">{proposalContent.evidence.sources} fontes</span>
                              <span className="tag">{proposalContent.evidence.facts} fatos</span>
                              <span className="tag">{proposalContent.evidence.hypotheses} hipóteses</span>
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                      proposalContent.text && <p className="muted">{proposalContent.text}</p>
                    )}
                  </article>
                )}

                {proposalCompareState && proposalCompareItems.length >= 2 && (
                  <ProposalComparePanel
                    proposals={proposalCompareItems}
                    baseArtifactId={proposalCompareState.baseId}
                    targetArtifactId={proposalCompareState.targetId}
                    onChangeBaseId={baseId =>
                      setProposalCompareState(prev => (prev ? { ...prev, baseId } : null))
                    }
                    onChangeTargetId={targetId =>
                      setProposalCompareState(prev => (prev ? { ...prev, targetId } : null))
                    }
                    onClose={() => setProposalCompareState(null)}
                  />
                )}

                {analysisResult && (
                  <article className="card" role="region" aria-label="Evidências da análise">
                    <p className="eyebrow accent">EVIDÊNCIAS E FATOS DA ANÁLISE</p>

                    {analysisResult.sources && analysisResult.sources.length > 0 && (
                      <details open>
                        <summary>Fontes consultadas ({analysisResult.sources.length})</summary>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
                          {analysisResult.sources.map(src => (
                            <div key={src.id} className="source-item">
                              <div className="row spread">
                                <span className="eyebrow">{src.id}</span>
                                <small className="muted">{formatDate(src.retrieved_at)}</small>
                              </div>
                              <a href={src.url} target="_blank" rel="noreferrer">
                                {src.title || src.url}
                              </a>
                            </div>
                          ))}
                        </div>
                      </details>
                    )}

                    {analysisResult.facts && analysisResult.facts.length > 0 && (
                      <details open style={{ marginTop: '10px' }}>
                        <summary>Fatos validados ({analysisResult.facts.length})</summary>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
                          {analysisResult.facts.map(fact => (
                            <div key={fact.id} className="fact-item">
                              <div className="row spread">
                                <span className="eyebrow">{fact.id}</span>
                                <small className="muted">Fontes: {fact.source_ids.join(', ')}</small>
                              </div>
                              <p>{fact.text}</p>
                              {fact.metric_refs && fact.metric_refs.length > 0 && (
                                <div className="row" style={{ flexWrap: 'wrap', gap: '6px' }}>
                                  {fact.metric_refs.map((m, idx) => (
                                    <span key={idx} className="tag small">
                                      {m.source_id} · {m.field}: {m.value.toLocaleString('pt-BR')}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </details>
                    )}

                    {analysisResult.hypotheses && analysisResult.hypotheses.length > 0 && (
                      <details open style={{ marginTop: '10px' }}>
                        <summary>Hipóteses ({analysisResult.hypotheses.length})</summary>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
                          {analysisResult.hypotheses.map(hyp => (
                            <div key={hyp.id} className="hypothesis-item">
                              <div className="row spread">
                                <span className="eyebrow">{hyp.id}</span>
                                <span className="tag small">Confiança: {hyp.confidence}</span>
                              </div>
                              <p>{hyp.text}</p>
                              <small className="muted">Baseada em: {hyp.based_on.join(', ')}</small>
                              <small className="muted">Como testar: {hyp.how_to_test}</small>
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                  </article>
                )}

                {current.status === 'awaiting_approval' && current.current_artifact_id && (
                  <article className="card sunken" role="region" aria-label="Decisão editorial">
                    <p className="eyebrow accent">DECISÃO EDITORIAL HUMANA</p>
                    <h3>Aprovação da proposta</h3>
                    <p className="muted">
                      Avalie a proposta acima e registre sua decisão editorial. Para solicitar alterações, descreva
                      obrigatoriamente o feedback para a nova revisão.
                    </p>

                    <label>
                      Feedback / Alterações solicitadas
                      <textarea
                        value={feedback}
                        onChange={e => {
                          setFeedback(e.target.value);
                          if (feedbackError) setFeedbackError('');
                        }}
                        placeholder="Descreva as correções ou ajustes desejados na abordagem, formato ou premissa..."
                        maxLength={2000}
                        rows={3}
                      />
                    </label>
                    {feedbackError && (
                      <StatusMessage state="error">
                        {feedbackError}
                      </StatusMessage>
                    )}

                    <div className="actions">
                      <button
                        className="button primary"
                        disabled={busy}
                        onClick={() => void handleReview('approve')}
                      >
                        Aprovar
                      </button>
                      <button
                        className="button"
                        disabled={busy}
                        onClick={() => void handleReview('reject')}
                      >
                        Rejeitar
                      </button>
                      <button
                        className="button"
                        disabled={busy || !feedback.trim()}
                        onClick={() => void handleReview('request_changes')}
                      >
                        Solicitar alterações
                      </button>
                    </div>
                  </article>
                )}

                {current.approvals && current.approvals.length > 0 && (
                  <article className="card" role="region" aria-label="Histórico de decisões">
                    <p className="eyebrow">HISTÓRICO DE APROVAÇÕES</p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      {current.approvals.map(app => (
                        <div key={app.id} className="fact-item">
                          <div className="row spread">
                            <span className="eyebrow">Revisão {app.revision}</span>
                            <span className={`tag ${app.decision === 'approve' ? 'good' : 'bad'}`}>
                              {app.decision === 'approve'
                                ? 'Aprovado'
                                : app.decision === 'reject'
                                ? 'Rejeitado'
                                : 'Alterações solicitadas'}
                            </span>
                          </div>
                          {app.feedback && <p className="muted">Feedback: {app.feedback}</p>}
                          <small className="muted">{formatDate(app.created_at)}</small>
                        </div>
                      ))}
                    </div>
                  </article>
                )}

                {current.status === 'approved' && onOpenCenter && (
                  <article className="card" role="region" aria-label="Próxima ação da proposta aprovada">
                    <p className="eyebrow good">PROPOSTA APROVADA</p>
                    <p className="muted">
                      Gere e revise o roteiro logo abaixo; depois acompanhe a produção e o histórico na
                      Central de Produção.
                    </p>
                    <div className="actions">
                      <button className="button" type="button" onClick={onOpenCenter}>
                        Abrir Central de Produção
                      </button>
                    </div>
                  </article>
                )}

                <ScriptReview
                  connection={connection}
                  workflow={current}
                  proposalArtifact={proposalArtifact}
                  autoOpenEditor={autoOpenEditor}
                  onAutoOpenConsumed={() => setAutoOpenEditor(null)}
                  focusNonce={scriptFocusNonce}
                />
              </>
            )}
          </div>
        </div>
      )}

      <HumanCheckpointDialog
        open={checkpointDialogOpen && Boolean(current?.pending_authorization)}
        busy={busy}
        loadingLimits={securityStatusLoading}
        limitStatus={checkpointLimits.status}
        error={dialogError}
        what={
          current?.pending_authorization
            ? `Execução cognitiva no Hermes — etapa ${STAGE_LABELS[current.pending_authorization.stage] || current.pending_authorization.stage} (workflow ${current.id})`
            : 'Execução cognitiva no Hermes'
        }
        estimatedCostUsd={checkpointLimits.estimatedCostLabel}
        limitUsd={checkpointLimits.limitLabel}
        validityLabel={checkpointLimits.validityLabel}
        onCancel={() => {
          setCheckpointDialogOpen(false);
          setDialogError('');
        }}
        onConfirm={() => executeAuthorize(true)}
      />
    </section>
  );
}
