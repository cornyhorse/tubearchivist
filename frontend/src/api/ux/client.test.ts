import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as client from './client';

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(
    async () => new Response(JSON.stringify({ ok: true }), { status: 200 }),
  );
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

const lastCall = () => {
  const [url, init] = fetchMock.mock.lastCall!;
  return {
    path: String(url).replace(/^https?:\/\/[^/]+/, ''),
    method: init.method,
    body: init.body ? JSON.parse(init.body) : undefined,
  };
};

describe('ux client', () => {
  it('builds query strings without empty values', async () => {
    await client.listFound({ tab: 'waiting', q: '', channel: null, page: 2, error: false });
    expect(lastCall().path).toBe('/api/found/?tab=waiting&page=2&error=false');
    await client.getQueueSummary();
    expect(lastCall().path).toBe('/api/queue/summary/');
  });

  it('sends every call to the contract routes', async () => {
    const rule = client.toRuleBody({
      enabled: true,
      title_include: '',
      title_exclude: '',
      action: 'ignore',
    })!;
    const calls: [() => Promise<unknown>, string, string, unknown?][] = [
      [() => client.listJobs({ status: 'running' }), 'GET', '/api/queue/jobs/?status=running'],
      [
        () => client.createJob({ type: 'check_channel', target_id: 'UC1' }),
        'POST',
        '/api/queue/jobs/',
        { priority: 'high', type: 'check_channel', target_id: 'UC1' },
      ],
      [() => client.jobAction(5, 'stop'), 'POST', '/api/queue/jobs/5/action/', { action: 'stop' }],
      [() => client.listLanes(), 'GET', '/api/queue/lanes/'],
      [
        () => client.updateLane('scan', { paused: true }),
        'PATCH',
        '/api/queue/lanes/scan/',
        { paused: true },
      ],
      [() => client.listScheduled(), 'GET', '/api/queue/scheduled/'],
      [
        () => client.updateScheduled('x', { enabled: false }),
        'PATCH',
        '/api/queue/scheduled/x/',
        { enabled: false },
      ],
      [() => client.runScheduled('x'), 'POST', '/api/queue/scheduled/x/run/'],
      [
        () => client.foundAction({ action: 'ignore', ids: ['a'] }),
        'POST',
        '/api/found/actions/',
        { action: 'ignore', ids: ['a'] },
      ],
      [() => client.previewAdd({ urls: 'u' }), 'POST', '/api/add/preview/', { urls: 'u' }],
      [
        () => client.addUrls({ urls: 'u', mode: 'once' } as never),
        'POST',
        '/api/add/',
        { urls: 'u', mode: 'once' },
      ],
      [() => client.listRules(), 'GET', '/api/rules/'],
      [() => client.listRulePresets(), 'GET', '/api/rules/presets/'],
      [() => client.getRule('default'), 'GET', '/api/rules/default/'],
      [() => client.getRule('channel', 'UC1'), 'GET', '/api/rules/channel/UC1/'],
      [() => client.saveRule('playlist', 'PL1', rule), 'PUT', '/api/rules/playlist/PL1/', rule],
      [() => client.deleteRule('channel', 'UC1'), 'DELETE', '/api/rules/channel/UC1/'],
      [
        () => client.previewRule({ scope: 'default' }),
        'POST',
        '/api/rules/preview/',
        { scope: 'default' },
      ],
      [() => client.getEffectiveRule({ video: 'v1' }), 'GET', '/api/rules/effective/?video=v1'],
      [() => client.listVideoOverrides(), 'GET', '/api/rules/videos/'],
      [
        () => client.setVideoOverride('v1', 'ignore'),
        'PUT',
        '/api/rules/videos/v1/',
        { override: 'ignore', note: '' },
      ],
      [() => client.clearVideoOverride('v1'), 'DELETE', '/api/rules/videos/v1/'],
      [() => client.listSchemes(), 'GET', '/api/numbering/schemes/'],
      [
        () => client.previewScheme('channel', 'UC1', 'date'),
        'POST',
        '/api/numbering/channel/UC1/preview/',
        { scheme: 'date' },
      ],
      [
        () => client.setScheme('playlist', 'PL1', 'date'),
        'PUT',
        '/api/numbering/playlist/PL1/',
        { scheme: 'date' },
      ],
      [() => client.getVideoNumbering('v1'), 'GET', '/api/numbering/video/v1/'],
      [
        () =>
          client.setVideoNumbering('v1', {
            series_kind: 'channel',
            series_id: 'UC1',
            season: 1,
            episode: 2,
          }),
        'PUT',
        '/api/numbering/video/v1/',
        { series_kind: 'channel', series_id: 'UC1', season: 1, episode: 2 },
      ],
      [
        () => client.clearVideoNumbering('v1', 'channel', 'UC1'),
        'DELETE',
        '/api/numbering/video/v1/?series_kind=channel&series_id=UC1',
      ],
      [() => client.getMediaServer(), 'GET', '/api/mediaserver/'],
      [() => client.saveMediaServer({ url: 'x' }), 'PUT', '/api/mediaserver/', { url: 'x' }],
      [() => client.testMediaServer({ url: 'x' }), 'POST', '/api/mediaserver/test/', { url: 'x' }],
      [() => client.listLibraries(), 'GET', '/api/mediaserver/libraries/'],
      [
        () => client.notifyMediaServer({ all: true }),
        'POST',
        '/api/mediaserver/notify/',
        { all: true },
      ],
      [() => client.refreshMediaServer(), 'POST', '/api/mediaserver/refresh/', {}],
      [
        () => client.refreshMediaServer('lib'),
        'POST',
        '/api/mediaserver/refresh/',
        { library_id: 'lib' },
      ],
    ];
    for (const [call, method, path, body] of calls) {
      await call();
      expect(lastCall()).toEqual({ method, path, body });
    }
  });

  it('knows when an endpoint is not built', () => {
    expect(client.notBuilt({ status: 501 })).toBe(true);
    expect(client.notBuilt({ status: 404 })).toBe(true);
    expect(client.notBuilt({ status: 200 })).toBe(false);
  });

  it('fills a rule', () => {
    expect(client.toRuleBody(null)).toBeNull();
    expect(client.toRuleBody({ min_duration: 60 } as never)).toEqual({
      enabled: true,
      min_duration: 60,
      max_duration: null,
      vid_types: [],
      title_include: '',
      title_exclude: '',
      published_after: null,
      action: 'ignore',
    });
  });
});
