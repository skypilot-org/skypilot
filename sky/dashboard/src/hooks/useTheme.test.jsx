import React from 'react';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThemeProvider, useTheme } from '@/hooks/useTheme';
import { ThemeMenu, ThemeToggle } from '@/components/elements/ThemeToggle';
import { NO_FLASH_SCRIPT, THEME_STORAGE_KEY, resolveTheme } from '@/lib/theme';

let systemDark;
let mediaListeners;

beforeEach(() => {
  systemDark = false;
  mediaListeners = [];
  window.localStorage.clear();
  document.documentElement.className = '';
  delete document.documentElement.dataset.theme;
  window.matchMedia = jest.fn().mockImplementation(() => ({
    get matches() {
      return systemDark;
    },
    addEventListener: (_, cb) => mediaListeners.push(cb),
    removeEventListener: (_, cb) => {
      mediaListeners = mediaListeners.filter((l) => l !== cb);
    },
  }));
});

const renderWithToggle = () =>
  render(
    <ThemeProvider>
      <ThemeToggle showLabels />
    </ThemeProvider>
  );

describe('resolveTheme', () => {
  test('explicit choices ignore the system setting', () => {
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
  });

  test('system follows the system setting', () => {
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
  });
});

describe('ThemeProvider', () => {
  test('defaults to system and follows prefers-color-scheme live', () => {
    systemDark = true;
    renderWithToggle();
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.dataset.theme).toBe('dark');

    systemDark = false;
    act(() => mediaListeners.forEach((cb) => cb({ matches: false })));
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  test('persists the choice and applies it', async () => {
    renderWithToggle();
    await userEvent.click(screen.getByRole('tab', { name: 'Dark' }));
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(screen.getByRole('tab', { name: 'Dark' })).toHaveAttribute(
      'aria-selected',
      'true'
    );
  });

  test('restores a stored choice over the system setting', () => {
    systemDark = true;
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light');
    renderWithToggle();
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(screen.getByRole('tab', { name: 'Light' })).toHaveAttribute(
      'aria-selected',
      'true'
    );
  });

  test('ignores an unknown stored value', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'solarized');
    const Probe = () => <span>{useTheme().theme}</span>;
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>
    );
    expect(screen.getByText('system')).toBeInTheDocument();
  });
});

describe('ThemeMenu', () => {
  const renderMenu = () =>
    render(
      <ThemeProvider>
        <ThemeMenu />
      </ThemeProvider>
    );

  test('opens a menu, applies the pick and closes', async () => {
    renderMenu();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Theme' }));
    expect(
      screen.getByRole('menuitemradio', { name: 'System' })
    ).toHaveAttribute('aria-checked', 'true');
    await userEvent.click(screen.getByRole('menuitemradio', { name: 'Dark' }));
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  test('closes on Escape and on an outside click', async () => {
    renderMenu();
    const open = () =>
      userEvent.click(screen.getByRole('button', { name: 'Theme' }));
    await open();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    await open();
    await userEvent.click(document.body);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});

describe('NO_FLASH_SCRIPT', () => {
  const run = () => new Function(NO_FLASH_SCRIPT)();

  test('sets dark before React runs when the stored choice is dark', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark');
    run();
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  test('follows the system when nothing is stored', () => {
    systemDark = true;
    run();
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  test('a stored light choice beats a dark system', () => {
    systemDark = true;
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light');
    run();
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });
});
