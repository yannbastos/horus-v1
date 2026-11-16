import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

type MockUpdatePhase =
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

type MockUpdateState = {
  phase: MockUpdatePhase;
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

type UpdaterSecurityMockWindow = Window & {
  __updaterMock: {
    calls: InvokeCall[];
    emitState: (state: MockUpdateState) => void;
    setNextCheckState: (state: MockUpdateState) => void;
    setNextInstallState: (state: MockUpdateState) => void;
    deferNextCheck: () => void;
    resolveDeferredCheck: (state: MockUpdateState) => void;
    deferNextInstall: () => void;
    resolveDeferredInstall: (state: MockUpdateState) => void;
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
    let shouldDeferCheck = false;
    let shouldDeferInstall = false;
    let pendingCheckResolve: ((state: MockUpdateState) => void) | null = null;
    let pendingInstallResolve: ((state: MockUpdateState) => void) | null = null;

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
        deferNextCheck() {
          shouldDeferCheck = true;
        },
        resolveDeferredCheck(next: MockUpdateState) {
          const resolve = pendingCheckResolve;
          pendingCheckResolve = null;
          shouldDeferCheck = false;
          currentState = structuredClone(next);
          resolve?.(currentState);
        },
        deferNextInstall() {
          shouldDeferInstall = true;
        },
        resolveDeferredInstall(next: MockUpdateState) {
          const resolve = pendingInstallResolve;
          pendingInstallResolve = null;
          shouldDeferInstall = false;
          currentState = structuredClone(next);
          resolve?.(currentState);
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
            if (shouldDeferCheck) {
              return new Promise<MockUpdateState>(resolve => {
                pendingCheckResolve = resolve;
              });
            }
            if (nextCheckState) {
              currentState = nextCheckState;
              nextCheckState = null;
            }
            return currentState;
          }
          if (command === 'update_install') {
            if (shouldDeferInstall) {
              return new Promise<MockUpdateState>(resolve => {
                pendingInstallResolve = resolve;
              });
            }
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

async function getCallsByCommand(page: Page, command: string): Promise<InvokeCall[]> {
  return page.evaluate(
    cmd =>
      (window as unknown as UpdaterSecurityMockWindow).__updaterMock.calls.filter(
        c => c.command === cmd,
      ),
    command,
  );
}

test('S1: UI livre recebe bloqueio do backend em update_install e exibe bloqueio sem progresso nem sucesso', async ({
  page,
}) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'available',
      available: {
        version: '0.2.0',
        notes: 'Atualização pronta para teste de bloqueio.',
        pub_date: '2026-11-16T14:00:00Z',
      },
    }),
  );
  await openSettings(page);

  await expect(page.getByTestId('update-install-button')).toBeEnabled();
  await expect(page.getByTestId('update-blocked-notice')).toHaveCount(0);

  await page.evaluate(() => {
    (window as unknown as UpdaterSecurityMockWindow).__updaterMock.setNextInstallState({
      phase: 'blocked',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      last_check_at: '2026-11-16T12:00:00Z',
      available: {
        version: '0.2.0',
        notes: 'Atualização pronta para teste de bloqueio.',
        pub_date: '2026-11-16T14:00:00Z',
      },
      blocked_reason: 'job_running',
      progress: null,
      backup_path_label: null,
      error: null,
    });
  });

  await page.getByTestId('update-install-button').click();
  await expect(page.getByTestId('update-confirm-dialog')).toBeVisible();
  await page.getByTestId('update-confirm-install-button').click();

  await expect(page.getByTestId('update-confirm-dialog')).toHaveCount(0);
  await expect(page.getByTestId('update-blocked-notice')).toBeVisible();
  await expect(page.getByTestId('update-blocked-notice')).toContainText(
    'renderizações, transcrições',
  );
  await expect(page.getByTestId('update-install-button')).toBeDisabled();

  await expect(page.getByTestId('update-status-backing-up')).toHaveCount(0);
  await expect(page.getByTestId('update-status-downloading')).toHaveCount(0);
  await expect(page.getByTestId('update-status-installing')).toHaveCount(0);
  await expect(page.getByTestId('update-status-up-to-date')).toHaveCount(0);

  const installCalls = await getCallsByCommand(page, 'update_install');
  expect(installCalls).toEqual([
    { command: 'update_install', args: { confirmed_version: '0.2.0' } },
  ]);
});

test('S3: recusa versão igual e versão menor sem botão de tentar novamente e sem ação para instalar versão anterior', async ({
  page,
}) => {
  await setupTauriUpdater(page, makeState({ phase: 'idle', current_version: '0.1.0' }));
  await openSettings(page);

  // Versão igual (0.1.0 == 0.1.0)
  await page.evaluate(() => {
    (window as unknown as UpdaterSecurityMockWindow).__updaterMock.setNextCheckState({
      phase: 'error',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      last_check_at: '2026-11-16T12:05:00Z',
      available: null,
      blocked_reason: null,
      progress: null,
      backup_path_label: null,
      error: {
        code: 'downgrade_refused',
        message: 'manifest version 0.1.0 <= current 0.1.0',
      },
    });
  });

  await page.getByTestId('update-check-button').click();
  await expect(page.getByTestId('update-error-message')).toContainText(
    'igual ou inferior à instalada',
  );
  await expect(page.getByTestId('update-error-detail')).toContainText('0.1.0 <= current 0.1.0');
  await expect(page.getByTestId('update-retry-button')).toHaveCount(0);
  await expect(page.getByTestId('update-install-button')).toHaveCount(0);
  await expect(page.getByTestId('update-confirm-dialog')).toHaveCount(0);
  await expect(page.getByTestId('update-available-notice')).toHaveCount(0);

  // Versão menor (0.0.9 < 0.1.0)
  await page.evaluate(() => {
    (window as unknown as UpdaterSecurityMockWindow).__updaterMock.setNextCheckState({
      phase: 'error',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      last_check_at: '2026-11-16T12:06:00Z',
      available: null,
      blocked_reason: null,
      progress: null,
      backup_path_label: null,
      error: {
        code: 'downgrade_refused',
        message: 'manifest version 0.0.9 <= current 0.1.0',
      },
    });
  });

  await page.getByTestId('update-check-button').click();
  await expect(page.getByTestId('update-error-message')).toContainText(
    'igual ou inferior à instalada',
  );
  await expect(page.getByTestId('update-error-detail')).toContainText('0.0.9 <= current 0.1.0');
  await expect(page.getByTestId('update-retry-button')).toHaveCount(0);
  await expect(page.getByTestId('update-install-button')).toHaveCount(0);
  await expect(page.getByTestId('update-confirm-dialog')).toHaveCount(0);
  await expect(page.getByTestId('update-available-notice')).toHaveCount(0);

  const installCalls = await getCallsByCommand(page, 'update_install');
  expect(installCalls).toHaveLength(0);
});

test('S4: durante verificação, download e instalação os controles ficam desabilitados e cliques repetidos não duplicam chamadas IPC', async ({
  page,
}) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'idle',
      available: null,
    }),
  );
  await openSettings(page);

  // 1) Verificação concorrente: update_check pendente + cliques repetidos
  await page.evaluate(() => {
    (window as unknown as UpdaterSecurityMockWindow).__updaterMock.deferNextCheck();
  });

  await page.getByTestId('update-check-button').click();
  await expect(page.getByTestId('update-check-button')).toBeDisabled();
  await expect(page.getByTestId('update-auto-check-switch')).toBeDisabled();

  await page.getByTestId('update-check-button').click({ force: true });
  await page.getByTestId('update-check-button').click({ force: true });
  await page.getByTestId('update-auto-check-switch').click({ force: true });

  expect(await getCallsByCommand(page, 'update_check')).toHaveLength(1);
  expect(await getCallsByCommand(page, 'update_set_auto_check')).toHaveLength(0);

  await page.evaluate(() => {
    (window as unknown as UpdaterSecurityMockWindow).__updaterMock.resolveDeferredCheck({
      phase: 'available',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      last_check_at: '2026-11-16T12:10:00Z',
      available: { version: '0.2.0', notes: 'Pacote válido.', pub_date: null },
      blocked_reason: null,
      progress: null,
      backup_path_label: null,
      error: null,
    });
  });

  await expect(page.getByTestId('update-install-button')).toBeEnabled();

  // 2) Instalação em voo: update_install pendente + cliques repetidos
  await page.evaluate(() => {
    (window as unknown as UpdaterSecurityMockWindow).__updaterMock.deferNextInstall();
  });

  await page.getByTestId('update-install-button').click();
  await page.getByTestId('update-confirm-install-button').click();

  await expect(page.getByTestId('update-confirm-dialog')).toHaveCount(0);
  await expect(page.getByTestId('update-check-button')).toBeDisabled();
  await expect(page.getByTestId('update-auto-check-switch')).toBeDisabled();
  await expect(page.getByTestId('update-install-button')).toBeDisabled();

  await page.getByTestId('update-install-button').click({ force: true });
  await page.getByTestId('update-check-button').click({ force: true });

  expect(await getCallsByCommand(page, 'update_install')).toHaveLength(1);
  expect(await getCallsByCommand(page, 'update_check')).toHaveLength(1);

  // 3) Download em progresso: controles desabilitados e sem ação de instalação
  await page.evaluate(() => {
    (window as unknown as UpdaterSecurityMockWindow).__updaterMock.resolveDeferredInstall({
      phase: 'downloading',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      last_check_at: '2026-11-16T12:10:00Z',
      available: { version: '0.2.0', notes: 'Pacote válido.', pub_date: null },
      blocked_reason: null,
      progress: { downloaded: 2048, total: 8192 },
      backup_path_label: 'horus-backup-0.1.0.db',
      error: null,
    });
  });

  await expect(page.getByTestId('update-status-downloading')).toBeVisible();
  await expect(page.getByTestId('update-check-button')).toBeDisabled();
  await expect(page.getByTestId('update-auto-check-switch')).toBeDisabled();
  await expect(page.getByTestId('update-install-button')).toHaveCount(0);

  await page.getByTestId('update-check-button').click({ force: true });
  expect(await getCallsByCommand(page, 'update_check')).toHaveLength(1);
  expect(await getCallsByCommand(page, 'update_install')).toHaveLength(1);

  // 4) Instalação em andamento: controles continuam desabilitados
  await page.evaluate(() => {
    (window as unknown as UpdaterSecurityMockWindow).__updaterMock.emitState({
      phase: 'installing',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      last_check_at: '2026-11-16T12:10:00Z',
      available: { version: '0.2.0', notes: 'Pacote válido.', pub_date: null },
      blocked_reason: null,
      progress: null,
      backup_path_label: 'horus-backup-0.1.0.db',
      error: null,
    });
  });

  await expect(page.getByTestId('update-status-installing')).toBeVisible();
  await expect(page.getByTestId('update-check-button')).toBeDisabled();
  await expect(page.getByTestId('update-auto-check-switch')).toBeDisabled();
  await expect(page.getByTestId('update-install-button')).toHaveCount(0);

  await page.getByTestId('update-check-button').click({ force: true });
  expect(await getCallsByCommand(page, 'update_check')).toHaveLength(1);
  expect(await getCallsByCommand(page, 'update_install')).toHaveLength(1);
});

test('S5: assinatura inválida exibe erro claro, sem sucesso e sem repetir ou chamar update_install novamente', async ({
  page,
}) => {
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'available',
      available: {
        version: '0.2.0',
        notes: 'Atualização para teste de assinatura.',
        pub_date: null,
      },
    }),
  );
  await openSettings(page);

  await page.evaluate(() => {
    (window as unknown as UpdaterSecurityMockWindow).__updaterMock.setNextInstallState({
      phase: 'error',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      last_check_at: '2026-11-16T12:00:00Z',
      available: null,
      blocked_reason: null,
      progress: null,
      backup_path_label: 'horus-backup-0.1.0.db',
      error: {
        code: 'signature_invalid',
        message: 'assinatura minisign divergente',
      },
    });
  });

  await page.getByTestId('update-install-button').click();
  await page.getByTestId('update-confirm-install-button').click();

  await expect(page.getByTestId('update-error-message')).toContainText(
    'assinatura digital do pacote de atualização é inválida',
  );
  await expect(page.getByTestId('update-status-up-to-date')).toHaveCount(0);
  await expect(page.getByTestId('update-retry-button')).toHaveCount(0);
  await expect(page.getByTestId('update-install-button')).toHaveCount(0);

  await page.waitForTimeout(250);
  expect(await getCallsByCommand(page, 'update_install')).toHaveLength(1);
  expect(await getCallsByCommand(page, 'update_check')).toHaveLength(0);
});

test('S6: tela não exibe atualizado em fases intermediárias até o limite do contrato UpdateState', async ({
  page,
}) => {
  // Nota de contrato: UpdatePhase em frontend/src/services/updates.ts ainda não distingue
  // "instalado" de "saudável após reiniciar"; este teste cobre todas as fases intermediárias
  // existentes e falha pós-instalação sem inventar fase nova.
  await setupTauriUpdater(
    page,
    makeState({
      phase: 'available',
      available: { version: '0.2.0', notes: 'Notas.', pub_date: null },
    }),
  );
  await openSettings(page);

  const section = page.getByTestId('settings-updates-section');

  const intermediatePhases: MockUpdateState[] = [
    makeState({ phase: 'checking' }),
    makeState({ phase: 'backing_up', backup_path_label: 'horus-backup-0.1.0.db' }),
    makeState({
      phase: 'downloading',
      available: { version: '0.2.0', notes: '', pub_date: null },
      progress: { downloaded: 512, total: 1024 },
    }),
    makeState({
      phase: 'installing',
      available: { version: '0.2.0', notes: '', pub_date: null },
      backup_path_label: 'horus-backup-0.1.0.db',
    }),
  ];

  for (const state of intermediatePhases) {
    await page.evaluate(next => {
      (window as unknown as UpdaterSecurityMockWindow).__updaterMock.emitState(next);
    }, state);

    await expect(page.getByTestId('update-status-up-to-date')).toHaveCount(0);
    await expect(section).not.toContainText(/já está na versão mais recente|atualizado com sucesso/i);
  }

  await page.evaluate(() => {
    (window as unknown as UpdaterSecurityMockWindow).__updaterMock.emitState({
      phase: 'error',
      current_version: '0.1.0',
      auto_check: true,
      configured: true,
      last_check_at: '2026-11-16T12:00:00Z',
      available: null,
      blocked_reason: null,
      progress: null,
      backup_path_label: 'horus-backup-0.1.0.db',
      error: {
        code: 'install_failed',
        message: 'backend health check failed after restart',
      },
    });
  });

  await expect(page.getByTestId('update-error-message')).toContainText(
    'instalação da atualização falhou',
  );
  await expect(page.getByTestId('update-status-up-to-date')).toHaveCount(0);
});
