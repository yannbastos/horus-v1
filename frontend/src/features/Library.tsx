import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../services/api';
import type { Connection, Media } from '../services/api';
import { nativeDesktop, selectDirectory, selectMedia } from '../platform/desktop';
import { SectionHeader, sections } from '../components/Sections';
import { StatusMessage } from '../components/StatusMessage';
import {
  MediaPlayer,
  formatMediaDuration,
  formatMediaSize,
  getMediaFormatLabel,
  getMediaKind,
} from '../components/MediaPlayer';
import type { LoadedMediaMetadata, PlayingMedia } from '../components/MediaPlayer';

type MediaTypeFilter = 'all' | 'video' | 'audio';
type MediaSortOrder = 'name_asc' | 'size_desc' | 'size_asc';

export default function Library({ connection }: { connection: Connection | null }) {
  const [media, setMedia] = useState<Media[]>([]);
  const [playing, setPlaying] = useState<PlayingMedia | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState<MediaTypeFilter>('all');
  const [sortOrder, setSortOrder] = useState<MediaSortOrder>('name_asc');
  const [metadataByKey, setMetadataByKey] = useState<Record<string, LoadedMediaMetadata>>({});

  const input = useRef<HTMLInputElement>(null);
  const blob = useRef<string | null>(null);

  const loadMedia = useCallback(async (conn: Connection) => {
    setLoading(true);
    setError('');
    try {
      const list = await api<Media[]>(conn, '/v1/media');
      setMedia(list);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (connection) {
      void loadMedia(connection);
    } else {
      setMedia([]);
      setLoading(false);
    }
    return () => {
      if (blob.current) {
        URL.revokeObjectURL(blob.current);
        blob.current = null;
      }
    };
  }, [connection, loadMedia]);

  async function choose(folder = false) {
    setError('');
    if (!nativeDesktop) {
      input.current?.click();
      return;
    }
    if (!connection) {
      setError('O backend local precisa estar disponível para registrar mídia.');
      return;
    }
    setLoading(true);
    try {
      const path = folder ? await selectDirectory() : await selectMedia();
      if (!path) return;
      await api(connection, folder ? '/v1/media/folder' : '/v1/media', {
        method: 'POST',
        body: JSON.stringify({ path }),
      });
      const list = await api<Media[]>(connection, '/v1/media');
      setMedia(list);
      if (!list.length) {
        setError('Nenhum arquivo compatível encontrado nessa pasta.');
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  const handleMetadataLoaded = useCallback((item: PlayingMedia, meta: LoadedMediaMetadata) => {
    const key = item.id ?? item.name;
    setMetadataByKey(prev => ({
      ...prev,
      [key]: meta,
    }));
  }, []);

  const counts = useMemo(() => {
    let video = 0;
    let audio = 0;
    let totalBytes = 0;
    for (const item of media) {
      totalBytes += item.size || 0;
      if (getMediaKind(item.name) === 'audio') {
        audio += 1;
      } else {
        video += 1;
      }
    }
    return { video, audio, totalBytes };
  }, [media]);

  const filteredMedia = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    const filtered = media.filter(item => {
      const kind = getMediaKind(item.name);
      if (typeFilter !== 'all' && kind !== typeFilter) {
        return false;
      }
      if (!normalizedQuery) return true;
      const format = getMediaFormatLabel(item.name).toLowerCase();
      return (
        item.name.toLowerCase().includes(normalizedQuery) ||
        format.includes(normalizedQuery)
      );
    });

    return [...filtered].sort((a, b) => {
      if (sortOrder === 'size_desc') return b.size - a.size;
      if (sortOrder === 'size_asc') return a.size - b.size;
      return a.name.localeCompare(b.name, 'pt-BR', { sensitivity: 'base' });
    });
  }, [media, searchQuery, typeFilter, sortOrder]);

  function clearFilters() {
    setSearchQuery('');
    setTypeFilter('all');
    setSortOrder('name_asc');
  }

  return (
    <div className="page">
      <SectionHeader section={sections.library} />

      <div className="actions">
        <button className="button primary" type="button" onClick={() => void choose()}>
          SELECIONAR VÍDEO
        </button>
        <button
          className="button"
          type="button"
          disabled={!nativeDesktop || !connection || loading}
          onClick={() => void choose(true)}
        >
          ADICIONAR PASTA
        </button>
        {connection && (
          <button
            className="button"
            type="button"
            disabled={loading}
            onClick={() => void loadMedia(connection)}
          >
            ATUALIZAR LISTA
          </button>
        )}
      </div>

      <input
        ref={input}
        className="visually-hidden"
        type="file"
        accept="video/*,audio/*"
        aria-label="Selecionar mídia local"
        onChange={event => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (!file) return;
          if (blob.current) URL.revokeObjectURL(blob.current);
          blob.current = URL.createObjectURL(file);
          setPlaying({
            name: file.name,
            url: blob.current,
            size: file.size,
            origin: 'local_file',
          });
          setError('');
        }}
      />

      {loading && (
        <StatusMessage state="loading">Carregando biblioteca de mídia…</StatusMessage>
      )}

      {error && <StatusMessage state="error">{error}</StatusMessage>}

      {playing && (
        <MediaPlayer
          item={playing}
          onClose={() => setPlaying(null)}
          onError={msg => setError(msg)}
          onMetadataLoaded={handleMetadataLoaded}
        />
      )}

      {media.length > 0 && (
        <section
          className="card sunken library-toolbar"
          role="region"
          aria-label="Busca e filtros da biblioteca"
        >
          <div className="library-filters-row">
            <label className="library-search-label">
              Buscar por nome ou formato
              <input
                type="search"
                aria-label="Buscar mídia por nome"
                placeholder="Ex.: entrevista, .mp4, .wav…"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
              />
            </label>

            <div className="library-type-group" role="group" aria-label="Filtrar por tipo de mídia">
              <button
                type="button"
                className={`button ${typeFilter === 'all' ? 'active' : ''}`}
                aria-pressed={typeFilter === 'all'}
                onClick={() => setTypeFilter('all')}
              >
                TODOS ({media.length})
              </button>
              <button
                type="button"
                className={`button ${typeFilter === 'video' ? 'active' : ''}`}
                aria-pressed={typeFilter === 'video'}
                onClick={() => setTypeFilter('video')}
              >
                VÍDEO ({counts.video})
              </button>
              <button
                type="button"
                className={`button ${typeFilter === 'audio' ? 'active' : ''}`}
                aria-pressed={typeFilter === 'audio'}
                onClick={() => setTypeFilter('audio')}
              >
                ÁUDIO ({counts.audio})
              </button>
            </div>

            <label className="library-sort-label">
              Ordenar por
              <select
                aria-label="Ordenar mídias"
                value={sortOrder}
                onChange={e => setSortOrder(e.target.value as MediaSortOrder)}
              >
                <option value="name_asc">Nome (A–Z)</option>
                <option value="size_desc">Maior tamanho</option>
                <option value="size_asc">Menor tamanho</option>
              </select>
            </label>
          </div>

          <div className="row spread">
            <StatusMessage state="empty" role="status">
              Exibindo {filteredMedia.length} de {media.length} arquivo(s) · Total:{' '}
              {formatMediaSize(counts.totalBytes)}
            </StatusMessage>
            {(searchQuery.trim() !== '' || typeFilter !== 'all' || sortOrder !== 'name_asc') && (
              <button type="button" className="button" onClick={clearFilters}>
                LIMPAR FILTROS
              </button>
            )}
          </div>
        </section>
      )}

      {media.length > 0 ? (
        filteredMedia.length > 0 ? (
          <section className="media-list" aria-label="Arquivos registrados">
            {filteredMedia.map(item => {
              const kind = getMediaKind(item.name);
              const format = getMediaFormatLabel(item.name);
              const meta = metadataByKey[item.id] ?? metadataByKey[item.name];
              const isCurrent = playing?.id === item.id;

              return (
                <button
                  type="button"
                  className={`card media-item ${isCurrent ? 'selected' : ''}`}
                  key={item.id}
                  aria-current={isCurrent ? 'true' : undefined}
                  onClick={() => {
                    setError('');
                    setPlaying({
                      id: item.id,
                      name: item.name,
                      url: connection!.baseUrl + item.stream_path,
                      size: item.size,
                      origin: 'registered',
                    });
                  }}
                >
                  <div className="row spread">
                    <strong>{item.name}</strong>
                    <div className="row" style={{ gap: '6px', flexWrap: 'wrap' }}>
                      <span className="tag">{kind === 'audio' ? 'ÁUDIO' : 'VÍDEO'}</span>
                      <span className="tag">{format}</span>
                      {meta?.durationSeconds != null && (
                        <span className="tag">{formatMediaDuration(meta.durationSeconds)}</span>
                      )}
                    </div>
                  </div>
                  <span className="muted">
                    {formatMediaSize(item.size)} · Duração:{' '}
                    {meta?.durationSeconds != null
                      ? formatMediaDuration(meta.durationSeconds)
                      : 'Ao abrir prévia'}
                    {meta?.width && meta?.height ? ` · ${meta.width}×${meta.height}` : ''} ·{' '}
                    {isCurrent ? 'Em reprodução' : 'Abrir prévia'}
                  </span>
                </button>
              );
            })}
          </section>
        ) : (
          <div className="card sunken">
            <StatusMessage state="empty" role="status">
              Nenhuma mídia encontrada para o filtro atual.
            </StatusMessage>
          </div>
        )
      ) : (
        !loading && (
          <div className="empty-state">
            <span className="tag">ARQUIVOS LOCAIS</span>
            <h2>Traga a mídia da sua próxima produção.</h2>
            <p className="muted">
              Selecione um vídeo para abrir a prévia. No desktop, arquivos registrados ficam
              disponíveis novamente após reiniciar.
            </p>
          </div>
        )
      )}
    </div>
  );
}
