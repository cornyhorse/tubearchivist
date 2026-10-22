import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

const saveEmbyTags = vi.fn();
vi.mock('../api/actions/updateEmbyTags', () => ({
  default: (...args: unknown[]) => saveEmbyTags(...args),
}));
const saveAutoDownload = vi.fn();
vi.mock('../api/actions/updateAutoDownload', () => ({
  default: (...args: unknown[]) => saveAutoDownload(...args),
}));
import ChannelTable from './ChannelTable';
import { ChannelType } from '../pages/Channels';

const channel = (changes: Partial<ChannelType>): ChannelType => ({
  channel_active: true,
  channel_banner_url: '',
  channel_description: '',
  channel_id: 'UC1',
  channel_last_refresh: '2026-10-09T10:00:00+00:00',
  channel_name: 'Trains',
  channel_subs: 10,
  channel_subscribed: true,
  channel_thumb_url: '',
  channel_tvart_url: '',
  ...changes,
});

const rows = [
  channel({
    counts: { downloaded: 1200, queued: 3, ignored: 0, errors: 0 },
    latest_upload: '2026-10-08',
    emby_numbering: 'year_frozen',
    auto_download: true,
  }),
  channel({ channel_id: 'UC2', channel_name: 'Coasters', channel_subscribed: false }),
];

afterEach(cleanup);

const renderTable = (props: Partial<Parameters<typeof ChannelTable>[0]> = {}) =>
  render(
    <MemoryRouter>
      <ChannelTable
        channels={rows}
        defaultRule={null}
        isAdmin
        sort={{ key: 'name', order: 'asc' }}
        onSortChange={() => {}}
        selected={new Set()}
        onSelectedChange={() => {}}
        onToggleSubscribe={() => {}}
        onChanged={() => {}}
        {...props}
      />
    </MemoryRouter>,
  );

describe('ChannelTable', () => {
  it('shows counts, dashes for missing ones, and badges', () => {
    renderTable();
    const [, first, second] = screen.getAllByRole('row');
    expect(within(first).getByRole('link', { name: 'Trains' })).toBeTruthy();
    expect(within(first).getByText((1200).toLocaleString())).toBeTruthy();
    expect(within(first).getByRole('link', { name: '3' }).getAttribute('href')).toContain(
      'ignored=false',
    );
    expect(
      within(first).getByRole('button', { name: /numbering for Trains: By year/ }),
    ).toBeTruthy();
    expect(within(second).getAllByText('—').length).toBeGreaterThanOrEqual(4);
    expect(within(second).getByText('No')).toBeTruthy();
  });

  it('toggles subscription per row and sorts by a column', () => {
    const onToggleSubscribe = vi.fn();
    const onSortChange = vi.fn();
    renderTable({ onToggleSubscribe, onSortChange });
    fireEvent.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(onToggleSubscribe).toHaveBeenCalledWith(rows[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Subscribe' }));
    expect(onToggleSubscribe).toHaveBeenLastCalledWith(rows[1]);
    fireEvent.click(screen.getByRole('button', { name: /Queued/ }));
    expect(onSortChange).toHaveBeenCalledWith({ key: 'queued', order: 'asc' });
  });

  it('shows auto-download per row, off unless opted in, and saves a switch', async () => {
    saveAutoDownload.mockResolvedValue({ status: 200 });
    const onChanged = vi.fn();
    renderTable({ onChanged });
    expect(screen.getByRole('columnheader', { name: /Auto-download/ })).toBeTruthy();
    const trains = screen.getByRole('switch', { name: 'Auto-download for Trains' });
    const coasters = screen.getByRole('switch', { name: 'Auto-download for Coasters' });
    expect((trains as HTMLInputElement).checked).toBe(true);
    expect((coasters as HTMLInputElement).checked).toBe(false);

    fireEvent.click(coasters);

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(saveAutoDownload).toHaveBeenCalledWith('channel', 'UC2', true);
  });

  it('shows who sees each channel and saves a tag switch', async () => {
    saveEmbyTags.mockResolvedValue({ status: 200 });
    const onChanged = vi.fn();
    render(
      <MemoryRouter>
        <ChannelTable
          channels={[{ ...rows[0], emby_tags: ['David'] }, rows[1]]}
          defaultRule={null}
          isAdmin
          embyTags={['David']}
          sort={{ key: 'name', order: 'asc' }}
          onSortChange={() => {}}
          selected={new Set()}
          onSelectedChange={() => {}}
          onToggleSubscribe={() => {}}
          onChanged={onChanged}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('columnheader', { name: /Visible to/ })).toBeTruthy();
    const trains = screen.getByRole('switch', { name: 'Visible to David: Trains' });
    const coasters = screen.getByRole('switch', { name: 'Visible to David: Coasters' });
    expect((trains as HTMLInputElement).checked).toBe(true);
    expect((coasters as HTMLInputElement).checked).toBe(false);

    fireEvent.click(coasters);

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(saveEmbyTags).toHaveBeenCalledWith('channel', 'UC2', ['David']);
  });

  it('is read-only for viewers', () => {
    renderTable({ isAdmin: false });
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
    expect(screen.queryByRole('button', { name: /numbering/ })).toBeNull();
  });
});
