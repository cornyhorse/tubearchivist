import { VideoType } from '../../pages/Home';
import loadVideoListByFilter, { VideoTypes, WatchTypes } from './loadVideoListByPage';
import APIClient from '../../functions/APIClient';
import { VideoListByFilterResponseType } from './loadVideoListByPage';

type RecentQuery = {
  limit: number;
  q?: string;
  watch?: WatchTypes;
  type?: VideoTypes | null;
};

const MAX_PAGES = 10;

/**
 * The newest downloads, up to `limit`. Asks for one big page; a server
 * without page_size answers with the user's page size, so keep paging.
 */
const loadRecentVideos = async ({ limit, q, watch, type }: RecentQuery): Promise<VideoType[]> => {
  const params = new URLSearchParams({ sort: 'downloaded', order: 'desc' });
  params.append('page_size', String(limit));
  if (q?.trim()) params.append('q', q.trim());
  if (watch) params.append('watch', watch);
  if (type) params.append('type', type);

  const first = await APIClient<VideoListByFilterResponseType>(`/api/video/?${params}`);
  const videos = [...(first.data?.data ?? [])];
  const lastPage = first.data?.paginate?.last_page ?? 0;

  for (let page = 2; videos.length < limit && page <= Math.min(lastPage, MAX_PAGES); page++) {
    const next = await loadVideoListByFilter({
      page,
      sort: 'downloaded',
      order: 'desc',
      watch: watch ?? null,
      type: type ?? undefined,
    });
    const items = next.data?.data ?? [];
    if (items.length === 0) break;
    videos.push(...items);
  }
  return videos.slice(0, limit);
};

export default loadRecentVideos;
