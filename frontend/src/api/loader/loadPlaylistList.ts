import { PaginationType } from '../../components/Pagination';
import APIClient from '../../functions/APIClient';
import { PlaylistType } from './loadPlaylistById';

export type PlaylistsResponseType = {
  data?: PlaylistType[];
  paginate?: PaginationType;
};

type PlaylistCategoryType = 'regular' | 'custom';

type LoadPlaylistListProps = {
  channel?: string;
  page?: number | undefined;
  subscribed?: boolean | null;
  autoDownload?: boolean | null;
  embyTag?: string | null;
  type?: PlaylistCategoryType;
};

const loadPlaylistList = async ({
  channel,
  page,
  subscribed,
  autoDownload,
  embyTag,
  type,
}: LoadPlaylistListProps) => {
  const searchParams = new URLSearchParams();

  if (channel) searchParams.append('channel', channel);
  if (page) searchParams.append('page', page.toString());
  if (subscribed !== undefined && subscribed !== null)
    searchParams.append('subscribed', subscribed.toString());
  if (autoDownload !== undefined && autoDownload !== null)
    searchParams.append('auto_download', autoDownload.toString());
  if (embyTag) searchParams.append('emby_tag', embyTag);
  if (type) searchParams.append('type', type);

  const endpoint = `/api/playlist/${searchParams.toString() ? `?${searchParams.toString()}` : ''}`;
  return APIClient<PlaylistsResponseType>(endpoint);
};

export default loadPlaylistList;
