import { ViewStylesType } from '../../configuration/constants/ViewStyle';
import { ThemeSlug } from '../../configuration/theme/theme';
import APIClient from '../../functions/APIClient';
import { SortByType, SortOrderType, VideoTypes } from '../loader/loadVideoListByPage';

export const FileSizeUnits = {
  Binary: 'binary',
  Metric: 'metric',
};

export type UserConfigType = {
  stylesheet: ThemeSlug;
  page_size: number;
  sort_by: SortByType;
  sort_order: SortOrderType;
  view_style_home: ViewStylesType;
  view_style_channel: ViewStylesType;
  view_style_downloads: ViewStylesType;
  view_style_playlist: ViewStylesType;
  vid_type_filter: VideoTypes | null;
  grid_items: number;
  hide_watched: boolean | null;
  hide_watched_channel: boolean | null;
  hide_watched_playlist: boolean | null;
  file_size_unit: 'binary' | 'metric';
  show_ignored_only: boolean;
  show_subed_only: boolean | null;
  show_subed_only_playlists: boolean | null;
  show_help_text: boolean;
};

const updateUserConfig = async (config: Partial<UserConfigType>) => {
  return APIClient<UserConfigType>('/api/user/me/', {
    method: 'POST',
    body: config,
  });
};

export default updateUserConfig;
