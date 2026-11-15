import AxeBuilder from '@axe-core/playwright';
import config from '../playwright.config';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

const corsHeaders = {
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type,idempotency-key',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
};

const SESSION_TOKEN = 'test-session-token-hor-088-comments';
const WORKFLOW_ID = '11111111222233334444555566667778';
const PROPOSAL_ARTIFACT_ID = 'aaaaaaaabbbbbbbbccccccccddddeeee';
const SCRIPT_V2_ID = '99999999888877776666555544443333';
const SCRIPT_V2_HASH = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
const PROD_ID = 'f1f2f3f4a1a2a3a4b1b2b3b4c1c2c3c5';
const VIDEO_ARTIFACT_ID = '00001111222233334444555566667778';
const VIDEO_V2_SHA256 = '30ab612eba3e7345e9b3cae3620b672aed985aa4199473fa8416d7a79647c0b2';
const VIDEO_V1_SHA256 = '99ab612eba3e7345e9b3cae3620b672aed985aa4199473fa8416d7a79647c0ff';

interface MockComment {
  id: string;
  production_id: string;
  artifact_sha256: string;
  at_seconds: number;
  text: string;
  author: string;
  author_kind: 'human';
  status: 'open' | 'resolved';
  created_at: string;
  trail: Array<{
    action: 'created' | 'resolved' | 'reopened';
    author: string;
    at: string;
    note?: string;
  }>;
}

async function connectSession(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill(SESSION_TOKEN);
  await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();
}

async function runAxeInBothThemes(page: Page, contextLabel: string) {
  const originalTheme = await page.evaluate(
    () => document.querySelector('.horus')?.getAttribute('data-theme') ?? 'light'
  );

  for (const theme of [originalTheme, originalTheme === 'dark' ? 'light' : 'dark']) {
    await page.evaluate(value => {
      document.querySelector('.horus')?.setAttribute('data-theme', value);
    }, theme);
    await page.evaluate(() =>
      Promise.all(document.getAnimations().map(anim => anim.finished.catch(() => undefined)))
    );
    const results = await new AxeBuilder({ page })
      .include('[data-testid="review-comments-panel"]')
      .withTags(WCAG_TAGS)
      .analyze();
    expect(
      results.violations,
      `${contextLabel} (${theme}) encontrou violações WCAG: ${JSON.stringify(results.violations)}`
    ).toEqual([]);
  }

  await page.evaluate(value => {
    document.querySelector('.horus')?.setAttribute('data-theme', value);
  }, originalTheme);
}

function createFixtures() {
  const workflow = {
    id: WORKFLOW_ID,
    status: 'approved',
    revision: 1,
    executor: 'hermes_simulated',
    simulated: true,
    request: { objective: 'Validar comentários por timecode e versão (HOR-088).' },
    current_artifact_id: PROPOSAL_ARTIFACT_ID,
    pending_authorization: null,
    steps: [
      {
        id: 'job_step_1',
        workflow_id: WORKFLOW_ID,
        workflow_revision: 1,
        step_key: 'proposal',
        step_index: 0,
        status: 'succeeded',
        attempt: 1,
        created_at: '2026-11-15T10:00:00Z',
        updated_at: '2026-11-15T10:00:04Z',
        depends_on: null,
        error: null,
        result: null,
      },
    ],
    artifacts: [
      {
        id: PROPOSAL_ARTIFACT_ID,
        workflow_id: WORKFLOW_ID,
        revision: 1,
        stage: 'proposal',
        job_id: 'job_step_1',
        attempt_id: 'att_prop_1',
        schema_version: 2,
        content: {
          schema_version: 2,
          simulated: true,
          stage: 'proposal',
          title: 'Revisão com Comentários por Timecode',
          result_status: 'complete',
          recommendation: {
            title_options: ['Revisão com Comentários por Timecode'],
            premise: 'Testar fluxo de comentários na revisão.',
            angle: 'Timecode + versão.',
            format: 'short',
            originality: 'Dados locais.',
            differs_from: [],
            risks: [],
            licensing_notes: 'Demonstração.',
          },
          evidence: { sources: 1, facts: 1, hypotheses: 0 },
          requires_human_review: true,
        },
        content_hash: 'sha256_prop_hash_0123456789abcdef',
        created_at: '2026-11-15T10:00:04Z',
      },
    ],
    approvals: [
      {
        id: 'app_prop_1',
        workflow_id: WORKFLOW_ID,
        revision: 1,
        artifact_id: PROPOSAL_ARTIFACT_ID,
        decision: 'approve',
        feedback: '',
        created_at: '2026-11-15T10:00:05Z',
      },
    ],
    cognitive_runs: [],
    authorizations: [],
    created_at: '2026-11-15T10:00:00Z',
    updated_at: '2026-11-15T10:00:05Z',
  };

  const script = {
    id: SCRIPT_V2_ID,
    workflow_id: WORKFLOW_ID,
    workflow_revision: 1,
    proposal_artifact_id: PROPOSAL_ARTIFACT_ID,
    proposal_content_hash: 'sha256_prop_hash_0123456789abcdef',
    version: 2,
    content_hash: SCRIPT_V2_HASH,
    status: 'approved',
    origin: 'human',
    document: {
      schema_version: 1 as const,
      simulated: true as const,
      title: 'Roteiro Teste HOR-088',
      sections: [
        {
          id: 'hook',
          kind: 'hook' as const,
          heading: 'Abertura',
          narration: 'Olá mundo.',
          source_ids: ['video_01'],
        },
      ],
    },
    created_at: '2026-11-15T10:01:00Z',
    updated_at: '2026-11-15T10:01:00Z',
  };

  const production = {
    id: PROD_ID,
    script_id: SCRIPT_V2_ID,
    script_version: 2,
    script_content_hash: SCRIPT_V2_HASH,
    job_id: 'job_prod_088',
    mode: 'local_pilot',
    stage: 'review',
    status: 'succeeded',
    error: null,
    created_at: '2026-11-15T10:02:00Z',
    updated_at: '2026-11-15T10:03:00Z',
    artifacts: [
      {
        id: VIDEO_ARTIFACT_ID,
        kind: 'video' as const,
        relative_path: 'canais/demo/videos/fixture/render/short-v2.mp4',
        size_bytes: 96902,
        sha256: VIDEO_V2_SHA256,
      },
    ],
    review: null,
    demo_only: true,
    real_voice: false,
    speech_aligned: false,
    narration_kind: 'local_tts',
    alignment_method: 'measured_sections',
    publishable: false,
    quality_report: null,
    asset_provenance: null,
    quality_acknowledgement_required: false,
  };

  return { workflow, script, production };
}

test.describe('Comentários por timecode e versão (HOR-088)', () => {
  test('lista por timecode com salto no player, diferencia outra versão, cria, resolve/reabre com trilha, filtra e passa no axe', async ({
    page,
  }) => {
    const { workflow, script, production } = createFixtures();
    const commentsStore: MockComment[] = [
      {
        id: '00000000000000000000000000000001',
        production_id: PROD_ID,
        artifact_sha256: VIDEO_V2_SHA256,
        at_seconds: 1.5,
        text: 'Ajustar corte inicial da cena 1.',
        author: 'Yann',
        author_kind: 'human',
        status: 'open',
        created_at: '2026-11-15T10:10:00Z',
        trail: [
          {
            action: 'created',
            author: 'Yann',
            at: '2026-11-15T10:10:00Z',
          },
        ],
      },
      {
        id: '00000000000000000000000000000002',
        production_id: PROD_ID,
        artifact_sha256: VIDEO_V1_SHA256,
        at_seconds: 4.25,
        text: 'Áudio estava baixo na versão anterior.',
        author: 'Ana',
        author_kind: 'human',
        status: 'resolved',
        created_at: '2026-11-15T09:50:00Z',
        trail: [
          {
            action: 'created',
            author: 'Ana',
            at: '2026-11-15T09:50:00Z',
          },
          {
            action: 'resolved',
            author: 'Yann',
            at: '2026-11-15T10:05:00Z',
            note: 'Normalizado na nova renderização.',
          },
        ],
      },
    ];

    const capturedRequests: Array<{ method: string; path: string; body?: unknown; key?: string | null }> = [];

    await page.route('http://127.0.0.1:8765/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const pathUrl = url.pathname;

      if (request.method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers: corsHeaders });
        return;
      }
      if (pathUrl === '/health') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { status: 'ready' } });
        return;
      }
      if (pathUrl === '/v1/hermes') {
        await route.fulfill({
          status: 200,
          headers: corsHeaders,
          json: { state: 'ready', connected: true, cli_available: true, configured: true, features: [] },
        });
        return;
      }
      if (pathUrl === '/v1/jobs') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: [] });
        return;
      }
      if (pathUrl === '/v1/workflows') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: [workflow] });
        return;
      }
      if (pathUrl === `/v1/workflows/${WORKFLOW_ID}`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: workflow });
        return;
      }
      if (pathUrl === `/v1/workflows/${WORKFLOW_ID}/scripts`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [script] } });
        return;
      }
      if (pathUrl === `/v1/scripts/${SCRIPT_V2_ID}/productions`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [production] } });
        return;
      }
      if (pathUrl === `/v1/productions/${PROD_ID}`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: production });
        return;
      }
      if (pathUrl === `/v1/productions/${PROD_ID}/license-reviews`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [] } });
        return;
      }
      if (pathUrl === `/v1/productions/${PROD_ID}/artifacts/${VIDEO_ARTIFACT_ID}`) {
        await route.fulfill({
          status: 200,
          headers: { ...corsHeaders, 'Content-Type': 'video/mp4' },
          body: Buffer.from(''),
        });
        return;
      }

      if (pathUrl === `/v1/productions/${PROD_ID}/comments` && request.method() === 'GET') {
        const statusParam = url.searchParams.get('status');
        const shaParam = url.searchParams.get('artifact_sha256');
        const scoped = shaParam
          ? commentsStore.filter(c => c.artifact_sha256 === shaParam)
          : commentsStore;
        const counts = {
          open: scoped.filter(c => c.status === 'open').length,
          resolved: scoped.filter(c => c.status === 'resolved').length,
        };
        const items = scoped
          .filter(c => !statusParam || c.status === statusParam)
          .slice()
          .sort((a, b) => a.at_seconds - b.at_seconds || a.id.localeCompare(b.id));
        await route.fulfill({
          status: 200,
          headers: corsHeaders,
          json: { items, counts, next_after: null },
        });
        return;
      }

      if (pathUrl === `/v1/productions/${PROD_ID}/comments` && request.method() === 'POST') {
        const body = request.postDataJSON() as {
          artifact_sha256: string;
          at_seconds: number;
          text: string;
          author: string;
        };
        capturedRequests.push({
          method: 'POST',
          path: pathUrl,
          body,
          key: request.headers()['idempotency-key'] ?? null,
        });
        const created: MockComment = {
          id: `0000000000000000000000000000000${commentsStore.length + 1}`,
          production_id: PROD_ID,
          artifact_sha256: body.artifact_sha256,
          at_seconds: body.at_seconds,
          text: body.text,
          author: body.author,
          author_kind: 'human',
          status: 'open',
          created_at: '2026-11-15T10:15:00Z',
          trail: [{ action: 'created', author: body.author, at: '2026-11-15T10:15:00Z' }],
        };
        commentsStore.push(created);
        await route.fulfill({ status: 201, headers: corsHeaders, json: created });
        return;
      }

      const decisionMatch = pathUrl.match(
        new RegExp(`^/v1/productions/${PROD_ID}/comments/([a-f0-9]{32})/(resolve|reopen)$`)
      );
      if (decisionMatch && request.method() === 'POST') {
        const [, commentId, action] = decisionMatch;
        const body = request.postDataJSON() as { author: string; note?: string };
        capturedRequests.push({
          method: 'POST',
          path: pathUrl,
          body,
          key: request.headers()['idempotency-key'] ?? null,
        });
        const target = commentsStore.find(c => c.id === commentId);
        if (!target) {
          await route.fulfill({ status: 404, headers: corsHeaders, json: { detail: 'Não encontrado' } });
          return;
        }
        target.status = action === 'resolve' ? 'resolved' : 'open';
        target.trail.push({
          action: action === 'resolve' ? 'resolved' : 'reopened',
          author: body.author,
          at: '2026-11-15T10:20:00Z',
          ...(body.note ? { note: body.note } : {}),
        });
        await route.fulfill({ status: 200, headers: corsHeaders, json: target });
        return;
      }

      await route.fulfill({ status: 404, headers: corsHeaders, json: { detail: `Unhandled: ${pathUrl}` } });
    });

    await connectSession(page);
    await page.getByRole('button', { name: 'Produção', exact: true }).click();

    const panel = page.getByTestId('review-comments-panel');
    await expect(panel).toBeVisible();

    // Lista inicial com comentário da versão atual e de outra versão
    await expect(panel.getByText('Ajustar corte inicial da cena 1.')).toBeVisible();
    await expect(panel.getByText('Áudio estava baixo na versão anterior.')).toBeVisible();
    await expect(
      panel.getByText(/Atenção: este comentário pertence a outra versão do vídeo/)
    ).toBeVisible();
    await expect(panel.getByText('Normalizado na nova renderização.')).toBeVisible();

    // Salto no player ao clicar no timecode do comentário
    await panel.getByRole('button', { name: /Ir para comentário em 00:04\.25/ }).click();
    await expect(page.locator('[data-current-timecode="4.25"]')).toBeVisible();

    // Adicionar comentário no tempo atual do player
    await panel.getByLabel('Autor do comentário').fill('Yann Bastos');
    await panel
      .getByLabel('Texto do comentário por timecode')
      .fill('Legenda precisa de maior contraste.');
    await panel.getByRole('button', { name: 'Adicionar comentário' }).click();

    await expect(panel.getByText(/Comentário registrado em 00:04\.25/)).toBeVisible();
    await expect(panel.getByText('Legenda precisa de maior contraste.')).toBeVisible();

    const createReq = capturedRequests.find(r => r.path.endsWith('/comments'));
    expect(createReq?.key).toBeTruthy();
    expect(createReq?.body).toEqual({
      artifact_sha256: VIDEO_V2_SHA256,
      at_seconds: 4.25,
      text: 'Legenda precisa de maior contraste.',
      author: 'Yann Bastos',
    });

    // Resolver o primeiro comentário com nota na trilha
    const firstComment = panel.locator('[data-comment-id="00000000000000000000000000000001"]');
    await firstComment.getByRole('button', { name: 'Resolver' }).click();
    await firstComment.getByLabel(/Nota para resolver comentário/).fill('Corte ajustado na v2.');
    await firstComment.getByRole('button', { name: 'Confirmar resolução' }).click();
    await expect(panel.getByText('Comentário em 00:01.500 marcado como resolvido.')).toBeVisible();
    await expect(firstComment.getByText(/Resolvido por Yann Bastos.*Corte ajustado na v2\./)).toBeVisible();

    // Reabrir o comentário da versão anterior com nota na trilha
    const secondComment = panel.locator('[data-comment-id="00000000000000000000000000000002"]');
    await secondComment.getByRole('button', { name: 'Reabrir' }).click();
    await secondComment.getByLabel(/Nota para reabrir comentário/).fill('Conferir novamente no trecho.');
    await secondComment.getByRole('button', { name: 'Confirmar reabertura' }).click();
    await expect(panel.getByText('Comentário em 00:04.250 reaberto.')).toBeVisible();
    await expect(
      secondComment.getByText(/Reaberto por Yann Bastos.*Conferir novamente no trecho\./)
    ).toBeVisible();

    // Filtrar por status (Resolvidos / Abertos / Todos)
    await panel.getByRole('button', { name: /^Resolvidos \(1\)/ }).click();
    await expect(panel.getByText('Ajustar corte inicial da cena 1.')).toBeVisible();
    await expect(panel.getByText('Legenda precisa de maior contraste.')).toBeHidden();

    await panel.getByRole('button', { name: /^Abertos \(2\)/ }).click();
    await expect(panel.getByText('Legenda precisa de maior contraste.')).toBeVisible();
    await expect(panel.getByText('Ajustar corte inicial da cena 1.')).toBeHidden();

    await panel.getByRole('button', { name: /^Todos \(3\)/ }).click();

    // Filtrar por versão (Versão em revisão vs Outra versão)
    await panel.getByLabel('Filtrar comentários por versão do vídeo').selectOption(VIDEO_V2_SHA256);
    await expect(panel.getByText('Legenda precisa de maior contraste.')).toBeVisible();
    await expect(panel.getByText('Áudio estava baixo na versão anterior.')).toBeHidden();

    await panel.getByLabel('Filtrar comentários por versão do vídeo').selectOption('all');
    await expect(panel.getByText('Áudio estava baixo na versão anterior.')).toBeVisible();

    // Auditoria de acessibilidade (axe-core) nos dois temas
    await runAxeInBothThemes(page, 'Painel de comentários por timecode e versão');
  });

  test('exibe StatusMessage de erro quando a API de comentários falha', async ({ page }) => {
    const { workflow, script, production } = createFixtures();

    await page.route('http://127.0.0.1:8765/**', async route => {
      const request = route.request();
      const pathUrl = new URL(request.url()).pathname;

      if (request.method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers: corsHeaders });
        return;
      }
      if (pathUrl === '/health') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { status: 'ready' } });
        return;
      }
      if (pathUrl === '/v1/hermes') {
        await route.fulfill({
          status: 200,
          headers: corsHeaders,
          json: { state: 'ready', connected: true, cli_available: true, configured: true, features: [] },
        });
        return;
      }
      if (pathUrl === '/v1/jobs') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: [] });
        return;
      }
      if (pathUrl === '/v1/workflows') {
        await route.fulfill({ status: 200, headers: corsHeaders, json: [workflow] });
        return;
      }
      if (pathUrl === `/v1/workflows/${WORKFLOW_ID}`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: workflow });
        return;
      }
      if (pathUrl === `/v1/workflows/${WORKFLOW_ID}/scripts`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [script] } });
        return;
      }
      if (pathUrl === `/v1/scripts/${SCRIPT_V2_ID}/productions`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [production] } });
        return;
      }
      if (pathUrl === `/v1/productions/${PROD_ID}`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: production });
        return;
      }
      if (pathUrl === `/v1/productions/${PROD_ID}/license-reviews`) {
        await route.fulfill({ status: 200, headers: corsHeaders, json: { items: [] } });
        return;
      }
      if (pathUrl === `/v1/productions/${PROD_ID}/comments` && request.method() === 'GET') {
        await route.fulfill({
          status: 409,
          headers: corsHeaders,
          json: { detail: 'O arquivo de comentários desta produção está ilegível; nada foi alterado.' },
        });
        return;
      }
      await route.fulfill({ status: 404, headers: corsHeaders, json: { detail: `Unhandled: ${pathUrl}` } });
    });

    await connectSession(page);
    await page.getByRole('button', { name: 'Produção', exact: true }).click();

    const panel = page.getByTestId('review-comments-panel');
    await expect(
      panel.getByRole('alert').filter({
        hasText: 'O arquivo de comentários desta produção está ilegível; nada foi alterado.',
      })
    ).toBeVisible();
  });
});
