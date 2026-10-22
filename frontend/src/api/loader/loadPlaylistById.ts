import APIClient from '../../functions/APIClient';

export type PlaylistEntryType = {
  youtube_id: string;
  title: string;
  uploader: string;
  idx: number;
  downloaded: boolean;
};

export type PlaylistType = {
  playlist_active: boolean;
  playlist_channel: string;
  playlist_channel_id: string;
  playlist_description: string | null;
  playlist_entries: PlaylistEntryType[];
  playlist_sort_order: 'top' | 'bottom';
  playlist_id: string;
  playlist_last_refresh: string;
  playlist_name: string;
  playlist_subscribed: boolean;
  playlist_thumbnail: string;
  playlist_type: string;
  /** opted in: checks download new videos the rule allows; off by default */
  auto_download?: boolean;
  /** Emby tags on the show; none hides it from tag-restricted Emby users */
  emby_tags?: string[];
  _index: string;
  _score: number;
};

export type PlaylistResponseType = PlaylistType;

const loadPlaylistById = async (playlistId: string | undefined) => {
  return APIClient<PlaylistResponseType>(`/api/playlist/${playlistId}/`);
};

export default loadPlaylistById;
