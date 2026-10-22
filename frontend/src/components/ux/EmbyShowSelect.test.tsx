import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EmbyShowSelect from './EmbyShowSelect';

const save = vi.fn();
vi.mock('../../api/actions/updateEmbyShow', () => ({
  default: (...args: unknown[]) => save(...args),
}));

afterEach(() => {
  cleanup();
  save.mockReset();
});

describe('EmbyShowSelect', () => {
  it('is plain text without edit rights, defaulting to subscribed only', () => {
    render(<EmbyShowSelect id="c1" name="Trains" value={undefined} onSaved={() => {}} />);
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.getByText('Subscribed only (default)').getAttribute('title')).toContain(
      'Other videos',
    );
  });

  it('shows the stored value in read-only mode', () => {
    render(<EmbyShowSelect id="c1" name="Trains" value="never" onSaved={() => {}} />);
    expect(screen.getByText('Never (in Other videos)')).toBeTruthy();
  });

  it('saves the new choice', async () => {
    save.mockResolvedValue({ status: 200 });
    const onSaved = vi.fn();
    render(<EmbyShowSelect id="c1" name="Trains" value="auto" canEdit onSaved={onSaved} />);
    const select = screen.getByRole('combobox', { name: 'Emby show for Trains' });
    expect((select as HTMLSelectElement).value).toBe('auto');
    expect(select.getAttribute('aria-describedby')).toBeTruthy();

    fireEvent.change(select, { target: { value: 'always' } });

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(save).toHaveBeenCalledWith('c1', 'always');
  });

  it('reports a rejected save', async () => {
    save.mockRejectedValue({ status: 400 });
    const onSaved = vi.fn();
    render(<EmbyShowSelect id="c1" name="Trains" value="auto" canEdit onSaved={onSaved} />);

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'never' } });

    expect((await screen.findByRole('alert')).textContent).toContain('Could not save');
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('reports an error status', async () => {
    save.mockResolvedValue({ status: 500 });
    render(<EmbyShowSelect id="c1" name="Trains" value="auto" canEdit onSaved={() => {}} />);

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'never' } });

    expect(await screen.findByRole('alert')).toBeTruthy();
  });
});
