import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PlaylistTable from './PlaylistTable';
import { PlaylistType } from '../api/loader/loadPlaylistById';

vi.mock('../api/actions/updateAutoDownload', () => ({ default: vi.fn() }));
const saveEmbyPlaylist = vi.fn();
vi.mock('../api/actions/updateEmbyPlaylist', () => ({
  default: (...args: unknown[]) => saveEmbyPlaylist(...args),
}));
const saveEmbyTags = vi.fn();
vi.mock('../api/actions/updateEmbyTags', () => ({
  default: (...args: unknown[]) => saveEmbyTags(...args),
}));

const playlist = (changes: Partial<PlaylistType>): PlaylistType => ({
  playlist_active: true,
  playlist_channel: 'Trains',
  playlist_channel_id: 'UC1',
  playlist_description: null,
  playlist_entries: [],
  playlist_sort_order: 'top',
  playlist_id: 'PL1',
  playlist_last_refresh: '2026-10-09T10:00:00+00:00',
  playlist_name: 'Rides',
  playlist_subscribed: true,
  playlist_thumbnail: '',
  playlist_type: 'regular',
  _index: '',
  _score: 0,
  ...changes,
});

const rows = [
  playlist({ auto_download: true }),
  playlist({ playlist_id: 'PL2', playlist_name: 'Old', playlist_subscribed: false }),
  playlist({ playlist_id: 'TA_custom', playlist_name: 'Mine', playlist_type: 'custom' }),
];

afterEach(cleanup);

const renderTable = (props: Partial<Parameters<typeof PlaylistTable>[0]> = {}) =>
  render(
    <MemoryRouter>
      <PlaylistTable
        playlists={rows}
        isAdmin
        selected={new Set()}
        onSelectedChange={() => {}}
        onToggleSubscribe={() => {}}
        onChanged={() => {}}
        {...props}
      />
    </MemoryRouter>,
  );

describe('PlaylistTable', () => {
  it('shows auto-download, off unless opted in, none for your own playlists', () => {
    renderTable();
    const [, first, second, custom] = screen.getAllByRole('row');
    const on = within(first).getByRole('switch', { name: 'Auto-download for Rides' });
    expect((on as HTMLInputElement).checked).toBe(true);
    const off = within(second).getByRole('switch', { name: 'Auto-download for Old' });
    expect((off as HTMLInputElement).checked).toBe(false);
    expect(within(custom).queryByRole('switch')).toBeNull();
    expect(within(custom).getByText('Your playlist')).toBeTruthy();
    expect(within(custom).queryByRole('button', { name: /Subscribe/ })).toBeNull();
  });

  it('shows who sees each playlist and saves a tag switch', async () => {
    saveEmbyTags.mockResolvedValue({ status: 200 });
    const onChanged = vi.fn();
    renderTable({ embyTags: ['David'], onChanged });
    const [, first, second, custom] = screen.getAllByRole('row');
    expect(screen.getByRole('columnheader', { name: /Visible to/ })).toBeTruthy();
    expect(within(custom).queryByRole('switch', { name: /Visible to/ })).toBeNull();
    const tagged = within(first).getByRole('switch', { name: 'Visible to David: Rides' });
    const untagged = within(second).getByRole('switch', { name: 'Visible to David: Old' });
    expect((tagged as HTMLInputElement).checked).toBe(false);

    fireEvent.click(untagged);

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(saveEmbyTags).toHaveBeenCalledWith('playlist', 'PL2', ['David']);
  });

  it('shows the Emby playlist switch, on unless off, and saves it', async () => {
    saveEmbyPlaylist.mockResolvedValue({ status: 200 });
    const onChanged = vi.fn();
    renderTable({
      onChanged,
      playlists: [
        playlist({}),
        playlist({ playlist_id: 'PL2', playlist_name: 'Old', emby_playlist: false }),
        rows[2],
      ],
    });
    const [, first, second, custom] = screen.getAllByRole('row');
    expect(screen.getByRole('columnheader', { name: /Emby playlist/ })).toBeTruthy();
    expect(within(custom).queryByRole('switch', { name: /Emby playlist/ })).toBeNull();
    const on = within(first).getByRole('switch', { name: 'Emby playlist for Rides' });
    expect((on as HTMLInputElement).checked).toBe(true);
    const off = within(second).getByRole('switch', { name: 'Emby playlist for Old' });
    expect((off as HTMLInputElement).checked).toBe(false);

    fireEvent.click(off);

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(saveEmbyPlaylist).toHaveBeenCalledWith('PL2', true);
  });

  it('toggles subscription per row and selects rows for bulk actions', () => {
    const onToggleSubscribe = vi.fn();
    const onSelectedChange = vi.fn();
    renderTable({ onToggleSubscribe, onSelectedChange });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe from Rides' }));
    expect(onToggleSubscribe).toHaveBeenCalledWith(rows[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Subscribe to Old' }));
    expect(onToggleSubscribe).toHaveBeenLastCalledWith(rows[1]);
    fireEvent.click(screen.getAllByRole('checkbox', { name: /Old/ })[0]);
    expect(onSelectedChange).toHaveBeenCalledWith(new Set(['PL2']));
  });

  it('is read-only for viewers', () => {
    renderTable({ isAdmin: false });
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
    // auto-download (one row on) plus the Emby playlist column (two regular rows, default on)
    expect(screen.getAllByText('On').length).toBe(3);
  });
});
