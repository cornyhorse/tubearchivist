import { create } from 'zustand';
import { UserConfigType } from '../api/actions/updateUserConfig';
import { ViewStylesEnum, ViewStylesType } from '../configuration/constants/ViewStyle';
import { SortOrderEnum, SortOrderType } from '../api/loader/loadVideoListByPage';
import { DEFAULT_THEME, isTheme, THEME_CACHE_KEY, ThemeSlug } from '../configuration/theme/theme';

// start from the cached theme, so nothing changes before the user's config loads
const cachedTheme = (): ThemeSlug => {
  try {
    const cached = localStorage.getItem(THEME_CACHE_KEY);
    return isTheme(cached) ? cached : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
};

interface UserConfigState {
  userConfig: UserConfigType;
  setUserConfig: (userConfig: UserConfigType) => void;
}

export const useUserConfigStore = create<UserConfigState>(set => ({
  userConfig: {
    stylesheet: cachedTheme(),
    page_size: 12,
    sort_by: 'published',
    sort_order: SortOrderEnum.Desc as SortOrderType,
    view_style_home: ViewStylesEnum.Grid as ViewStylesType,
    view_style_channel: ViewStylesEnum.List as ViewStylesType,
    view_style_downloads: ViewStylesEnum.List as ViewStylesType,
    view_style_playlist: ViewStylesEnum.Grid as ViewStylesType,
    vid_type_filter: null,
    grid_items: 3,
    hide_watched: null,
    hide_watched_channel: null,
    hide_watched_playlist: null,
    file_size_unit: 'binary',
    show_ignored_only: false,
    show_subed_only: null,
    show_subed_only_playlists: null,
    show_help_text: true,
  },
  setUserConfig: userConfig => set({ userConfig }),
}));
