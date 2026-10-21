import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AutoDownloadSwitch from './AutoDownloadSwitch';

const save = vi.fn();
vi.mock('../../api/actions/updateAutoDownload', () => ({
  default: (...args: unknown[]) => save(...args),
}));

afterEach(() => {
  cleanup();
  save.mockReset();
});

describe('AutoDownloadSwitch', () => {
  it('is plain text without edit rights, off when unset', () => {
    render(
      <AutoDownloadSwitch
        kind="channel"
        id="c1"
        name="Trains"
        value={undefined}
        onSaved={() => {}}
      />,
    );
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.getByText('Off').getAttribute('title')).toContain('Found videos');
  });

  it('is a labelled switch that saves the new value', async () => {
    save.mockResolvedValue({ status: 200 });
    const onSaved = vi.fn();
    render(
      <AutoDownloadSwitch
        kind="playlist"
        id="PL1"
        name="Coasters"
        value={false}
        canEdit
        onSaved={onSaved}
      />,
    );
    const toggle = screen.getByRole('switch', { name: 'Auto-download for Coasters' });
    expect((toggle as HTMLInputElement).checked).toBe(false);
    expect(toggle.getAttribute('aria-describedby')).toBeTruthy();

    fireEvent.click(toggle);

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(save).toHaveBeenCalledWith('playlist', 'PL1', true);
  });

  it('switches off and reports a failed save', async () => {
    save.mockRejectedValue({ status: 400, message: 'bad' });
    const onSaved = vi.fn();
    render(
      <AutoDownloadSwitch kind="channel" id="c1" name="Trains" value canEdit onSaved={onSaved} />,
    );
    const toggle = screen.getByRole('switch');
    expect((toggle as HTMLInputElement).checked).toBe(true);

    fireEvent.click(toggle);

    expect((await screen.findByRole('alert')).textContent).toContain('Could not save');
    expect(save).toHaveBeenCalledWith('channel', 'c1', false);
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('reports an error status', async () => {
    save.mockResolvedValue({ status: 500 });
    render(
      <AutoDownloadSwitch
        kind="channel"
        id="c1"
        name="Trains"
        value={false}
        canEdit
        onSaved={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole('switch'));

    expect(await screen.findByRole('alert')).toBeTruthy();
  });
});
