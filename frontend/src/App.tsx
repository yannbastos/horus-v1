import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { EyeMark } from './components/Identity';
import { NotificationBellButton, NotificationsPanel } from './components/NotificationsCenter';
import { navigation, Overview, SectionHeader, sections } from './components/Sections';
import type { Page } from './components/Sections';
import { ShortcutsHelpModal } from './components/ShortcutsHelpModal';
import { connectDesktop, nativeDesktop } from './platform/desktop';
import { api } from './services/api';
import type { Connection, HermesStatus } from './services/api';
import { useNotificationsBell } from './services/notifications';
import type { NotificationItem } from './services/notifications';
import { useProductionObserver } from './services/productionNotices';
import {
  isEditableTarget,
  loadShortcutPreferences,
  matchesShortcut,
  saveShortcutPreferences,
} from './services/shortcuts';
import type { ShortcutId, ShortcutPreferences } from './services/shortcuts';
import type { EditorialFocusTarget } from './features/EditorialWorkflows';
import type { ErrorCenterItem } from './services/errorCenter';
import type { Theme } from './features/Settings';

const Library = lazy(() => import('./features/Library'));
const JobWorkspace = lazy(() => import('./features/JobWorkspace'));
const Settings = lazy(() => import('./features/Settings'));
const Transcription = lazy(() => import('./features/Transcription'));
const Radar = lazy(() => import('./features/Radar'));
const EditorialWorkflows = lazy(() => import('./features/EditorialWorkflows'));
const ProductionCenter = lazy(() => import('./features/ProductionCenter'));
const ErrorCenter = lazy(() => import('./features/ErrorCenter'));

export default function App() {
  const [page, setPage] = useState<Page | 'production-center'>('overview');
  const [theme, setTheme] = useState<Theme>(() => {
    const value = localStorage.getItem('horus.theme');
    return value === 'light' || value === 'dark' ? value : 'system';
  });
  const [systemDark, setSystemDark] = useState(matchMedia('(prefers-color-scheme: dark)').matches);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [hermes, setHermes] = useState<HermesStatus | null>(null);
  const [backendError, setBackendError] = useState('');
  // Montada na primeira visita e mantida oculta depois, preservando fila e texto ao trocar de tela.
  const [transcriptionOpened, setTranscriptionOpened] = useState(false);
  useEffect(() => { if (page === 'transcription') setTranscriptionOpened(true); }, [page]);
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const change = () => setSystemDark(media.matches);
    media.addEventListener('change', change); return () => media.removeEventListener('change', change);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setHermes(null);
    if (connection) void api<HermesStatus>(connection, '/v1/hermes', { signal: controller.signal })
      .then(report => { if (!controller.signal.aborted) setHermes(report); })
      .catch(() => { /* O painel de Ajustes oferece uma nova verificação explícita. */ });
    return () => controller.abort();
  }, [connection]);
  useEffect(() => { localStorage.setItem('horus.theme', theme); }, [theme]);
  useEffect(() => {
    let active = true;
    void connectDesktop().then(async candidate => {
      if (!candidate) return;
      await api(candidate, '/health');
      if (active) setConnection(candidate);
    }).catch(reason => { if (active) setBackendError(String(reason)); });
    return () => { active = false; };
  }, []);
  const effectiveTheme = theme === 'system' ? (systemDark ? 'dark' : 'light') : theme;
  const { activeCount: activeProductions, notice: productionNotice, dismissNotice } = useProductionObserver(connection);
  const {
    available: notificationsAvailable,
    unreadCount,
    setUnreadCount,
  } = useNotificationsBell(connection);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notificationsHiddenBy404, setNotificationsHiddenBy404] = useState(false);
  const [bellAnnouncement, setBellAnnouncement] = useState('');
  const [notificationFocusTarget, setNotificationFocusTarget] = useState<EditorialFocusTarget | null>(null);
  const [focusJobId, setFocusJobId] = useState<string | null>(null);
  const [errorCenterFocusNonce, setErrorCenterFocusNonce] = useState(0);
  const [shortcutPrefs, setShortcutPrefs] = useState<ShortcutPreferences>(loadShortcutPreferences);
  const [shortcutsHelpOpen, setShortcutsHelpOpen] = useState(false);
  const [librarySearchNonce, setLibrarySearchNonce] = useState(0);
  const [navAnnouncement, setNavAnnouncement] = useState('');
  const bellButtonRef = useRef<HTMLButtonElement | null>(null);
  const shortcutsTriggerRef = useRef<HTMLButtonElement | null>(null);
  const prevUnreadRef = useRef(0);

  const handleShortcutPrefsChange = useCallback((next: ShortcutPreferences) => {
    saveShortcutPreferences(next);
    setShortcutPrefs(next);
  }, []);

  useEffect(() => {
    setNotificationsHiddenBy404(false);
    setNotificationsOpen(false);
    setBellAnnouncement('');
    prevUnreadRef.current = 0;
  }, [connection]);

  useEffect(() => {
    if (unreadCount > prevUnreadRef.current && !notificationsOpen) {
      setBellAnnouncement(
        `${unreadCount} ${unreadCount === 1 ? 'notificação não lida' : 'notificações não lidas'}.`,
      );
    }
    prevUnreadRef.current = unreadCount;
  }, [unreadCount, notificationsOpen]);

  const showNotificationsBell = Boolean(
    connection && notificationsAvailable === true && !notificationsHiddenBy404,
  );

  const handleUnavailableNotifications = useCallback(() => {
    setNotificationsHiddenBy404(true);
  }, []);

  const handleCloseNotifications = useCallback(() => {
    setNotificationsOpen(false);
  }, []);

  const handleNavigateFromNotification = useCallback(
    (item: NotificationItem) => {
      setNotificationsOpen(false);
      if (item.ref.kind === 'production' && productionNotice?.productionId === item.ref.id) {
        dismissNotice();
      }
      setNotificationFocusTarget({
        kind: item.ref.kind,
        id: item.ref.id,
        nonce: Date.now(),
      });
      setPage('production');
    },
    [dismissNotice, productionNotice],
  );

  const handleNavigateFromError = useCallback((item: ErrorCenterItem) => {
    if (item.ref.kind === 'job') {
      setFocusJobId(item.ref.id);
      setPage('production');
      return;
    }
    const targetId =
      item.ref.kind === 'workflow' && item.ref.workflow_id
        ? item.ref.workflow_id
        : item.ref.id;
    setNotificationFocusTarget({
      kind: item.ref.kind,
      id: targetId,
      nonce: Date.now(),
    });
    setPage('production');
  }, []);

  useEffect(() => {
    if (!shortcutPrefs.enabled) return;

    const NAV_TARGETS: { id: ShortcutId; page: Page | 'production-center'; label: string }[] = [
      { id: 'nav.overview', page: 'overview', label: 'Visão geral' },
      { id: 'nav.radar', page: 'radar', label: 'Radar' },
      { id: 'nav.library', page: 'library', label: 'Biblioteca' },
      { id: 'nav.production', page: 'production', label: 'Produção' },
      { id: 'nav.production-center', page: 'production-center', label: 'Central de Produção' },
      { id: 'nav.channels', page: 'channels', label: 'Canais' },
      { id: 'nav.history', page: 'history', label: 'Histórico operacional' },
      { id: 'nav.transcription', page: 'transcription', label: 'Transcrição' },
      { id: 'nav.settings', page: 'settings', label: 'Ajustes' },
    ];

    const handleGlobalKeyDown = (event: KeyboardEvent) => {
      if (event.repeat || event.isComposing) return;

      const editable = isEditableTarget(event.target);

      if (!editable && matchesShortcut(event, shortcutPrefs.bindings['help.open'])) {
        event.preventDefault();
        setShortcutsHelpOpen(prev => !prev);
        return;
      }

      if (shortcutsHelpOpen) return;

      if (showNotificationsBell && matchesShortcut(event, shortcutPrefs.bindings['notifications.toggle'])) {
        event.preventDefault();
        setNotificationsOpen(prev => !prev);
        return;
      }

      if (notificationsOpen) return;

      if (matchesShortcut(event, shortcutPrefs.bindings['library.search'])) {
        event.preventDefault();
        setPage('library');
        setLibrarySearchNonce(value => value + 1);
        setNavAnnouncement('Biblioteca: busca focada.');
        return;
      }

      if (matchesShortcut(event, shortcutPrefs.bindings['nav.error-center'])) {
        event.preventDefault();
        setPage('history');
        setErrorCenterFocusNonce(n => n + 1);
        setNavAnnouncement('Navegou para Central de erros.');
        return;
      }

      for (const target of NAV_TARGETS) {
        if (matchesShortcut(event, shortcutPrefs.bindings[target.id])) {
          event.preventDefault();
          setPage(target.page);
          setNavAnnouncement(`Navegou para ${target.label}.`);
          requestAnimationFrame(() => {
            document.getElementById('main')?.focus();
          });
          return;
        }
      }
    };

    window.addEventListener('keydown', handleGlobalKeyDown);
    return () => window.removeEventListener('keydown', handleGlobalKeyDown);
  }, [notificationsOpen, shortcutPrefs, shortcutsHelpOpen, showNotificationsBell]);

  return <div className="horus" data-theme={effectiveTheme}>
    <a className="skip-link" href="#main">Ir para o conteúdo</a>
    <aside className="sidebar" aria-label="Navegação lateral"><div className="brand"><div className="brand-name"><EyeMark /><span>HORUS</span></div><p className="eyebrow">INTELIGÊNCIA AUDIOVISUAL</p></div>
      <nav aria-label="Navegação principal"><p className="eyebrow navigation-label">ESPAÇO DE TRABALHO</p>{navigation.map(item => <button key={item.id} aria-current={page === item.id ? 'page' : undefined} onClick={() => setPage(item.id)}>{item.id === 'production' && activeProductions > 0 ? `${item.title} (${activeProductions})` : item.title}</button>)}<button aria-current={page === 'production-center' ? 'page' : undefined} onClick={() => setPage('production-center')}>Central de Produção</button><p className="eyebrow navigation-label tools-label">FERRAMENTAS</p><button aria-current={page === 'transcription' ? 'page' : undefined} onClick={() => setPage('transcription')}>Transcrição</button>{showNotificationsBell && <NotificationBellButton buttonRef={bellButtonRef} unreadCount={unreadCount} open={notificationsOpen} onToggle={() => setNotificationsOpen(prev => !prev)} />}</nav>
      <div className="sr-only" aria-live="polite" aria-atomic="true">{bellAnnouncement}</div>
      <div className="sr-only" aria-live="polite" aria-atomic="true">{navAnnouncement}</div>
      <div className="sidebar-bottom"><div className="integration"><p className="eyebrow">HERMES AGENT</p><p className="muted"><span className="status-dot" />{hermes?.connected ? 'API verificada' : hermes?.cli_available ? 'CLI localizado' : 'Integração pendente'}</p></div><button ref={shortcutsTriggerRef} type="button" className="shortcuts-help-trigger" data-testid="shortcuts-help-button" aria-keyshortcuts={shortcutPrefs.enabled ? shortcutPrefs.bindings['help.open'] || undefined : undefined} onClick={() => setShortcutsHelpOpen(true)}>Atalhos ({shortcutPrefs.bindings['help.open'] || '?'})</button><button className="settings-navigation" aria-current={page === 'settings' ? 'page' : undefined} onClick={() => setPage('settings')}>Ajustes</button><p className="eyebrow signature">SEU ESPAÇO DE CRIAÇÃO</p></div>
    </aside>
    <div className="workspace-shell"><header className="topbar"><div className="row"><span className="eyebrow accent">HORUS</span><span className="muted">/</span><span className="eyebrow">CENTRAL AUDIOVISUAL</span></div><span className="tag">APLICATIVO EM DESENVOLVIMENTO</span></header>
      {productionNotice && (
        <div
          className={`production-notice ${productionNotice.kind}`}
          role="status"
          aria-live="polite"
          aria-label="Aviso de produção"
          data-testid="production-notice"
        >
          <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
            <span className={
              productionNotice.kind === 'succeeded'
                ? 'tag good'
                : productionNotice.kind === 'interrupted'
                  ? 'tag warn'
                  : 'tag bad'
            }>
              {productionNotice.badge}
            </span>
            <span>{productionNotice.message}</span>
          </div>
          <div className="actions">
            <button
              type="button"
              className="button primary"
              onClick={() => {
                dismissNotice();
                setPage('production');
              }}
            >
              Abrir produção
            </button>
            <button
              type="button"
              className="button"
              onClick={() => dismissNotice()}
              aria-label="Fechar aviso de produção"
            >
              Fechar
            </button>
          </div>
        </div>
      )}
      <main id="main" tabIndex={-1}><Suspense fallback={<p className="loading" role="status">Abrindo seu espaço…</p>}>
        {page === 'overview' ? <Overview navigate={setPage} connection={connection} />
          : page === 'settings' ? <Settings theme={theme} setTheme={setTheme} connection={connection} connect={candidate => { setConnection(candidate); setBackendError(''); }} hermes={hermes} onHermesStatus={setHermes} shortcutPrefs={shortcutPrefs} onChangeShortcutPrefs={handleShortcutPrefsChange} />
          : page === 'library' ? <Library connection={connection} searchFocusNonce={librarySearchNonce} searchShortcutBinding={shortcutPrefs.enabled ? shortcutPrefs.bindings['library.search'] : undefined} />
          : page === 'transcription' ? null
          : page === 'production-center' ? <div className="page"><ProductionCenter connection={connection} onOpenProduction={stepId => {
              if (stepId) {
                setNotificationFocusTarget({ kind: 'guide_step', id: stepId, nonce: Date.now() });
              }
              setPage('production');
            }} onOpenSettings={() => setPage('settings')} /></div>
          : page === 'radar' ? <div className="page"><SectionHeader section={sections.radar} /><Radar connection={connection} onOpenProduction={() => setPage('production')} /></div>
          : <div className="page"><SectionHeader section={sections[page]} />{page === 'production' && <EditorialWorkflows connection={connection} onOpenCenter={() => setPage('production-center')} focusTarget={notificationFocusTarget} />}{(page === 'production' || page === 'channels') && <JobWorkspace connection={connection} focusJobId={focusJobId} />}{page === 'history' && <ErrorCenter connection={connection} onNavigateItem={handleNavigateFromError} focusNonce={errorCenterFocusNonce} />}</div>}
        {(transcriptionOpened || page === 'transcription') && (
          <Transcription
            connection={connection}
            hidden={page !== 'transcription'}
            shortcutsEnabled={shortcutPrefs.enabled}
            saveShortcutBinding={shortcutPrefs.bindings['transcription.save']}
            onOpenSettings={() => {
              setPage('settings');
              requestAnimationFrame(() => {
                document.getElementById('transcription-models')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
              });
            }}
          />
        )}
      </Suspense></main>
      <footer><span>{backendError ? `Backend indisponível: ${backendError}` : connection ? 'Backend local conectado' : nativeDesktop ? 'Preparando backend local…' : 'Prévia no navegador'}</span><span>Transcrição local com Whisper · Roteiro manual e produção local ativos.</span></footer>
    </div>
    {showNotificationsBell && (
      <NotificationsPanel
        connection={connection}
        open={notificationsOpen}
        unreadCount={unreadCount}
        onUnreadCountChange={setUnreadCount}
        onUnavailable={handleUnavailableNotifications}
        onNavigateItem={handleNavigateFromNotification}
        onClose={handleCloseNotifications}
        triggerRef={bellButtonRef}
      />
    )}
    <ShortcutsHelpModal
      open={shortcutsHelpOpen}
      enabled={shortcutPrefs.enabled}
      bindings={shortcutPrefs.bindings}
      onClose={() => setShortcutsHelpOpen(false)}
      onOpenSettings={() => setPage('settings')}
      triggerRef={shortcutsTriggerRef}
    />
  </div>;
}
