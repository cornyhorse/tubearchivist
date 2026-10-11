import { HomeLayoutType } from '../api/actions/updateUserConfig';
import updateUserConfig from '../api/actions/updateUserConfig';
import { useUserConfigStore } from '../stores/UserConfigStore';

export const HOME_LAYOUT_CACHE_KEY = 'ta-home-layout';

export const HOME_LAYOUTS: { value: HomeLayoutType; label: string; help: string }[] = [
  { value: 'default', label: 'Classic', help: 'Continue watching, then recent videos' },
  { value: 'shelves', label: 'Shelves', help: 'One row per channel, newest uploads first' },
  { value: 'timeline', label: 'Timeline', help: 'Downloads grouped by day, then by channel' },
  { value: 'whatsnew', label: 'What’s new', help: 'Recent downloads as a table you can search' },
];

const isLayout = (value: unknown): value is HomeLayoutType =>
  HOME_LAYOUTS.some(layout => layout.value === value);

const cached = (): HomeLayoutType | null => {
  try {
    const value = localStorage.getItem(HOME_LAYOUT_CACHE_KEY);
    return isLayout(value) ? value : null;
  } catch {
    return null;
  }
};

/**
 * The user's Home layout. Saved in the user config; until the server knows
 * the setting, this browser remembers it.
 */
const useHomeLayout = (): [HomeLayoutType, (layout: HomeLayoutType) => Promise<void>] => {
  const { userConfig, setUserConfig } = useUserConfigStore();
  const layout = isLayout(userConfig.home_layout)
    ? userConfig.home_layout
    : (cached() ?? 'default');

  const setLayout = async (next: HomeLayoutType) => {
    setUserConfig({ ...userConfig, home_layout: next });
    try {
      localStorage.setItem(HOME_LAYOUT_CACHE_KEY, next);
    } catch {
      // storage blocked: the server copy still works
    }
    try {
      const { data } = await updateUserConfig({ home_layout: next });
      if (data) setUserConfig({ ...data, home_layout: data.home_layout ?? next });
    } catch (error) {
      console.error('could not save the home layout', error);
    }
  };

  return [layout, setLayout];
};

export default useHomeLayout;
