import { expect, test } from '@playwright/test';
import {
  corsHeaders,
  installBackend,
  newBackendState,
  openProduction,
  PRODUCTION_ID,
  productionFixture,
  SESSION_TOKEN,
  VIDEO_ID,
  VIDEO_SHA,
} from './production-journey-helpers';

const THUMB_HASH_1 = '1'.repeat(64);
const THUMB_HASH_2 = '2'.repeat(64);
const PNG_ID_1 = 'png_art_111111111111111111111111';
const JPG_ID_1 = 'jpg_art_111111111111111111111111';
const PNG_ID_2 = 'png_art_222222222222222222222222';
const JPG_ID_2 = 'jpg_art_222222222222222222222222';
const PNG_SHA_1 = 'aaaa1111'.repeat(8);
const JPG_SHA_1 = 'bbbb1111'.repeat(8);
const PNG_SHA_2 = 'cccc2222'.repeat(8);
const JPG_SHA_2 = 'dddd2222'.repeat(8);

// PNG 1x1 válido em bytes para prévia via Blob URL
const TINY_PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64'
);

const DEFAULT_DISCLOSURES = [
  'Narração sintética/voz local: confira a voz selecionada e seus termos de uso.',
  'Licenças pendentes: confira os direitos de todos os assets antes de publicar.',
  'publishable=false: este rascunho não autoriza publicação automática.',
];

function makeMetadataPayload(
  title = 'Como garantir sincronismo real no Hórus',
  editableBody = 'Introdução: Roteiro validado localmente.\n\nFechamento: Conclusão objetiva.'
) {
  const footer = '\n\nDivulgações obrigatórias:\n' + DEFAULT_DISCLOSURES.join('\n');
  return {
    title,
    description: editableBody + footer,
    sources: [
      {
        id: 'src_horus_arch',
        title: 'Arquitetura Canônica do Hórus',
        url: 'https://example.org/docs/architecture',
      },
      {
        id: 'src_qc_spec',
        title: 'Especificação Local de QC Audiovisual',
        url: null,
      },
    ],
    disclosures: DEFAULT_DISCLOSURES,
    licenses_pending: true,
    publishable: false,
  };
}

function makeThumbnailResponse(
  timecodeSeconds: number,
  hashHex: string,
  pngId: string,
  jpgId: string,
  pngSha: string,
  jpgSha: string
) {
  return {
    timecode_seconds: timecodeSeconds,
    video_sha256: VIDEO_SHA,
    width: 1080,
    height: 1920,
    artifacts: [
      {
        id: pngId,
        kind: 'thumbnail',
        relative_path: `publishing/thumbnails/${hashHex}/thumbnail.png`,
        sha256: pngSha,
        size_bytes: 18432,
      },
      {
        id: jpgId,
        kind: 'thumbnail',
        relative_path: `publishing/thumbnails/${hashHex}/thumbnail.jpg`,
        sha256: jpgSha,
        size_bytes: 9216,
      },
    ],
    created_at: '2026-11-14T11:30:00Z',
    publishable: false,
  };
}

function approvedProductionWithTimelineFixture() {
  return productionFixture({
    stage: 'review',
    status: 'succeeded',
    word_timings: {
      schema_version: 1,
      duration_seconds: 12.0,
      source: 'tts_engine_events',
      engine: { name: 'kokoro', voice: 'pf_dora' },
      words: [
        { index: 0, text: 'Como', start: 0.0, end: 0.5, char_offset: 0, char_length: 4, section: 0 },
        { index: 1, text: 'sincronizar', start: 4.2, end: 4.9, char_offset: 5, char_length: 11, section: 0 },
      ],
    },
    artifacts: [
      {
        id: VIDEO_ID,
        kind: 'video',
        relative_path: 'render/short.mp4',
        sha256: VIDEO_SHA,
        size_bytes: 20480,
      },
    ],
    review: {
      decision: 'approve',
      artifact_sha256: VIDEO_SHA,
      reviewed_at: '2026-11-14T11:00:00Z',
    },
  });
}

test.describe('Preparação de publicação manual — Thumbnail e Metadados (HOR-056)', () => {
  test('extrai thumbnail em segundos informados, mostra prévia autenticada e metadados 1080×1920, e permite regenerar usando o quadro atual do player', async ({
    page,
  }) => {
    const thumbCalls: Array<{ timecode_seconds: unknown; key: string }> = [];
    const artifactAuthHeaders: string[] = [];

    const state = newBackendState({
      productions: [approvedProductionWithTimelineFixture()],
      extra: async (route, path, method) => {
        if (path === `/v1/productions/${PRODUCTION_ID}/exports` && method === 'GET') {
          await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [] } });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/metadata` && method === 'GET') {
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: makeMetadataPayload(),
          });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/thumbnail` && method === 'POST') {
          const body = route.request().postDataJSON() as { timecode_seconds?: unknown };
          const key = route.request().headers()['idempotency-key'] ?? '';
          thumbCalls.push({ timecode_seconds: body.timecode_seconds, key });

          const isSecond = thumbCalls.length >= 2;
          const payload = isSecond
            ? makeThumbnailResponse(
                Number(body.timecode_seconds),
                THUMB_HASH_2,
                PNG_ID_2,
                JPG_ID_2,
                PNG_SHA_2,
                JPG_SHA_2
              )
            : makeThumbnailResponse(
                Number(body.timecode_seconds),
                THUMB_HASH_1,
                PNG_ID_1,
                JPG_ID_1,
                PNG_SHA_1,
                JPG_SHA_1
              );
          await route.fulfill({ status: 201, headers: corsHeaders, json: payload });
          return true;
        }
        if (
          (path === `/v1/productions/${PRODUCTION_ID}/artifacts/${PNG_ID_1}` ||
            path === `/v1/productions/${PRODUCTION_ID}/artifacts/${PNG_ID_2}`) &&
          method === 'GET'
        ) {
          artifactAuthHeaders.push(route.request().headers()['authorization'] ?? '');
          await route.fulfill({
            status: 200,
            headers: { ...corsHeaders, 'Content-Type': 'image/png' },
            body: TINY_PNG_BYTES,
          });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/artifacts/${VIDEO_ID}` && method === 'GET') {
          await route.fulfill({
            status: 200,
            headers: { ...corsHeaders, 'Content-Type': 'video/mp4' },
            body: Buffer.from('mp4-fixture'),
          });
          return true;
        }
        return false;
      },
    });

    await installBackend(page, state);
    const region = await openProduction(page);

    const exportPanel = region.getByTestId('production-export-panel');
    const thumbSection = exportPanel.getByTestId('publishing-thumbnail-section');
    await expect(thumbSection).toBeVisible();

    // 1. Escolhe momento informando o campo de segundos (1.5s) e gera thumbnail
    const tcInput = thumbSection.getByTestId('thumbnail-timecode-input');
    await tcInput.fill('1.5');
    await thumbSection.getByTestId('thumbnail-generate-button').click();

    await expect(thumbSection.getByTestId('thumbnail-result')).toBeVisible();
    const previewImg = thumbSection.getByTestId('thumbnail-preview-image');
    await expect(previewImg).toBeVisible();
    await expect(previewImg).toHaveAttribute('src', /^blob:/);

    const thumbMeta = thumbSection.getByTestId('thumbnail-metadata');
    await expect(thumbMeta).toContainText('Dimensões: 1080×1920');
    await expect(thumbMeta).toContainText('1.5s');
    await expect(thumbMeta).toContainText(`publishing/thumbnails/${THUMB_HASH_1}/thumbnail.png`);
    await expect(thumbMeta).toContainText(`publishing/thumbnails/${THUMB_HASH_1}/thumbnail.jpg`);
    await expect(thumbMeta).toContainText(PNG_SHA_1);
    await expect(thumbMeta).toContainText(JPG_SHA_1);

    expect(thumbCalls).toHaveLength(1);
    expect(thumbCalls[0].timecode_seconds).toBe(1.5);
    expect(thumbCalls[0].key).toBeTruthy();
    expect(artifactAuthHeaders).toEqual([`Bearer ${SESSION_TOKEN}`]);

    // 2. Navega para um momento no player / timeline (palavra em 4.2s) e clica em "Usar o quadro atual"
    const tcWorkspace = region.getByRole('region', { name: 'Revisão e navegação por timecode' });
    await tcWorkspace.getByRole('button', { name: /sincronizar/i }).click();

    await thumbSection.getByTestId('thumbnail-use-current-frame').click();
    await expect(tcInput).toHaveValue('4.2');

    // 3. Regenera em outro momento (4.2s) com nova Idempotency-Key e atualiza prévia/metadados
    await thumbSection.getByTestId('thumbnail-generate-button').click();
    await expect(thumbMeta).toContainText('4.2s');
    await expect(thumbMeta).toContainText(`publishing/thumbnails/${THUMB_HASH_2}/thumbnail.png`);
    await expect(thumbMeta).toContainText(PNG_SHA_2);

    expect(thumbCalls).toHaveLength(2);
    expect(thumbCalls[1].timecode_seconds).toBe(4.2);
    expect(thumbCalls[1].key).not.toBe(thumbCalls[0].key);
    expect(artifactAuthHeaders).toHaveLength(2);
  });

  test('exibe erros acionáveis do backend ao gerar thumbnail (409 FFmpeg/duração e 422 timecode) e rotaciona chave na tentativa seguinte', async ({
    page,
  }) => {
    const keys: string[] = [];
    let shouldFail = true;
    const conflictMsg =
      'Timecode fora da duração do vídeo; use um valor de zero até antes do fim.';

    const state = newBackendState({
      productions: [approvedProductionWithTimelineFixture()],
      extra: async (route, path, method) => {
        if (path === `/v1/productions/${PRODUCTION_ID}/exports` && method === 'GET') {
          await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [] } });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/metadata` && method === 'GET') {
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: makeMetadataPayload(),
          });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/thumbnail` && method === 'POST') {
          keys.push(route.request().headers()['idempotency-key'] ?? '');
          if (shouldFail) {
            await route.fulfill({
              status: 409,
              headers: corsHeaders,
              json: { detail: conflictMsg },
            });
            return true;
          }
          await route.fulfill({
            status: 201,
            headers: corsHeaders,
            json: makeThumbnailResponse(
              0.8,
              THUMB_HASH_1,
              PNG_ID_1,
              JPG_ID_1,
              PNG_SHA_1,
              JPG_SHA_1
            ),
          });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/artifacts/${PNG_ID_1}` && method === 'GET') {
          await route.fulfill({
            status: 200,
            headers: { ...corsHeaders, 'Content-Type': 'image/png' },
            body: TINY_PNG_BYTES,
          });
          return true;
        }
        return false;
      },
    });

    await installBackend(page, state);
    const region = await openProduction(page);
    const thumbSection = region.getByTestId('publishing-thumbnail-section');

    await thumbSection.getByTestId('thumbnail-timecode-input').fill('0.8');
    await thumbSection.getByTestId('thumbnail-generate-button').click();

    const alert = thumbSection.getByTestId('thumbnail-error-alert');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(conflictMsg);

    shouldFail = false;
    await thumbSection.getByTestId('thumbnail-generate-button').click();
    await expect(thumbSection.getByTestId('thumbnail-result')).toBeVisible();
    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(keys[0]);
  });

  test('carrega rascunho determinístico de metadados, atualiza contadores (100/5000), preserva sources e disclosures fixos, indica alterações não salvas e salva via PUT', async ({
    page,
  }) => {
    const putPayloads: Array<Record<string, unknown>> = [];
    let shouldFailPut422 = false;

    const state = newBackendState({
      productions: [approvedProductionWithTimelineFixture()],
      extra: async (route, path, method) => {
        if (path === `/v1/productions/${PRODUCTION_ID}/exports` && method === 'GET') {
          await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [] } });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/metadata` && method === 'GET') {
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: makeMetadataPayload(),
          });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/metadata` && method === 'PUT') {
          const body = route.request().postDataJSON() as Record<string, unknown>;
          putPayloads.push(body);
          if (shouldFailPut422) {
            await route.fulfill({
              status: 422,
              headers: corsHeaders,
              json: {
                detail: 'Título vazio ou descrição sem espaço para as divulgações obrigatórias.',
              },
            });
            return true;
          }
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: makeMetadataPayload(String(body.title ?? ''), String(body.description ?? '')),
          });
          return true;
        }
        return false;
      },
    });

    await installBackend(page, state);
    const region = await openProduction(page);
    const metaSection = region.getByTestId('publishing-metadata-section');
    await expect(metaSection).toBeVisible();

    const titleInput = metaSection.getByTestId('metadata-title-input');
    const descInput = metaSection.getByTestId('metadata-description-input');
    await expect(titleInput).toHaveValue('Como garantir sincronismo real no Hórus');
    await expect(descInput).toHaveValue(
      'Introdução: Roteiro validado localmente.\n\nFechamento: Conclusão objetiva.'
    );

    // Contadores iniciais (39/100 e 73/5000)
    await expect(metaSection.getByTestId('metadata-title-counter')).toContainText('39/100');
    await expect(metaSection.getByTestId('metadata-description-counter')).toContainText('73/5000');

    // Fontes e divulgações obrigatórias estão visíveis como texto fixo (sem inputs editáveis)
    const sourcesBlock = metaSection.getByTestId('metadata-sources-block');
    await expect(sourcesBlock).toContainText('Arquitetura Canônica do Hórus');
    await expect(sourcesBlock).toContainText('src_horus_arch');
    await expect(sourcesBlock).toContainText('https://example.org/docs/architecture');
    await expect(sourcesBlock).toContainText('src_qc_spec');
    await expect(sourcesBlock.locator('input, textarea, button')).toHaveCount(0);

    const disclosuresBlock = metaSection.getByTestId('metadata-disclosures-block');
    for (const disclosure of DEFAULT_DISCLOSURES) {
      await expect(disclosuresBlock).toContainText(disclosure);
    }
    await expect(disclosuresBlock.locator('input, textarea, button')).toHaveCount(0);

    // Edita título e descrição -> indicação de alterações não salvas e atualização dos contadores
    await expect(metaSection.getByTestId('metadata-unsaved-badge')).toHaveCount(0);
    await titleInput.fill('Novo título revisado para publicação manual');
    await descInput.fill('Descrição revisada pelo operador humano antes de exportar o ZIP.');

    await expect(metaSection.getByTestId('metadata-unsaved-badge')).toBeVisible();
    await expect(metaSection.getByTestId('metadata-unsaved-badge')).toContainText(
      'Alterações não salvas'
    );
    await expect(metaSection.getByTestId('metadata-title-counter')).toContainText('43/100');
    await expect(metaSection.getByTestId('metadata-description-counter')).toContainText('64/5000');

    // Simula erro 422 do backend ao salvar
    shouldFailPut422 = true;
    await metaSection.getByTestId('metadata-save-button').click();
    const errorAlert = metaSection.getByTestId('metadata-error-alert');
    await expect(errorAlert).toBeVisible();
    await expect(errorAlert).toContainText(
      'Título vazio ou descrição sem espaço para as divulgações obrigatórias.'
    );
    await expect(metaSection.getByTestId('metadata-unsaved-badge')).toBeVisible();

    // Salva com sucesso -> limpa aviso de alterações não salvas e mostra confirmação
    shouldFailPut422 = false;
    await metaSection.getByTestId('metadata-save-button').click();
    await expect(metaSection.getByTestId('metadata-unsaved-badge')).toHaveCount(0);
    await expect(metaSection.getByTestId('metadata-saved-badge')).toContainText('Metadados salvos');

    expect(putPayloads).toHaveLength(2);
    expect(putPayloads[1]).toEqual({
      title: 'Novo título revisado para publicação manual',
      description: 'Descrição revisada pelo operador humano antes de exportar o ZIP.',
    });
  });

  test('exibe lembrete claro para re-exportar o pacote após gerar thumbnail ou salvar metadados e rotaciona a Idempotency-Key do export', async ({
    page,
  }) => {
    const exportKeys: string[] = [];

    const state = newBackendState({
      productions: [approvedProductionWithTimelineFixture()],
      extra: async (route, path, method) => {
        if (path === `/v1/productions/${PRODUCTION_ID}/exports` && method === 'GET') {
          await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [] } });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/metadata` && method === 'GET') {
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: makeMetadataPayload(),
          });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/metadata` && method === 'PUT') {
          const body = route.request().postDataJSON() as Record<string, string>;
          await route.fulfill({
            status: 200,
            headers: corsHeaders,
            json: makeMetadataPayload(body.title, body.description),
          });
          return true;
        }
        if (path === `/v1/productions/${PRODUCTION_ID}/export` && method === 'POST') {
          const key = route.request().headers()['idempotency-key'] ?? '';
          exportKeys.push(key);
          const idx = exportKeys.length;
          await route.fulfill({
            status: 201,
            headers: corsHeaders,
            json: {
              id: `${String(idx).repeat(64)}`,
              production_id: PRODUCTION_ID,
              created_at: '2026-11-14T11:35:00Z',
              relative_path: `exports/${PRODUCTION_ID}/pkg-${idx}.zip`,
              size_bytes: 16384 * idx,
              sha256: `${String(idx).repeat(64)}`,
              publishable: false,
            },
          });
          return true;
        }
        return false;
      },
    });

    await installBackend(page, state);
    const region = await openProduction(page);
    const exportPanel = region.getByTestId('production-export-panel');

    const reminder = exportPanel.getByTestId('publishing-reexport-reminder');
    await expect(reminder).toBeVisible();
    await expect(reminder).toContainText(
      /exporte de novo para incluir a thumbnail\/metadados no pacote/i
    );

    // 1. Exporta um primeiro pacote antes de editar os metadados
    await exportPanel.getByRole('button', { name: 'Exportar pacote' }).click();
    await expect(exportPanel.getByTestId('exports-list').locator('li')).toHaveCount(1);
    expect(exportKeys).toHaveLength(1);

    // 2. Edita e salva os metadados -> lembrete destaca que há alterações preparadas para nova exportação
    const metaSection = exportPanel.getByTestId('publishing-metadata-section');
    await metaSection.getByTestId('metadata-title-input').fill('Título atualizado pós-export');
    await metaSection.getByTestId('metadata-save-button').click();
    await expect(metaSection.getByTestId('metadata-saved-badge')).toBeVisible();

    await expect(reminder).toHaveAttribute('data-needs-reexport', 'true');
    await expect(reminder).toContainText(
      'Alterações preparadas — exporte de novo para incluir a thumbnail/metadados no pacote.'
    );

    // 3. Exporta de novo -> usa nova Idempotency-Key e adiciona o novo pacote à lista
    await exportPanel.getByRole('button', { name: 'Exportar pacote' }).click();
    await expect(exportPanel.getByTestId('exports-list').locator('li')).toHaveCount(2);
    expect(exportKeys).toHaveLength(2);
    expect(exportKeys[1]).not.toBe(exportKeys[0]);
    await expect(reminder).toHaveAttribute('data-needs-reexport', 'false');
  });
});
