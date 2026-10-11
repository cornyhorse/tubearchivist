import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NumberingBadge, RuleBadge } from './Badges';

const saveRule = vi.fn();
const saveNumbering = vi.fn();
vi.mock('../../api/actions/updateDownloadRule', () => ({
  default: (...args: unknown[]) => saveRule(...args),
}));
vi.mock('../../api/actions/updateEmbyNumbering', async importOriginal => ({
  ...(await importOriginal<object>()),
  default: (...args: unknown[]) => saveNumbering(...args),
}));

afterEach(() => {
  cleanup();
  saveRule.mockReset();
  saveNumbering.mockReset();
});

describe('RuleBadge', () => {
  it('is plain text without edit rights', () => {
    render(<RuleBadge channelId="c1" channelName="Trains" rule={null} onSaved={() => {}} />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('Default').getAttribute('title')).toContain('default rule');
  });

  it('opens the rule editor and saves to the channel', async () => {
    saveRule.mockResolvedValue({ status: 200 });
    const onSaved = vi.fn();
    render(
      <RuleBadge
        channelId="c1"
        channelName="Trains"
        canEdit
        rule={{
          enabled: true,
          min_duration: 600,
          max_duration: null,
          vid_types: [],
          title_include: '',
          title_exclude: '',
          published_after: null,
          action: 'ignore',
        }}
        onSaved={onSaved}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Download rule for Trains: > 10 min/ }));
    expect(screen.getByRole('dialog', { name: 'Download rule: Trains' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save rule' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(saveRule.mock.calls[0][0]).toBe('c1');
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('NumberingBadge', () => {
  it('is plain text without edit rights', () => {
    render(
      <NumberingBadge
        kind="channel"
        id="c1"
        name="Trains"
        value={undefined}
        options={['date', 'year_frozen']}
        onSaved={() => {}}
      />,
    );
    expect(screen.getByText('By date')).toBeTruthy();
  });

  it('changes the scheme from a dialog', async () => {
    saveNumbering.mockResolvedValue({ status: 200 });
    const onSaved = vi.fn();
    render(
      <NumberingBadge
        kind="playlist"
        id="p1"
        name="Best of"
        value="playlist_position"
        options={['playlist_position', 'date']}
        canEdit
        onSaved={onSaved}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: /Emby numbering for Best of: Playlist order/ }),
    );
    fireEvent.change(screen.getByRole('combobox', { name: 'Emby episode numbering' }), {
      target: { value: 'date' },
    });
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(saveNumbering).toHaveBeenCalledWith('playlist', 'p1', 'date');
  });
});
