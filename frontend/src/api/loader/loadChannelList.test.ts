import { afterEach, describe, expect, it, vi } from 'vitest';
import loadChannelList from './loadChannelList';

const fetchMock = vi.fn<(url: string) => Promise<Response>>(
  async () => new Response('{"data": []}', { status: 200 }),
);
vi.stubGlobal('fetch', fetchMock);
afterEach(() => fetchMock.mockClear());

const path = () => String(fetchMock.mock.lastCall![0]).replace(/^https?:\/\/[^/]+/, '');

describe('loadChannelList', () => {
  it('keeps the old page and subscribed arguments', async () => {
    await loadChannelList(2, true);
    expect(path()).toBe('/api/channel/?page=2&filter=subscribed');
    await loadChannelList(0, false);
    expect(path()).toBe('/api/channel/?filter=unsubscribed');
    await loadChannelList(0);
    expect(path()).toBe('/api/channel/');
  });

  it('sends search, sort, page size and the has_* flags', async () => {
    await loadChannelList({
      page: 1,
      filter: 'has_rule',
      q: ' train ',
      sort: 'queued',
      order: 'desc',
      pageSize: 100,
    });
    expect(path()).toBe(
      '/api/channel/?page=1&has_rule=true&q=train&sort=queued&order=desc&page_size=100',
    );
  });

  it('sends the auto-download filter as its own flag', async () => {
    await loadChannelList({ page: 0, filter: 'auto_download' });
    expect(path()).toBe('/api/channel/?auto_download=true');
  });
});
