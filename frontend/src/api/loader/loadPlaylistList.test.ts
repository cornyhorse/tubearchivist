import { afterEach, describe, expect, it, vi } from 'vitest';
import loadPlaylistList from './loadPlaylistList';

const fetchMock = vi.fn<(url: string) => Promise<Response>>(
  async () => new Response('{"data": []}', { status: 200 }),
);
vi.stubGlobal('fetch', fetchMock);
afterEach(() => fetchMock.mockClear());

const path = () => String(fetchMock.mock.lastCall![0]).replace(/^https?:\/\/[^/]+/, '');

describe('loadPlaylistList', () => {
  it('filters by auto-download only when asked', async () => {
    await loadPlaylistList({ page: 2, subscribed: true, autoDownload: false });
    expect(path()).toBe('/api/playlist/?page=2&subscribed=true&auto_download=false');
    await loadPlaylistList({ autoDownload: null });
    expect(path()).toBe('/api/playlist/');
  });

  it('filters by Emby tag only when asked', async () => {
    await loadPlaylistList({ embyTag: 'David' });
    expect(path()).toBe('/api/playlist/?emby_tag=David');
    await loadPlaylistList({ embyTag: '' });
    expect(path()).toBe('/api/playlist/');
  });
});
