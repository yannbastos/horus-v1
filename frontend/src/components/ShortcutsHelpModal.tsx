import { useEffect, useRef } from 'react';
import type { RefObject } from 'react';
import {
  CATEGORY_LABELS,
  formatKeyParts,
  SHORTCUT_DEFINITIONS,
} from '../services/shortcuts';
import type { ShortcutCategory, ShortcutId } from '../services/shortcuts';

export type ShortcutsHelpModalProps = {
  open: boolean;
  enabled: boolean;
  bindings: Record<ShortcutId, string>;
  onClose: () => void;
  onOpenSettings: () => void;
  triggerRef?: RefObject<HTMLButtonElement | null>;
};

const CATEGORIES: ShortcutCategory[] = ['general', 'navigation', 'contextual'];

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  const selector = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(', ');
  return Array.from(container.querySelectorAll<HTMLElement>(selector)).filter(
    el => !el.hasAttribute('hidden') && el.offsetParent !== null,
  );
}

export function ShortcutsHelpModal({
  open,
  enabled,
  bindings,
  onClose,
  onOpenSettings,
  triggerRef,
}: ShortcutsHelpModalProps) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });
    return () => {
      cancelAnimationFrame(frame);
      const restoreTarget = triggerRef?.current ?? previousFocusRef.current;
      if (restoreTarget && document.contains(restoreTarget)) {
        restoreTarget.focus();
      }
    };
  }, [open, triggerRef]);

  if (!open) return null;

  function handleKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onClose();
      return;
    }

    if (event.key === 'Tab' && dialogRef.current) {
      const focusable = getFocusableElements(dialogRef.current);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  return (
    <div
      className="shortcuts-backdrop"
      data-testid="shortcuts-help-backdrop"
      onClick={event => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={dialogRef}
        id="shortcuts-help-dialog"
        className="shortcuts-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcuts-help-title"
        aria-describedby="shortcuts-help-description"
        data-testid="shortcuts-help-dialog"
        onKeyDown={handleKeyDown}
      >
        <header className="shortcuts-modal-header">
          <div>
            <p className="eyebrow accent">PRODUTIVIDADE E ACESSIBILIDADE</p>
            <div className="row" style={{ gap: 'var(--Space3)', marginTop: 4, flexWrap: 'wrap' }}>
              <h2 id="shortcuts-help-title">Atalhos de teclado</h2>
              <span className={enabled ? 'tag good' : 'tag warn'} data-testid="shortcuts-status-tag">
                {enabled ? 'ATIVADOS' : 'DESATIVADOS'}
              </span>
            </div>
            <p id="shortcuts-help-description" className="muted">
              Atalhos com modificador ou <kbd className="shortcut-kbd">?</kbd> fora de campos de texto, preservando digitação e leitores de tela.
            </p>
          </div>
          <div className="actions">
            <button
              type="button"
              className="button"
              data-testid="shortcuts-open-settings"
              onClick={() => {
                onClose();
                onOpenSettings();
              }}
            >
              Configurar em Ajustes
            </button>
            <button
              ref={closeButtonRef}
              type="button"
              className="button primary"
              aria-label="Fechar painel de atalhos"
              data-testid="shortcuts-help-close"
              onClick={onClose}
            >
              Fechar
            </button>
          </div>
        </header>

        <div className="shortcuts-modal-body" tabIndex={0} aria-label="Lista de atalhos">
          {!enabled && (
            <div className="card sunken" role="status">
              <p className="muted" style={{ margin: 0 }}>
                Os atalhos globais estão desativados nas suas preferências locais. Você pode reativá-los em Ajustes.
              </p>
            </div>
          )}

          {CATEGORIES.map(category => {
            const items = SHORTCUT_DEFINITIONS.filter(item => item.category === category);
            return (
              <section key={category} className="shortcuts-group" aria-label={CATEGORY_LABELS[category]}>
                <h3 className="eyebrow">{CATEGORY_LABELS[category]}</h3>
                <ul className="shortcuts-list">
                  {items.map(item => {
                    const binding = bindings[item.id];
                    const parts = formatKeyParts(binding);
                    return (
                      <li key={item.id} className="shortcut-row">
                        <div className="shortcut-info">
                          <strong>{item.label}</strong>
                          <span className="muted">{item.description}</span>
                        </div>
                        <div className="shortcut-keys" aria-label={binding ? `Atalho ${binding}` : 'Desativado'}>
                          {parts.length > 0 ? (
                            parts.map((part, idx) => (
                              <span key={`${item.id}-${idx}`} className="shortcut-key-wrap">
                                {idx > 0 && <span className="muted" aria-hidden="true">+</span>}
                                <kbd className="shortcut-kbd">{part}</kbd>
                              </span>
                            ))
                          ) : (
                            <span className="tag">DESATIVADO</span>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}

          <section className="shortcuts-group" aria-label="Editor de roteiro">
            <h3 className="eyebrow">Editor de roteiro</h3>
            <ul className="shortcuts-list">
              <li className="shortcut-row">
                <div className="shortcut-info">
                  <strong>Salvar roteiro</strong>
                  <span className="muted">Atalho nativo do editor manual de roteiros em Produção.</span>
                </div>
                <div className="shortcut-keys" aria-label="Atalho Ctrl+S">
                  <kbd className="shortcut-kbd">Ctrl</kbd>
                  <span className="muted" aria-hidden="true">+</span>
                  <kbd className="shortcut-kbd">S</kbd>
                </div>
              </li>
            </ul>
          </section>
        </div>
      </section>
    </div>
  );
}
