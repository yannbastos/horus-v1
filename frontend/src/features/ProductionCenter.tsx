import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Connection } from '../services/api';
import { describeLoadError, filterAndSort, loadProductionCenter } from '../services/productionCenter';
import type { CenterRow, StageFilter } from '../services/productionCenter';
import { StatusMessage } from '../components/StatusMessage';
import CenterFilters from './production-center/CenterFilters';
import CenterTable from './production-center/CenterTable';
import FirstVideoGuide from './production-center/FirstVideoGuide';

export default function ProductionCenter({ connection, onOpenProduction, onOpenSettings }: {
  connection: Connection | null;
  onOpenProduction: () => void;
  onOpenSettings?: () => void;
}) {
  const [rows, setRows] = useState<CenterRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [stage, setStage] = useState<StageFilter>('all');
  const [onlyBlocked, setOnlyBlocked] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const reload = useCallback(async (signal?: AbortSignal) => {
    if (!connection) return;
    setLoading(true);
    setError('');
    try {
      const loaded = await loadProductionCenter(connection, signal);
      if (!signal?.aborted) setRows(loaded);
    } catch (reason) {
      if (!signal?.aborted) { setRows(null); setError(describeLoadError(reason)); }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [connection]);

  useEffect(() => {
    const controller = new AbortController();
    setRows(null);
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  const visible = useMemo(() => (rows ? filterAndSort(rows, stage, onlyBlocked) : []), [rows, stage, onlyBlocked]);

  return (
    <section className="card" aria-labelledby="production-center-title" style={{ display: 'grid', gap: 16 }}>
      <div className="row spread">
        <div>
          <p className="eyebrow accent">CENTRAL DE PRODUÇÃO</p>
          <h2 id="production-center-title">Cada vídeo, sua etapa e o próximo passo humano</h2>
          <p className="muted">Proposta, roteiro, produção e revisão humana. Estados vêm do backend local; o que não vem aparece como “não informado”. Não há publicação.</p>
        </div>
        <button type="button" className="button" onClick={() => { setRefreshKey(value => value + 1); void reload(); }} disabled={!connection || loading}>ATUALIZAR</button>
      </div>
      <FirstVideoGuide
        connection={connection}
        refreshKey={refreshKey}
        onOpenSettings={onOpenSettings ?? (() => {})}
        onOpenProduction={onOpenProduction}
      />
      {!connection && <StatusMessage state="empty" role="status">Conecte o backend local em Ajustes para ver a Central de Produção.</StatusMessage>}
      {connection && loading && <StatusMessage state="loading">Carregando vídeos…</StatusMessage>}
      {error && <StatusMessage state="error">{error}</StatusMessage>}
      {connection && rows && (
        <>
          <CenterFilters stage={stage} onlyBlocked={onlyBlocked} onStage={setStage} onBlocked={setOnlyBlocked} />
          <StatusMessage state="success">{visible.length} de {rows.length} vídeo(s) exibido(s). A lista do backend é limitada a 100 fluxos.</StatusMessage>
          {visible.length === 0
            ? <StatusMessage state="empty">{rows.length === 0 ? 'Nenhum fluxo editorial registrado.' : 'Nenhum vídeo corresponde aos filtros.'}</StatusMessage>
            : <CenterTable rows={visible} onOpen={() => onOpenProduction()} />}
        </>
      )}
    </section>
  );
}
