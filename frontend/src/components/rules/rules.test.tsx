import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clientMock, resetClientMock } from '../../test/mockClient';
import { ok, verdict } from '../../test/fixtures';
import RuleEditor from './RuleEditor';
import VerdictBadge from './VerdictBadge';
import NumberingEditor from './NumberingEditor';
import VideoChoices from './VideoChoices';

vi.mock('../../api/ux/client', async importOriginal => ({
  ...(await importOriginal<object>()),
  ...(await import('../../test/mockClient')).clientMock,
}));
const legacyRule = vi.fn();
vi.mock('../../api/actions/updateDownloadRule', () => ({
  default: (...args: unknown[]) => legacyRule(...args),
}));
const legacyNumbering = vi.fn();
vi.mock('../../api/actions/updateEmbyNumbering', async importOriginal => ({
  ...(await importOriginal<object>()),
  default: (...args: unknown[]) => legacyNumbering(...args),
}));

beforeEach(() => {
  resetClientMock();
  legacyRule.mockReset().mockResolvedValue({ status: 200 });
  legacyNumbering.mockReset().mockResolvedValue({ status: 200 });
});
afterEach(cleanup);

const preview = {
  total: 50,
  download: 38,
  skipped: 12,
  reasons: [{ key: 'vid_type', label: 'Shorts', count: 12 }],
  videos: [
    {
      youtube_id: 'a',
      title: 'A long one',
      channel_id: 'UC1',
      vid_type: 'videos',
      duration_seconds: 4000,
      published: '2026-01-01',
      verdict: verdict(),
    },
    {
      youtube_id: 'b',
      title: 'A short',
      channel_id: 'UC1',
      vid_type: 'shorts',
      duration_seconds: 30,
      published: '2026-01-01',
      verdict: verdict({ allowed: false, reason: 'a Short' }),
    },
  ],
};

describe('RuleEditor', () => {
  it('applies presets, previews, and saves through the rules API', async () => {
    clientMock.previewRule.mockResolvedValue(ok(preview));
    clientMock.saveRule.mockResolvedValue(ok({}));
    const onSaved = vi.fn();
    render(
      <RuleEditor scope="channel" targetId="UC1" rule={null} onSaved={onSaved} previewDelay={0} />,
    );

    expect(screen.getByText('Matches every video.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'No Shorts' }));
    expect(screen.getByRole('button', { name: 'No Shorts' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(screen.getByText('Downloads only: No Shorts.')).toBeTruthy();

    await waitFor(() => expect(screen.getByText(/38 download, 12 skipped/)).toBeTruthy());
    expect(screen.getByText('skip: a Short')).toBeTruthy();
    expect(clientMock.previewRule).toHaveBeenLastCalledWith(
      expect.objectContaining({ scope: 'channel', target_id: 'UC1', limit: 50 }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save rule' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(clientMock.saveRule.mock.calls[0][2].vid_types).toEqual(['videos', 'streams']);
  });

  it('asks for a value for presets that need one', async () => {
    const onChange = vi.fn();
    render(
      <RuleEditor scope="default" rule={null} mode="draft" onChange={onChange} previewDelay={0} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Title contains…' }));
    fireEvent.submit(screen.getByRole('textbox', { name: 'Title contains' }).closest('form')!);
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Title contains' }), {
      target: { value: 'train' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ title_include: 'train' }));
    fireEvent.click(screen.getByRole('button', { name: 'Only after date…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save rule' })).toBeNull();
    await waitFor(() => expect(screen.getByText(/needs a newer server/)).toBeTruthy());
  });

  it('edits every field', () => {
    const onChange = vi.fn();
    render(<RuleEditor scope="default" rule={null} mode="draft" onChange={onChange} />);
    fireEvent.click(screen.getByRole('switch'));
    expect(screen.getByText('Switched off: every video is downloaded.')).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Shorts' }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ vid_types: ['videos', 'streams'] }),
    );
    fireEvent.click(screen.getByRole('checkbox', { name: 'Shorts' }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ vid_types: [] }));
    fireEvent.change(screen.getByLabelText('Longer than (minutes)'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('Shorter than (minutes)'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Title doesn’t contain (regex)'), {
      target: { value: 'live' },
    });
    fireEvent.change(screen.getByLabelText('Uploaded on or after'), {
      target: { value: '2026-01-01' },
    });
    fireEvent.change(screen.getByLabelText('Videos that don’t match'), {
      target: { value: 'skip' },
    });
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        enabled: false,
        min_duration: 120,
        max_duration: null,
        title_exclude: 'live',
        published_after: '2026-01-01',
        action: 'skip',
      }),
    );
  });

  it('falls back to the old rule API and removes rules', async () => {
    clientMock.listRulePresets.mockResolvedValue(
      ok([
        {
          key: 'x',
          label: 'Server preset',
          description: '',
          patch: { min_duration: 60 },
          needs_value: null,
          value_field: null,
        },
      ]),
    );
    const onSaved = vi.fn();
    render(
      <RuleEditor
        scope="channel"
        targetId="UC1"
        rule={{
          enabled: true,
          min_duration: 60,
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
    await waitFor(() => expect(screen.getByRole('button', { name: 'Server preset' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Save rule' }));
    await waitFor(() => expect(legacyRule).toHaveBeenCalledWith('UC1', expect.anything()));
    fireEvent.click(screen.getByRole('button', { name: 'Remove, use the default rule' }));
    await waitFor(() => expect(legacyRule).toHaveBeenLastCalledWith('UC1', null));
    expect(onSaved).toHaveBeenCalledTimes(2);
  });

  it('reports save errors', async () => {
    clientMock.saveRule.mockResolvedValue({ status: 400, error: { error: 'bad regex' } });
    clientMock.deleteRule.mockResolvedValue({ status: 500 });
    render(
      <RuleEditor
        scope="default"
        rule={{
          enabled: true,
          min_duration: null,
          max_duration: null,
          vid_types: [],
          title_include: '(',
          title_exclude: '',
          published_after: null,
          action: 'ignore',
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save rule' }));
    await waitFor(() => expect(screen.getByText('Not saved: bad regex')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Remove the default rule' }));
    await waitFor(() => expect(screen.getByText('Not removed')).toBeTruthy());
  });
});

describe('VerdictBadge', () => {
  it('is plain text for viewers and per-video choices', () => {
    const { rerender } = render(<VerdictBadge verdict={verdict()} />);
    expect(screen.getByText('allowed').tagName).toBe('SPAN');
    rerender(
      <VerdictBadge
        canEdit
        verdict={verdict({
          override: 'download',
          rule: { scope: 'video', target_id: 'v', summary: 'Your choice' },
        })}
      />,
    );
    expect(screen.getByText('download anyway').tagName).toBe('SPAN');
    rerender(<VerdictBadge canEdit verdict={verdict({ rule: null })} />);
    expect(screen.getByText('allowed').getAttribute('title')).toBe('No rule applies.');
  });

  it('opens the rule that decided', async () => {
    clientMock.getRule.mockResolvedValue(
      ok({
        enabled: true,
        min_duration: 600,
        title_include: '',
        title_exclude: '',
        action: 'ignore',
        summary: '>10 min',
      }),
    );
    clientMock.saveRule.mockResolvedValue(ok({}));
    const onChanged = vi.fn();
    render(
      <VerdictBadge
        canEdit
        onChanged={onChanged}
        verdict={verdict({
          allowed: false,
          reason: 'shorter than 10 min',
          rule: { scope: 'channel', target_id: 'UC1', summary: '>10 min' },
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'skipped: shorter than 10 min' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Rule: >10 min' })).toBeTruthy());
    expect(clientMock.getRule).toHaveBeenCalledWith('channel', 'UC1');
    fireEvent.click(screen.getByRole('button', { name: 'Save rule' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('opens an empty editor for the default rule on an older server', async () => {
    render(
      <VerdictBadge
        canEdit
        verdict={verdict({ rule: { scope: 'default', target_id: '', summary: 'x' } })}
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Default rule' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('NumberingEditor', () => {
  const sample = [
    {
      youtube_id: 'a',
      title: 'A',
      published: '',
      season: 2025,
      episode: 14,
      name: 'S2025E14 - A',
      manual: false,
      old_season: 2025,
      old_episode: 6200,
      old_name: 'S2025E6200 - A',
    },
    {
      youtube_id: 'b',
      title: 'B',
      published: '',
      season: 2025,
      episode: 3,
      name: 'S2025E03 - B',
      manual: true,
      old_season: 2025,
      old_episode: 3,
      old_name: 'S2025E03 - B',
    },
  ];

  it('warns how many episodes renumber, then saves', async () => {
    clientMock.previewScheme.mockResolvedValue(
      ok({ scheme: 'year_frozen', episodes: 120, renumbers: 114, sample }),
    );
    clientMock.setScheme.mockResolvedValue(
      ok({ scheme: 'year_frozen', renumbered: 114, job_id: 1 }),
    );
    const onSaved = vi.fn();
    render(
      <NumberingEditor
        kind="channel"
        id="UC1"
        value="date"
        options={['date', 'year_frozen']}
        onSaved={onSaved}
      />,
    );
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'year_frozen' } });
    await waitFor(() => expect(screen.getByText(/renumbers 114 of 120 episodes/)).toBeTruthy());
    expect(screen.getByText('S2025E6200 - A')).toBeTruthy();
    expect(screen.getByText('set by hand')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Renumber 114 episodes' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(clientMock.setScheme).toHaveBeenCalledWith('channel', 'UC1', 'year_frozen');
  });

  it('says when nothing moves, and falls back on older servers', async () => {
    clientMock.previewScheme.mockResolvedValue(
      ok({ scheme: 'date', episodes: 3, renumbers: 0, sample }),
    );
    const onSaved = vi.fn();
    render(
      <NumberingEditor
        kind="playlist"
        id="PL1"
        value="date"
        options={['date', 'playlist_position']}
        onSaved={onSaved}
      />,
    );
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'playlist_position' } });
    await waitFor(() => expect(screen.getByText('No episodes change number.')).toBeTruthy());
    clientMock.previewScheme.mockRejectedValue(new Error('offline'));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'date' } });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'playlist_position' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(legacyNumbering).toHaveBeenCalledWith('playlist', 'PL1', 'playlist_position');
  });
});

describe('VideoChoices', () => {
  const row = {
    series_kind: 'channel' as const,
    series_id: 'UC1',
    series_title: 'Trains',
    scheme: 'date' as const,
    season: 2025,
    episode: 6200,
    name: 'S2025E6200 - A',
    manual: false,
  };

  it('is hidden on older servers', async () => {
    const { container } = render(<VideoChoices youtubeId="v1" />);
    await waitFor(() => expect(clientMock.getEffectiveRule).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });

  it('overrides the rules and sets episode numbers by hand', async () => {
    clientMock.getEffectiveRule.mockResolvedValue(
      ok({
        source: { scope: 'channel', target_id: 'UC1', summary: 'No Shorts' },
        rule: null,
        override: null,
      }),
    );
    clientMock.getVideoNumbering.mockResolvedValue(ok({ data: [row] }));
    clientMock.setVideoOverride.mockResolvedValue(ok({}));
    clientMock.setVideoNumbering.mockResolvedValueOnce({ status: 409 }).mockResolvedValue(ok(row));
    render(<VideoChoices youtubeId="v1" />);
    await waitFor(() =>
      expect(screen.getByText('Decided by the channel rule: No Shorts.')).toBeTruthy(),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Always ignore' }));
    await waitFor(() => expect(clientMock.setVideoOverride).toHaveBeenCalledWith('v1', 'ignore'));
    fireEvent.click(screen.getByRole('button', { name: 'Download anyway' }));
    await waitFor(() => expect(clientMock.setVideoOverride).toHaveBeenCalledWith('v1', 'download'));

    fireEvent.click(screen.getByRole('button', { name: 'Set number…' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Episode' }), {
      target: { value: '7' },
    });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Season' }), {
      target: { value: '1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(screen.getByText('Another episode already has that number.')).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('spinbutton')).toBeNull());
    expect(clientMock.setVideoNumbering).toHaveBeenLastCalledWith('v1', {
      series_kind: 'channel',
      series_id: 'UC1',
      season: 1,
      episode: 7,
    });
  });

  it('shows an override and a manual number, and undoes both', async () => {
    clientMock.getEffectiveRule.mockResolvedValue(
      ok({ source: null, rule: null, override: 'ignore' }),
    );
    clientMock.getVideoNumbering.mockResolvedValue(ok({ data: [{ ...row, manual: true }] }));
    clientMock.clearVideoOverride.mockResolvedValue({ status: 204 });
    clientMock.clearVideoNumbering.mockResolvedValue({ status: 204 });
    clientMock.setVideoNumbering.mockRejectedValue({ message: 'boom' });
    render(<VideoChoices youtubeId="v1" />);
    await waitFor(() =>
      expect(screen.getByText('You chose to always ignore this video.')).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Let the rules decide' }));
    await waitFor(() => expect(clientMock.clearVideoOverride).toHaveBeenCalledWith('v1'));
    fireEvent.click(screen.getByRole('button', { name: 'Automatic' }));
    await waitFor(() =>
      expect(clientMock.clearVideoNumbering).toHaveBeenCalledWith('v1', 'channel', 'UC1'),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Set number…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByText('boom')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await act(async () => {});
  });
});
