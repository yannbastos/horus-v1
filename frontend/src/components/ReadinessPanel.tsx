import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../services/api';
import type { Connection } from '../services/api';
import { nativeDesktop, selectLocalPath } from '../platform/desktop';
import { StatusMessage } from './StatusMessage';

type DiagnosticCheck = { id: string; name: string; available: boolean; detail: string };
type ProductionDiagnosticCheck = DiagnosticCheck & { required_for?: string[] };
type DiagnosticsResponse = {
  checks?: DiagnosticCheck[];
  notice?: string;
  production?: {
    ready: boolean;
    missing: string[];
    checks: ProductionDiagnosticCheck[];
  } | null;
};

type OptionalComponentSpec = {
  id: 'whisper' | 'kokoro' | 'yt-dlp' | 'ffmpeg';
  title: string;
  pickMode: 'directory' | 'file';
  pickLabel: string;
  placeholder: string;
  envVar: string;
  whatItUnlocks: string;
  installSteps: string;
  commandHint: string;
  isAvailable: (checks: DiagnosticCheck[], prodChecks: ProductionDiagnosticCheck[]) => boolean;
};

const COMPONENTS: OptionalComponentSpec[] = [
  {
    id: 'whisper',
    title: 'Whisper (transcrição e legendas)',
    pickMode: 'directory',
    pickLabel: 'Selecionar pasta do modelo',
    placeholder: 'C:\\Modelos\\faster-whisper-small',
    envVar: 'HORUS_ALIGNMENT_MODEL',
    whatItUnlocks: 'Transcreve áudios e vídeos offline para texto e alinha legendas palavra por palavra.',
    installSteps:
      'Instale Python com faster-whisper localmente e baixe o modelo small para uma pasta no computador (nenhum download automático é feito pelo Hórus).',
    commandHint: 'pip install faster-whisper',
    isAvailable: (checks, prodChecks) =>
      checks.some(c => c.id === 'faster_whisper' && c.available) ||
      prodChecks.some(c => c.id === 'alignment_engine' && c.available),
  },
  {
    id: 'kokoro',
    title: 'Kokoro (narração neural local)',
    pickMode: 'directory',
    pickLabel: 'Selecionar pasta do Kokoro',
    placeholder: 'C:\\Ferramentas\\kokoro-models',
    envVar: 'HORUS_KOKORO_MODEL_DIR',
    whatItUnlocks: 'Gera narração local com vozes neurais sem depender de nuvem ou API paga.',
    installSteps:
      'Prepare o ambiente Python do Kokoro e a pasta com os pesos e vozes oficiais; depois aponte HORUS_KOKORO_PYTHON e HORUS_KOKORO_MODEL_DIR.',
    commandHint: 'setx HORUS_KOKORO_MODEL_DIR "<pasta-do-kokoro>"',
    isAvailable: (_checks, prodChecks) =>
      prodChecks.some(c => c.id === 'kokoro' && c.available),
  },
  {
    id: 'yt-dlp',
    title: 'yt-dlp (download de referências)',
    pickMode: 'file',
    pickLabel: 'Selecionar arquivo yt-dlp.exe',
    placeholder: 'C:\\Ferramentas\\yt-dlp.exe',
    envVar: 'PATH',
    whatItUnlocks: 'Permite baixar vídeos de referência autorizados por você para transcrição local.',
    installSteps:
      'Baixe o executável yt-dlp.exe manualmente ou instale pelo winget/pip e mantenha-o acessível no PATH do Windows.',
    commandHint: 'winget install yt-dlp.yt-dlp',
    isAvailable: checks =>
      checks.some(c => (c.id === 'yt-dlp' || c.id === 'yt_dlp') && c.available),
  },
  {
    id: 'ffmpeg',
    title: 'FFmpeg (áudio, vídeo e render final)',
    pickMode: 'file',
    pickLabel: 'Selecionar arquivo ffmpeg.exe',
    placeholder: 'C:\\Ferramentas\\ffmpeg\\bin\\ffmpeg.exe',
    envVar: 'HORUS_FFMPEG',
    whatItUnlocks: 'Converte mídia, mescla áudio/vídeo e renderiza legendas finais em 1080×1920.',
    installSteps:
      'Baixe uma compilação do FFmpeg com suporte a libass/subtitles (com ffprobe.exe na mesma pasta) e aponte HORUS_FFMPEG para o executável.',
    commandHint: 'winget install Gyan.FFmpeg',
    isAvailable: (checks, prodChecks) =>
      checks.some(c => c.id === 'ffmpeg' && c.available) ||
      prodChecks.some(c => c.id === 'ffmpeg' && c.available),
  },
];

const STORAGE_PREFIX = 'horus.optionalComponentPath.';

function readStoredPaths(): Record<string, string> {
  const result: Record<string, string> = {};
  for (const spec of COMPONENTS) {
    const value = localStorage.getItem(`${STORAGE_PREFIX}${spec.id}`);
    if (value) result[spec.id] = value;
  }
  return result;
}

export function ReadinessPanel({
  connection,
  onOpenSettings,
}: {
  connection: Connection | null;
  onOpenSettings?: () => void;
}) {
  const [report, setReport] = useState<DiagnosticsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selectedPaths, setSelectedPaths] = useState<Record<string, string>>(() => readStoredPaths());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftPath, setDraftPath] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const loadDiagnostics = useCallback(() => {
    setRevision(v => v + 1);
  }, []);

  useEffect(() => {
    if (!connection) {
      setReport(null);
      setError('');
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void api<DiagnosticsResponse>(connection, '/v1/diagnostics', { signal: controller.signal })
      .then(data => {
        if (!controller.signal.aborted) setReport(data);
      })
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [connection, revision]);

  const items = useMemo(() => {
    const checks = report?.checks ?? [];
    const prodChecks = report?.production?.checks ?? [];
    return COMPONENTS.map(spec => ({
      spec,
      available: spec.isAvailable(checks, prodChecks),
    }));
  }, [report]);

  const missingItems = items.filter(item => !item.available);
  const availableItems = items.filter(item => item.available);

  async function handlePickPath(spec: OptionalComponentSpec) {
    if (nativeDesktop) {
      const picked = await selectLocalPath(spec.pickMode === 'directory');
      if (picked) {
        localStorage.setItem(`${STORAGE_PREFIX}${spec.id}`, picked);
        setSelectedPaths(prev => ({ ...prev, [spec.id]: picked }));
      }
      return;
    }
    setEditingId(spec.id);
    setDraftPath(selectedPaths[spec.id] ?? '');
  }

  function saveManualPath(id: string) {
    const trimmed = draftPath.trim();
    if (trimmed) {
      localStorage.setItem(`${STORAGE_PREFIX}${id}`, trimmed);
      setSelectedPaths(prev => ({ ...prev, [id]: trimmed }));
    } else {
      localStorage.removeItem(`${STORAGE_PREFIX}${id}`);
      setSelectedPaths(prev => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
    setEditingId(null);
    setDraftPath('');
  }

  async function copyCommand(id: string, command: string) {
    try {
      await navigator.clipboard?.writeText(command);
      setCopiedId(id);
      setTimeout(() => setCopiedId(curr => (curr === id ? null : curr)), 2000);
    } catch {
      /* ambiente sem clipboard */
    }
  }

  return (
    <section
      className="card sunken"
      aria-labelledby="first-run-readiness-title"
      data-testid="first-run-readiness"
    >
      <div className="row spread">
        <div>
          <p className="eyebrow accent">PRIMEIRA EXECUÇÃO · PRONTIDÃO LOCAL</p>
          <h2 id="first-run-readiness-title">O que já funciona e o que é opcional</h2>
        </div>
        <div className="actions">
          {connection && (
            <button
              type="button"
              className="button"
              disabled={loading}
              onClick={loadDiagnostics}
            >
              VERIFICAR NOVAMENTE
            </button>
          )}
          {onOpenSettings && (
            <button type="button" className="button" onClick={onOpenSettings}>
              ABRIR AJUSTES
            </button>
          )}
        </div>
      </div>

      {!connection && (
        <StatusMessage state="empty" testId="first-run-disconnected">
          Conecte o backend local em Ajustes para verificar quais componentes opcionais (Whisper,
          Kokoro, yt-dlp e FFmpeg) já estão instalados neste computador.
        </StatusMessage>
      )}

      {connection && loading && (
        <StatusMessage state="loading" ariaLive="polite" testId="first-run-loading">
          Verificando componentes locais neste computador…
        </StatusMessage>
      )}

      {connection && error && (
        <StatusMessage state="error" testId="first-run-error">
          Não foi possível consultar o diagnóstico local: {error}
        </StatusMessage>
      )}

      {connection && !loading && !error && report && (
        <>
          <div className="card" style={{ gap: 10 }}>
            <p className="eyebrow">JÁ FUNCIONA AGORA</p>
            <p className="muted">
              Interface local, Biblioteca de mídia, organização de produções e preparação de
              roteiros já funcionam sem downloads adicionais.
              {availableItems.length > 0
                ? ` Componentes prontos neste computador: ${availableItems.map(i => i.spec.title.split(' ')[0]).join(', ')}.`
                : ' Nenhum dos 4 componentes opcionais foi detectado ainda — você pode instalar apenas os que precisar.'}
            </p>
          </div>

          <StatusMessage
            state={missingItems.length === 0 ? 'success' : 'empty'}
            testId="first-run-summary"
          >
            {missingItems.length === 0
              ? 'Tudo pronto: Whisper, Kokoro, yt-dlp e FFmpeg estão disponíveis localmente.'
              : `${missingItems.length} de ${COMPONENTS.length} componentes opcionais ainda não instalados (${missingItems.map(i => i.spec.title.split(' ')[0]).join(', ')}). Nenhum download automático é executado; siga as instruções abaixo para instalar localmente.`}
          </StatusMessage>

          <div className="readiness-components-grid" style={{ display: 'grid', gap: 12 }}>
            {items.map(({ spec, available }) => {
              const savedPath = selectedPaths[spec.id];
              const isEditing = editingId === spec.id;
              return (
                <article
                  key={spec.id}
                  className="card"
                  data-testid={`optional-component-${spec.id}`}
                >
                  <div className="row spread">
                    <h3>{spec.title}</h3>
                    <span className={available ? 'tag good' : 'tag warn'}>
                      {available ? 'DISPONÍVEL' : 'OPCIONAL AUSENTE'}
                    </span>
                  </div>
                  <p className="muted">{spec.whatItUnlocks}</p>
                  {!available && (
                    <>
                      <p className="small-text">{spec.installSteps}</p>
                      <div className="row spread" style={{ flexWrap: 'wrap', gap: 8 }}>
                        <code className="eyebrow" style={{ letterSpacing: 0.4 }}>
                          {spec.commandHint}
                        </code>
                        <div className="actions">
                          <button
                            type="button"
                            className="button"
                            onClick={() => void copyCommand(spec.id, spec.commandHint)}
                          >
                            {copiedId === spec.id ? 'COPIADO' : 'COPIAR COMANDO'}
                          </button>
                          <button
                            type="button"
                            className="button"
                            data-testid={`select-component-${spec.id}`}
                            onClick={() => void handlePickPath(spec)}
                          >
                            {spec.pickLabel.toUpperCase()}
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                  {savedPath && (
                    <StatusMessage state="success" tone="plain" testId={`saved-path-${spec.id}`}>
                      Caminho anotado para {spec.envVar}: {savedPath}
                    </StatusMessage>
                  )}
                  {isEditing && (
                    <form
                      className="row"
                      style={{ flexWrap: 'wrap', gap: 8 }}
                      onSubmit={event => {
                        event.preventDefault();
                        saveManualPath(spec.id);
                      }}
                    >
                      <label style={{ flex: '1 1 260px' }}>
                        Caminho local ({spec.envVar})
                        <input
                          value={draftPath}
                          onChange={e => setDraftPath(e.target.value)}
                          placeholder={spec.placeholder}
                          autoComplete="off"
                        />
                      </label>
                      <button type="submit" className="button primary">
                        SALVAR CAMINHO
                      </button>
                      <button
                        type="button"
                        className="button"
                        onClick={() => setEditingId(null)}
                      >
                        CANCELAR
                      </button>
                    </form>
                  )}
                </article>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
