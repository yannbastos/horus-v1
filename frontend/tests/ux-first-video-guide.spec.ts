import config from '../playwright.config';
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const corsHeaders = {
  'Access-Control-Allow-Origin': new URL(config.use!.baseURL!).origin,
  'Access-Control-Allow-Headers': 'authorization,content-type,idempotency-key',
  'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS',
};

const WORKFLOW_ID = '11111111000000000000000000000000';
const SCRIPT_ID = '5c110000000000000000000000000000';
const PRODUCTION_ID = '9d110000000000000000000000000000';
const SHA = 'a'.repeat(64);

type Json = Record<string, unknown>;

type MockScenario = {
  defaultVoice?: { provider: string; voice_id: string } | null;
  voiceFail?: boolean;
  workflows?: Json[];
  workflowsFail?: boolean;
  scripts?: Json[];
  scriptsFail?: boolean;
  productions?: Json[];
  productionsFail?: boolean;
};

function makeWorkflow(status = 'approved'): Json {
  return {
    id: WORKFLOW_ID,
    status,
    revision: 1,
    created_at: '2026-11-12T10:00:00Z',
    updated_at: '2026-11-12T10:05:00Z',
  };
}

function makeScript(status: 'awaiting_review' | 'approved'): Json {
  return {
    id: SCRIPT_ID,
    workflow_id: WORKFLOW_ID,
    workflow_revision: 1,
    proposal_artifact_id: 'aaaaaaaabbbbbbbbccccccccdddddddd',
    proposal_content_hash: 'hash-proposta',
    version: 1,
    status,
    origin: 'human',
    authored_by: 'human',
    created_at: '2026-11-12T10:15:00Z',
    review:
      status === 'approved'
        ? { decision: 'approve', feedback: '', reviewed_at: '2026-11-12T10:20:00Z' }
        : null,
    document: {
      schema_version: 1,
      simulated: false,
      title: 'Meu primeiro vídeo piloto',
      sections: [
        { id: 'inicio', kind: 'hook', heading: 'Gancho', narration: 'Abertura.', source_ids: [] },
        { id: 'meio', kind: 'body', heading: 'Desenvolvimento', narration: 'Corpo.', source_ids: [] },
        { id: 'fim', kind: 'conclusion', heading: 'Conclusão', narration: 'Fechamento.', source_ids: [] },
      ],
    },
  };
}

function makeProduction(overrides: Json): Json {
  return {
    id: PRODUCTION_ID,
    script_id: SCRIPT_ID,
    script_version: 1,
    script_content_hash: 'hash-roteiro',
    job_id: 'job-first-video-1',
    mode: 'local_pilot',
    render_quality: 'final',
    narration: { provider: 'kokoro', voice_id: 'pf_dora' },
    stage: 'render',
    status: 'running',
    created_at: '2026-11-12T11:00:00Z',
    updated_at: '2026-11-12T11:10:00Z',
    artifacts: [],
    review: null,
    error: null,
    demo_only: true,
    publishable: false,
    quality_report: null,
    asset_provenance: null,
    quality_acknowledgement_required: false,
    ...overrides,
  };
}

async function installBackend(page: Page, scenario: MockScenario) {
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: corsHeaders });
    }
    const ok = (json: unknown) => route.fulfill({ status: 200, headers: corsHeaders, json });

    if (path === '/health') return ok({ status: 'ready' });
    if (path === '/v1/hermes') {
      return ok({ state: 'ready', connected: true, cli_available: true, configured: true, features: [] });
    }
    if (path === '/v1/jobs') return ok([]);
    if (path === '/v1/narration/preference') {
      if (scenario.voiceFail) {
        return route.fulfill({
          status: 500,
          headers: corsHeaders,
          json: { code: 'narration_preference_error', message: 'Falha ao ler preferência de voz.' },
        });
      }
      if (!scenario.defaultVoice) {
        return route.fulfill({ status: 404, headers: corsHeaders, json: { detail: 'Nenhuma voz padrão salva.' } });
      }
      return ok(scenario.defaultVoice);
    }
    if (path === '/v1/workflows') {
      if (scenario.workflowsFail) {
        return route.fulfill({ status: 500, headers: corsHeaders, json: { detail: 'Banco indisponível.' } });
      }
      return ok(scenario.workflows ?? []);
    }
    if (path === `/v1/workflows/${WORKFLOW_ID}/scripts`) {
      if (scenario.scriptsFail) {
        return route.fulfill({ status: 500, headers: corsHeaders, json: { detail: 'Falha ao ler roteiros.' } });
      }
      return ok({ items: scenario.scripts ?? [] });
    }
    if (path === `/v1/scripts/${SCRIPT_ID}/productions`) {
      if (scenario.productionsFail) {
        return route.fulfill({ status: 500, headers: corsHeaders, json: { detail: 'Falha ao ler produções.' } });
      }
      return ok({ items: scenario.productions ?? [] });
    }
    if (path === `/v1/productions/${PRODUCTION_ID}/license-reviews`) {
      return ok({
        production_id: PRODUCTION_ID,
        current: [],
        history: [],
        effective_provenance: null,
        publishable: false,
      });
    }
    return route.fulfill({ status: 404, headers: corsHeaders, json: { detail: `não mockado: ${path}` } });
  });
}

async function openProductionCenter(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill('test-session-token-first-video-0123456789');
  await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();
  await page.getByRole('button', { name: 'Central de Produção', exact: true }).click();
}

test('nada feito: exibe os 5 passos pendentes em lista ordenada acessível', async ({ page }) => {
  await installBackend(page, { defaultVoice: null, workflows: [] });
  await openProductionCenter(page);

  const guide = page.getByTestId('first-video-guide');
  await expect(guide).toBeVisible();
  await expect(guide).toContainText('0 de 5 passos concluídos.');

  const list = guide.locator('ol');
  await expect(list).toBeVisible();
  await expect(list.locator('li')).toHaveCount(5);

  const steps = ['voice', 'write_script', 'approve_script', 'produce_final', 'review_approve'];
  for (const step of steps) {
    await expect(page.getByTestId(`guide-step-${step}`)).toContainText('Pendente');
  }
  await expect(page.getByTestId('guide-step-voice')).toHaveAttribute('aria-current', 'step');
});

test('voz escolhida: marca passo 1 como feito e navega para Ajustes e Produção', async ({ page }) => {
  await installBackend(page, {
    defaultVoice: { provider: 'kokoro', voice_id: 'pf_dora' },
    workflows: [],
  });
  await openProductionCenter(page);

  const guide = page.getByTestId('first-video-guide');
  await expect(guide).toContainText('1 de 5 passos concluídos.');
  await expect(page.getByTestId('guide-step-voice')).toContainText('Feito');
  await expect(page.getByTestId('guide-step-write_script')).toContainText('Pendente');
  await expect(page.getByTestId('guide-step-write_script')).toHaveAttribute('aria-current', 'step');

  await page.getByTestId('guide-step-voice').getByRole('button', { name: /Abrir Ajustes/i }).click();
  await expect(page.getByRole('heading', { name: 'Aparência' })).toBeVisible();

  await page.getByRole('button', { name: 'Central de Produção', exact: true }).click();
  await page.getByTestId('guide-step-write_script').getByRole('button', { name: /Abrir Produção/i }).click();
  await expect(page.getByRole('heading', { name: 'Da ideia à próxima entrega.' })).toBeVisible();
});

test('roteiro aprovado: passos 1 a 3 feitos e passos 4 e 5 pendentes', async ({ page }) => {
  await installBackend(page, {
    defaultVoice: { provider: 'kokoro', voice_id: 'pf_dora' },
    workflows: [makeWorkflow('approved')],
    scripts: [makeScript('approved')],
    productions: [],
  });
  await openProductionCenter(page);

  await expect(page.getByTestId('first-video-guide')).toContainText('3 de 5 passos concluídos.');
  await expect(page.getByTestId('guide-step-voice')).toContainText('Feito');
  await expect(page.getByTestId('guide-step-write_script')).toContainText('Feito');
  await expect(page.getByTestId('guide-step-approve_script')).toContainText('Feito');
  await expect(page.getByTestId('guide-step-produce_final')).toContainText('Pendente');
  await expect(page.getByTestId('guide-step-review_approve')).toContainText('Pendente');
});

test('produção em andamento: passo 4 exibe Em andamento para Final 1080×1920', async ({ page }) => {
  await installBackend(page, {
    defaultVoice: { provider: 'kokoro', voice_id: 'pf_dora' },
    workflows: [makeWorkflow('approved')],
    scripts: [makeScript('approved')],
    productions: [makeProduction({ status: 'running', stage: 'render', render_quality: 'final' })],
  });
  await openProductionCenter(page);

  await expect(page.getByTestId('first-video-guide')).toContainText('3 de 5 passos concluídos.');
  await expect(page.getByTestId('guide-step-produce_final')).toContainText('Em andamento');
  await expect(page.getByTestId('guide-step-produce_final')).toHaveAttribute('aria-current', 'step');
  await expect(page.getByTestId('guide-step-review_approve')).toContainText('Pendente');
});

test('vídeo aprovado: painel some sozinho e oferece opção de reabrir', async ({ page }) => {
  await installBackend(page, {
    defaultVoice: { provider: 'kokoro', voice_id: 'pf_dora' },
    workflows: [makeWorkflow('approved')],
    scripts: [makeScript('approved')],
    productions: [
      makeProduction({
        status: 'succeeded',
        stage: 'review',
        render_quality: 'final',
        review: { decision: 'approve', artifact_sha256: SHA, reviewed_at: '2026-11-12T12:00:00Z' },
      }),
    ],
  });
  await openProductionCenter(page);

  await expect(page.getByTestId('first-video-guide')).toHaveCount(0);
  const reopenBar = page.getByTestId('first-video-guide-reopen');
  await expect(reopenBar).toBeVisible();
  await expect(reopenBar).toContainText('Primeiro vídeo aprovado.');

  await reopenBar.getByRole('button', { name: /Reabrir guia/i }).click();
  const guide = page.getByTestId('first-video-guide');
  await expect(guide).toBeVisible();
  await expect(guide).toContainText('5 de 5 passos concluídos.');
  await expect(page.getByTestId('guide-step-review_approve')).toContainText('Feito');
});

test('falha de API num passo mostra "não foi possível verificar" sem quebrar os demais', async ({ page }) => {
  await installBackend(page, {
    voiceFail: true,
    workflows: [makeWorkflow('approved')],
    scripts: [makeScript('approved')],
    productions: [],
  });
  await openProductionCenter(page);

  const guide = page.getByTestId('first-video-guide');
  await expect(guide).toBeVisible();
  await expect(page.getByTestId('guide-step-voice')).toContainText('não foi possível verificar');
  await expect(page.getByTestId('guide-step-write_script')).toContainText('Feito');
  await expect(page.getByTestId('guide-step-approve_script')).toContainText('Feito');
  await expect(page.getByTestId('guide-step-produce_final')).toContainText('Pendente');
});

test('recolher lembra preferência no localStorage, funciona sem localStorage e navega por teclado', async ({ page }) => {
  await installBackend(page, {
    defaultVoice: { provider: 'kokoro', voice_id: 'pf_dora' },
    workflows: [],
  });
  await openProductionCenter(page);

  const toggle = page.getByTestId('first-video-guide').getByRole('button', { name: 'RECOLHER' });
  await toggle.focus();
  await expect(toggle).toBeFocused();
  await page.keyboard.press('Enter');

  await expect(page.locator('#first-video-guide-list')).toHaveCount(0);
  await expect(page.getByTestId('first-video-guide').getByRole('button', { name: 'EXPANDIR' })).toHaveAttribute(
    'aria-expanded',
    'false'
  );

  // Recarrega e confirma que permanece recolhido via localStorage.
  await openProductionCenter(page);
  await expect(page.locator('#first-video-guide-list')).toHaveCount(0);
  await page.getByTestId('first-video-guide').getByRole('button', { name: 'EXPANDIR' }).click();
  await expect(page.locator('#first-video-guide-list')).toBeVisible();

  // Simula localStorage bloqueado/indisponível para a chave do guia: o painel continua funcionando sem lançar erro.
  await page.addInitScript(() => {
    const original = window.localStorage;
    const wrapped = {
      getItem: (key: string) => {
        if (key === 'horus.first_video_guide.collapsed') throw new Error('SecurityError');
        return original.getItem(key);
      },
      setItem: (key: string, value: string) => {
        if (key === 'horus.first_video_guide.collapsed') throw new Error('QuotaExceededError');
        original.setItem(key, value);
      },
      removeItem: (key: string) => original.removeItem(key),
      clear: () => original.clear(),
      key: (index: number) => original.key(index),
      get length() { return original.length; },
    };
    Object.defineProperty(window, 'localStorage', { value: wrapped, configurable: true });
  });
  await openProductionCenter(page);
  await expect(page.getByTestId('first-video-guide')).toBeVisible();
  await page.getByTestId('first-video-guide').getByRole('button', { name: 'RECOLHER' }).click();
  await expect(page.locator('#first-video-guide-list')).toHaveCount(0);
});

test('HOR-143: guia Escrever roteiro abre fluxo manual sem proposta prévia, cria workflow e abre ScriptEditor + separa CLI legado (com axe)', async ({ page }) => {
  const manualWorkflowId = 'wf-manual-hor143';
  const manualProposalId = 'art-manual-proposal-hor143';
  let manualCreated = false;
  let manualWorkflowBody: Record<string, unknown> | null = null;
  let manualScriptCreated = false;

  await page.route('http://127.0.0.1:8765/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    if (method === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: corsHeaders });
    }
    const ok = (json: unknown, status = 200) => route.fulfill({ status, headers: corsHeaders, json });
    if (path === '/health') return ok({ status: 'ready' });
    if (path === '/v1/hermes') {
      return ok({ state: 'not_checked', connected: false, cli_available: false, configured: false, message: 'OK', checked_at: null, version: null, features: [] });
    }
    if (path === '/v1/notifications/unread-count') return ok({ count: 0 });
    if (path === '/v1/narration/default-voice') return ok({ voice: { provider: 'kokoro', voice_id: 'pf_dora' } });
    if (path === '/v1/jobs') return ok([]);

    if (path === '/v1/workflows/manual' && method === 'POST') {
      manualWorkflowBody = route.request().postDataJSON() as Record<string, unknown>;
      manualCreated = true;
      return ok({
        workflow_id: manualWorkflowId,
        status: 'approved',
        proposal_artifact_id: manualProposalId,
      }, 201);
    }

    if (path === '/v1/workflows') {
      if (!manualCreated) return ok([]);
      return ok([{
        id: manualWorkflowId,
        status: 'approved',
        stage: 'proposal',
        revision: 1,
        created_at: '2026-11-16T00:00:00Z',
        updated_at: '2026-11-16T00:00:00Z',
        simulated: false,
        executor: 'manual',
      }]);
    }

    if (path === `/v1/workflows/${manualWorkflowId}`) {
      return ok({
        id: manualWorkflowId,
        status: 'approved',
        stage: 'proposal',
        revision: 1,
        current_artifact_id: manualProposalId,
        created_at: '2026-11-16T00:00:00Z',
        updated_at: '2026-11-16T00:00:00Z',
        simulated: false,
        executor: 'manual',
        artifacts: [{
          id: manualProposalId,
          workflow_id: manualWorkflowId,
          revision: 1,
          stage: 'proposal',
          created_at: '2026-11-16T00:00:00Z',
          content: {
            stage: 'proposal',
            title: String(manualWorkflowBody?.title ?? 'Meu primeiro vídeo'),
            recommendation: { premise: 'Roteiro manual criado pelo usuário.' },
          },
        }],
        approvals: [{
          id: 'app-manual-1',
          workflow_id: manualWorkflowId,
          revision: 1,
          artifact_id: manualProposalId,
          decision: 'approve',
          feedback: '',
          created_at: '2026-11-16T00:00:00Z',
        }],
        cognitive_runs: [],
        events: [],
      });
    }

    if (path === `/v1/workflows/${manualWorkflowId}/scripts` && method === 'GET') {
      if (!manualScriptCreated) return ok({ items: [] });
      return ok({
        items: [{
          id: 'script-manual-hor143',
          workflow_id: manualWorkflowId,
          workflow_revision: 1,
          proposal_artifact_id: manualProposalId,
          proposal_content_hash: 'hash-hor143',
          version: 1,
          status: 'awaiting_review',
          origin: 'human',
          authored_by: 'human',
          created_at: '2026-11-16T00:00:00Z',
          review: null,
          document: {
            schema_version: 1,
            simulated: false,
            title: String(manualWorkflowBody?.title ?? 'Meu primeiro vídeo'),
            sections: [
              { id: 'sec-1', kind: 'hook', heading: 'Abertura', narration: 'Texto inicial do vídeo.', source_ids: [] },
            ],
          },
        }],
      });
    }

    if (path === `/v1/workflows/${manualWorkflowId}/scripts` && method === 'POST') {
      manualScriptCreated = true;
      const body = route.request().postDataJSON() as { document: Record<string, unknown> };
      return ok({
        id: 'script-manual-hor143',
        workflow_id: manualWorkflowId,
        workflow_revision: 1,
        proposal_artifact_id: manualProposalId,
        proposal_content_hash: 'hash-hor143',
        version: 1,
        status: 'awaiting_review',
        origin: 'human',
        authored_by: 'human',
        created_at: '2026-11-16T00:00:00Z',
        review: null,
        document: body.document,
      }, 201);
    }

    return route.fulfill({ status: 404, headers: corsHeaders, json: { detail: `não mockado: ${path}` } });
  });

  const pageErrors: string[] = [];
  page.on('pageerror', err => pageErrors.push(err.message));
  await openProductionCenter(page);

  // Clicar em "Abrir Produção" no passo 2 (Escrever roteiro) abre diretamente o formulário de roteiro do zero
  await page.getByTestId('guide-step-write_script').getByRole('button', { name: /Abrir Produção/i }).click();

  const manualForm = page.getByTestId('manual-script-create-form');
  await expect(manualForm).toBeVisible();

  // Seção legada (FABRICA_HOME / Consulta de perfil) fica recolhida em Recursos avançados
  const legacyAdvanced = page.getByTestId('legacy-factory-advanced');
  await expect(legacyAdvanced).toBeVisible();
  await expect(legacyAdvanced).not.toHaveAttribute('open', '');
  await expect(page.getByRole('button', { name: 'CONSULTAR PERFIL' })).toHaveCount(0);

  // Auditoria axe no formulário de roteiro do zero + seção avançada recolhida
  const axeForm = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(axeForm.violations).toEqual([]);

  // Preenche só o título, cria workflow manual e abre ScriptEditor automaticamente com o título preenchido
  await manualForm.getByLabel('Título do roteiro').fill('Meu primeiro vídeo local');
  await manualForm.getByRole('button', { name: 'Criar e abrir editor' }).click();

  expect(manualWorkflowBody).toEqual({ title: 'Meu primeiro vídeo local' });
  const editor = page.getByRole('region', { name: /Editor de roteiro humano/i });
  await expect(editor).toBeVisible();
  await expect(editor.getByLabel(/Título do roteiro/i)).toHaveValue('Meu primeiro vídeo local');

  // Preenche a primeira seção e salva
  await editor.getByLabel(/Narração/i).first().fill('Olá, este é o meu primeiro vídeo criado localmente.');
  await editor.getByRole('button', { name: 'Salvar roteiro', exact: true }).click();
  await expect(page.getByText('Olá, este é o meu primeiro vídeo criado localmente.')).toBeVisible();

  // Expandindo Recursos avançados, mostra explicação clara e os controles legados
  await legacyAdvanced.locator('summary').click();
  await expect(legacyAdvanced).toHaveAttribute('open', '');
  await expect(legacyAdvanced).toContainText('local_pilot');
  await expect(legacyAdvanced).toContainText('FABRICA_HOME');
  await expect(page.getByRole('button', { name: 'CONSULTAR PERFIL' })).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test('HOR-143: Produção sem workflows exibe estado vazio claro para escrever roteiro do zero', async ({ page }) => {
  await installBackend(page, { defaultVoice: null, workflows: [] });
  await page.goto('/');
  await page.getByRole('button', { name: 'Ajustes', exact: true }).click();
  await page.getByLabel('Token da sessão').fill('test-session-token-first-video-0123456789');
  await page.getByRole('button', { name: 'CONECTAR BACKEND', exact: true }).click();
  await page.getByRole('button', { name: 'Produção', exact: true }).click();

  const emptyState = page.getByTestId('editorial-empty-state');
  await expect(emptyState).toBeVisible();
  await expect(emptyState.getByRole('button', { name: 'Escrever roteiro do zero' })).toBeVisible();

  const axeEmpty = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(axeEmpty.violations).toEqual([]);
});
