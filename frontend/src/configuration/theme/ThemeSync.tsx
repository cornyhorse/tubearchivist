import { useEffect } from 'react';
import { useUserConfigStore } from '../../stores/UserConfigStore';
import { applyTheme } from './theme';

/** apply the signed-in user's theme whenever their config changes */
const ThemeSync = () => {
  const stylesheet = useUserConfigStore(state => state.userConfig.stylesheet);

  useEffect(() => {
    applyTheme(stylesheet);
  }, [stylesheet]);

  return null;
};

export default ThemeSync;
