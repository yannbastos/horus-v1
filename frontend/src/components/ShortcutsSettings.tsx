import { useEffect, useState } from 'react';
import {
  CATEGORY_LABELS,
  createDefaultShortcutPreferences,
  findShortcutConflict,
  formatKeyParts,
  isSafeShortcutCombo,
  normalizeKeyCombo,
  SHORTCUT_DEFINITIONS,
} from '../services/shortcuts';
import type { ShortcutId, ShortcutPreferences } from '../services/shortcuts';
import { StatusMessage } from './StatusMessage';

export type ShortcutsSettingsProps = {
  preferences: ShortcutPreferences;
  onChange: (next: ShortcutPreferences) => void;
};

export function ShortcutsSettings({ preferences, onChange }: ShortcutsSettingsProps) {
  const [capturingId, setCapturingId] = useState<ShortcutId | null>(null);
  const [captureError, setCaptureError] = useState('');
  const [announcement, setAnnouncement] = useState('');

  useEffect(() => {
    if (!capturingId) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();

      if (event.key === 'Escape') {
        setCapturingId(null);
        setCaptureError('');
        setAnnouncement('Edição de atalho cancelada.');
        return;
      }

      const combo = normalizeKeyCombo(event);
      if (!combo) return;

      if (!isSafeShortcutCombo(combo)) {
        setCaptureError(
          `A tecla "${combo}" pode conflitar com digitação ou leitores de tela. Use Ctrl, Alt, F1–F12 ou ?.`,
        );
        return;
      }

      const conflict = findShortcutConflict(preferences.bindings, capturingId, combo);
      if (conflict) {
        setCaptureError(`"${combo}" já está em uso por "${conflict.label}". Escolha outra combinação.`);
        return;
      }

      const def = SHORTCUT_DEFINITIONS.find(item => item.id === capturingId);
      onChange({
        ...preferences,
        bindings: {
          ...preferences.bindings,
          [capturingId]: combo,
        },
      });
      setCapturingId(null);
      setCaptureError('');
      setAnnouncement(`Atalho de ${def?.label ?? capturingId} atualizado para ${combo}.`);
    };

    window.addEventListener('keydown', handleKeyDown, true);
    return () => window.removeEventListener('keydown', handleKeyDown, true);
  }, [capturingId, onChange, preferences]);

  function toggleEnabled() {
    const nextEnabled = !preferences.enabled;
    setCapturingId(null);
    setCaptureError('');
    onChange({ ...preferences, enabled: nextEnabled });
    setAnnouncement(nextEnabled ? 'Atalhos de teclado ativados.' : 'Atalhos de teclado desativados.');
  }

  function resetSingle(id: ShortcutId) {
    const def = SHORTCUT_DEFINITIONS.find(item => item.id === id);
    if (!def) return;
    setCapturingId(null);
    setCaptureError('');
    onChange({
      ...preferences,
      bindings: {
        ...preferences.bindings,
        [id]: def.defaultBinding,
      },
    });
    setAnnouncement(`Atalho de ${def.label} restaurado para ${def.defaultBinding}.`);
  }

  function disableSingle(id: ShortcutId) {
    const def = SHORTCUT_DEFINITIONS.find(item => item.id === id);
    setCapturingId(null);
    setCaptureError('');
    onChange({
      ...preferences,
      bindings: {
        ...preferences.bindings,
        [id]: '',
      },
    });
    setAnnouncement(`Atalho de ${def?.label ?? id} desativado.`);
  }

  function resetAll() {
    setCapturingId(null);
    setCaptureError('');
    onChange(createDefaultShortcutPreferences());
    setAnnouncement('Todos os atalhos foram restaurados para o padrão.');
  }

  return (
    <section
      className="card"
      aria-labelledby="shortcuts-settings-title"
      data-testid="shortcuts-settings"
    >
      <div className="row spread" style={{ flexWrap: 'wrap', gap: 'var(--Space4)' }}>
        <div>
          <p className="eyebrow">PRODUTIVIDADE</p>
          <h2 id="shortcuts-settings-title">Atalhos de teclado</h2>
          <p className="muted">
            Preferência salva localmente neste navegador/dispositivo. Atalhos não disparam durante digitação e evitam teclas simples usadas por leitores de tela.
          </p>
        </div>
        <div className="actions">
          <button
            type="button"
            role="switch"
            aria-checked={preferences.enabled}
            className={`button ${preferences.enabled ? 'active' : ''}`}
            data-testid="shortcuts-enabled-toggle"
            onClick={toggleEnabled}
          >
            {preferences.enabled ? 'ATALHOS ATIVADOS' : 'ATALHOS DESATIVADOS'}
          </button>
          <button
            type="button"
            className="button"
            data-testid="shortcuts-reset-all"
            onClick={resetAll}
          >
            RESTAURAR PADRÕES
          </button>
        </div>
      </div>

      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>

      {capturingId && (
        <StatusMessage state="loading" ariaLive="polite" testId="shortcut-capture-prompt">
          Pressione a nova combinação (com Ctrl, Alt, F1–F12 ou ?). Pressione Esc para cancelar.
        </StatusMessage>
      )}

      {captureError && (
        <StatusMessage state="error" testId="shortcut-capture-error">
          {captureError}
        </StatusMessage>
      )}

      <ul className="shortcuts-settings-list" aria-label="Lista de atalhos configuráveis">
        {SHORTCUT_DEFINITIONS.map(def => {
          const binding = preferences.bindings[def.id];
          const parts = formatKeyParts(binding);
          const isCapturing = capturingId === def.id;
          const isDefault = binding === def.defaultBinding;

          return (
            <li
              key={def.id}
              className="shortcut-settings-item"
              data-testid={`shortcut-row-${def.id}`}
            >
              <div className="shortcut-info">
                <div className="row" style={{ gap: 'var(--Space2)', flexWrap: 'wrap' }}>
                  <strong>{def.label}</strong>
                  <span className="tag">{CATEGORY_LABELS[def.category]}</span>
                </div>
                <span className="muted">{def.description}</span>
              </div>

              <div className="shortcut-settings-controls">
                <div className="shortcut-keys" aria-label={binding ? `Atalho atual ${binding}` : 'Atalho desativado'}>
                  {isCapturing ? (
                    <span className="tag warn">AGUARDANDO TECLA…</span>
                  ) : parts.length > 0 ? (
                    parts.map((part, idx) => (
                      <span key={`${def.id}-${idx}`} className="shortcut-key-wrap">
                        {idx > 0 && <span className="muted" aria-hidden="true">+</span>}
                        <kbd className="shortcut-kbd">{part}</kbd>
                      </span>
                    ))
                  ) : (
                    <span className="tag">DESATIVADO</span>
                  )}
                </div>

                <div className="actions">
                  <button
                    type="button"
                    className={`button ${isCapturing ? 'primary' : ''}`}
                    disabled={!preferences.enabled}
                    data-testid={`shortcut-edit-${def.id}`}
                    aria-label={`Alterar atalho de ${def.label}`}
                    onClick={() => {
                      setCaptureError('');
                      setCapturingId(isCapturing ? null : def.id);
                    }}
                  >
                    {isCapturing ? 'Cancelar' : 'Alterar'}
                  </button>
                  {binding && (
                    <button
                      type="button"
                      className="button"
                      disabled={!preferences.enabled}
                      data-testid={`shortcut-disable-${def.id}`}
                      aria-label={`Desativar atalho de ${def.label}`}
                      onClick={() => disableSingle(def.id)}
                    >
                      Desativar
                    </button>
                  )}
                  {!isDefault && (
                    <button
                      type="button"
                      className="button"
                      disabled={!preferences.enabled}
                      data-testid={`shortcut-reset-${def.id}`}
                      aria-label={`Restaurar atalho padrão de ${def.label}`}
                      onClick={() => resetSingle(def.id)}
                    >
                      Padrão
                    </button>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
