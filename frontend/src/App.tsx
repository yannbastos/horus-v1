import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { EyeMark } from './components/Identity';
import { NotificationBellButton, NotificationsPanel } from './components/NotificationsCenter';
import { navigation, Overview, Preview, SectionHeader, sections } from './components/Sections';
import type { Page } from './components/Sections';
import { connectDesktop, nativeDesktop } from './platform/desktop';
import { api } from './services/api';
import type { Connection, HermesStatus } from './services/api';
import { useNotificationsBell } from './services/notifications';
import type { NotificationItem } from './services/notifications';
import { useProductionObserver } from './services/productionNotices';
import type { EditorialFocusTarget } from './features/EditorialWorkflows';
import type { Theme } from './features/Settings';

const Library = lazy(() => import('./features/Library'));
const JobWorkspace = lazy(() => import('./features/JobWorkspace'));
const Settings = lazy(() => import('./features/Settings'));
const Transcription = lazy(() => import('./features/Transcription'));
const Radar = lazy(() => import('./features/Radar'));
const EditorialWorkflows = lazy(() => import('./features/EditorialWorkflows'));
const ProductionCenter = lazy(() => import('./features/ProductionCenter'));

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
  const bellButtonRef = useRef<HTMLButtonElement | null>(null);
  const prevUnreadRef = useRef(0);

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

  return <div className="horus" data-theme={effectiveTheme}>
    <a className="skip-link" href="#main">Ir para o conteúdo</a>
    <aside className="sidebar" aria-label="Navegação lateral"><div className="brand"><div className="brand-name"><EyeMark /><span>HORUS</span></div><p className="eyebrow">INTELIGÊNCIA AUDIOVISUAL</p></div>
      <nav aria-label="Navegação principal"><p className="eyebrow navigation-label">ESPAÇO DE TRABALHO</p>{navigation.map(item => <button key={item.id} aria-current={page === item.id ? 'page' : undefined} onClick={() => setPage(item.id)}>{item.id === 'production' && activeProductions > 0 ? `${item.title} (${activeProductions})` : item.title}</button>)}<button aria-current={page === 'production-center' ? 'page' : undefined} onClick={() => setPage('production-center')}>Central de Produção</button><p className="eyebrow navigation-label tools-label">FERRAMENTAS</p><button aria-current={page === 'transcription' ? 'page' : undefined} onClick={() => setPage('transcription')}>Transcrição</button>{showNotificationsBell && <NotificationBellButton buttonRef={bellButtonRef} unreadCount={unreadCount} open={notificationsOpen} onToggle={() => setNotificationsOpen(prev => !prev)} />}</nav>
      <div className="sr-only" aria-live="polite" aria-atomic="true">{bellAnnouncement}</div>
      <div className="sidebar-bottom"><div className="integration"><p className="eyebrow">HERMES AGENT</p><p className="muted"><span className="status-dot" />{hermes?.connected ? 'API verificada' : hermes?.cli_available ? 'CLI localizado' : 'Integração pendente'}</p></div><button className="settings-navigation" aria-current={page === 'settings' ? 'page' : undefined} onClick={() => setPage('settings')}>Ajustes</button><p className="eyebrow signature">SEU ESPAÇO DE CRIAÇÃO</p></div>
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
          : page === 'settings' ? <Settings theme={theme} setTheme={setTheme} connection={connection} connect={candidate => { setConnection(candidate); setBackendError(''); }} hermes={hermes} onHermesStatus={setHermes} />
          : page === 'library' ? <Library connection={connection} />
          : page === 'transcription' ? null
          : page === 'production-center' ? <div className="page"><ProductionCenter connection={connection} onOpenProduction={() => setPage('production')} onOpenSettings={() => setPage('settings')} /></div>
          : page === 'radar' ? <div className="page"><SectionHeader section={sections.radar} /><Radar connection={connection} onOpenProduction={() => setPage('production')} /></div>
          : <div className="page"><SectionHeader section={sections[page]} />{page === 'production' && <EditorialWorkflows connection={connection} onOpenCenter={() => setPage('production-center')} focusTarget={notificationFocusTarget} />}{(page === 'production' || page === 'channels') && <JobWorkspace connection={connection} />}{page === 'history' && <Preview section={sections[page]} />}</div>}
        {(transcriptionOpened || page === 'transcription') && (
          <Transcription
            connection={connection}
            hidden={page !== 'transcription'}
            onOpenSettings={() => {
              setPage('settings');
              requestAnimationFrame(() => {
                document.getElementById('transcription-models')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
              });
            }}
          />
        )}
      </Suspense></main>
      <footer><span>{backendError ? `Backend indisponível: ${backendError}` : connection ? 'Backend local conectado' : nativeDesktop ? 'Preparando backend local…' : 'Prévia no navegador'}</span><span>Transcrição local com Whisper · Orquestração e módulos editoriais em preparação.</span></footer>
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
  </div>;
}
