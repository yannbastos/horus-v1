import type { DiskEncryptionDiagnostics } from '../services/api';
import { StatusCard, StatusBadge } from './SecurityWidgets';
import { StatusMessage } from './StatusMessage';

const FALLBACK_GUIDANCE_LINES = [
  'O que é: a criptografia de disco (BitLocker no Windows Pro/Enterprise/Education; "criptografia de dispositivo" no Windows Home e em alguns computadores) embaralha todo o volume para que ninguém leia os arquivos sem a chave.',
  'Por que importa: o Hórus guarda roteiros, mídia, comentários e a conexão do Hermes na pasta de dados, sem criptografia própria; o banco de dados fica no Supabase (PostgreSQL), fora deste volume. A criptografia do volume protege esses dados se o computador for perdido ou roubado.',
  'Como ativar: no Windows Pro/Enterprise/Education, abra Configurações > Privacidade e segurança > Criptografia de dispositivo, ou Painel de Controle > Sistema e Segurança > BitLocker. No Windows Home, use Configurações > Privacidade e segurança > Criptografia de dispositivo (em computadores compatíveis, com conta Microsoft). Guarde a chave de recuperação fora do computador.',
];

function parseGuidanceLines(guidance: string): string[] {
  const lines = guidance
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);
  return lines.length > 0 ? lines : FALLBACK_GUIDANCE_LINES;
}

export function DiskEncryptionCard({
  diskEncryption,
}: {
  diskEncryption: DiskEncryptionDiagnostics | null;
}) {
  const guidanceLines = parseGuidanceLines(diskEncryption?.guidance ?? '');
  const volumeLabel = diskEncryption?.volume ? `Volume ${diskEncryption.volume}` : null;

  const badge = !diskEncryption ? (
    <StatusBadge state="unverified" reason="Criptografia do disco não informada no diagnóstico." />
  ) : diskEncryption.status === 'encrypted' ? (
    <StatusBadge state="active" reason="Criptografia de disco ligada no volume dos dados." />
  ) : diskEncryption.status === 'not_encrypted' ? (
    <span className="tag warn" data-state="not_encrypted">
      SEM CRIPTOGRAFIA
    </span>
  ) : (
    <StatusBadge state="unverified" reason="Não foi possível confirmar a criptografia do volume." />
  );

  return (
    <StatusCard
      eyebrow="PROTEÇÃO DO DISCO"
      title="Criptografia do Volume"
      subtitle="Consulta somente leitura do volume onde ficam os dados locais do Hórus. Nunca bloqueia produções."
      ariaLabel="Criptografia do Volume"
      badge={
        <div className="row" style={{ gap: 'var(--Space3)', flexWrap: 'wrap' }}>
          {volumeLabel && <span className="tag">{volumeLabel}</span>}
          {badge}
        </div>
      }
    >
      <div data-testid="disk-encryption-section" style={{ display: 'grid', gap: 'var(--Space4)' }}>
        {!diskEncryption && (
          <StatusMessage state="empty" testId="disk-encryption-status">
            Estado da criptografia do disco não informado por este backend. Nenhuma função do Hórus é bloqueada.
          </StatusMessage>
        )}

        {diskEncryption?.status === 'encrypted' && (
          <StatusMessage state="success" tone="good" testId="disk-encryption-status">
            {diskEncryption.detail ||
              `${volumeLabel ?? 'O volume dos dados'} está com criptografia de disco ligada.`}
          </StatusMessage>
        )}

        {diskEncryption?.status === 'not_encrypted' && (
          <>
            <StatusMessage state="empty" role="status" testId="disk-encryption-status">
              {diskEncryption.detail ||
                `${volumeLabel ?? 'O volume dos dados'} está sem criptografia de disco ativa (ou com a proteção suspensa).`}
            </StatusMessage>
            <p className="muted small" data-testid="disk-encryption-nonblocking">
              Esta verificação apenas informa e não impede nenhuma operação ou produção no Hórus.
            </p>
            <div
              className="fact-item"
              data-testid="disk-encryption-guidance"
              style={{ display: 'grid', gap: 'var(--Space2)' }}
            >
              <p className="eyebrow">COMO PROTEGER SEUS DADOS</p>
              {guidanceLines.map((line, index) => (
                <p key={index} className="muted small">
                  {line}
                </p>
              ))}
            </div>
          </>
        )}

        {diskEncryption?.status === 'unverified' && (
          <StatusMessage state="empty" role="status" testId="disk-encryption-status">
            {diskEncryption.detail ||
              `Não foi possível confirmar se ${volumeLabel ?? 'o volume dos dados'} está criptografado.`}
          </StatusMessage>
        )}

        {diskEncryption?.status !== 'not_encrypted' && (
          <details data-testid="disk-encryption-guidance-details">
            <summary className="small" style={{ cursor: 'pointer' }}>
              Como verificar ou ativar a criptografia de disco no Windows
            </summary>
            <div style={{ display: 'grid', gap: 'var(--Space2)', marginTop: 'var(--Space3)' }}>
              {guidanceLines.map((line, index) => (
                <p key={index} className="muted small">
                  {line}
                </p>
              ))}
            </div>
          </details>
        )}
      </div>
    </StatusCard>
  );
}
