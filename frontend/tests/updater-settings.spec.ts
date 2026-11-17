import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

type MockUpdateState = {
  phase:
    | 'disabled'
    | 'idle'
    | 'checking'
    | 'up_to_date'
    | 'available'
    | 'blocked'
    | 'backing_up'
    | 'downloading'
    | 'installing'
    | 'restarting'
    | 'error';
  current_version: string;
  installed_version?: string;
  auto_check: boolean;
  configured: boolean;
  channel?: string | null;
  enrolled?: boolean;
  last_check_at: string | null;
  available: { version: string; notes: string; pub_date: string | null } | null;
  available_version?: string | null;
  notes?: string | null;
  blocked_reason: 'job_running' | 'backend_unavailable' | null;
  progress: { phase?: string; downloaded: number; total: number | null } | null;
  backup_path_label: string | null;
  just_updated?: boolean;
  updated_from_version?: string | null;
  error: { code: string; message: string } | null;
  last_error?: { code: string; message: string } | null;
};

type InvokeCall = { command: string; args: Record<string, unknown> };

type UpdaterMockWindow = Window & {
  __updaterMock: {
    calls: InvokeCall[];
    emitState: (state: MockUpdateState) => void;
    setNextCheckState: (state: MockUpdateState) => void;
    setNextInstallState: (state: MockUpdateState) => void;
  };
};

function makeState(overrides: Partial<MockUpdateState> = {}): MockUpdateState {
  return {
    phase: 'idle',
    current_version: '0.1.0',
    auto_check: true,
    configured: true,
    last_check_at: '2026-11-16T12:00:00Z',
    available: null,
    blocked_reason: null,
    progress: null,
    backup_path_label: null,
    error: null,
    ...overrides,
  };
}

async function setupTauriUpdater(page: Page, initialState: MockUpdateState) {
  await page.addInitScript((seed: MockUpdateState) => {
    let id = 0;
    let currentState: MockUpdateState = structuredClone(seed);
    let nextCheckState: MockUpdateState | null = null;
    let nextInstallState: MockUpdateState | null = null;

    const callbacks = new Map<number, (event: unknown) => void>();
    const listeners: Record<string, { id: number; handler: number }[]> = {};
    const calls: InvokeCall[] = [];

    function emitState(next: MockUpdateState) {
      currentState = structuredClone(next);
      for (const item of [...(listeners['update://state'] ?? [])]) {
        callbacks.get(item.handler)?.({
          event: 'update://state',
          id: item.id,
          payload: currentState,
        });
      }
    }

    Object.assign(window, {
      isTauri: true,
      __updaterMock: {
        calls,
        emitState,
        setNextCheckState(next: MockUpdateState) {
          nextCheckState = structuredClone(next);
        },
        setNextInstallState(next: MockUpdateState) {
          nextInstallState = structuredClone(next);
        },
      },
      __TAURI_EVENT_PLUGIN_INTERNALS__: {
        unregisterListener(event: string, identifier: number) {
          listeners[event] = (listeners[event] ?? []).filter(item => item.id !== identifier);
        },
      },
      __TAURI_INTERNALS__: {
        metadata: { currentWindow: { label: 'main' }, currentWebview: { label: 'main' } },
        transformCallback(callback: (event: unknown) => void) {
          callbacks.set(++id, callback);
          return id;
        },
        unregisterCallback(identifier: number) {
          callbacks.delete(identifier);
        },
        async invoke(command: string, args: Record<string, unknown> = {}) {
          calls.push({ command, args });
          if (command === 'backend_connection') {
            return null;
          }
          if (command === 'update_get_state') {
            return currentState;
          }
          if (command === 'update_set_auto_check') {
            currentState = { ...currentState, auto_check: Boolean(args.enabled) };
            return currentState;
          }
          if (command === 'update_check') {
            if (nextCheckState) {
              currentState = nextCheckState;
              nextCheckState = null;
            }
            return currentState;
          }
          if (command === 'update_install') {
            if (nextInstallState) {
              currentState = nextInstallState;
              nextInstallState = null;
            }
            return currentState;
          }
          if (command === 'update_enroll') {
            currentState = { ...currentState, enrolled: true, error: null, phase: 'idle' };
            return currentState;
          }
          if (command === 'update_clear_enrollment') {
            currentState = { ...currentState, enrolled: false, available: null, phase: 'idle' };
            return currentState;
          }
          if (command === 'plugin:event|listen') {
            const identifier = ++id;
            const eventName = String(args.event ?? '');
            (listeners[eventName] ??= []).push({
              id: identifier,
              handler: Number(args.handler),
            });
            return identifier;
          }
          if (command === 'plugin:event|unlisten') {
            return;
          }
          throw new Error(`Comando inesperado: ${command}`);
        },
      },
    });
  }, initialState);
}

async function openSettings(page: Page) {
  await page.goto('/');
  await page.locator('.settings-navigation').click();
  await expect(page.getByTestId('settings-updates-section')).toBeVisible();
}

test('exibe estado disabled sem botões de ação quando canal não está configurado', async ({
  page,
}) => {
  await setupTauriUpdater(
    page,
    makeState({ phase: 'disabled', configured: false, auto_check: false }),
  );
  await openSettings(page);

  await expect(page.getByTestId('update-disabled-message')).toContainText(
    'não possui um canal de atualização configurado',
  );
  await expect(page.getByTestId('update-check-button')).toHaveCount(0);
  await expect(page.getByTestId('update-auto-check-switch')).toHaveCount(0);
  await expect(page.getByTestId('update-install-button')).toHaveCount(0);
  await expect(page.getByTestId('update-available-notice')).toHaveCount(0);
});

test('desliga a verificação automática via update_set_auto_check', async ({ page }) => {
  await setupTauriUpdater(page, makeState({ phase: 'idle', auto_check: true }));
  await openSettings(page);

  const toggle = page.getByTestId('update-auto-check-switch');
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await toggle.click();

  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await expect(toggle).toHaveText('DESATIVADO');

  const calls = await page.evaluate(() =>
    (window as unknown as UpdaterMockWindow).__updaterMock.calls.filter(
      c => c.command === 'update_set_auto_check',
    ),
  );
  expect(calls).toEqual([{ command: 'update_set_auto_check', args: { enabled: false } }]);
});

test('exibe aviso discreto na inicialização, abre confirmação e chama update_install com a versão', async ({
  page,
}) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'available',
      available: {
        version: '0.2.0',
        notes: 'Correções na fila de render e melhoria de estabilidade.',
        pub_date: '2026-11-16T14:00:00Z',
      },
    }),
  );

  await page.goto('/');

  const notice = page.getByTestId('update-available-notice');
  await expect(notice).toBeVisible();
  await expect(notice).toContainText('0.2.0');

  await page.getByTestId('update-notice-open-settings').click();
  await expect(page.getByTestId('settings-updates-section')).toBeVisible();
  await expect(page.getByTestId('update-available-card')).toContainText('0.2.0');
  await expect(page.getByTestId('update-available-card')).toContainText(
    'Correções na fila de render',
  );

  await page.evaluate(() => {
    (window as unknown as UpdaterMockWindow).__updaterMock.setNextInstallState({
      phase: 'backing_up',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      last_check_at: '2026-11-16T12:00:00Z',
      available: {
        version: '0.2.0',
        notes: 'Correções na fila de render e melhoria de estabilidade.',
        pub_date: '2026-11-16T14:00:00Z',
      },
      blocked_reason: null,
      progress: null,
      backup_path_label: 'horus-backup-0.1.0.db',
      error: null,
    });
  });

  await page.getByTestId('update-install-button').click();
  const dialog = page.getByTestId('update-confirm-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Backup dos dados locais');
  await expect(dialog).toContainText('Download do pacote');
  await expect(dialog).toContainText('Verificação da assinatura digital');
  await expect(dialog).toContainText('Reinício do aplicativo');

  const axeResults = await new AxeBuilder({ page })
    .include('[data-testid="update-confirm-dialog"]')
    .analyze();
  expect(axeResults.violations).toEqual([]);

  await page.getByTestId('update-confirm-install-button').click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId('update-status-backing-up')).toBeVisible();

  const installCalls = await page.evaluate(() =>
    (window as unknown as UpdaterMockWindow).__updaterMock.calls.filter(
      c => c.command === 'update_install',
    ),
  );
  expect(installCalls).toEqual([
    { command: 'update_install', args: { confirmed_version: '0.2.0' } },
  ]);

  await page.evaluate(() => {
    (window as unknown as UpdaterMockWindow).__updaterMock.emitState({
      phase: 'downloading',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      last_check_at: '2026-11-16T12:00:00Z',
      available: { version: '0.2.0', notes: '', pub_date: null },
      blocked_reason: null,
      progress: { downloaded: 5242880, total: 10485760 },
      backup_path_label: 'horus-backup-0.1.0.db',
      error: null,
    });
  });
  await expect(page.getByTestId('update-status-downloading')).toContainText('50%');
});

test('cancelar a confirmação não chama update_install e restaura o foco', async ({ page }) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'available',
      available: { version: '0.2.0', notes: 'Notas da versão.', pub_date: null },
    }),
  );
  await openSettings(page);

  await page.getByTestId('update-install-button').click();
  await expect(page.getByTestId('update-confirm-dialog')).toBeVisible();

  await page.getByTestId('update-confirm-dialog').getByRole('button', { name: 'Cancelar' }).click();
  await expect(page.getByTestId('update-confirm-dialog')).toHaveCount(0);
  await expect(page.getByTestId('update-install-button')).toBeFocused();

  const installCalls = await page.evaluate(() =>
    (window as unknown as UpdaterMockWindow).__updaterMock.calls.filter(
      c => c.command === 'update_install',
    ),
  );
  expect(installCalls).toHaveLength(0);
});

test('bloqueia instalação quando há job em execução', async ({ page }) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'blocked',
      blocked_reason: 'job_running',
      available: { version: '0.2.0', notes: 'Notas da versão.', pub_date: null },
    }),
  );
  await openSettings(page);

  await expect(page.getByTestId('update-blocked-notice')).toContainText(
    'renderizações, transcrições',
  );
  await expect(page.getByTestId('update-install-button')).toBeDisabled();
});

test('exibe erro claro quando assinatura é inválida sem botão de tentar novamente', async ({
  page,
}) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'error',
      error: {
        code: 'signature_invalid',
        message: 'assinatura minisign divergente',
      },
    }),
  );
  await openSettings(page);

  await expect(page.getByTestId('update-error-message')).toContainText(
    'assinatura digital do pacote de atualização é inválida',
  );
  await expect(page.getByTestId('update-retry-button')).toHaveCount(0);
});

test('exibe erro claro quando downgrade é recusado sem botão de tentar novamente', async ({
  page,
}) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'error',
      error: {
        code: 'downgrade_refused',
        message: 'manifest version 0.0.9 <= current 0.1.0',
      },
    }),
  );
  await openSettings(page);

  await expect(page.getByTestId('update-error-message')).toContainText(
    'igual ou inferior à instalada',
  );
  await expect(page.getByTestId('update-retry-button')).toHaveCount(0);
});

for (const scenario of [
  { code: 'network', text: 'erro de rede', retry: true, recovery: false },
  { code: 'manifest_invalid', text: 'manifesto da atualização é inválido', retry: false, recovery: false },
  { code: 'signature_invalid', text: 'assinatura digital do pacote de atualização é inválida', retry: false, recovery: false },
  { code: 'install_failed', text: 'instalação da atualização falhou', retry: true, recovery: true },
  { code: 'startup_failed', text: 'inicialização após a atualização não foi validada', retry: false, recovery: true },
  { code: 'downgrade_refused', text: 'igual ou inferior à instalada', retry: false, recovery: false },
  { code: 'future_error', text: 'Não foi possível concluir a atualização', retry: false, recovery: false },
]) {
  test(`A1: ${scenario.code} orienta sem expor detalhe técnico e só permite ação segura`, async ({ page }) => {
    await setupTauriUpdater(page, makeState({
      phase: 'error',
      error: { code: scenario.code, message: 'C:/Users/private/token=SECRET stack trace' },
    }));
    await openSettings(page);
    await expect(page.getByTestId('update-error-message')).toContainText(scenario.text);
    await expect(page.getByTestId('settings-updates-section')).not.toContainText('SECRET');
    await expect(page.getByTestId('update-error-detail')).toHaveCount(0);
    await expect(page.getByTestId('update-status-up-to-date')).toHaveCount(0);
    await expect(page.getByTestId('update-install-button')).toHaveCount(0);
    await expect(page.getByTestId('update-retry-button')).toHaveCount(scenario.retry ? 1 : 0);
    const recovery = page.getByTestId('update-recovery-guidance');
    await expect(recovery).toHaveCount(scenario.recovery ? 1 : 0);
    if (scenario.recovery) {
      await expect(recovery).toContainText('update-backups/pre-update-*.zip');
      await expect(recovery).toContainText('setup.exe.sig');
      await expect(recovery).toContainText('primeira instalação não guarda');
      await expect(recovery).toContainText('pasta nova');
      await expect(recovery).toContainText('Não há downgrade automático');
      await expect(recovery).toContainText('docs/adr-atualizador.md');
    }
    if (scenario.retry) {
      await page.evaluate(() => {
        (window as unknown as UpdaterMockWindow).__updaterMock.setNextCheckState({
          phase: 'up_to_date', current_version: '0.1.0', auto_check: true, configured: true,
          last_check_at: null, available: null, blocked_reason: null, progress: null,
          backup_path_label: null, error: null,
        });
      });
      await page.getByTestId('update-retry-button').click();
      await expect(page.getByTestId('update-status-up-to-date')).toBeVisible();
    }
    const calls = await page.evaluate(() => (window as unknown as UpdaterMockWindow).__updaterMock.calls);
    expect(calls.filter(c => c.command === 'update_check')).toHaveLength(scenario.retry ? 1 : 0);
    expect(calls.filter(c => c.command === 'update_install')).toHaveLength(0);
  });
}

test('fluxo completo: verificar -> aviso -> notas -> confirmar -> progresso -> reinício -> pós-atualização', async ({
  page,
}) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'idle',
      current_version: '0.1.0',
      channel: 'test',
      enrolled: true,
    }),
  );
  await openSettings(page);

  await expect(page.getByTestId('update-channel')).toHaveText('test');
  await expect(page.getByTestId('update-enrollment-status')).toHaveText('Inscrito');

  await page.evaluate(() => {
    (window as unknown as UpdaterMockWindow).__updaterMock.setNextCheckState({
      phase: 'available',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      channel: 'test',
      enrolled: true,
      last_check_at: '2026-11-17T10:00:00Z',
      available: {
        version: '0.2.0-test.1',
        notes: 'Melhoria de estabilidade na fila.',
        pub_date: '2026-11-17T09:50:00Z',
      },
      blocked_reason: null,
      progress: null,
      backup_path_label: null,
      error: null,
    });
  });

  await page.getByTestId('update-check-button').click();
  await expect(page.getByTestId('update-available-notice')).toContainText('0.2.0-test.1');
  await expect(page.getByTestId('update-available-version')).toContainText('0.2.0-test.1');
  await expect(page.getByTestId('update-available-notes')).toContainText('Melhoria de estabilidade na fila.');
  await expect(page.getByTestId('update-deferral-note')).toContainText('adiada ou bloqueada');

  await page.evaluate(() => {
    (window as unknown as UpdaterMockWindow).__updaterMock.setNextInstallState({
      phase: 'downloading',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      channel: 'test',
      enrolled: true,
      last_check_at: '2026-11-17T10:00:00Z',
      available: { version: '0.2.0-test.1', notes: 'Melhoria de estabilidade na fila.', pub_date: null },
      blocked_reason: null,
      progress: { phase: 'downloading', downloaded: 2500, total: 10000 },
      backup_path_label: null,
      error: null,
    });
  });

  await page.getByTestId('update-install-button').click();
  await expect(page.getByTestId('update-confirm-deferral-warning')).toContainText('adiada ou bloqueada');
  await page.getByTestId('update-confirm-install-button').click();

  const downloadBar = page.getByTestId('update-status-downloading').getByRole('progressbar');
  await expect(downloadBar).toHaveAttribute('aria-valuenow', '25');
  await expect(page.getByTestId('update-status-downloading')).toContainText('25%');

  await page.evaluate(() => {
    (window as unknown as UpdaterMockWindow).__updaterMock.emitState({
      phase: 'installing',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      channel: 'test',
      enrolled: true,
      last_check_at: '2026-11-17T10:00:00Z',
      available: { version: '0.2.0-test.1', notes: '', pub_date: null },
      blocked_reason: null,
      progress: null,
      backup_path_label: 'horus-backup-0.1.0.db',
      error: null,
    });
  });
  await expect(page.getByTestId('update-status-installing').getByRole('progressbar')).toBeVisible();

  await page.evaluate(() => {
    (window as unknown as UpdaterMockWindow).__updaterMock.emitState({
      phase: 'restarting',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      channel: 'test',
      enrolled: true,
      last_check_at: '2026-11-17T10:00:00Z',
      available: null,
      blocked_reason: null,
      progress: null,
      backup_path_label: null,
      error: null,
    });
  });
  await expect(page.getByTestId('update-status-restarting')).toContainText('Reiniciando o aplicativo');
  await expect(page.getByTestId('update-status-restarting').getByRole('progressbar')).toBeVisible();

  await page.evaluate(() => {
    (window as unknown as UpdaterMockWindow).__updaterMock.emitState({
      phase: 'up_to_date',
      current_version: '0.2.0-test.1',
      auto_check: true,
      configured: true,
      channel: 'test',
      enrolled: true,
      last_check_at: '2026-11-17T10:02:00Z',
      available: null,
      blocked_reason: null,
      progress: null,
      backup_path_label: null,
      just_updated: true,
      updated_from_version: '0.1.0',
      error: null,
    });
  });
  await expect(page.getByTestId('update-current-version')).toHaveText('0.2.0-test.1');
  await expect(page.getByTestId('update-post-update-notice')).toContainText('0.2.0-test.1');
  await expect(page.getByTestId('update-post-update-notice')).toContainText('0.1.0');
});

test('inscrição e remoção de token no canal com teclado e acessibilidade', async ({ page }) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'idle',
      channel: 'test',
      enrolled: false,
    }),
  );
  await openSettings(page);

  await expect(page.getByTestId('update-enrollment-status')).toHaveText('Não inscrito');
  const input = page.getByTestId('update-enroll-token-input');
  await expect(input).toHaveAttribute('type', 'password');
  await expect(page.getByTestId('update-enroll-submit-button')).toBeDisabled();

  const axeResults = await new AxeBuilder({ page })
    .include('[data-testid="settings-updates-section"]')
    .analyze();
  expect(axeResults.violations).toEqual([]);

  await input.focus();
  await input.fill('horus_tok_demo_value_999');
  await expect(page.getByTestId('update-enroll-submit-button')).toBeEnabled();
  await input.press('Enter');

  await expect(page.getByTestId('update-enrollment-status')).toHaveText('Inscrito');
  await expect(page.getByTestId('update-enroll-token-input')).toHaveCount(0);

  await page.getByTestId('update-clear-enrollment-button').click();
  await expect(page.getByTestId('update-enrollment-status')).toHaveText('Não inscrito');
  await expect(page.getByTestId('update-enroll-token-input')).toHaveValue('');

  const enrollCalls = await page.evaluate(() =>
    (window as unknown as UpdaterMockWindow).__updaterMock.calls.filter(
      c => c.command === 'update_enroll' || c.command === 'update_clear_enrollment',
    ),
  );
  expect(enrollCalls).toEqual([
    { command: 'update_enroll', args: { token: 'horus_tok_demo_value_999' } },
    { command: 'update_clear_enrollment', args: {} },
  ]);
});

test('degradação graciosa quando backend omite campos novos ou usa nomes aditivos', async ({
  page,
}) => {
  const pageErrors: string[] = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await page.addInitScript(() => {
    const callbacks = new Map<number, (event: unknown) => void>();
    let id = 0;
    Object.assign(window, {
      isTauri: true,
      __TAURI_EVENT_PLUGIN_INTERNALS__: { unregisterListener() {} },
      __TAURI_INTERNALS__: {
        metadata: { currentWindow: { label: 'main' }, currentWebview: { label: 'main' } },
        transformCallback(callback: (event: unknown) => void) {
          callbacks.set(++id, callback);
          return id;
        },
        unregisterCallback(identifier: number) {
          callbacks.delete(identifier);
        },
        async invoke(command: string) {
          if (command === 'backend_connection') return null;
          if (command === 'plugin:event|listen') return ++id;
          if (command === 'plugin:event|unlisten') return;
          if (command === 'update_get_state') {
            return {
              configured: true,
              installed_version: '0.1.5',
              available_version: '0.2.0',
              notes: 'Notas aditivas',
              phase: 'available',
            };
          }
          return null;
        },
      },
    });
  });

  await openSettings(page);

  await expect(page.getByTestId('update-current-version')).toHaveText('0.1.5');
  await expect(page.getByTestId('update-available-version')).toContainText('0.2.0');
  await expect(page.getByTestId('update-available-notes')).toContainText('Notas aditivas');
  await expect(page.getByTestId('update-channel')).toHaveCount(0);
  await expect(page.getByTestId('update-channel-access')).toHaveCount(0);
  expect(pageErrors).toEqual([]);
});

test('progresso indeterminado quando backend envia progress.phase sem downloaded ou total', async ({
  page,
}) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'downloading',
      available: { version: '0.2.0', notes: 'Notas.', pub_date: null },
      progress: { phase: 'downloading', total: null },
    }),
  );
  await openSettings(page);

  const downloading = page.getByTestId('update-status-downloading');
  const bar = downloading.getByRole('progressbar');
  await expect(bar).toBeVisible();
  await expect(bar).not.toHaveAttribute('aria-valuenow', /.+/);
  await expect(downloading).toContainText('Fase: downloading');
  await expect(downloading).not.toContainText('%');
});
