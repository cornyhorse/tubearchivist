import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ThemePicker from './ThemePicker';
import { useUserConfigStore } from '../stores/UserConfigStore';

const update = vi.fn();
vi.mock('../api/actions/updateUserConfig', () => ({
  default: (config: object) => update(config),
}));

beforeEach(() => {
  document.head.innerHTML = '<meta name="color-scheme" content="light dark" />';
  document.documentElement.removeAttribute('data-theme');
  update.mockReset();
  const state = useUserConfigStore.getState();
  state.setUserConfig({ ...state.userConfig, stylesheet: 'device' });
});

afterEach(cleanup);

describe('ThemePicker', () => {
  it('marks the current theme pressed', () => {
    render(<ThemePicker />);
    const pressed = screen
      .getAllByRole('button')
      .filter(b => b.getAttribute('aria-pressed') === 'true');
    expect(pressed.map(b => b.textContent)).toEqual(['Like my device']);
  });

  it('applies and saves a choice, then clears it again', async () => {
    update.mockImplementation(async (config: object) => ({
      status: 200,
      data: { ...useUserConfigStore.getState().userConfig, ...config },
    }));
    render(<ThemePicker />);

    fireEvent.click(screen.getByRole('button', { name: 'High contrast' }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('contrast');
    expect(update).toHaveBeenCalledWith({ stylesheet: 'contrast' });
    expect(
      await screen.findByRole('button', { name: 'High contrast', pressed: true }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Like my device' }));
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(update).toHaveBeenLastCalledWith({ stylesheet: 'device' });
  });
});
