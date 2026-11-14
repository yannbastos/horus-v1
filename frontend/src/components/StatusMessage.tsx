import type { CSSProperties, ReactNode } from 'react';

export type OperationalState = 'loading' | 'error' | 'empty' | 'success';

export interface StatusMessageProps {
  /** Estado operacional canônico do design system (loading | error | empty | success). */
  state: OperationalState;
  /** Rótulo/eyebrow opcional em bloco (renderiza dentro de .fact-item). */
  eyebrow?: string;
  /** Papel ARIA explícito; usa padrão semântico por estado ('alert' para error, 'status' para loading/success). */
  role?: 'status' | 'alert';
  /** Tom visual opcional para o estado 'success' ('muted' padrão ou 'good'). */
  tone?: 'muted' | 'good';
  /** Estilo inline opcional quando necessário para espaçamento local. */
  style?: CSSProperties;
  /** Identificador de teste opcional. */
  testId?: string;
  /** Conteúdo textual ou elementos filhos da mensagem de estado. */
  children: ReactNode;
}

/**
 * Componente compartilhado para mensagens operacionais de estado (loading, error, empty, success),
 * alinhado aos contratos visuais e semânticos do catálogo Storybook do Hórus.
 */
export function StatusMessage({
  state,
  eyebrow,
  role,
  tone = 'muted',
  style,
  testId,
  children,
}: StatusMessageProps) {
  const resolvedRole =
    role ?? (state === 'error' ? 'alert' : state === 'loading' || state === 'success' ? 'status' : undefined);

  if (eyebrow) {
    return (
      <div className="fact-item" role={resolvedRole} style={style} data-testid={testId}>
        <p className="eyebrow">{eyebrow}</p>
        <p className={state === 'error' ? 'error' : state === 'loading' ? 'loading' : tone === 'good' ? 'good' : 'muted'}>
          {children}
        </p>
      </div>
    );
  }

  const className =
    state === 'error'
      ? 'error'
      : state === 'loading'
        ? 'loading'
        : tone === 'good'
          ? 'good'
          : 'muted';

  return (
    <p className={className} role={resolvedRole} style={style} data-testid={testId}>
      {children}
    </p>
  );
}
