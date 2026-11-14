import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { StatusMessage } from './StatusMessage';

export type MediaKind = 'video' | 'audio';

export type LoadedMediaMetadata = {
  durationSeconds: number | null;
  width: number | null;
  height: number | null;
};

export type PlayingMedia = {
  id?: string;
  name: string;
  url: string;
  size?: number | null;
  origin?: 'local_file' | 'registered';
};

const AUDIO_EXTENSIONS = new Set(['wav', 'mp3', 'ogg', 'm4a', 'flac', 'aac']);

export function getMediaExtension(name: string): string {
  const trimmed = name.trim();
  const dot = trimmed.lastIndexOf('.');
  if (dot <= 0 || dot === trimmed.length - 1) return '';
  return trimmed.slice(dot + 1).toLowerCase();
}

export function getMediaFormatLabel(name: string): string {
  const ext = getMediaExtension(name);
  return ext ? ext.toUpperCase() : 'MÍDIA';
}

export function getMediaKind(name: string, mimeType?: string): MediaKind {
  if (mimeType && mimeType.toLowerCase().startsWith('audio/')) {
    return 'audio';
  }
  const ext = getMediaExtension(name);
  return AUDIO_EXTENSIONS.has(ext) ? 'audio' : 'video';
}

export function formatMediaSize(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatMediaDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—';
  const total = Math.floor(seconds);
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const mm = String(mins).padStart(2, '0');
  const ss = String(secs).padStart(2, '0');
  if (hrs > 0) {
    return `${String(hrs).padStart(2, '0')}:${mm}:${ss}`;
  }
  return `${mm}:${ss}`;
}

export type SubtitleCue = {
  startSeconds: number;
  endSeconds: number;
  text: string;
};

function parseTimestampToSeconds(raw: string): number | null {
  const clean = raw.trim().replace(',', '.');
  const parts = clean.split(':');
  if (parts.length === 3) {
    const h = Number(parts[0]);
    const m = Number(parts[1]);
    const s = Number(parts[2]);
    if (Number.isFinite(h) && Number.isFinite(m) && Number.isFinite(s)) {
      return h * 3600 + m * 60 + s;
    }
  } else if (parts.length === 2) {
    const m = Number(parts[0]);
    const s = Number(parts[1]);
    if (Number.isFinite(m) && Number.isFinite(s)) {
      return m * 60 + s;
    }
  }
  return null;
}

export function parseSubtitleCues(rawContent: string): SubtitleCue[] {
  const normalized = rawContent.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const blocks = normalized.split(/\n{2,}/);
  const cues: SubtitleCue[] = [];

  for (const block of blocks) {
    const lines = block
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean);
    if (lines.length === 0 || lines[0].startsWith('WEBVTT')) continue;

    const arrowIndex = lines.findIndex(line => line.includes('-->'));
    if (arrowIndex === -1) continue;

    const timingLine = lines[arrowIndex];
    const [rawStart, rawEndWithSettings] = timingLine.split('-->');
    if (!rawStart || !rawEndWithSettings) continue;

    const rawEnd = rawEndWithSettings.trim().split(/\s+/)[0] ?? '';
    const startSeconds = parseTimestampToSeconds(rawStart);
    const endSeconds = parseTimestampToSeconds(rawEnd);
    const text = lines.slice(arrowIndex + 1).join(' ');

    if (
      startSeconds != null &&
      endSeconds != null &&
      endSeconds >= startSeconds &&
      text.length > 0
    ) {
      cues.push({ startSeconds, endSeconds, text });
    }
  }

  return cues;
}

export function convertSrtToVtt(rawContent: string): string {
  const normalized = rawContent.replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
  if (normalized.startsWith('WEBVTT')) {
    return normalized;
  }
  const vttBody = normalized.replace(
    /(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})/g,
    '$1.$2 --> $3.$4'
  );
  return `WEBVTT\n\n${vttBody}\n`;
}

export function MediaPlayer({
  item,
  onClose,
  onError,
  onMetadataLoaded,
  initialSubtitleCues,
  initialSubtitleLabel,
}: {
  item: PlayingMedia;
  onClose: () => void;
  onError?: (message: string) => void;
  onMetadataLoaded?: (item: PlayingMedia, meta: LoadedMediaMetadata) => void;
  initialSubtitleCues?: SubtitleCue[];
  initialSubtitleLabel?: string;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const subtitleInputRef = useRef<HTMLInputElement>(null);
  const subtitleBlobRef = useRef<string | null>(null);

  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);
  const [subtitleTrackUrl, setSubtitleTrackUrl] = useState<string | null>(null);
  const [subtitleLabel, setSubtitleLabel] = useState<string>(initialSubtitleLabel ?? '');
  const [subtitleCues, setSubtitleCues] = useState<SubtitleCue[]>(initialSubtitleCues ?? []);
  const [subtitlesEnabled, setSubtitlesEnabled] = useState(true);
  const [subtitleError, setSubtitleError] = useState('');

  const kind = getMediaKind(item.name);
  const formatLabel = getMediaFormatLabel(item.name);

  useEffect(() => {
    setIsPlaying(false);
    setIsMuted(false);
    setPlaybackRate(1);
    setCurrentTime(0);
    setDuration(null);
    setDimensions(null);
    setSubtitleError('');
    if (!initialSubtitleCues) {
      setSubtitleCues([]);
      setSubtitleLabel('');
    }
    if (subtitleBlobRef.current) {
      URL.revokeObjectURL(subtitleBlobRef.current);
      subtitleBlobRef.current = null;
    }
    setSubtitleTrackUrl(null);
  }, [item.url, initialSubtitleCues]);

  useEffect(() => {
    return () => {
      if (subtitleBlobRef.current) {
        URL.revokeObjectURL(subtitleBlobRef.current);
        subtitleBlobRef.current = null;
      }
    };
  }, []);

  function togglePlayPause() {
    const el = videoRef.current;
    if (!el) return;
    if (el.paused) {
      void el.play().catch(() => {
        /* ignore autoplay/user-gesture rejection */
      });
    } else {
      el.pause();
    }
  }

  function seekRelative(deltaSeconds: number) {
    const el = videoRef.current;
    if (!el) return;
    const maxTime = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : Infinity;
    const next = Math.max(0, Math.min(maxTime, (el.currentTime || 0) + deltaSeconds));
    el.currentTime = next;
    setCurrentTime(next);
  }

  function toggleMute() {
    const el = videoRef.current;
    if (!el) return;
    const next = !el.muted;
    el.muted = next;
    setIsMuted(next);
  }

  function handleRateChange(nextRate: number) {
    setPlaybackRate(nextRate);
    if (videoRef.current) {
      videoRef.current.playbackRate = nextRate;
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    const target = event.target as HTMLElement | null;
    if (
      target &&
      (target.tagName === 'INPUT' ||
        target.tagName === 'SELECT' ||
        target.tagName === 'TEXTAREA' ||
        target.tagName === 'VIDEO')
    ) {
      return;
    }

    if (event.key === ' ' || event.key === 'k' || event.key === 'K') {
      event.preventDefault();
      togglePlayPause();
    } else if (event.key === 'ArrowLeft' || event.key === 'j' || event.key === 'J') {
      event.preventDefault();
      seekRelative(-5);
    } else if (event.key === 'ArrowRight' || event.key === 'l' || event.key === 'L') {
      event.preventDefault();
      seekRelative(5);
    } else if (event.key === 'm' || event.key === 'M') {
      event.preventDefault();
      toggleMute();
    }
  }

  async function handleSubtitleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;

    setSubtitleError('');
    try {
      const rawText = await file.text();
      const cues = parseSubtitleCues(rawText);
      if (cues.length === 0) {
        setSubtitleError('Arquivo de legenda sem blocos de tempo válidos (.SRT ou .VTT).');
        return;
      }
      const vttText = convertSrtToVtt(rawText);
      if (subtitleBlobRef.current) {
        URL.revokeObjectURL(subtitleBlobRef.current);
      }
      const blob = new Blob([vttText], { type: 'text/vtt' });
      const url = URL.createObjectURL(blob);
      subtitleBlobRef.current = url;
      setSubtitleTrackUrl(url);
      setSubtitleCues(cues);
      setSubtitleLabel(file.name);
      setSubtitlesEnabled(true);
    } catch {
      setSubtitleError('Não foi possível ler o arquivo de legenda local.');
    }
  }

  function clearSubtitle() {
    if (subtitleBlobRef.current) {
      URL.revokeObjectURL(subtitleBlobRef.current);
      subtitleBlobRef.current = null;
    }
    setSubtitleTrackUrl(null);
    setSubtitleCues([]);
    setSubtitleLabel('');
    setSubtitleError('');
  }

  const activeCue =
    subtitlesEnabled && subtitleCues.length > 0
      ? subtitleCues.find(cue => currentTime >= cue.startSeconds && currentTime <= cue.endSeconds) ??
        null
      : null;

  return (
    <section
      className="card player"
      role="region"
      aria-label={`Player de mídia: ${item.name}`}
      tabIndex={-1}
      onKeyDown={handleKeyDown}
    >
      <div className="row spread">
        <div className="player-header-info">
          <div className="row" style={{ flexWrap: 'wrap', gap: '6px' }}>
            <span className="tag">{kind === 'audio' ? 'ÁUDIO' : 'VÍDEO'}</span>
            <span className="tag">{formatLabel}</span>
            {item.size != null && <span className="tag">{formatMediaSize(item.size)}</span>}
            {duration != null && <span className="tag">DURAÇÃO {formatMediaDuration(duration)}</span>}
            {dimensions && (
              <span className="tag">
                {dimensions.width}×{dimensions.height}
              </span>
            )}
          </div>
          <h3>{item.name}</h3>
        </div>
        <button className="button" type="button" onClick={onClose}>
          FECHAR PRÉVIA
        </button>
      </div>

      <video
        ref={videoRef}
        key={item.url}
        controls
        preload="metadata"
        src={item.url}
        aria-label={`Reprodutor de ${item.name}`}
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
        onVolumeChange={e => setIsMuted(e.currentTarget.muted)}
        onTimeUpdate={e => setCurrentTime(e.currentTarget.currentTime)}
        onLoadedMetadata={e => {
          const el = e.currentTarget;
          const dur = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : null;
          const width = el.videoWidth > 0 ? el.videoWidth : null;
          const height = el.videoHeight > 0 ? el.videoHeight : null;
          setDuration(dur);
          setDimensions(width && height ? { width, height } : null);
          onMetadataLoaded?.(item, { durationSeconds: dur, width, height });
        }}
        onError={() =>
          onError?.(
            'Não foi possível reproduzir este formato. Tente um MP4 H.264/AAC ou WebM compatível.'
          )
        }
      >
        {subtitleTrackUrl && subtitlesEnabled && (
          <track
            kind="captions"
            src={subtitleTrackUrl}
            srcLang="pt"
            label={subtitleLabel || 'Legendas locais'}
            default
          />
        )}
      </video>

      {subtitleCues.length > 0 && subtitlesEnabled && (
        <div
          className="player-caption-box"
          role="status"
          aria-live="polite"
          aria-label="Legenda atual"
        >
          <span className="eyebrow accent">LEGENDA ({subtitleLabel || 'LOCAL'})</span>
          <p>{activeCue ? activeCue.text : '—'}</p>
        </div>
      )}

      {subtitleError && <StatusMessage state="error">{subtitleError}</StatusMessage>}

      <div className="player-controls-bar" role="group" aria-label="Controles acessíveis do player">
        <div className="actions">
          <button
            className="button"
            type="button"
            onClick={togglePlayPause}
          >
            {isPlaying ? 'PAUSAR' : 'REPRODUZIR'}
          </button>
          <button
            className="button"
            type="button"
            onClick={() => seekRelative(-5)}
          >
            RETROCEDER 5S
          </button>
          <button
            className="button"
            type="button"
            onClick={() => seekRelative(5)}
          >
            AVANÇAR 5S
          </button>
          <button
            className="button"
            type="button"
            aria-pressed={isMuted}
            onClick={toggleMute}
          >
            {isMuted ? 'ATIVAR SOM' : 'SILENCIAR'}
          </button>
          <label className="player-rate-label">
            <span>Velocidade de reprodução</span>
            <select
              value={String(playbackRate)}
              onChange={e => handleRateChange(Number(e.target.value))}
            >
              <option value="0.5">0.5x</option>
              <option value="0.75">0.75x</option>
              <option value="1">1x</option>
              <option value="1.25">1.25x</option>
              <option value="1.5">1.5x</option>
              <option value="2">2x</option>
            </select>
          </label>
        </div>

        <div className="actions">
          <input
            ref={subtitleInputRef}
            className="visually-hidden"
            type="file"
            accept=".srt,.vtt"
            aria-label="Selecionar legenda local (.srt ou .vtt)"
            onChange={e => void handleSubtitleFile(e)}
          />
          <button
            className="button"
            type="button"
            onClick={() => subtitleInputRef.current?.click()}
          >
            CARREGAR LEGENDA (.SRT/.VTT)
          </button>
          {subtitleCues.length > 0 && (
            <>
              <button
                className={`button ${subtitlesEnabled ? 'active' : ''}`}
                type="button"
                aria-pressed={subtitlesEnabled}
                onClick={() => setSubtitlesEnabled(prev => !prev)}
              >
                {subtitlesEnabled ? 'OCULTAR LEGENDA' : 'EXIBIR LEGENDA'}
              </button>
              <button className="button" type="button" onClick={clearSubtitle}>
                REMOVER LEGENDA
              </button>
            </>
          )}
        </div>
      </div>

      <div className="row spread player-footer-meta">
        <span className="eyebrow" aria-label="Posição de reprodução">
          TEMPO: {formatMediaDuration(currentTime)} / {formatMediaDuration(duration)}
        </span>
        <small className="muted">
          Atalhos no painel: Espaço/K reproduzir · Setas/J/L ±5s · M silenciar
        </small>
      </div>
    </section>
  );
}
