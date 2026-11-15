import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import {
  connectSession,
  corsHeaders,
  installBackend,
  newBackendState,
} from './production-journey-helpers';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

async function runAxeInBothThemes(page: Page, contextLabel: string) {
  const originalTheme = await page.evaluate(
    () => document.querySelector('.horus')?.getAttribute('data-theme') ?? 'light',
  );

  for (const theme of [originalTheme, originalTheme === 'dark' ? 'light' : 'dark']) {
    await page.evaluate(value => {
      document.querySelector('.horus')?.setAttribute('data-theme', value);
    }, theme);
    await page.evaluate(() =>
      Promise.all(document.getAnimations().map(anim => anim.finished.catch(() => undefined))),
    );
    const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
    const formatted = results.violations.map(v => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      nodes: v.nodes.map(n => n.target),
    }));
    expect(
      formatted,
      `Violações axe-core (${WCAG_TAGS.join(', ')}) em [${contextLabel} · tema ${theme}]`,
    ).toEqual([]);
  }

  await page.evaluate(value => {
    document.querySelector('.horus')?.setAttribute('data-theme', value);
  }, originalTheme);
}

async function setupConnectedApp(page: Page) {
  const state = newBackendState();
  await installBackend(page, state);

  await page.route('http://127.0.0.1:8765/v1/media', async route => {
    await route.fulfill({
      status: 200,
      headers: { ...corsHeaders, 'content-type': 'application/json' },
      body: JSON.stringify([
        {
          name: 'entrevista-piloto.mp4',
          path: 'C:\\midia\\entrevista-piloto.mp4',
          size: 1024 * 1024,
          type: 'video',
          extension: '.mp4',
          url: '/v1/media/file?path=entrevista-piloto.mp4',
        },
      ]),
    });
  });

  await page.route('http://127.0.0.1:8765/v1/notifications**', async route => {
    await route.fulfill({
      status: 200,
      headers: { ...corsHeaders, 'content-type': 'application/json' },
      body: JSON.stringify({
        items: [
          {
            id: 'notif-shortcut-1',
            kind: 'production.succeeded',
            severity: 'success',
            title: 'Produção concluída',
            message: 'Vídeo pronto para revisão.',
            at: '2026-11-15T08:00:00Z',
            ref: { kind: 'production', id: 'prod-1' },
            pending: false,
            read: false,
          },
        ],
        unread_count: 1,
        next_cursor: null,
      }),
    });
  });

  await connectSession(page);
  await page.getByRole('button', { name: 'Visão geral', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Um novo olhar para criar.' })).toBeVisible();
}

test.describe('Atalhos de teclado e auditoria de acessibilidade (HOR-101)', () => {
  test('painel de ajuda (?), armadilha de foco, Escape e proteção em campos de texto', async ({ page }) => {
    await setupConnectedApp(page);

    // Abre o painel de ajuda via tecla ?
    await page.keyboard.press('?');
    const dialog = page.getByTestId('shortcuts-help-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute('role', 'dialog');
    await expect(dialog).toHaveAttribute('aria-modal', 'true');

    // Foco inicial vai para o botão Fechar
    const closeBtn = page.getByTestId('shortcuts-help-close');
    await expect(closeBtn).toBeFocused();

    // Tab / Shift+Tab mantém o foco preso dentro do modal (closeBtn -> modal-body -> open-settings)
    await page.keyboard.press('Tab');
    await expect(dialog.locator('.shortcuts-modal-body')).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.getByTestId('shortcuts-open-settings')).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Shift+Tab');
    await expect(closeBtn).toBeFocused();

    // Auditoria axe-core com o painel de ajuda aberto (temas claro e escuro)
    await runAxeInBothThemes(page, 'Painel de ajuda de atalhos');

    // Escape fecha o painel
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();

    // Em campo de texto (Transcrição textarea), ? digita normalmente e NÃO abre o modal
    await page.keyboard.press('Alt+8');
    const transcriptBox = page.getByRole('textbox', { name: 'Transcrição' });
    await expect(transcriptBox).toBeVisible();
    await transcriptBox.focus();
    await page.keyboard.type('Tudo certo?');
    await expect(transcriptBox).toHaveValue('Tudo certo?');
    await expect(dialog).toBeHidden();
  });

  test('navegação entre seções, busca na Biblioteca (Ctrl+K), notificações (Alt+N) e salvar transcrição (Ctrl+S)', async ({
    page,
  }) => {
    await setupConnectedApp(page);

    // Alt+3 -> Biblioteca
    await page.keyboard.press('Alt+3');
    await expect(page.getByRole('heading', { level: 1, name: 'Sua mídia, em um lugar.' })).toBeVisible();
    await expect(page.locator('#main')).toBeFocused();

    // Ctrl+K -> foca busca da Biblioteca (mesmo vindo de outra seção)
    await page.keyboard.press('Alt+1');
    await expect(page.getByRole('heading', { level: 1, name: 'Um novo olhar para criar.' })).toBeVisible();
    await page.keyboard.press('Control+k');
    const searchInput = page.getByRole('searchbox', { name: 'Buscar mídia por nome' });
    await expect(searchInput).toBeVisible();
    await expect(searchInput).toBeFocused();

    // Alt+N -> abre e fecha Notificações
    await page.keyboard.press('Alt+n');
    await expect(page.getByTestId('notifications-panel')).toBeVisible();
    await page.keyboard.press('Alt+n');
    await expect(page.getByTestId('notifications-panel')).toBeHidden();

    // Alt+8 -> Transcrição e Ctrl+S salva .txt
    await page.keyboard.press('Alt+8');
    const transcriptBox = page.getByRole('textbox', { name: 'Transcrição' });
    await transcriptBox.fill('Texto transcrito para teste de atalho.');
    const downloadPromise = page.waitForEvent('download');
    await page.keyboard.press('Control+s');
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('transcricao.txt');
  });

  test('configuração e desativação de atalhos em Ajustes com persistência local e validação de conflitos', async ({
    page,
  }) => {
    await setupConnectedApp(page);

    // Alt+9 -> Ajustes
    await page.keyboard.press('Alt+9');
    const settingsSection = page.getByTestId('shortcuts-settings');
    await expect(settingsSection).toBeVisible();
    await runAxeInBothThemes(page, 'Ajustes com seção de atalhos');

    // Desativar todos os atalhos impede ? e Alt+3
    const toggle = page.getByTestId('shortcuts-enabled-toggle');
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-checked', 'false');

    await page.keyboard.press('?');
    await expect(page.getByTestId('shortcuts-help-dialog')).toBeHidden();
    await page.keyboard.press('Alt+3');
    await expect(page.getByTestId('shortcuts-settings')).toBeVisible();

    // Reativar atalhos
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-checked', 'true');

    // Alterar atalho de Biblioteca para tecla insegura (letra simples) é rejeitado
    await page.getByTestId('shortcut-edit-nav.library').click();
    await expect(page.getByTestId('shortcut-capture-prompt')).toBeVisible();
    await page.keyboard.press('k');
    await expect(page.getByTestId('shortcut-capture-error')).toContainText('conflitar');

    // Alterar para combinação já usada (Alt+1) é rejeitado por conflito
    await page.keyboard.press('Alt+1');
    await expect(page.getByTestId('shortcut-capture-error')).toContainText('já está em uso');

    // Alterar para combinação válida (Alt+L) funciona e persiste após reload
    await page.keyboard.press('Alt+l');
    await expect(page.getByTestId('shortcut-capture-prompt')).toBeHidden();
    await expect(page.getByTestId('shortcut-row-nav.library')).toContainText('Alt');
    await expect(page.getByTestId('shortcut-row-nav.library')).toContainText('L');

    await page.reload();
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.keyboard.press('Alt+l');
    await expect(page.getByRole('heading', { level: 1, name: 'Sua mídia, em um lugar.' })).toBeVisible();

    // Restaurar padrões
    await page.keyboard.press('Alt+9');
    await page.getByTestId('shortcuts-reset-all').click();
    await expect(page.getByTestId('shortcut-row-nav.library')).toContainText('3');
  });

  test('auditoria de landmarks, skip-link e axe-core nas telas principais', async ({ page }) => {
    await setupConnectedApp(page);

    // Landmarks principais presentes
    await expect(page.getByRole('banner')).toBeVisible();
    await expect(page.getByRole('complementary', { name: 'Navegação lateral' })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Navegação principal' })).toBeVisible();
    await expect(page.getByRole('main')).toBeVisible();
    await expect(page.getByRole('contentinfo')).toBeVisible();

    // Skip link funciona por teclado a partir do topo do documento
    await page.reload();
    await expect(page.getByRole('banner')).toBeVisible();
    await page.keyboard.press('Tab');
    const skipLink = page.getByRole('link', { name: 'Ir para o conteúdo' });
    await expect(skipLink).toBeFocused();
    await page.getByRole('button', { name: 'Visão geral', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Um novo olhar para criar.' })).toBeVisible();

    // Axe nas telas principais em ambos os temas
    await runAxeInBothThemes(page, 'Visão geral');

    await page.keyboard.press('Alt+3');
    await expect(page.getByRole('heading', { level: 1, name: 'Sua mídia, em um lugar.' })).toBeVisible();
    await runAxeInBothThemes(page, 'Biblioteca');

    await page.keyboard.press('Alt+5');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await runAxeInBothThemes(page, 'Central de Produção');

    await page.keyboard.press('Alt+8');
    await expect(page.getByRole('textbox', { name: 'Transcrição' })).toBeVisible();
    await runAxeInBothThemes(page, 'Transcrição');
  });
});
