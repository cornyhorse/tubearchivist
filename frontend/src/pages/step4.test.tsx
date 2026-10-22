import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clientMock, resetClientMock } from '../test/mockClient';
import { found, job, ok, previewItem, verdict } from '../test/fixtures';
import FoundVideos from './FoundVideos';
import Queue from './Queue';
import Rules from './Rules';
import AddDialog from '../components/AddDialog';
import QueueIndicator from '../components/QueueIndicator';
import MediaServerSettings from '../components/settings/MediaServerSettings';
import useQueueAvailable, { resetQueueAvailable } from '../functions/useQueueAvailable';
import { useUserAccountStore } from '../stores/UserAccountStore';

vi.mock('../api/ux/client', async importOriginal => ({
  ...(await importOriginal<object>()),
  ...(await import('../test/mockClient')).clientMock,
}));
vi.mock('./Download', () => ({ default: () => <p>old downloads page</p> }));
const task = vi.fn();
vi.mock('../api/actions/updateTaskByName', () => ({ default: (name: string) => task(name) }));
const legacyAdd = vi.fn();
vi.mock('../api/actions/updateDownloadQueue', () => ({
  default: (args: unknown) => legacyAdd(args),
}));
const channelList = vi.fn();
vi.mock('../api/loader/loadChannelList', () => ({ default: (args: unknown) => channelList(args) }));
const defaultRule = vi.fn();
vi.mock('../api/loader/loadDownloadRule', () => ({ default: () => defaultRule() }));
vi.mock('../api/actions/updateUserConfig', () => ({ default: vi.fn().mockResolvedValue({}) }));

import { useUserConfigStore } from '../stores/UserConfigStore';

const admin = () => {
  const config = useUserConfigStore.getState();
  config.setUserConfig({ ...config.userConfig, view_style_downloads: 'list' });
  useUserAccountStore.getState().setUserAccount({ is_staff: true, is_superuser: true } as never);
};

const wrap = (node: React.ReactNode) => render(<MemoryRouter>{node}</MemoryRouter>);

window.scrollTo = vi.fn() as never;

beforeEach(() => {
  resetClientMock();
  resetQueueAvailable();
  task.mockReset();
  legacyAdd.mockReset().mockResolvedValue({});
  channelList.mockReset().mockResolvedValue({ data: { data: [] } });
  defaultRule.mockReset().mockResolvedValue({ data: null });
  admin();
});
afterEach(cleanup);

const foundPage = (
  data = [
    found('a'),
    found('b', { state: 'queued', job: { id: 9, status: 'queued', position: 3, progress: null } }),
  ],
) =>
  ok({
    data,
    paginate: { current_page: 0, total_hits: 250, last_page: 3 },
    counts: { waiting: 250, ignored: 12, failed: 1, downloaded: 4 },
    facets: { channels: [{ channel_id: 'UC1', channel_name: 'Trains', count: 250 }] },
  });

describe('FoundVideos', () => {
  it('falls back to the old page on an older server', async () => {
    wrap(<FoundVideos />);
    await waitFor(() => expect(screen.getByText('old downloads page')).toBeTruthy());
  });

  it('shows tabs with counts, rows with verdicts and status', async () => {
    clientMock.listFound.mockResolvedValue(foundPage());
    wrap(<FoundVideos />);
    await waitFor(() => expect(screen.getByRole('tab', { name: /Waiting\s*250/ })).toBeTruthy());
    expect(screen.getByRole('tab', { name: /Ignored\s*12/ }).getAttribute('aria-selected')).toBe(
      'false',
    );
    expect(screen.getByText('Waiting for you')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Queued, #3' })).toBeTruthy();
    expect(screen.getAllByText('allowed')).toHaveLength(2);
    expect(screen.getByRole('combobox', { name: 'Channel' })).toBeTruthy();
    expect(clientMock.listFound).toHaveBeenCalledWith(
      expect.objectContaining({ tab: 'waiting', sort: '-found', page: 1, page_size: 100 }),
    );
  });

  it('runs row and bulk actions, including all matching', async () => {
    clientMock.listFound.mockResolvedValue(foundPage());
    clientMock.foundAction.mockResolvedValue(ok({ matched: 2, changed: 2, job_ids: [] }));
    wrap(<FoundVideos />);
    await waitFor(() => expect(screen.getByText('Video a')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Ignore: Video a' }));
    await waitFor(() =>
      expect(clientMock.foundAction).toHaveBeenCalledWith({ action: 'ignore', ids: ['a'] }),
    );

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all on this page' }));
    fireEvent.click(screen.getByRole('button', { name: 'Download 2' }));
    await waitFor(() =>
      expect(clientMock.foundAction).toHaveBeenLastCalledWith({
        action: 'download_now',
        ids: ['a', 'b'],
      }),
    );
    await waitFor(() => expect(screen.getByText('2 of 2 videos updated.')).toBeTruthy());

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all on this page' }));
    fireEvent.click(screen.getByRole('button', { name: 'Select all 250 matching' }));
    expect(screen.getByText('250 videos selected')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Forget 250' }));
    await waitFor(() =>
      expect(clientMock.foundAction).toHaveBeenLastCalledWith({
        action: 'forget',
        filter: { tab: 'waiting' },
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
  });

  it('filters, switches tabs, sorts and pages', async () => {
    clientMock.listFound.mockResolvedValue(foundPage());
    wrap(<FoundVideos />);
    await waitFor(() => expect(screen.getByText('Video a')).toBeTruthy());
    fireEvent.click(screen.getByRole('tab', { name: /Ignored/ }));
    await waitFor(() =>
      expect(clientMock.listFound).toHaveBeenLastCalledWith(
        expect.objectContaining({ tab: 'ignored' }),
      ),
    );
    expect(screen.getByRole('button', { name: 'Download anyway: Video a' })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('tab', { name: /Ignored/ }), { key: 'ArrowRight' });
    await waitFor(() =>
      expect(clientMock.listFound).toHaveBeenLastCalledWith(
        expect.objectContaining({ tab: 'failed' }),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Shorts' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Channel' }), {
      target: { value: 'UC1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'More filters' }));
    fireEvent.change(screen.getByLabelText(/Longer than/), { target: { value: '60' } });
    fireEvent.click(screen.getByLabelText(/Skipped by a rule/));
    fireEvent.click(screen.getByLabelText(/Has an error/));
    fireEvent.change(screen.getByLabelText(/Shorter than/), { target: { value: '90' } });
    fireEvent.change(screen.getByLabelText(/Published from/), { target: { value: '2026-01-01' } });
    fireEvent.change(screen.getByLabelText(/^to/), { target: { value: '2026-02-01' } });
    await waitFor(() =>
      expect(clientMock.listFound).toHaveBeenLastCalledWith(
        expect.objectContaining({
          tab: 'failed',
          vid_type: 'shorts',
          channel: 'UC1',
          min_duration: 3600,
          max_duration: 5400,
          skipped_by_rule: true,
          error: true,
          published_from: '2026-01-01',
          published_to: '2026-02-01',
        }),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    fireEvent.click(
      within(screen.getByRole('columnheader', { name: /^Length/ })).getByRole('button'),
    );
    await waitFor(() =>
      expect(clientMock.listFound).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: 'duration' }),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() =>
      expect(clientMock.listFound).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cards' }));
    await waitFor(() => expect(screen.queryByRole('table')).toBeNull());
    expect(screen.getAllByRole('article')).toHaveLength(2);
  });

  it('shows failures and downloads, and the toolbar actions', async () => {
    clientMock.listFound.mockResolvedValue(
      foundPage([
        found('f', { state: 'failed', error: '403 Forbidden' }),
        found('d', {
          state: 'downloaded',
          downloaded_at: new Date().toISOString(),
          channel_indexed: false,
        }),
        found('g', {
          state: 'downloading',
          job: { id: 2, status: 'running', position: 0, progress: 0.4 },
          verdict: verdict({ allowed: false, reason: 'a Short' }),
        }),
        found('h', { state: 'ignored' }),
      ]),
    );
    clientMock.createJob.mockResolvedValueOnce(ok({ job: job(1), merged: true }));
    clientMock.foundAction.mockResolvedValue(ok({ matched: 5, changed: 5, job_ids: [] }));
    wrap(<FoundVideos />);
    await waitFor(() => expect(screen.getByText(/403 Forbidden/)).toBeTruthy());
    expect(screen.getByText('Downloaded just now')).toBeTruthy();
    expect(screen.getByText('Downloading 40%')).toBeTruthy();
    expect(screen.getAllByText('Ignored').length).toBe(2);

    fireEvent.click(screen.getByRole('button', { name: /Check channels for new videos/ }));
    await waitFor(() => expect(screen.getByText(/already waiting; it moved up/)).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /Check channels for new videos/ }));
    await waitFor(() => expect(task).toHaveBeenCalledWith('update_subscribed'));

    fireEvent.click(screen.getByRole('button', { name: /Download all allowed/ }));
    await waitFor(() =>
      expect(clientMock.foundAction).toHaveBeenLastCalledWith({
        action: 'download',
        filter: { tab: 'waiting' },
      }),
    );
    await waitFor(() => expect(screen.getByText('5 downloads queued.')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: /^Add…/ }));
    expect(screen.getByRole('dialog')).toBeTruthy();
  });
});

describe('AddDialog', () => {
  it('previews, adjusts the rule, and adds', async () => {
    clientMock.previewAdd.mockResolvedValue(
      ok({ items: [previewItem()], errors: [{ url: 'nope', error: 'not a YouTube link' }] }),
    );
    clientMock.getRule.mockResolvedValue(
      ok({
        enabled: true,
        vid_types: ['videos'],
        title_include: '',
        title_exclude: '',
        action: 'ignore',
        summary: 'x',
      }),
    );
    clientMock.addUrls.mockResolvedValue(
      ok({ jobs: [job(1)], errors: [{ url: 'bad', error: 'gone' }] }),
    );
    const onAdded = vi.fn();
    const onClose = vi.fn();
    wrap(<AddDialog onClose={onClose} onAdded={onAdded} />);

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'https://youtube.com/@trains' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() =>
      expect(
        screen.getByText('142 videos: 118 will download, 24 skipped (Shorts 20, >1 h 4).'),
      ).toBeTruthy(),
    );
    expect(screen.getByText('nope: not a YouTube link')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Change the rule for this add…' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'No Shorts' })).toBeTruthy());
    expect(clientMock.getRule).toHaveBeenCalledWith('default', '');
    fireEvent.click(screen.getByRole('button', { name: 'Skip over 1 hour' }));
    await waitFor(() => expect(clientMock.previewAdd).toHaveBeenCalledTimes(2), { timeout: 2000 });
    expect(clientMock.previewAdd.mock.lastCall![0].rule.max_duration).toBe(3600);

    fireEvent.change(screen.getByRole('combobox', { name: /Emby numbering/ }), {
      target: { value: 'year_frozen' },
    });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'How many of the newest videos' }), {
      target: { value: '5' },
    });
    fireEvent.focus(screen.getByRole('spinbutton', { name: 'How many of the newest videos' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Added' })).toBeTruthy());
    expect(clientMock.addUrls).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: 'newest_n',
        newest_n: 5,
        numbering: 'year_frozen',
        save_rule: false,
        priority: 'high',
      }),
    );
    expect(clientMock.addUrls.mock.lastCall![0].auto_download).toBeUndefined();
    expect(screen.getByText('bad: gone')).toBeTruthy();
    expect(onAdded).toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole('button', { name: 'Close' }).at(-1)!);
    expect(onClose).toHaveBeenCalled();
  });

  it('marks a changed paste as stale and picks "once" for a single video', async () => {
    clientMock.previewAdd.mockResolvedValue(
      ok({
        items: [
          previewItem({
            kind: 'video',
            subscribed: false,
            verdict: { total: 1, download: 1, skipped: 0, already_have: 0, reasons: [] },
          }),
        ],
        errors: [],
      }),
    );
    wrap(<AddDialog onClose={() => {}} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'v1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() => expect(screen.getByText('This video will download.')).toBeTruthy());
    expect(
      (screen.getByRole('radio', { name: /Download it once/ }) as HTMLInputElement).checked,
    ).toBe(true);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'v2' } });
    expect(screen.getByText('The links changed. Preview again to update.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Add' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('reports preview and add errors', async () => {
    clientMock.previewAdd.mockRejectedValueOnce({ message: 'YouTube said no' });
    wrap(<AddDialog onClose={() => {}} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() => expect(screen.getByText('YouTube said no')).toBeTruthy());
    clientMock.previewAdd.mockResolvedValue(
      ok({ items: [previewItem({ subscribed: true })], errors: [] }),
    );
    clientMock.addUrls.mockRejectedValue({});
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() => expect(screen.getByText('Subscribed')).toBeTruthy());
    fireEvent.click(screen.getByRole('radio', { name: /Subscribe and keep checking/ }));
    // auto-download is opt in: unticked unless you tick it
    const auto = screen.getByRole('checkbox', { name: /Auto-download new videos/ });
    expect((auto as HTMLInputElement).checked).toBe(false);
    fireEvent.click(auto);
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(screen.getByText('Could not add those links.')).toBeTruthy());
    expect(clientMock.addUrls.mock.lastCall![0]).toMatchObject({
      mode: 'subscribe',
      auto_download: true,
    });
    clientMock.addUrls.mockRejectedValue({ status: 400, message: 'x' });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(screen.getByText(/None of those links could be read/)).toBeTruthy());
  });

  it('uses the old add form on an older server', async () => {
    const onClose = vi.fn();
    wrap(<AddDialog onClose={onClose} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'abc' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() => expect(screen.getByText(/can’t preview links yet/)).toBeTruthy());
    fireEvent.click(screen.getByRole('checkbox', { name: /Add quickly/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /Download again/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /Start downloading now/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(legacyAdd).toHaveBeenCalledWith({
      youtubeIdStrings: 'abc',
      autostart: false,
      flat: true,
      force: true,
    });
  });
});

describe('Queue', () => {
  const lanes = [
    {
      name: 'scan',
      label: 'Checking YouTube',
      description: '',
      paused: false,
      concurrency: 2,
      min_interval_seconds: 0,
      running: 1,
      queued: 2,
    },
    {
      name: 'download',
      label: 'Downloading',
      description: '',
      paused: true,
      concurrency: 1,
      min_interval_seconds: 0,
      running: 0,
      queued: 1,
    },
  ];

  const setup = () => {
    clientMock.listJobs.mockImplementation(async ({ status }: { status: string }) => {
      if (status === 'running') {
        return ok({
          data: [
            job(1, {
              status: 'running',
              actions: ['stop'],
              started_at: new Date().toISOString(),
              created_by: 'user',
              progress: {
                fraction: 0.5,
                message: 'Downloading video',
                speed: '4MiB/s',
                eta_seconds: 90,
                current: null,
                total: null,
              },
            }),
            job(2, {
              status: 'running',
              type_label: 'Check channel',
              target: { kind: 'channel', id: 'UC1', title: 'Trains', url: null },
              actions: ['stop'],
              children: 3,
              progress: {
                fraction: null,
                message: 'Reading',
                speed: null,
                eta_seconds: null,
                current: null,
                total: null,
              },
            }),
          ],
        });
      }
      if (status === 'history') {
        return ok({
          data: [
            job(5, {
              status: 'failed',
              error: '429',
              actions: ['retry'],
              duration_seconds: 12,
              finished_at: new Date().toISOString(),
            }),
            job(6, { status: 'done', result: { found: 3, allowed: 2, ignored: 1 }, actions: [] }),
            job(7, { status: 'done', result: { status_code: 204, paths: 2 }, actions: [] }),
            job(8, { status: 'cancelled', result: { size: '1 MB' }, actions: ['retry'] }),
          ],
        });
      }
      return ok({
        data: [
          job(3, {
            position: 1,
            priority: 100,
            merged: 2,
            actions: ['move_to_top', 'move_up', 'move_down', 'pause', 'cancel'] as never,
          }),
          job(10, {
            label: 'Download video: Riding',
            target: {
              kind: 'video',
              id: 'r',
              title: 'Riding',
              url: 'https://youtube.com/watch?v=r',
            },
          }),
          job(4, {
            status: 'paused',
            position: null,
            actions: ['resume', 'cancel'],
            target: { kind: 'playlist', id: 'PL1', title: 'Best of', url: null },
          }),
        ],
      });
    });
    clientMock.listLanes.mockResolvedValue(ok(lanes));
    clientMock.listScheduled.mockResolvedValue(
      ok([
        {
          name: 'update_subscribed',
          label: 'Check channels for new videos',
          job_type: 'check_subscriptions',
          lane: 'scan',
          schedule: '0 */6 *',
          schedule_human: 'Every 6 hours',
          enabled: true,
          next_run: new Date(Date.now() + 3600000).toISOString(),
          last_run: new Date().toISOString(),
          last_job: null,
        },
        {
          name: 'download_pending',
          label: 'Download queued videos',
          job_type: 'download_video',
          lane: 'download',
          schedule: null,
          schedule_human: 'Off',
          enabled: false,
          next_run: null,
          last_run: null,
          last_job: null,
        },
      ]),
    );
    for (const fn of [
      clientMock.jobAction,
      clientMock.updateLane,
      clientMock.updateScheduled,
      clientMock.runScheduled,
    ]) {
      fn.mockResolvedValue(ok({}));
    }
  };

  it('says when the server has no queue', async () => {
    wrap(<Queue />);
    await waitFor(() => expect(screen.getByText(/doesn’t have the work queue yet/)).toBeTruthy());
  });

  it('shows running, up next, scheduled and history, with controls', async () => {
    setup();
    wrap(<Queue />);
    await waitFor(() =>
      expect(screen.getByRole('progressbar', { name: 'Download “Video 1”' })).toBeTruthy(),
    );
    expect(
      screen.getByRole('progressbar', { name: 'Download “Video 1”' }).getAttribute('aria-valuenow'),
    ).toBe('50');
    expect(screen.getByText(/Downloading video · 50% · 4MiB\/s · 1 min 30 s left/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Check channel: Trains' })).toBeTruthy();
    expect(
      (await screen.findByRole('link', { name: 'Download video: Riding' })).getAttribute('href'),
    ).toBe('https://youtube.com/watch?v=r');
    await waitFor(() => expect(screen.getByText(/high priority · asked for 3 times/)).toBeTruthy());
    expect(screen.getByText('Every 6 hours')).toBeTruthy();
    expect(screen.getByText('Failed: 429')).toBeTruthy();
    expect(screen.getByText('Found 3: 2 allowed, 1 ignored')).toBeTruthy();
    expect(screen.getByText('Media server answered 204 for 2 paths')).toBeTruthy();
    expect(screen.getByText('Cancelled. size: 1 MB')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Stop: Download “Video 1”' }));
    await waitFor(() => expect(clientMock.jobAction).toHaveBeenCalledWith(1, 'stop'));
    fireEvent.click(screen.getByRole('button', { name: 'Up: Download “Video 3”' }));
    await waitFor(() => expect(clientMock.jobAction).toHaveBeenLastCalledWith(3, 'move_up'));
    fireEvent.click(screen.getByRole('button', { name: 'Resume: Download: Best of' }));
    await waitFor(() => expect(clientMock.jobAction).toHaveBeenLastCalledWith(4, 'resume'));
    fireEvent.click(screen.getAllByRole('button', { name: 'Retry: Download “Video 5”' })[0]);
    await waitFor(() => expect(clientMock.jobAction).toHaveBeenLastCalledWith(5, 'retry'));

    fireEvent.click(screen.getByRole('button', { name: 'Pause Checking YouTube' }));
    await waitFor(() =>
      expect(clientMock.updateLane).toHaveBeenCalledWith('scan', { paused: true }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Resume Downloading' }));
    await waitFor(() =>
      expect(clientMock.updateLane).toHaveBeenLastCalledWith('download', { paused: false }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Run now: Download queued videos' }));
    await waitFor(() => expect(clientMock.runScheduled).toHaveBeenCalledWith('download_pending'));
    fireEvent.click(screen.getByRole('button', { name: 'Pause: Check channels for new videos' }));
    await waitFor(() =>
      expect(clientMock.updateScheduled).toHaveBeenCalledWith('update_subscribed', {
        enabled: false,
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Downloading' }));
    await waitFor(() =>
      expect(clientMock.listJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ lane: 'download' }),
      ),
    );
  });

  it('polls running jobs and folds long queues', async () => {
    vi.useFakeTimers();
    setup();
    const many = Array.from({ length: 60 }, (_, index) =>
      job(100 + index, { position: index + 1 }),
    );
    clientMock.listJobs.mockImplementation(async ({ status }: { status: string }) =>
      ok({ data: status === 'queued,paused' ? many : [] }),
    );
    wrap(<Queue />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(screen.getByText('Nothing is running.')).toBeTruthy();
    const calls = clientMock.listJobs.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10500);
    });
    expect(clientMock.listJobs.mock.calls.length).toBeGreaterThan(calls + 4);
    fireEvent.click(screen.getByRole('button', { name: 'Show all 60' }));
    expect(screen.getByRole('button', { name: 'Show fewer' })).toBeTruthy();
    vi.useRealTimers();
  });
});

describe('QueueIndicator and useQueueAvailable', () => {
  it('hides on an older server', async () => {
    const { container } = wrap(<QueueIndicator />);
    await waitFor(() => expect(clientMock.getQueueSummary).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });

  it('shows running, queued and failed counts, and refreshes', async () => {
    vi.useFakeTimers();
    clientMock.getQueueSummary
      .mockResolvedValueOnce(ok({ running: 2, queued: 14, paused: 0, failed_24h: 1, lanes: [] }))
      .mockResolvedValueOnce(ok({ running: 0, queued: 0, paused: 0, failed_24h: 0, lanes: [] }))
      .mockRejectedValue(new Error('offline'));
    wrap(<QueueIndicator interval={1000} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(screen.getByRole('link').textContent).toContain('2 running · 14 queued · 1 failed');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole('link').textContent).toContain('Idle');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole('link').textContent).toContain('Idle');
    vi.useRealTimers();
  });

  it('caches whether the queue exists', async () => {
    clientMock.getQueueSummary.mockResolvedValue(
      ok({ running: 0, queued: 0, paused: 0, failed_24h: 0, lanes: [] }),
    );
    const Probe = () => <p>{useQueueAvailable() ? 'yes' : 'no'}</p>;
    render(<Probe />);
    await waitFor(() => expect(screen.getByText('yes')).toBeTruthy());
    cleanup();
    render(<Probe />);
    await waitFor(() => expect(screen.getByText('yes')).toBeTruthy());
    expect(clientMock.getQueueSummary).toHaveBeenCalledTimes(1);
    resetQueueAvailable();
    clientMock.getQueueSummary.mockRejectedValue(new Error('x'));
    cleanup();
    render(<Probe />);
    await act(async () => {});
    expect(screen.getByText('no')).toBeTruthy();
  });
});

describe('Rules', () => {
  const rule = {
    enabled: true,
    min_duration: 600,
    max_duration: null,
    vid_types: [],
    title_include: '',
    title_exclude: '',
    published_after: null,
    action: 'ignore',
    summary: '>10 min',
  };

  it('lists rules and per-video choices, and edits them', async () => {
    clientMock.listRules.mockResolvedValue(
      ok({
        data: [
          {
            scope: 'default',
            target_id: '',
            target_title: 'Default rule',
            target_subscribed: true,
            rule,
          },
          {
            scope: 'channel',
            target_id: 'UC1',
            target_title: 'Trains',
            target_subscribed: false,
            rule: { ...rule, enabled: false, action: 'skip' },
          },
          {
            scope: 'playlist',
            target_id: 'PL1',
            target_title: 'Best of',
            target_subscribed: true,
            rule: { ...rule, summary: '' },
          },
        ],
      }),
    );
    clientMock.listVideoOverrides.mockResolvedValue(
      ok({
        data: [
          {
            youtube_id: 'v1',
            override: 'ignore',
            note: '',
            title: 'Bad video',
            channel_id: 'UC1',
            created_at: new Date().toISOString(),
          },
          {
            youtube_id: 'v2',
            override: 'download',
            note: '',
            title: null,
            channel_id: null,
            created_at: new Date().toISOString(),
          },
        ],
      }),
    );
    clientMock.clearVideoOverride.mockResolvedValue({ status: 204 });
    channelList.mockResolvedValue({
      data: {
        data: [
          { channel_id: 'UC2', channel_name: 'Coasters' },
          { channel_id: 'UC1', channel_name: 'Trains' },
        ],
      },
    });
    wrap(<Rules />);
    await waitFor(() =>
      expect(screen.getByText('Every channel and playlist without its own rule')).toBeTruthy(),
    );
    expect(screen.getByRole('link', { name: 'Trains' }).getAttribute('href')).toBe(
      '/channel/UC1/about/',
    );
    expect(screen.getByText('(not subscribed)')).toBeTruthy();
    expect(screen.getByText('Rule off')).toBeTruthy();
    expect(screen.getByText('Left out')).toBeTruthy();
    expect(screen.getAllByText('> 10 min').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Set a default rule…' })).toBeNull();

    expect(screen.getByText('Always ignore')).toBeTruthy();
    expect(screen.getByText('Always download')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Forget the choice for Bad video' }));
    await waitFor(() => expect(clientMock.clearVideoOverride).toHaveBeenCalledWith('v1'));

    fireEvent.click(screen.getByRole('button', { name: 'Edit the rule for Trains' }));
    expect(screen.getByRole('dialog', { name: 'Rule: Trains' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    await waitFor(() => expect(screen.getByRole('option', { name: 'Coasters' })).toBeTruthy());
    expect(screen.queryByRole('option', { name: 'Trains' })).toBeNull();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'UC2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add rule…' }));
    expect(screen.getByRole('dialog', { name: 'Rule: Coasters' })).toBeTruthy();
    clientMock.saveRule.mockResolvedValue(ok({}));
    fireEvent.click(screen.getByRole('button', { name: 'Save rule' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(clientMock.saveRule).toHaveBeenCalledWith('channel', 'UC2', expect.anything());
  });

  it('builds the list from the old API on an older server', async () => {
    defaultRule.mockResolvedValue({ data: { ...rule, vid_types: ['videos'] } });
    channelList.mockResolvedValue({
      data: {
        data: [
          {
            channel_id: 'UC1',
            channel_name: 'Trains',
            channel_subscribed: true,
            download_rule: rule,
          },
          { channel_id: 'UC2', channel_name: 'None', download_rule: null },
        ],
      },
    });
    wrap(<Rules />);
    await waitFor(() => expect(screen.getByText('Only Videos · > 10 min')).toBeTruthy());
    expect(screen.getAllByRole('row')).toHaveLength(3);
    expect(screen.getByText('None yet.')).toBeTruthy();
  });

  it('offers a default rule when there is none', async () => {
    channelList.mockResolvedValue({ data: {} });
    wrap(<Rules />);
    await waitFor(() =>
      expect(screen.getByText('No rules: every video found is downloaded.')).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Set a default rule…' }));
    expect(screen.getByRole('dialog', { name: 'Rule: Default rule' })).toBeTruthy();
  });
});

describe('MediaServerSettings', () => {
  const server = {
    type: 'emby',
    url: 'http://emby:8096',
    api_key_set: true,
    api_key_source: 'db',
    library_id: 'lib',
    path_from: '/youtube/.emby',
    path_to: '',
    notify_enabled: true,
    debounce_seconds: 30,
    last_notify: {
      at: new Date().toISOString(),
      status_code: 204,
      paths: 3,
      job_id: 1,
      error: null,
    },
  };

  it('explains when the server is too old', async () => {
    render(
      <MemoryRouter>
        <MediaServerSettings />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/comes with a newer server/)).toBeTruthy());
  });

  it('saves the Emby tags to offer as a clean list', async () => {
    clientMock.getMediaServer.mockResolvedValue(ok({ ...server, known_tags: ['David'] }));
    clientMock.listLibraries.mockResolvedValue(ok({ data: [] }));
    clientMock.saveMediaServer.mockResolvedValue(ok({ ...server, known_tags: ['David', 'Ann'] }));
    wrap(<MediaServerSettings />);
    const field = (await screen.findByLabelText('Emby tags to offer')) as HTMLInputElement;
    expect(field.value).toBe('David');

    fireEvent.change(field, { target: { value: ' David, Ann ,, David' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.getByText('Saved')).toBeTruthy());
    expect(clientMock.saveMediaServer).toHaveBeenCalledWith(
      expect.objectContaining({ known_tags: ['David', 'Ann'] }),
    );
    expect((screen.getByLabelText('Emby tags to offer') as HTMLInputElement).value).toBe(
      'David, Ann',
    );
  });

  it('saves the Emby playlist sync switch and default users, only for Emby', async () => {
    clientMock.getMediaServer.mockResolvedValue(
      ok({ ...server, playlists_enabled: false, playlist_default_users: ['Matt'] }),
    );
    clientMock.listLibraries.mockResolvedValue(ok({ data: [] }));
    clientMock.saveMediaServer.mockResolvedValue(
      ok({ ...server, playlists_enabled: true, playlist_default_users: ['Matt', 'Ann'] }),
    );
    wrap(<MediaServerSettings />);
    const sync = (await screen.findByRole('switch', {
      name: 'Sync Emby playlists',
    })) as HTMLInputElement;
    expect(sync.checked).toBe(false);
    const users = screen.getByLabelText('Emby users who get every playlist') as HTMLInputElement;
    expect(users.value).toBe('Matt');
    expect(screen.getByText(/one copy per user/)).toBeTruthy();

    fireEvent.click(sync);
    fireEvent.change(users, { target: { value: ' Matt, Ann ,, Matt' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.getByText('Saved')).toBeTruthy());
    expect(clientMock.saveMediaServer).toHaveBeenCalledWith(
      expect.objectContaining({ playlists_enabled: true, playlist_default_users: ['Matt', 'Ann'] }),
    );
    expect(
      (screen.getByLabelText('Emby users who get every playlist') as HTMLInputElement).value,
    ).toBe('Matt, Ann');

    fireEvent.change(screen.getByLabelText('Media server'), { target: { value: 'jellyfin' } });
    expect(screen.queryByRole('switch', { name: 'Sync Emby playlists' })).toBeNull();
    expect(screen.queryByLabelText('Emby users who get every playlist')).toBeNull();
  });

  it('tests, picks a library and saves without echoing the key', async () => {
    clientMock.getMediaServer.mockResolvedValue(ok(server));
    clientMock.listLibraries.mockResolvedValue(
      ok({
        data: [
          { id: 'lib', name: 'YouTube', collection_type: 'tvshows', paths: [], contains_ta: true },
        ],
      }),
    );
    clientMock.testMediaServer
      .mockResolvedValueOnce(
        ok({ ok: true, server_name: 'emby', product: 'Emby Server', version: '4.10', error: null }),
      )
      .mockResolvedValueOnce(
        ok({
          ok: false,
          server_name: null,
          product: null,
          version: null,
          error: 'connection refused',
        }),
      );
    clientMock.saveMediaServer.mockResolvedValue(ok({ ...server, url: 'http://new:8096' }));
    clientMock.notifyMediaServer.mockResolvedValue(ok({}));
    clientMock.refreshMediaServer.mockResolvedValue(ok({}));
    wrap(<MediaServerSettings />);
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'YouTube (has TA’s folder)' })).toBeTruthy(),
    );
    expect(screen.getByText(/answered 204 for 3 files/)).toBeTruthy();
    expect(screen.getByText('A key is saved. Type a new one to replace it.')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    await waitFor(() =>
      expect(screen.getByText(/Connected to emby \(Emby Server 4.10\)/)).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    await waitFor(() => expect(screen.getByText('✗ connection refused')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Address'), { target: { value: 'http://new:8096' } });
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'secret' } });
    fireEvent.change(screen.getByLabelText('Folder as the server sees it'), {
      target: { value: '/media/.emby' },
    });
    fireEvent.click(screen.getByRole('switch', { name: 'On' }));
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByText('Saved')).toBeTruthy());
    expect(clientMock.saveMediaServer).toHaveBeenCalledWith(
      expect.objectContaining({
        url: 'http://new:8096',
        api_key: 'secret',
        path_to: '/media/.emby',
        notify_enabled: false,
        debounce_seconds: 10,
        library_id: 'lib',
      }),
    );
    expect((screen.getByLabelText('API key') as HTMLInputElement).value).toBe('');

    fireEvent.click(screen.getByRole('button', { name: 'Remove the key' }));
    expect(screen.getByText('Will be removed when you save.')).toBeTruthy();
    clientMock.saveMediaServer.mockRejectedValueOnce({ message: 'bad url' });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByText('Not saved: bad url')).toBeTruthy());
    expect(clientMock.saveMediaServer).toHaveBeenLastCalledWith(
      expect.objectContaining({ api_key: null }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Keep the key' }));

    fireEvent.click(screen.getByRole('button', { name: 'Tell it about every file' }));
    await waitFor(() => expect(clientMock.notifyMediaServer).toHaveBeenCalledWith({ all: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Full library refresh' }));
    await waitFor(() => expect(clientMock.refreshMediaServer).toHaveBeenCalledWith('lib'));
  });

  it('locks a key set by the environment and explains library errors', async () => {
    clientMock.getMediaServer.mockResolvedValue(
      ok({
        ...server,
        type: 'jellyfin',
        api_key_source: 'env',
        last_notify: { ...server.last_notify, error: 'timeout' },
      }),
    );
    clientMock.listLibraries.mockResolvedValueOnce({ status: 409 });
    wrap(<MediaServerSettings />);
    await waitFor(() => expect(screen.getByText('Save the connection first.')).toBeTruthy());
    expect((screen.getByLabelText('API key') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText('Set by the environment.')).toBeTruthy();
    expect(screen.getByText(/failed, timeout/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Media server'), { target: { value: '' } });
    expect(screen.queryByLabelText('Address')).toBeNull();
  });

  it('shows server errors from the library list', async () => {
    clientMock.getMediaServer.mockResolvedValue(ok({ ...server, last_notify: null }));
    clientMock.listLibraries.mockResolvedValueOnce({
      status: 502,
      error: { error: 'Emby is down' },
    });
    wrap(<MediaServerSettings />);
    await waitFor(() => expect(screen.getByText('Emby is down')).toBeTruthy());
    expect(screen.getByText('No updates sent yet.')).toBeTruthy();
    cleanup();
    clientMock.listLibraries.mockRejectedValueOnce({ message: 'offline' });
    wrap(<MediaServerSettings />);
    await waitFor(() => expect(screen.getByText('offline')).toBeTruthy());
  });
});
