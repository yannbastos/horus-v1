import type { CSSProperties, ReactNode } from 'react';

export type OperationalState = 'loading' | 'error' | 'empty' | 'success';

export interface StatusMessageProps {
  /** Estado operacional canônico do design system (loading | error | empty | success). */
  state: OperationalState;
  /** Rótulo/eyebrow opcional em bloco (renderiza dentro de .fact-item). */
  eyebrow?: string;
  /** Papel ARIA explícito; usa padrão semântico por estado ('alert' para error, 'status' para loading/success). */
  role?: 'status' | 'alert';
  /** Política de anúncio dinâmico ARIA ('polite' padrão quando state === 'loading'). */
  ariaLive?: 'polite' | 'assertive' | 'off';
  /** Tom visual opcional ('muted' padrão, 'good' para destaque positivo ou 'plain' sem classe de tom). */
  tone?: 'muted' | 'good' | 'plain';
  /** Estilo inline opcional quando necessário para espaçamento local. */
  style?: CSSProperties;
  /** Rótulo de acessibilidade opcional (aria-label). */
  ariaLabel?: string;
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
  ariaLive,
  tone = 'muted',
  style,
  ariaLabel,
  testId,
  children,
}: StatusMessageProps) {
  const resolvedRole =
    role ?? (state === 'error' ? 'alert' : state === 'loading' || state === 'success' ? 'status' : undefined);
  const resolvedAriaLive = ariaLive ?? (state === 'loading' ? 'polite' : undefined);

  const className =
    state === 'error'
      ? 'error'
      : state === 'loading'
        ? 'loading'
        : tone === 'good'
          ? 'good'
          : tone === 'plain'
            ? undefined
            : 'muted';

  if (eyebrow) {
    return (
      <div
        className="fact-item"
        role={resolvedRole}
        aria-live={resolvedAriaLive}
        aria-label={ariaLabel}
        style={style}
        data-testid={testId}
      >
        <p className="eyebrow">{eyebrow}</p>
        <p className={className}>{children}</p>
      </div>
    );
  }

  return (
    <p
      className={className}
      role={resolvedRole}
      aria-live={resolvedAriaLive}
      aria-label={ariaLabel}
      style={style}
      data-testid={testId}
    >
      {children}
    </p>
  );
}
