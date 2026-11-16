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
    | 'error';
  current_version: string;
  auto_check: boolean;
  configured: boolean;
  last_check_at: string | null;
  available: { version: string; notes: string; pub_date: string | null } | null;
  blocked_reason: 'job_running' | 'backend_unavailable' | null;
  progress: { downloaded: number; total: number | null } | null;
  backup_path_label: string | null;
  error: { code: string; message: string } | null;
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
