import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { VideoType } from '../../pages/Home';
import ShelvesHome from './ShelvesHome';
import TimelineHome from './TimelineHome';
import WhatsNewHome from './WhatsNewHome';
import useHomeLayout, { HOME_LAYOUT_CACHE_KEY } from '../../functions/useHomeLayout';
import { useUserConfigStore } from '../../stores/UserConfigStore';

const watched = vi.fn();
vi.mock('../../api/actions/updateWatchedState', () => ({
  default: (args: unknown) => watched(args),
}));
const saveConfig = vi.fn();
vi.mock('../../api/actions/updateUserConfig', () => ({
  default: (args: unknown) => saveConfig(args),
}));

const now = new Date(2026, 9, 10, 14, 0);
const daysAgo = (days: number) => now.getTime() / 1000 - days * 86400;

const video = (
  id: string,
  channel: string,
  published: string,
  downloaded: number,
  extra: Partial<VideoType> = {},
) =>
  ({
    youtube_id: id,
    title: `Title ${id}`,
    published,
    date_downloaded: downloaded,
    vid_type: 'videos',
    vid_thumb_url: '/t.jpg',
    player: { watched: false, duration: 60, duration_str: '1m 00s', progress: 0, position: 0 },
    channel: { channel_id: channel, channel_name: `Channel ${channel}`, channel_thumb_url: '' },
    ...extra,
  }) as unknown as VideoType;

const videos = [
  video('a1', 'A', '2026-10-01', daysAgo(0)),
  video('b1', 'B', '2026-10-09', daysAgo(1), {
    player: { watched: true, duration: 3700, duration_str: '1h 1m', progress: 0, position: 0 },
  }),
  video('c1', 'C', '2026-01-01', daysAgo(40), { vid_type: 'shorts', title: 'Short thing' }),
];

const wrap = (node: React.ReactNode) => render(<MemoryRouter>{node}</MemoryRouter>);

beforeEach(() => {
  watched.mockReset().mockResolvedValue({ status: 200 });
  saveConfig.mockReset();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe('ShelvesHome', () => {
  it('shows a shelf per channel with new counts', () => {
    wrap(<ShelvesHome videos={videos} now={now.getTime() / 1000} />);
    const headings = screen.getAllByRole('heading', { level: 2 });
    expect(headings.map(heading => heading.textContent)).toEqual([
      'Channel B',
      'Channel A',
      'Channel C',
    ]);
    expect(screen.getByText('1 new')).toBeTruthy();
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
  });

  it('says when empty', () => {
    wrap(<ShelvesHome videos={[]} />);
    expect(screen.getByText('No videos downloaded yet.')).toBeTruthy();
  });
});

describe('TimelineHome', () => {
  it('groups by day and toggles watched', async () => {
    wrap(<TimelineHome videos={videos} now={now} />);
    const today = screen.getByRole('region', { name: /Today/ });
    expect(within(today).getByRole('link', { name: 'Title a1' })).toBeTruthy();
    expect(screen.getByRole('region', { name: /Yesterday/ })).toBeTruthy();

    const toggle = within(today).getByRole('button', { name: 'Watched: Title a1' });
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle.getAttribute('aria-pressed')).toBe('true'));
    expect(watched).toHaveBeenCalledWith({ id: 'a1', is_watched: true });
  });

  it('says when empty', () => {
    wrap(<TimelineHome videos={[]} />);
    expect(screen.getByText('No videos downloaded yet.')).toBeTruthy();
  });
});

describe('WhatsNewHome', () => {
  const titles = () =>
    screen
      .getAllByRole('row')
      .slice(1)
      .map(row => within(row).queryAllByRole('link')[0]?.textContent ?? row.textContent);

  it('filters by watched state and type, and sorts', () => {
    wrap(<WhatsNewHome videos={videos} />);
    expect(screen.getByText('3 of the newest 3 downloads')).toBeTruthy();
    expect(titles()).toEqual(['Title a1', 'Title b1', 'Short thing']);

    fireEvent.click(screen.getByRole('button', { name: 'Not watched' }));
    expect(titles()).toEqual(['Title a1', 'Short thing']);
    fireEvent.click(screen.getByRole('button', { name: 'Shorts' }));
    expect(titles()).toEqual(['Short thing']);
    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    fireEvent.click(screen.getByRole('button', { name: 'Any type' }));

    const sortBy = (name: string) =>
      fireEvent.click(
        within(screen.getByRole('columnheader', { name: new RegExp(`^${name}`) })).getByRole(
          'button',
        ),
      );
    sortBy('Length');
    expect(titles()).toEqual(['Title a1', 'Short thing', 'Title b1']);
    sortBy('Length');
    expect(titles()[0]).toBe('Title b1');
    for (const name of ['Title', 'Channel', 'Published', 'Watched', 'Downloaded']) {
      sortBy(name);
    }
    expect(titles()).toHaveLength(3);
  });

  it('searches', async () => {
    wrap(<WhatsNewHome videos={videos} />);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'channel b' } });
    await waitFor(() => expect(titles()).toEqual(['Title b1']));
  });
});

describe('useHomeLayout', () => {
  const Probe = () => {
    const [layout, setLayout] = useHomeLayout();
    return (
      <button type="button" onClick={() => setLayout('timeline')}>
        {layout}
      </button>
    );
  };

  beforeEach(() => {
    const state = useUserConfigStore.getState();
    state.setUserConfig({ ...state.userConfig, home_layout: undefined });
  });

  it('falls back to this browser, then saves to the server', async () => {
    localStorage.setItem(HOME_LAYOUT_CACHE_KEY, 'shelves');
    saveConfig.mockResolvedValue({ data: { ...useUserConfigStore.getState().userConfig } });
    render(<Probe />);
    expect(screen.getByRole('button').textContent).toBe('shelves');
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => expect(saveConfig).toHaveBeenCalledWith({ home_layout: 'timeline' }));
    expect(screen.getByRole('button').textContent).toBe('timeline');
    expect(localStorage.getItem(HOME_LAYOUT_CACHE_KEY)).toBe('timeline');
  });

  it('keeps the choice when the server rejects it', async () => {
    saveConfig.mockRejectedValue({ status: 400 });
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<Probe />);
    expect(screen.getByRole('button').textContent).toBe('default');
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(screen.getByRole('button').textContent).toBe('timeline');
    error.mockRestore();
  });
});
