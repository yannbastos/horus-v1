import { useEffect, useId, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError } from '../services/api';
import type { Connection } from '../services/api';
import {
  deleteDatabaseConnection,
  describeDatabaseStatus,
  extractDatabaseStatusFromError,
  getDatabaseStatus,
  saveDatabaseConnection,
  testDatabaseConnection,
} from '../services/database';
import type { DatabaseStatus } from '../services/database';
import { StatusMessage } from './StatusMessage';

export interface DatabaseSettingsSectionProps {
  connection: Connection | null;
  status: DatabaseStatus | null;
  supported: boolean;
  onStatusChange: (next: DatabaseStatus | null, supported: boolean) => void;
}

export function DatabaseSettingsSection({
  connection,
  status,
  supported,
  onStatusChange,
}: DatabaseSettingsSectionProps) {
  const [host, setHost] = useState('');
  const [port, setPort] = useState('5432');
  const [database, setDatabase] = useState('postgres');
  const [user, setUser] = useState('');
  const [password, setPassword] = useState('');

  const [busy, setBusy] = useState<'load' | 'test' | 'save' | 'delete' | null>(null);
  const [feedback, setFeedback] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  const hostId = useId();
  const portId = useId();
  const dbId = useId();
  const userId = useId();
  const passwordId = useId();

  useEffect(() => {
    if (!connection) return;
    const controller = new AbortController();
    setBusy('load');
    void getDatabaseStatus(connection, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        if (result.supported) {
          onStatusChange(result.status, true);
        } else {
          onStatusChange(null, false);
        }
      })
      .catch(reason => {
        if (!controller.signal.aborted) {
          setFeedback({
            kind: 'error',
            text: reason instanceof Error ? reason.message : String(reason),
          });
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(null);
      });
    return () => controller.abort();
  }, [connection]); // eslint-disable-line react-hooks/exhaustive-deps

  function applyMutationError(reason: unknown, fallbackMessage: string) {
    if (reason instanceof ApiError) {
      if (reason.status === 401 || reason.status === 403) {
        setFeedback({
          kind: 'error',
          text: `Sessão local recusada pelo backend (${reason.status}). Reconecte o backend na aba GERAL e tente novamente.`,
        });
        return;
      }
      const envelopeStatus = extractDatabaseStatusFromError(reason);
      if (reason.status === 409) {
        if (envelopeStatus) onStatusChange(envelopeStatus, true);
        setFeedback({ kind: 'error', text: reason.message });
        return;
      }
      if (reason.status === 503 && envelopeStatus) {
        onStatusChange(envelopeStatus, true);
        setFeedback({ kind: 'error', text: reason.message });
        return;
      }
      setFeedback({ kind: 'error', text: reason.message });
      return;
    }
    setFeedback({
      kind: 'error',
      text: reason instanceof Error ? reason.message : fallbackMessage,
    });
  }

  async function handleTest() {
    if (!connection || busy) return;
    setBusy('test');
    setFeedback(null);
    try {
      const next = await testDatabaseConnection(connection);
      onStatusChange(next, true);
      if (next.state === 'connected') {
        setFeedback({
          kind: 'success',
          text: next.latency_ms !== null
            ? `Conexão verificada com sucesso (${next.latency_ms} ms).`
            : 'Conexão verificada com sucesso.',
        });
      }
    } catch (reason) {
      applyMutationError(reason, 'Não foi possível testar a conexão.');
    } finally {
      setBusy(null);
    }
  }

  async function handleSave(event: FormEvent) {
    event.preventDefault();
    if (!connection || busy) return;
    const trimmedHost = host.trim();
    const trimmedDb = database.trim();
    const trimmedUser = user.trim();
    const parsedPort = Number(port);
    if (!trimmedHost || !trimmedDb || !trimmedUser || password.length === 0) {
      setFeedback({
        kind: 'error',
        text: 'Preencha host, porta, banco, usuário e senha antes de salvar.',
      });
      return;
    }
    if (trimmedHost.length > 253 || trimmedDb.length > 128 || trimmedUser.length > 128 || password.length > 4096) {
      setFeedback({
        kind: 'error',
        text: 'Um ou mais campos excedem o limite permitido (host ≤ 253, banco/usuário ≤ 128, senha ≤ 4096).',
      });
      return;
    }
    if (!Number.isInteger(parsedPort) || parsedPort < 1 || parsedPort > 65535) {
      setFeedback({
        kind: 'error',
        text: 'Informe uma porta TCP válida (entre 1 e 65535).',
      });
      return;
    }

    const submittedPassword = password;
    // Limpa a senha do estado React imediatamente antes de enviar ao backend.
    setPassword('');
    setBusy('save');
    setFeedback(null);

    try {
      const next = await saveDatabaseConnection(connection, {
        host: trimmedHost,
        port: parsedPort,
        database: trimmedDb,
        user: trimmedUser,
        password: submittedPassword,
      });
      onStatusChange(next, true);
      if (next.state === 'connected') {
        setFeedback({
          kind: 'success',
          text: 'Credenciais salvas no cofre do sistema e conexão verificada.',
        });
      } else {
        setFeedback({
          kind: 'error',
          text: `Configuração salva no cofre do sistema, mas o estado atual é "${describeDatabaseStatus(next).eyebrow}".`,
        });
      }
    } catch (reason) {
      applyMutationError(reason, 'Não foi possível salvar as credenciais.');
    } finally {
      setBusy(null);
    }
  }

  async function handleDelete() {
    if (!connection || busy) return;
    setBusy('delete');
    setFeedback(null);
    try {
      const next = await deleteDatabaseConnection(connection);
      onStatusChange(
        next ?? {
          state: 'not_configured',
          latency_ms: null,
          schema_version: null,
          expected_schema_version: status?.expected_schema_version ?? null,
          last_error_code: 'database_not_configured',
          host_label: null,
          checked_at: new Date().toISOString(),
        },
        true,
      );
      setFeedback({
        kind: 'success',
        text: 'Credenciais removidas do cofre do sistema.',
      });
    } catch (reason) {
      applyMutationError(reason, 'Não foi possível remover as credenciais.');
    } finally {
      setBusy(null);
    }
  }

  const guidance = status ? describeDatabaseStatus(status) : null;
  const tagClass =
    guidance?.tone === 'good'
      ? 'tag good'
      : guidance?.tone === 'bad'
        ? 'tag bad'
        : guidance?.tone === 'warn'
          ? 'tag warn'
          : 'tag';

  return (
    <div id="settings-database" data-testid="database-settings-section" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--Space8)' }}>
      <section className="card" aria-label="Estado do banco de dados">
        <div className="row spread">
          <div>
            <p className="eyebrow accent">BANCO DE DADOS · SUPABASE POSTGRESQL</p>
            <h2>{guidance ? guidance.title : supported ? 'Consultando estado do banco…' : 'Endpoint de banco indisponível'}</h2>
          </div>
          {guidance && (
            <span className={tagClass} data-testid="database-state-badge">
              {guidance.eyebrow}
            </span>
          )}
        </div>

        {!connection && (
          <StatusMessage state="empty">
            Conecte o backend local primeiro para gerenciar a conexão com o Supabase PostgreSQL.
          </StatusMessage>
        )}

        {connection && !supported && (
          <StatusMessage state="empty" testId="database-legacy-notice">
            Este backend não expõe a rota <code>/v1/database/status</code>. Atualize o backend para configurar e diagnosticar o Supabase PostgreSQL por esta tela.
          </StatusMessage>
        )}

        {connection && supported && status && guidance && (
          <>
            <p role="status" aria-live="polite" data-testid="database-status-text">
              {guidance.summary}
            </p>

            <dl
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                gap: 'var(--Space4)',
                margin: 0,
                padding: 'var(--Space5)',
                border: '1px solid var(--Line)',
                background: 'var(--Sunken)',
              }}
              data-testid="database-diagnostics-grid"
            >
              <div>
                <dt className="eyebrow">HOST (RÓTULO)</dt>
                <dd style={{ margin: '4px 0 0' }} data-testid="database-host-label">
                  {status.host_label ?? 'Não configurado'}
                </dd>
              </div>
              <div>
                <dt className="eyebrow">LATÊNCIA</dt>
                <dd style={{ margin: '4px 0 0' }} data-testid="database-latency">
                  {status.latency_ms !== null ? `${status.latency_ms} ms` : '—'}
                </dd>
              </div>
              <div>
                <dt className="eyebrow">ESQUEMA (ENCONTRADO × ESPERADO)</dt>
                <dd style={{ margin: '4px 0 0' }} data-testid="database-schema-version">
                  {`${status.schema_version ?? '—'} × ${status.expected_schema_version ?? '—'}`}
                </dd>
              </div>
              <div>
                <dt className="eyebrow">ÚLTIMA VERIFICAÇÃO</dt>
                <dd style={{ margin: '4px 0 0' }}>
                  {status.checked_at ? new Date(status.checked_at).toLocaleString('pt-BR') : '—'}
                </dd>
              </div>
            </dl>

            {status.last_error_code && status.state !== 'connected' && (
              <p className="muted" data-testid="database-error-code">
                Código de diagnóstico: <code>{status.last_error_code}</code>
              </p>
            )}

            {guidance.hints.length > 0 && (
              <ul
                data-testid="database-hints"
                style={{ margin: 0, paddingLeft: 'var(--Space7)', display: 'grid', gap: 'var(--Space2)' }}
              >
                {guidance.hints.map(hint => (
                  <li key={hint} className="muted">
                    {hint}
                  </li>
                ))}
              </ul>
            )}

            <div className="actions">
              <button
                type="button"
                className="button primary"
                disabled={!connection || busy !== null}
                aria-busy={busy === 'test'}
                data-testid="database-test-button"
                onClick={() => void handleTest()}
              >
                {busy === 'test' ? 'TESTANDO CONEXÃO…' : 'Testar conexão'}
              </button>
              <button
                type="button"
                className="button danger"
                disabled={!connection || busy !== null || status.state === 'not_configured'}
                aria-busy={busy === 'delete'}
                data-testid="database-delete-button"
                onClick={() => void handleDelete()}
              >
                {busy === 'delete' ? 'REMOVENDO…' : 'Remover credenciais'}
              </button>
            </div>
          </>
        )}

        {feedback && (
          <StatusMessage
            state={feedback.kind === 'error' ? 'error' : 'success'}
            testId="database-feedback"
          >
            {feedback.text}
          </StatusMessage>
        )}
      </section>

      <section className="card" aria-label="Configurar conexão com o banco de dados">
        <p className="eyebrow">CREDENCIAIS DO BANCO</p>
        <h3>Configurar conexão Supabase PostgreSQL</h3>
        <p className="muted">
          As credenciais são enviadas apenas ao backend local e gravadas no cofre do Windows. A senha nunca é exibida novamente nem salva no navegador.
        </p>

        <form
          onSubmit={event => void handleSave(event)}
          autoComplete="off"
          data-testid="database-connection-form"
        >
          <label htmlFor={hostId}>
            Host (pooler Supavisor IPv4)
            <input
              id={hostId}
              name="db-host"
              type="text"
              value={host}
              onChange={e => setHost(e.target.value)}
              placeholder="aws-0-sa-east-1.pooler.supabase.com"
              autoComplete="off"
              disabled={!connection || busy !== null}
            />
          </label>

          <div className="row" style={{ gap: 'var(--Space5)', width: '100%', flexWrap: 'wrap' }}>
            <label htmlFor={portId} style={{ flex: '1 1 140px' }}>
              Porta
              <input
                id={portId}
                name="db-port"
                type="number"
                min={1}
                max={65535}
                value={port}
                onChange={e => setPort(e.target.value)}
                autoComplete="off"
                disabled={!connection || busy !== null}
              />
            </label>
            <label htmlFor={dbId} style={{ flex: '2 1 200px' }}>
              Banco de dados
              <input
                id={dbId}
                name="db-name"
                type="text"
                value={database}
                onChange={e => setDatabase(e.target.value)}
                autoComplete="off"
                disabled={!connection || busy !== null}
              />
            </label>
          </div>

          <label htmlFor={userId}>
            Usuário
            <input
              id={userId}
              name="db-user"
              type="text"
              value={user}
              onChange={e => setUser(e.target.value)}
              placeholder="horus_app.seu-project-ref"
              autoComplete="off"
              disabled={!connection || busy !== null}
            />
          </label>

          <label htmlFor={passwordId}>
            Senha
            <input
              id={passwordId}
              name="db-password"
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••••••"
              autoComplete="off"
              disabled={!connection || busy !== null}
            />
          </label>

          <div className="actions">
            <button
              type="submit"
              className="button primary"
              disabled={!connection || busy !== null}
              aria-busy={busy === 'save'}
              data-testid="database-save-button"
            >
              {busy === 'save' ? 'SALVANDO E TESTANDO…' : 'Salvar e conectar'}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
