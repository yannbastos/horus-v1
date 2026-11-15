export type ShortcutCategory = 'general' | 'navigation' | 'contextual';

export type ShortcutId =
  | 'help.open'
  | 'notifications.toggle'
  | 'nav.overview'
  | 'nav.radar'
  | 'nav.library'
  | 'nav.production'
  | 'nav.production-center'
  | 'nav.channels'
  | 'nav.history'
  | 'nav.transcription'
  | 'nav.settings'
  | 'library.search'
  | 'transcription.save';

export type ShortcutDefinition = {
  id: ShortcutId;
  label: string;
  description: string;
  category: ShortcutCategory;
  defaultBinding: string;
};

export type ShortcutPreferences = {
  enabled: boolean;
  bindings: Record<ShortcutId, string>;
};

export const SHORTCUTS_STORAGE_KEY = 'horus.shortcuts.v1';

export const SHORTCUT_DEFINITIONS: ShortcutDefinition[] = [
  {
    id: 'help.open',
    label: 'Ajuda de atalhos',
    description: 'Abre ou fecha este painel de referência rápida.',
    category: 'general',
    defaultBinding: '?',
  },
  {
    id: 'notifications.toggle',
    label: 'Notificações',
    description: 'Abre ou fecha a central de notificações.',
    category: 'general',
    defaultBinding: 'Alt+N',
  },
  {
    id: 'nav.overview',
    label: 'Ir para Visão geral',
    description: 'Abre a tela inicial de visão geral.',
    category: 'navigation',
    defaultBinding: 'Alt+1',
  },
  {
    id: 'nav.radar',
    label: 'Ir para Radar',
    description: 'Abre a seção Radar de referências.',
    category: 'navigation',
    defaultBinding: 'Alt+2',
  },
  {
    id: 'nav.library',
    label: 'Ir para Biblioteca',
    description: 'Abre a biblioteca de mídia local.',
    category: 'navigation',
    defaultBinding: 'Alt+3',
  },
  {
    id: 'nav.production',
    label: 'Ir para Produção',
    description: 'Abre os fluxos editoriais e produção.',
    category: 'navigation',
    defaultBinding: 'Alt+4',
  },
  {
    id: 'nav.production-center',
    label: 'Ir para Central de Produção',
    description: 'Abre a Central de Produção com o painel de vídeos.',
    category: 'navigation',
    defaultBinding: 'Alt+5',
  },
  {
    id: 'nav.channels',
    label: 'Ir para Canais',
    description: 'Abre a seção Canais.',
    category: 'navigation',
    defaultBinding: 'Alt+6',
  },
  {
    id: 'nav.history',
    label: 'Ir para Histórico operacional',
    description: 'Abre o histórico operacional.',
    category: 'navigation',
    defaultBinding: 'Alt+7',
  },
  {
    id: 'nav.transcription',
    label: 'Ir para Transcrição',
    description: 'Abre a ferramenta de transcrição local.',
    category: 'navigation',
    defaultBinding: 'Alt+8',
  },
  {
    id: 'nav.settings',
    label: 'Ir para Ajustes',
    description: 'Abre as configurações locais do Hórus.',
    category: 'navigation',
    defaultBinding: 'Alt+9',
  },
  {
    id: 'library.search',
    label: 'Buscar na Biblioteca',
    description: 'Abre a Biblioteca e foca o campo de busca.',
    category: 'contextual',
    defaultBinding: 'Ctrl+K',
  },
  {
    id: 'transcription.save',
    label: 'Salvar transcrição (.txt)',
    description: 'Salva o texto atual quando estiver na tela de Transcrição.',
    category: 'contextual',
    defaultBinding: 'Ctrl+S',
  },
];

export const CATEGORY_LABELS: Record<ShortcutCategory, string> = {
  general: 'Geral',
  navigation: 'Navegação entre seções',
  contextual: 'Ações contextuais',
};

export function createDefaultShortcutPreferences(): ShortcutPreferences {
  const bindings = {} as Record<ShortcutId, string>;
  for (const def of SHORTCUT_DEFINITIONS) {
    bindings[def.id] = def.defaultBinding;
  }
  return { enabled: true, bindings };
}

export function loadShortcutPreferences(): ShortcutPreferences {
  const defaults = createDefaultShortcutPreferences();
  try {
    const raw = localStorage.getItem(SHORTCUTS_STORAGE_KEY);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return defaults;
    }
    const record = parsed as Record<string, unknown>;
    const enabled = typeof record.enabled === 'boolean' ? record.enabled : defaults.enabled;
    const rawBindings =
      record.bindings && typeof record.bindings === 'object' && !Array.isArray(record.bindings)
        ? (record.bindings as Record<string, unknown>)
        : {};
    const bindings = { ...defaults.bindings };
    for (const def of SHORTCUT_DEFINITIONS) {
      const candidate = rawBindings[def.id];
      if (typeof candidate === 'string') {
        bindings[def.id] = candidate;
      }
    }
    return { enabled, bindings };
  } catch {
    return defaults;
  }
}

export function saveShortcutPreferences(prefs: ShortcutPreferences): void {
  try {
    localStorage.setItem(SHORTCUTS_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Ignora falhas de armazenamento (ex.: modo privado restrito).
  }
}

const MODIFIER_KEYS = new Set(['Control', 'Shift', 'Alt', 'Meta', 'AltGraph', 'CapsLock', 'NumLock', 'ScrollLock']);
const RESERVED_KEYS = new Set(['Tab', 'Enter', 'Escape', ' ']);

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return Boolean(target.closest('[contenteditable="true"], [role="textbox"]'));
}

export function normalizeKeyCombo(event: KeyboardEvent): string | null {
  if (event.isComposing || MODIFIER_KEYS.has(event.key)) return null;

  if (event.key === '?' && !event.ctrlKey && !event.altKey && !event.metaKey) {
    return '?';
  }

  const parts: string[] = [];
  if (event.ctrlKey || event.metaKey) parts.push('Ctrl');
  if (event.altKey) parts.push('Alt');
  if (event.shiftKey) parts.push('Shift');

  let key = event.key;
  if (key.length === 1) {
    key = key.toUpperCase();
  } else if (key === ' ') {
    key = 'Space';
  }

  parts.push(key);
  return parts.join('+');
}

export function isSafeShortcutCombo(combo: string): boolean {
  if (!combo) return false;
  if (combo === '?') return true;
  if (/^F([1-9]|1[0-2])$/.test(combo)) return true;

  const parts = combo.split('+');
  const key = parts[parts.length - 1];
  if (!key || RESERVED_KEYS.has(key)) return false;

  // Exige Ctrl ou Alt para não conflitar com digitação nem com navegação por letra de leitores de tela.
  return parts.includes('Ctrl') || parts.includes('Alt');
}

export function matchesShortcut(event: KeyboardEvent, binding: string): boolean {
  if (!binding) return false;
  if (binding === '?') {
    return event.key === '?' && !event.ctrlKey && !event.altKey && !event.metaKey;
  }
  const combo = normalizeKeyCombo(event);
  if (!combo) return false;
  return combo.toLowerCase() === binding.toLowerCase();
}

export function formatKeyParts(binding: string): string[] {
  if (!binding) return [];
  if (binding === '?') return ['?'];
  return binding.split('+').filter(Boolean);
}

export function findShortcutConflict(
  bindings: Record<ShortcutId, string>,
  targetId: ShortcutId,
  candidate: string,
): ShortcutDefinition | null {
  if (!candidate) return null;
  const normalized = candidate.toLowerCase();
  for (const def of SHORTCUT_DEFINITIONS) {
    if (def.id === targetId) continue;
    if (bindings[def.id]?.toLowerCase() === normalized) {
      return def;
    }
  }
  return null;
}
