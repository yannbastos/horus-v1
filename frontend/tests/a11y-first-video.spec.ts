import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import {
  connectSession,
  corsHeaders,
  installBackend,
  JOB_ID,
  newBackendState,
  openProduction,
  PRODUCTION_ID,
  productionFixture,
  PROPOSAL_ARTIFACT_ID,
  SCRIPT_HASH,
  SCRIPT_ID,
  VIDEO_ID,
  VIDEO_SHA,
  WORKFLOW_ID,
} from './production-journey-helpers';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

async function runAxeAuditForCurrentTheme(page: Page, contextLabel: string) {
  await page.evaluate(() =>
    Promise.race([
      Promise.all(document.getAnimations().map(anim => anim.finished.catch(() => undefined))),
      new Promise(resolve => setTimeout(resolve, 1000)),
    ])
  );
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  const formatted = results.violations.map(v => ({
    id: v.id,
    impact: v.impact,
    description: v.description,
    help: v.help,
    nodes: v.nodes.map(n => ({
      target: n.target,
      html: n.html.slice(0, 180),
      failureSummary: n.failureSummary,
    })),
  }));
  expect(
    formatted,
    `Violações axe-core (${WCAG_TAGS.join(', ')}) em [${contextLabel}]:\n${JSON.stringify(formatted, null, 2)}`
  ).toEqual([]);
}

async function runAxeAudit(page: Page, contextLabel: string) {
  const originalTheme = await page.evaluate(
    () => document.querySelector('.horus')?.getAttribute('data-theme') ?? 'light'
  );
  await runAxeAuditForCurrentTheme(page, `${contextLabel} (tema ${originalTheme})`);

  const alternateTheme = originalTheme === 'dark' ? 'light' : 'dark';
  await page.evaluate(theme => {
    document.querySelector('.horus')?.setAttribute('data-theme', theme);
  }, alternateTheme);
  try {
    await runAxeAuditForCurrentTheme(page, `${contextLabel} (tema ${alternateTheme})`);
  } finally {
    await page.evaluate(theme => {
      document.querySelector('.horus')?.setAttribute('data-theme', theme);
    }, originalTheme);
  }
}

function makeProductionChecks() {
  return [
    {
      id: 'factory',
      name: 'Fábrica do piloto',
      available: true,
      detail: 'Presença local verificada. Configure FABRICA_HOME.',
      required_for: ['draft', 'final'],
    },
    {
      id: 'ffmpeg',
      name: 'FFmpeg do piloto',
      available: false,
      detail: 'Indisponível. Configure HORUS_FFMPEG com o executável local.',
      required_for: ['draft', 'final'],
    },
    {
      id: 'sapi_pt_br',
      name: 'Voz SAPI pt-BR',
      available: true,
      detail: 'Presença local verificada.',
      required_for: ['windows_sapi'],
    },
    {
      id: 'kokoro',
      name: 'Kokoro local',
      available: true,
      detail: 'Presença local verificada.',
      required_for: ['kokoro'],
    },
  ];
}

test.describe('Acessibilidade (axe-core WCAG 2.1 AA + Teclado) — Jornada do primeiro vídeo (HOR-101)', () => {
  test('Central de Produção com guia "Meu primeiro vídeo" e tabela de vídeos não possui violações axe', async ({
    page,
  }) => {
    const state = newBackendState({
      preference: { provider: 'kokoro', voice_id: 'pf_dora' },
      productions: [
        productionFixture({
          stage: 'review',
          status: 'succeeded',
          render_quality: 'draft',
          narration: { provider: 'kokoro', voice_id: 'pf_dora' },
          quality_report: {
            overall_status: 'warn',
            checks: [
              {
                id: 'audio_loudness',
                category: 'audio',
                status: 'warn',
                method: 'measured',
                detail: '-26.0 LUFS.',
              },
            ],
          },
          review: null,
        }),
      ],
    });

    await installBackend(page, state);
    await connectSession(page);
    await page
      .getByRole('navigation', { name: 'Navegação principal' })
      .getByRole('button', { name: 'Central de Produção', exact: true })
      .click();

    await expect(page.getByTestId('first-video-guide')).toBeVisible();
    await expect(page.getByTestId('production-center-table')).toBeVisible();

    await runAxeAudit(page, 'Central de Produção + Guia Meu primeiro vídeo');
  });

  test('Editor de roteiro manual (com seções e validação) e revisão de roteiro não possuem violações axe', async ({
    page,
  }) => {
    const scriptV1 = {
      id: SCRIPT_ID,
      workflow_id: WORKFLOW_ID,
      workflow_revision: 1,
      proposal_artifact_id: PROPOSAL_ARTIFACT_ID,
      proposal_content_hash: 'sha256_prop_hash_0123456789abcdef',
      version: 1,
      status: 'changes_requested',
      origin: 'simulated',
      authored_by: 'simulated',
      document: {
        schema_version: 1,
        simulated: true,
        title: 'Roteiro inicial v1',
        sections: [
          {
            id: 'sec_hook',
            kind: 'hook',
            heading: 'Gancho inicial',
            narration: 'Como funciona o fluxo local antigo?',
            source_ids: ['video_01'],
          },
        ],
      },
      created_at: '2026-11-12T10:00:00Z',
      review: {
        decision: 'request_changes',
        feedback: 'Ajustar gancho.',
        reviewed_at: '2026-11-12T10:05:00Z',
      },
    };

    const scriptV2 = {
      id: SCRIPT_ID,
      workflow_id: WORKFLOW_ID,
      workflow_revision: 1,
      proposal_artifact_id: PROPOSAL_ARTIFACT_ID,
      proposal_content_hash: 'sha256_prop_hash_0123456789abcdef',
      version: 2,
      status: 'awaiting_review',
      origin: 'human',
      authored_by: 'human',
      document: {
        schema_version: 1,
        simulated: false,
        title: 'Roteiro revisado v2',
        sections: [
          {
            id: 'sec_hook',
            kind: 'hook',
            heading: 'Gancho revisado',
            narration: 'Como garantir que cada artefato corresponde ao roteiro aprovado?',
            source_ids: ['video_01'],
          },
        ],
      },
      created_at: '2026-11-12T10:20:00Z',
      review: null,
    };

    const state = newBackendState();
    state.extra = async (route, path, method) => {
      if (path === `/v1/workflows/${WORKFLOW_ID}/scripts` && method === 'GET') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [scriptV2, scriptV1] } });
        return true;
      }
      return false;
    };

    await installBackend(page, state);
    await connectSession(page);
    await page
      .getByRole('navigation', { name: 'Navegação principal' })
      .getByRole('button', { name: 'Produção', exact: true })
      .click();

    const scriptRegion = page.getByRole('region', { name: 'Roteiro e revisão humana' });
    await expect(scriptRegion).toBeVisible();

    // Abre comparação de versões do roteiro também
    const history = page.getByRole('region', { name: 'Histórico de versões do roteiro' });
    await history.getByRole('button', { name: 'Comparar com a anterior' }).click();
    await expect(page.getByRole('region', { name: 'Comparação de versões do roteiro' })).toBeVisible();

    await runAxeAudit(page, 'Revisão de roteiro + Comparação de versões do roteiro');

    // Abre o editor manual de roteiro e dispara estado de validação para auditar mensagens de erro
    await scriptRegion.getByRole('button', { name: 'Editar roteiro', exact: true }).click();
    const editor = page.getByRole('region', { name: 'Editor de roteiro humano' });
    await expect(editor).toBeVisible();

    const titleInput = editor.getByLabel(/Título do roteiro/);
    await titleInput.fill('');
    await page.keyboard.press('Control+s');
    await expect(titleInput).toHaveAttribute('aria-invalid', 'true');

    await runAxeAudit(page, 'Editor de roteiro humano com validação ativa');
  });

  test('Ajustes → Pronto para produzir? e Narração expandida não possuem violações axe', async ({
    page,
  }) => {
    const state = newBackendState({
      preference: { provider: 'kokoro', voice_id: 'pf_dora' },
    });
    state.extra = async (route, path) => {
      if (path === '/v1/diagnostics') {
        await route.fulfill({
          status: 200,
          headers: corsHeaders,
          json: {
            checks: [{ id: 'factory', name: 'CLI fabrica', available: true, detail: 'Localizado.' }],
            hermes_connected: true,
            notice: 'Verificação de presença apenas.',
            production: {
              ready: false,
              missing: ['ffmpeg'],
              checks: makeProductionChecks(),
            },
          },
        });
        return true;
      }
      return false;
    };

    await installBackend(page, state);
    await connectSession(page);

    const readiness = page.getByTestId('production-readiness');
    await expect(readiness).toBeVisible();
    await expect(readiness).toContainText('FALTA (1)');

    await page.getByRole('button', { name: 'ABRIR NARRAÇÃO' }).click();
    await expect(page.getByRole('region', { name: 'Narração' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Provedor Kokoro-82M' })).toBeVisible();

    await runAxeAudit(page, 'Ajustes → Pronto para produzir? + Narração expandida');
  });

  test('Tela de Produção em execução (progresso) e em falha (diagnóstico técnico expandido) não possuem violações axe', async ({
    page,
  }) => {
    let currentProd = productionFixture({
      stage: 'narration',
      status: 'running',
      created_at: '2026-11-14T08:00:00Z',
      updated_at: '2026-11-14T08:00:25Z',
    });

    const state = newBackendState({
      productions: [currentProd],
    });
    state.extra = async (route, path, method) => {
      if (path === `/v1/scripts/${SCRIPT_ID}/productions` && method === 'GET') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [currentProd] } });
        return true;
      }
      if (path === `/v1/productions/${PRODUCTION_ID}` && method === 'GET') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: currentProd });
        return true;
      }
      if (path === `/v1/jobs/${JOB_ID}/events`) {
        await route.fulfill({
          status: 200,
          headers: { ...corsHeaders, 'Content-Type': 'application/x-ndjson' },
          body: JSON.stringify({ sequence: 1, kind: 'state', message: currentProd.status }) + '\n',
        });
        return true;
      }
      return false;
    };

    await installBackend(page, state);
    const region = await openProduction(page);

    await expect(region.getByRole('status', { name: 'Progresso da execução' })).toBeVisible();
    await runAxeAudit(page, 'Tela de Produção — Em execução (Progresso)');

    // Muda para falha com diagnóstico técnico expandido
    currentProd = productionFixture({
      stage: 'render',
      status: 'failed',
      error: 'A renderização do vídeo falhou na etapa local.',
      created_at: '2026-11-14T08:00:00Z',
      updated_at: '2026-11-14T08:01:15Z',
      failure: {
        code: 'render_failed',
        stage: 'render',
        type: 'CalledProcessError',
        exit_code: 1,
        duration_seconds: 14.32,
        stderr_tail: 'Error: Remotion bundle failed\n  at renderMedia (index.js:42)',
        stderr_limit_bytes: 8192,
      },
    });

    await region.getByRole('button', { name: 'Recarregar produção atual' }).click();
    const failAlert = region.getByRole('alert', { name: 'Falha na produção' });
    await expect(failAlert).toBeVisible();

    const details = failAlert.locator('details.failure-diagnostics');
    await details.locator('summary').click();
    await expect(details).toHaveAttribute('open', '');

    await runAxeAudit(page, 'Tela de Produção — Falha com diagnóstico técnico expandido');
  });

  test('Tela de Produção em revisão (QC, licenças, timecode, comparação de versões e aprovação/exportação) não possui violações axe', async ({
    page,
  }) => {
    const prodBId = 'ddddddddccccccccbbbbbbbbaaaaaaaa';
    const prodA = productionFixture({
      id: PRODUCTION_ID,
      script_id: SCRIPT_ID,
      script_version: 2,
      script_content_hash: SCRIPT_HASH,
      mode: 'local_pilot',
      stage: 'review',
      status: 'succeeded',
      render_quality: 'draft',
      created_at: '2026-11-14T08:00:00Z',
      updated_at: '2026-11-14T08:05:00Z',
      narration: { provider: 'kokoro', voice_id: 'pf_dora' },
      quality_acknowledgement_required: true,
      word_timings: {
        schema_version: 1,
        duration_seconds: 15.0,
        source: 'tts_engine_events',
        engine: { name: 'kokoro', voice: 'pf_dora' },
        words: [
          { index: 0, text: 'Como', start: 0.0, end: 0.4, char_offset: 0, char_length: 4, section: 0 },
          { index: 1, text: 'garantir', start: 0.4, end: 1.0, char_offset: 5, char_length: 8, section: 0 },
        ],
      },
      quality_report: {
        overall_status: 'fail',
        tool: { name: 'horus-qc', version: '1.0' },
        checks: [
          {
            id: 'audio_clipping',
            category: 'audio',
            status: 'fail',
            method: 'measured',
            detail: 'Pico acima de 0 dBFS detectado.',
            timecode: { start: 12.4, end: 14.1 },
          },
          {
            id: 'audio_loudness',
            category: 'audio',
            status: 'warn',
            method: 'measured',
            detail: '-28.1 LUFS abaixo da faixa.',
            timecode: { start: 3.0, end: 8.5 },
          },
          {
            id: 'duration_video_audio',
            category: 'video',
            status: 'pass',
            method: 'measured',
            detail: 'Duração 15.0 s dentro do esperado.',
          },
        ],
      },
      asset_provenance: {
        assets: [
          {
            asset_id: 'voice_kokoro_pf_dora',
            kind: 'audio',
            origin: 'local_tts_model',
            relative_path: 'narration.wav',
            sha256: 'a'.repeat(64),
            license: {
              id: 'Apache-2.0',
              name: 'Apache-2.0',
              url: 'https://huggingface.co/hexgrad/Kokoro-82M',
              attribution_required: false,
            },
            commercial_use: 'unknown',
            status: 'unverified',
            verified_by: null,
          },
        ],
      },
      artifacts: [
        {
          id: VIDEO_ID,
          kind: 'video',
          relative_path: 'render/short.mp4',
          sha256: VIDEO_SHA,
          size_bytes: 102400,
          created_at: '2026-11-14T08:05:00Z',
        },
      ],
      review: null,
    });

    const prodB = productionFixture({
      id: prodBId,
      script_id: SCRIPT_ID,
      script_version: 2,
      script_content_hash: SCRIPT_HASH,
      mode: 'local_pilot',
      stage: 'review',
      status: 'succeeded',
      render_quality: 'final',
      narration: { provider: 'windows_sapi', voice_id: 'sapi-maria' },
      word_timings: {
        schema_version: 1,
        duration_seconds: 14.0,
        source: 'measured',
        engine: { name: 'windows_sapi', voice: 'sapi-maria' },
        words: [{ index: 0, word: 'Como', start: 0, end: 0.5 }],
      },
      quality_report: {
        overall_status: 'pass',
        checks: [{ id: 'duration_video_audio', category: 'video', status: 'pass', method: 'measured' }],
      },
      review: {
        decision: 'approve',
        artifact_sha256: VIDEO_SHA,
        reviewed_at: '2026-11-14T08:30:00Z',
      },
      created_at: '2026-11-14T07:50:00Z',
    });

    let activeProd: Record<string, unknown> = prodA;
    const state = newBackendState({
      productions: [prodA, prodB],
    });
    state.extra = async (route, path, method) => {
      if (path === `/v1/productions/${PRODUCTION_ID}` && method === 'GET') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: activeProd });
        return true;
      }
      if (path === `/v1/productions/${prodBId}` && method === 'GET') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: prodB });
        return true;
      }
      if (path === `/v1/productions/${PRODUCTION_ID}/license-reviews` && method === 'GET') {
        await route.fulfill({
          status: 200,
          headers: corsHeaders,
          json: {
            production_id: PRODUCTION_ID,
            effective_provenance: prodA.asset_provenance,
            current: [],
            history: [],
            publishable: false,
          },
        });
        return true;
      }
      if (path === `/v1/productions/${PRODUCTION_ID}/artifacts/${VIDEO_ID}/content`) {
        await route.fulfill({
          status: 200,
          headers: { ...corsHeaders, 'Content-Type': 'video/mp4' },
          body: Buffer.from('mp4'),
        });
        return true;
      }
      if (path === `/v1/productions/${PRODUCTION_ID}/exports` && method === 'GET') {
        await route.fulfill({
          status: 200,
          headers: corsHeaders,
          json: { items: [] },
        });
        return true;
      }
      if (path === `/v1/productions/${PRODUCTION_ID}/metadata` && method === 'GET') {
        await route.fulfill({
          status: 200,
          headers: corsHeaders,
          json: {
            title: 'Como garantir sincronismo real no Hórus',
            description:
              'Introdução: Roteiro validado localmente.\n\nDivulgações obrigatórias:\nNarração sintética/voz local: confira a voz selecionada e seus termos de uso.\nLicenças pendentes: confira os direitos de todos os assets antes de publicar.\npublishable=false: este rascunho não autoriza publicação automática.',
            sources: [
              {
                id: 'src_horus_arch',
                title: 'Arquitetura Canônica do Hórus',
                url: 'https://example.org/docs/architecture',
              },
            ],
            disclosures: [
              'Narração sintética/voz local: confira a voz selecionada e seus termos de uso.',
              'Licenças pendentes: confira os direitos de todos os assets antes de publicar.',
              'publishable=false: este rascunho não autoriza publicação automática.',
            ],
            licenses_pending: true,
            publishable: false,
          },
        });
        return true;
      }
      if (path === `/v1/productions/${PRODUCTION_ID}/thumbnail` && method === 'POST') {
        await route.fulfill({
          status: 201,
          headers: corsHeaders,
          json: {
            timecode_seconds: 1.2,
            video_sha256: VIDEO_SHA,
            width: 1080,
            height: 1920,
            artifacts: [
              {
                id: 'thumb_a11y_png',
                kind: 'thumbnail',
                relative_path: `publishing/thumbnails/${'a'.repeat(64)}/thumbnail.png`,
                sha256: 'd'.repeat(64),
                size_bytes: 16384,
              },
              {
                id: 'thumb_a11y_jpg',
                kind: 'thumbnail',
                relative_path: `publishing/thumbnails/${'a'.repeat(64)}/thumbnail.jpg`,
                sha256: 'e'.repeat(64),
                size_bytes: 8192,
              },
            ],
            created_at: '2026-11-14T09:00:30Z',
            publishable: false,
          },
        });
        return true;
      }
      if (path === `/v1/productions/${PRODUCTION_ID}/artifacts/thumb_a11y_png` && method === 'GET') {
        await route.fulfill({
          status: 200,
          headers: { ...corsHeaders, 'Content-Type': 'image/png' },
          body: Buffer.from(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
            'base64'
          ),
        });
        return true;
      }
      if (path === `/v1/productions/${PRODUCTION_ID}/export` && method === 'POST') {
        await route.fulfill({
          status: 201,
          headers: corsHeaders,
          json: {
            id: 'exp_a11y_0001',
            production_id: PRODUCTION_ID,
            relative_path: 'exports/horus-pacote-aaaabbbb.zip',
            sha256: 'c'.repeat(64),
            size_bytes: 524288,
            created_at: '2026-11-14T09:01:00Z',
          },
        });
        return true;
      }
      if (path === `/v1/productions/${PRODUCTION_ID}/review` && method === 'POST') {
        activeProd = {
          ...prodA,
          review: {
            decision: 'approve',
            artifact_sha256: VIDEO_SHA,
            reviewed_at: '2026-11-14T09:00:00Z',
            notes: [{ at_seconds: 12.4, text: 'Verificar pico de áudio no trecho.' }],
            acknowledge_quality_errors: true,
          },
        };
        await route.fulfill({ status: 200, headers: corsHeaders, json: activeProd });
        return true;
      }
      return false;
    };

    await installBackend(page, state);
    const region = await openProduction(page);

    await expect(region.getByRole('region', { name: 'Painel de qualidade (QC)' })).toBeVisible();
    await expect(region.getByRole('region', { name: 'Painel de licenças e proveniência' })).toBeVisible();
    const tcWorkspace = region.getByRole('region', { name: 'Revisão e navegação por timecode' });
    await expect(tcWorkspace).toBeVisible();

    // Adiciona uma nota por timecode para auditar também a lista editável de notas
    await tcWorkspace.getByLabel('Timecode da nova nota (s)').fill('12.4');
    await tcWorkspace.getByLabel('Texto da nova nota por timecode').fill('Verificar pico de áudio no trecho.');
    await tcWorkspace.getByRole('button', { name: 'Adicionar nota no timecode' }).click();
    await expect(tcWorkspace.getByLabel('Texto da nota 1')).toBeVisible();

    // Seleciona as duas produções para abrir a tabela comparativa de produções
    const prodHistory = region.getByRole('region', { name: 'Histórico de execuções da produção' });
    const checkboxes = prodHistory.getByRole('table', { name: 'Produções deste roteiro' }).getByRole('checkbox');
    await checkboxes.nth(0).check();
    await checkboxes.nth(1).check();
    await expect(prodHistory.getByRole('region', { name: 'Comparação entre duas produções' })).toBeVisible();

    await runAxeAudit(
      page,
      'Tela de Produção — Revisão (QC, licenças com formulário de verificação por asset, timecode e comparação de produções)'
    );

    // Aprova a produção e gera pacote de exportação para auditar também o estado aprovado + exportação
    const reviewRegion = region.getByRole('region', { name: 'Revisão humana final da produção' });
    await reviewRegion.getByLabel(/Confirmo ciência das pendências de QC/).check();
    await reviewRegion.getByRole('button', { name: 'Aprovar produção (hash verificado)', exact: true }).click();
    await expect(region.getByRole('region', { name: 'Decisão de revisão final registrada' })).toBeVisible();

    const exportPanel = region.getByTestId('production-export-panel');
    await expect(exportPanel).toBeVisible();
    await exportPanel.getByTestId('thumbnail-timecode-input').fill('1.2');
    await exportPanel.getByTestId('thumbnail-generate-button').click();
    await expect(exportPanel.getByTestId('thumbnail-preview-image')).toBeVisible();
    await exportPanel.getByTestId('metadata-title-input').fill('Título revisado para auditoria a11y');
    await expect(exportPanel.getByTestId('metadata-unsaved-badge')).toBeVisible();
    await exportPanel.getByRole('button', { name: 'Exportar pacote', exact: true }).click();
    await expect(exportPanel.getByTestId('export-ready-status')).toBeVisible();
    await expect(exportPanel.getByTestId('exports-list')).toBeVisible();

    await runAxeAudit(page, 'Tela de Produção — Produção aprovada, thumbnail, metadados e pacote exportado');
  });

  test('Navegação por teclado do caminho principal (Tab/Enter/Space) funciona sem armadilhas de foco', async ({
    page,
  }) => {
    const state = newBackendState({
      preference: { provider: 'kokoro', voice_id: 'pf_dora' },
      productions: [
        productionFixture({
          stage: 'review',
          status: 'succeeded',
          render_quality: 'draft',
          narration: { provider: 'kokoro', voice_id: 'pf_dora' },
          artifacts: [
            {
              id: VIDEO_ID,
              kind: 'video',
              relative_path: 'render/short.mp4',
              sha256: VIDEO_SHA,
              size_bytes: 102400,
              created_at: '2026-11-14T08:05:00Z',
            },
          ],
        }),
      ],
    });

    await installBackend(page, state);
    await page.goto('/');

    // 1. No carregamento inicial, o primeiro Tab deve focar o skip-link "Ir para o conteúdo"
    await page.keyboard.press('Tab');
    const skipLink = page.getByRole('link', { name: 'Ir para o conteúdo' });
    await expect(skipLink).toBeFocused();

    // Conecta a sessão via Ajustes
    await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
    await page.getByLabel('Token da sessão').fill('test-session-token-journey-0123456789');
    await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();

    // 2. Foca na barra lateral e avança com Tab até o botão "Central de Produção", ativando com Enter
    const firstNavBtn = page
      .getByRole('navigation', { name: 'Navegação principal' })
      .getByRole('button')
      .first();
    await firstNavBtn.focus();
    const centerNavBtn = page
      .getByRole('navigation', { name: 'Navegação principal' })
      .getByRole('button', { name: 'Central de Produção', exact: true });
    let foundCenterNav = false;
    for (let step = 0; step < 15; step++) {
      if (await centerNavBtn.evaluate(el => el === document.activeElement)) {
        foundCenterNav = true;
        break;
      }
      await page.keyboard.press('Tab');
    }
    expect(foundCenterNav, 'Botão Central de Produção deve ser alcançável via Tab').toBe(true);
    await page.keyboard.press('Enter');

    const guide = page.getByTestId('first-video-guide');
    await expect(guide).toBeVisible();

    // 3. Ativa o botão de recolher/expandir do guia "Meu primeiro vídeo" com Space e Enter
    const toggleGuideBtn = guide.getByRole('button', { name: 'RECOLHER' });
    await toggleGuideBtn.focus();
    await expect(toggleGuideBtn).toBeFocused();
    await page.keyboard.press('Space');
    await expect(guide.getByRole('button', { name: 'EXPANDIR' })).toBeVisible();

    await page.keyboard.press('Enter');
    await expect(guide.getByRole('button', { name: 'RECOLHER' })).toBeVisible();

    // 4. Abre a tela de Produção via botão do guia usando teclado (Enter)
    const openProdStepBtn = page
      .getByTestId('guide-step-produce_final')
      .getByRole('button', { name: /Abrir Produção/i });
    await openProdStepBtn.focus();
    await expect(openProdStepBtn).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('region', { name: 'Produção técnica local e revisão final' })).toBeVisible();

    // 5. Verifica que Tab avança continuamente por elementos distintos na tela de Produção (sem focus trap)
    const visitedElements: string[] = [];
    for (let step = 0; step < 18; step++) {
      await page.keyboard.press('Tab');
      const activeDesc = await page.evaluate(() => {
        const el = document.activeElement;
        if (!el || el === document.body) return 'BODY';
        const role = el.getAttribute('role') || el.tagName.toLowerCase();
        const label =
          el.getAttribute('aria-label') ||
          (el as HTMLElement).innerText?.trim().slice(0, 40) ||
          el.getAttribute('id') ||
          'unnamed';
        return `${role}:${label}`;
      });
      visitedElements.push(activeDesc);
    }

    const uniqueVisited = new Set(visitedElements.filter(item => item !== 'BODY'));
    expect(
      uniqueVisited.size,
      `Tab deve percorrer múltiplos controles sem ficar preso. Visitados: ${visitedElements.join(' -> ')}`
    ).toBeGreaterThanOrEqual(8);
  });
});
