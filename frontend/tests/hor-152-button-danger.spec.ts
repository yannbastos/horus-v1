import { expect, test } from '@playwright/test';
import { connectSession, installBackend, newBackendState } from './production-journey-helpers';

test.describe('HOR-152: definição única de .button.danger em app.css', () => {
  test('estados normal, hover, disabled e focus-visible nos temas escuro e claro', async ({ page }) => {
    const state = newBackendState();
    await installBackend(page, state);
    await connectSession(page);

    for (const theme of ['dark', 'light'] as const) {
      await page.evaluate(value => {
        const root = document.querySelector('.horus')!;
        root.setAttribute('data-theme', value);

        document.getElementById('hor-152-probe')?.remove();
        const probe = document.createElement('div');
        probe.id = 'hor-152-probe';
        probe.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:9999;display:flex;gap:8px;padding:8px;background:var(--Background);';

        const enabledBtn = document.createElement('button');
        enabledBtn.type = 'button';
        enabledBtn.className = 'button danger';
        enabledBtn.dataset.testid = 'hor152-enabled';
        enabledBtn.textContent = 'Excluir';

        const disabledBtn = document.createElement('button');
        disabledBtn.type = 'button';
        disabledBtn.className = 'button danger';
        disabledBtn.dataset.testid = 'hor152-disabled';
        disabledBtn.disabled = true;
        disabledBtn.textContent = 'Excluir desabilitado';

        probe.append(enabledBtn, disabledBtn);
        root.appendChild(probe);
      }, theme);

      const expectedTokens = await page.evaluate(() => {
        const root = document.querySelector('.horus')!;
        const probe = document.getElementById('hor-152-probe')!;
        const resolveColor = (token: string) => {
          const el = document.createElement('span');
          el.style.color = `var(${token})`;
          probe.appendChild(el);
          const color = getComputedStyle(el).color;
          el.remove();
          return color;
        };
        return {
          bad: resolveColor('--Bad'),
          invertFg: resolveColor('--InvertForeground'),
          muted: resolveColor('--Muted'),
          line: resolveColor('--Line'),
          accentInk: resolveColor('--AccentInk'),
          focusWidth: getComputedStyle(root).getPropertyValue('--FocusRingWidth').trim(),
          focusOffset: getComputedStyle(root).getPropertyValue('--FocusRingOffset').trim(),
        };
      });

      const enabled = page.getByTestId('hor152-enabled');
      const disabled = page.getByTestId('hor152-disabled');

      // Estado normal
      const normalStyles = await enabled.evaluate(el => {
        const cs = getComputedStyle(el);
        return { bg: cs.backgroundColor, color: cs.color, border: cs.borderTopColor };
      });
      expect(normalStyles.bg, `[${theme}] normal background`).toBe('rgba(0, 0, 0, 0)');
      expect(normalStyles.color, `[${theme}] normal color`).toBe(expectedTokens.bad);
      expect(normalStyles.border, `[${theme}] normal border`).toBe(expectedTokens.bad);

      // Estado hover (não desabilitado)
      await enabled.hover();
      await page.waitForTimeout(200);
      const hoverStyles = await enabled.evaluate(el => {
        const cs = getComputedStyle(el);
        return { bg: cs.backgroundColor, color: cs.color, border: cs.borderTopColor };
      });
      expect(hoverStyles.bg, `[${theme}] hover background`).toBe(expectedTokens.bad);
      expect(hoverStyles.color, `[${theme}] hover color`).toBe(expectedTokens.invertFg);
      expect(hoverStyles.border, `[${theme}] hover border`).toBe(expectedTokens.bad);

      // Estado focus-visible via teclado
      await page.mouse.move(0, 0);
      await page.evaluate(() => {
        document.getElementById('hor-152-probe')?.querySelector('button')?.focus();
      });
      // Garante disparo de :focus-visible por teclado com Shift+Tab -> Tab
      await page.keyboard.press('Tab');
      await page.keyboard.press('Shift+Tab');
      const focusStyles = await enabled.evaluate(el => {
        const cs = getComputedStyle(el);
        return {
          focused: document.activeElement === el,
          focusVisible: el.matches(':focus-visible'),
          outlineStyle: cs.outlineStyle,
          outlineWidth: cs.outlineWidth,
          outlineColor: cs.outlineColor,
          outlineOffset: cs.outlineOffset,
        };
      });
      expect(focusStyles.focused, `[${theme}] focused`).toBe(true);
      expect(focusStyles.focusVisible, `[${theme}] :focus-visible`).toBe(true);
      expect(focusStyles.outlineStyle, `[${theme}] outlineStyle`).toBe('solid');
      expect(focusStyles.outlineWidth, `[${theme}] outlineWidth`).toBe(expectedTokens.focusWidth);
      expect(focusStyles.outlineColor, `[${theme}] outlineColor`).toBe(expectedTokens.accentInk);
      expect(focusStyles.outlineOffset, `[${theme}] outlineOffset`).toBe(expectedTokens.focusOffset);

      // Estado disabled
      const disabledStyles = await disabled.evaluate(el => {
        const cs = getComputedStyle(el);
        return { bg: cs.backgroundColor, color: cs.color, border: cs.borderTopColor, opacity: cs.opacity };
      });
      expect(disabledStyles.bg, `[${theme}] disabled background`).toBe('rgba(0, 0, 0, 0)');
      expect(disabledStyles.color, `[${theme}] disabled color`).toBe(expectedTokens.muted);
      expect(disabledStyles.border, `[${theme}] disabled border`).toBe(expectedTokens.line);
      expect(Number(disabledStyles.opacity), `[${theme}] disabled opacity`).toBeCloseTo(0.65, 2);
    }
  });
});
