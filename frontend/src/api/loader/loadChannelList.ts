import { PaginationType } from '../../components/Pagination';
import APIClient from '../../functions/APIClient';
import { ChannelType } from '../../pages/Channels';
import { ConfigType } from '../../pages/Home';

export type ChannelsListResponse = {
  data: ChannelType[];
  paginate: PaginationType;
  config?: ConfigType;
};

export type ChannelListFilter =
  | 'subscribed'
  | 'unsubscribed'
  | 'has_queued'
  | 'has_rule'
  | 'has_errors';

export type ChannelSortKey =
  | 'name'
  | 'subscribed'
  | 'downloaded'
  | 'queued'
  | 'ignored'
  | 'latest_upload'
  | 'last_checked';

export type ChannelListQuery = {
  page: number;
  filter?: ChannelListFilter | null;
  q?: string;
  sort?: ChannelSortKey;
  order?: 'asc' | 'desc';
  pageSize?: number;
};

const loadChannelList = async (
  pageOrQuery: number | ChannelListQuery,
  showSubscribed: boolean | null = null,
) => {
  const query: ChannelListQuery =
    typeof pageOrQuery === 'number'
      ? {
          page: pageOrQuery,
          filter: showSubscribed === null ? null : showSubscribed ? 'subscribed' : 'unsubscribed',
        }
      : pageOrQuery;

  const searchParams = new URLSearchParams();
  if (query.page) searchParams.append('page', query.page.toString());
  if (query.filter) searchParams.append('filter', query.filter);
  if (query.q?.trim()) searchParams.append('q', query.q.trim());
  if (query.sort) searchParams.append('sort', query.sort);
  if (query.order) searchParams.append('order', query.order);
  if (query.pageSize) searchParams.append('page_size', query.pageSize.toString());

  const endpoint = `/api/channel/${searchParams.toString() ? `?${searchParams.toString()}` : ''}`;

  return APIClient<ChannelsListResponse>(endpoint);
};

export default loadChannelList;
