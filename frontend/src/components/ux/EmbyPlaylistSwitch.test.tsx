import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EmbyPlaylistSwitch from './EmbyPlaylistSwitch';

const save = vi.fn();
vi.mock('../../api/actions/updateEmbyPlaylist', () => ({
  default: (...args: unknown[]) => save(...args),
}));

afterEach(() => {
  cleanup();
  save.mockReset();
});

describe('EmbyPlaylistSwitch', () => {
  it('is plain text without edit rights, on when unset', () => {
    render(<EmbyPlaylistSwitch id="PL1" name="Rides" value={undefined} onSaved={() => {}} />);
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.getByText('On').getAttribute('title')).toContain('uploads playlist');
  });

  it('shows Off in read-only mode when switched off', () => {
    render(<EmbyPlaylistSwitch id="PL1" name="Rides" value={false} onSaved={() => {}} />);
    expect(screen.getByText('Off')).toBeTruthy();
  });

  it('is a labelled switch that saves the new value', async () => {
    save.mockResolvedValue({ status: 200 });
    const onSaved = vi.fn();
    render(<EmbyPlaylistSwitch id="PL1" name="Rides" value canEdit onSaved={onSaved} />);
    const toggle = screen.getByRole('switch', { name: 'Emby playlist for Rides' });
    expect((toggle as HTMLInputElement).checked).toBe(true);
    expect(toggle.getAttribute('aria-describedby')).toBeTruthy();

    fireEvent.click(toggle);

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(save).toHaveBeenCalledWith('PL1', false);
  });

  it('is on when unset and switches it on from off', async () => {
    save.mockResolvedValue({ status: 200 });
    render(<EmbyPlaylistSwitch id="PL1" name="Rides" value={false} canEdit onSaved={() => {}} />);
    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() => expect(save).toHaveBeenCalledWith('PL1', true));
  });

  it('reports a failed save', async () => {
    save.mockRejectedValue({ status: 400, message: 'bad' });
    const onSaved = vi.fn();
    render(<EmbyPlaylistSwitch id="PL1" name="Rides" value canEdit onSaved={onSaved} />);

    fireEvent.click(screen.getByRole('switch'));

    expect((await screen.findByRole('alert')).textContent).toContain('Could not save');
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('reports an error status', async () => {
    save.mockResolvedValue({ status: 500 });
    render(<EmbyPlaylistSwitch id="PL1" name="Rides" value canEdit onSaved={() => {}} />);

    fireEvent.click(screen.getByRole('switch'));

    expect(await screen.findByRole('alert')).toBeTruthy();
  });
});
