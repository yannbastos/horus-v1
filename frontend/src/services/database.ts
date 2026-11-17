import { ApiError, api } from './api';
import type { Connection } from './api';

export type DatabaseState =
  | 'not_configured'
  | 'connecting'
  | 'connected'
  | 'unreachable'
  | 'auth_failed'
  | 'schema_mismatch'
  | 'migrating'
  | 'vault_unavailable';

export type DatabaseStatus = {
  state: DatabaseState;
  latency_ms: number | null;
  schema_version: string | null;
  expected_schema_version: string | null;
  last_error_code: string | null;
  host_label: string | null;
  checked_at: string | null;
};

export type DatabaseStatusResult =
  | { supported: true; status: DatabaseStatus }
  | { supported: false; reason: 'not_found' | 'invalid_payload' };

export type DatabaseConnectionPayload = {
  host: string;
  port: number;
  database: string;
  user: string;
  password: string;
};

const VALID_STATES: ReadonlySet<string> = new Set<DatabaseState>([
  'not_configured',
  'connecting',
  'connected',
  'unreachable',
  'auth_failed',
  'schema_mismatch',
  'migrating',
  'vault_unavailable',
]);

export const DATABASE_ERROR_CODES = new Set([
  'database_not_configured',
  'database_unreachable',
  'database_dns_failed',
  'database_tls_invalid',
  'database_auth_failed',
  'database_role_invalid',
  'database_schema_mismatch',
  'database_migrating',
  'database_vault_unavailable',
  'database_internal_error',
]);

type DatabaseErrorListener = (code: string, message: string) => void;
const errorListeners = new Set<DatabaseErrorListener>();

/** Registra ouvinte para erros de banco detectados em chamadas /v1/* durante o uso. */
export function subscribeDatabaseApiErrors(listener: DatabaseErrorListener): () => void {
  errorListeners.add(listener);
  return () => {
    errorListeners.delete(listener);
  };
}

/** Notifica ouvintes quando uma resposta da API indica falha de banco de dados. */
export function notifyDatabaseApiError(code: string | undefined, message: string): void {
  if (!code || !DATABASE_ERROR_CODES.has(code)) return;
  for (const listener of errorListeners) {
    listener(code, message);
  }
}

export function isDatabaseState(value: unknown): value is DatabaseState {
  return typeof value === 'string' && VALID_STATES.has(value);
}

function readOptionalString(value: unknown): string | null {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed === '' ? null : trimmed;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return null;
}

function readOptionalNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.round(value);
  }
  return null;
}

/**
 * Converte resposta bruta de `/v1/database/*` para `DatabaseStatus`.
 * Ignora intencionalmente qualquer campo sensível (senha/credencial) caso venha por engano.
 */
export function parseDatabaseStatus(raw: unknown): DatabaseStatus | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const record = raw as Record<string, unknown>;
  if (!isDatabaseState(record.state)) return null;
  return {
    state: record.state,
    latency_ms: readOptionalNumber(record.latency_ms),
    schema_version: readOptionalString(record.schema_version),
    expected_schema_version: readOptionalString(record.expected_schema_version),
    last_error_code: readOptionalString(record.last_error_code),
    host_label: readOptionalString(record.host_label),
    checked_at: readOptionalString(record.checked_at),
  };
}

/** Consulta o estado atual da conexão com o Supabase PostgreSQL. */
export async function getDatabaseStatus(
  connection: Connection,
  signal?: AbortSignal,
): Promise<DatabaseStatusResult> {
  try {
    const raw = await api<unknown>(connection, '/v1/database/status', { signal });
    const parsed = parseDatabaseStatus(raw);
    if (!parsed) {
      return { supported: false, reason: 'invalid_payload' };
    }
    return { supported: true, status: parsed };
  } catch (reason) {
    if (reason instanceof ApiError && (reason.status === 404 || reason.status === 405 || reason.status === 501)) {
      return { supported: false, reason: 'not_found' };
    }
    throw reason;
  }
}

/**
 * Envia as credenciais do banco para o backend (que as armazena no cofre do Windows).
 * A senha nunca é retornada na resposta nem mantida em cache no frontend.
 */
export async function saveDatabaseConnection(
  connection: Connection,
  payload: DatabaseConnectionPayload,
): Promise<DatabaseStatus> {
  const raw = await api<unknown>(connection, '/v1/database/connection', {
    method: 'POST',
    body: JSON.stringify({
      host: payload.host.trim(),
      port: payload.port,
      database: payload.database.trim(),
      user: payload.user.trim(),
      password: payload.password,
    }),
  });
  const parsed = parseDatabaseStatus(raw);
  if (parsed) return parsed;
  const refreshed = await getDatabaseStatus(connection);
  if (refreshed.supported) return refreshed.status;
  throw new Error('O backend não retornou o estado atualizado da conexão com o banco.');
}

/** Remove as credenciais salvas no cofre do Windows via backend. */
export async function deleteDatabaseConnection(connection: Connection): Promise<DatabaseStatus | null> {
  const raw = await api<unknown>(connection, '/v1/database/connection', {
    method: 'DELETE',
  });
  return parseDatabaseStatus(raw);
}

/** Executa diagnóstico explícito de conexão e esquema do banco de dados. */
export async function testDatabaseConnection(connection: Connection): Promise<DatabaseStatus> {
  const raw = await api<unknown>(connection, '/v1/database/test', {
    method: 'POST',
  });
  const parsed = parseDatabaseStatus(raw);
  if (parsed) return parsed;
  const refreshed = await getDatabaseStatus(connection);
  if (refreshed.supported) return refreshed.status;
  throw new Error('O backend não retornou o resultado do diagnóstico do banco.');
}

/**
 * Extrai o snapshot `DatabaseStatus` do envelope `{detail:{code, message, status}}` quando presente.
 */
export function extractDatabaseStatusFromError(reason: unknown): DatabaseStatus | null {
  if (!(reason instanceof ApiError)) return null;
  if (!reason.detail || typeof reason.detail !== 'object' || Array.isArray(reason.detail)) {
    return null;
  }
  const detail = reason.detail as Record<string, unknown>;
  return parseDatabaseStatus(detail.status);
}

export function isDatabaseReady(status: DatabaseStatus | null | undefined): boolean {
  return Boolean(status && status.state === 'connected');
}

export function isDatabaseBlocking(
  supported: boolean,
  status: DatabaseStatus | null | undefined,
): boolean {
  if (!supported || !status) return false;
  return status.state !== 'connected';
}

export type DatabaseGuidance = {
  eyebrow: string;
  title: string;
  summary: string;
  tone: 'good' | 'warn' | 'bad' | 'muted';
  hints: string[];
};

/** Traduz estado e código de erro estável em orientação clara para o usuário. */
export function describeDatabaseStatus(status: DatabaseStatus): DatabaseGuidance {
  const code = status.last_error_code ?? '';
  if (status.state === 'connected') {
    return {
      eyebrow: 'CONECTADO',
      title: 'Supabase PostgreSQL conectado.',
      summary: status.host_label
        ? `Conexão operacional ativa com ${status.host_label}.`
        : 'Conexão operacional ativa com o banco de dados.',
      tone: 'good',
      hints: [],
    };
  }
  if (status.state === 'connecting') {
    return {
      eyebrow: 'CONECTANDO',
      title: 'Estabelecendo conexão com o banco…',
      summary: 'Aguarde a verificação inicial de rede e autenticação.',
      tone: 'muted',
      hints: [],
    };
  }
  if (status.state === 'migrating') {
    return {
      eyebrow: 'MIGRANDO ESQUEMA',
      title: 'Migração de esquema em andamento.',
      summary: 'As operações que dependem do banco ficam bloqueadas até a conclusão da migração.',
      tone: 'warn',
      hints: ['Aguarde alguns instantes e clique em "Testar conexão" para confirmar a conclusão.'],
    };
  }
  if (status.state === 'vault_unavailable' || code === 'database_vault_unavailable') {
    return {
      eyebrow: 'COFRE INDISPONÍVEL',
      title: 'Cofre do Windows indisponível.',
      summary:
        'Não foi possível acessar o cofre de credenciais do sistema operacional (falha fechada; nenhuma senha é mantida em cache de navegador).',
      tone: 'bad',
      hints: [
        'Verifique se o Gerenciador de Credenciais do Windows (Credential Manager) está ativo e acessível para o usuário atual.',
        'Após normalizar o cofre, clique em "Testar conexão" ou salve as credenciais novamente.',
      ],
    };
  }
  if (status.state === 'not_configured' || code === 'database_not_configured') {
    return {
      eyebrow: 'CONFIGURAÇÃO PENDENTE',
      title: 'Banco de dados não configurado.',
      summary:
        'O Hórus usa o Supabase PostgreSQL como único banco operacional. Configure a conexão antes de iniciar produções, roteiros ou revisões.',
      tone: 'warn',
      hints: [
        'Use os dados do pooler Supavisor (porta 5432) em Ajustes → Banco de dados.',
        'A senha é armazenada apenas no cofre do sistema operacional pelo backend local.',
      ],
    };
  }
  if (status.state === 'auth_failed' || code === 'database_auth_failed') {
    return {
      eyebrow: 'CREDENCIAL RECUSADA',
      title: 'Falha de autenticação no banco de dados.',
      summary:
        'O servidor recusou o usuário ou a senha configurados. Nenhuma operação foi salva localmente.',
      tone: 'bad',
      hints: [
        'No pooler Supavisor da Supabase, use o usuário operacional horus_app.<project-ref>.',
        'Confira se a senha informada corresponde à senha atual no painel da Supabase e salve novamente.',
      ],
    };
  }
  if (status.state === 'schema_mismatch' || code === 'database_role_invalid' || code === 'database_schema_mismatch') {
    if (code === 'database_role_invalid') {
      return {
        eyebrow: 'PAPEL OU PERMISSÃO INVÁLIDA',
        title: 'Permissões insuficientes no banco de dados.',
        summary:
          'O papel conectado não possui os privilégios mínimos exigidos pelo Hórus (esperado: horus_app com grants aplicados).',
        tone: 'bad',
        hints: [
          'Confirme que o usuário configurado pertence ao papel horus_app e não a roles administrativas ou anônimas.',
          'Após ajustar as permissões/grants no Supabase, clique em "Testar conexão".',
        ],
      };
    }
    const found = status.schema_version ?? 'desconhecida';
    const expected = status.expected_schema_version ?? 'não informada';
    return {
      eyebrow: 'ESQUEMA INCOMPATÍVEL',
      title: 'Versão do esquema incompatível.',
      summary: `Esquema encontrado (${found}) difere do esperado pelo backend (${expected}). As operações foram bloqueadas para proteger a integridade dos dados.`,
      tone: 'bad',
      hints: [
        'Aplique as migrações pendentes no banco Supabase antes de retomar o uso.',
        'Após atualizar o esquema, clique em "Testar conexão" para revalidar.',
      ],
    };
  }
  if (code === 'database_dns_failed') {
    return {
      eyebrow: 'FALHA DE DNS',
      title: 'Não foi possível resolver o endereço do banco.',
      summary:
        'O nome do host configurado não pôde ser resolvido pela rede atual. Nenhuma alteração foi salva localmente.',
      tone: 'bad',
      hints: [
        'Verifique sua conexão de rede, DNS corporativo ou VPN e confirme o host do pooler Supavisor.',
        'Evite o host direto db.<ref>.supabase.co em redes apenas IPv4.',
      ],
    };
  }
  if (code === 'database_tls_invalid') {
    return {
      eyebrow: 'FALHA DE TLS',
      title: 'Certificado TLS do banco recusado.',
      summary:
        'A verificação de certificado TLS falhou ao conectar ao Supabase PostgreSQL. A conexão foi bloqueada por segurança.',
      tone: 'bad',
      hints: [
        'Verifique se há proxy/interceptação TLS na porta 5432 ou se o host configurado corresponde ao pooler oficial da Supabase.',
      ],
    };
  }
  return {
    eyebrow: 'BANCO INACESSÍVEL',
    title: 'Não foi possível alcançar o banco de dados.',
    summary:
      'Sem conexão ativa com o Supabase PostgreSQL. Não há fallback local; seus dados não enviados permanecem na tela para você repetir quando a conexão voltar.',
    tone: 'bad',
    hints: [
      'Verifique sua conexão de rede, VPN corporativa ou regras de firewall para a porta 5432.',
      'Confirme se o host configurado aponta para o pooler IPv4 da Supabase (o host direto pode exigir IPv6).',
    ],
  };
}

/** Mapeia estado de banco retornado de forma síncrona por um erro de API. */
export function inferDatabaseStateFromErrorCode(code: string | undefined): DatabaseState | null {
  switch (code) {
    case 'database_not_configured':
      return 'not_configured';
    case 'database_auth_failed':
      return 'auth_failed';
    case 'database_role_invalid':
    case 'database_schema_mismatch':
      return 'schema_mismatch';
    case 'database_migrating':
      return 'migrating';
    case 'database_vault_unavailable':
      return 'vault_unavailable';
    case 'database_unreachable':
    case 'database_dns_failed':
    case 'database_tls_invalid':
      return 'unreachable';
    default:
      return null;
  }
}
