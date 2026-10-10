import { useRef } from 'react';
import updateUserConfig from '../api/actions/updateUserConfig';
import { applyTheme, THEMES, ThemeSlug } from '../configuration/theme/theme';
import { useUserConfigStore } from '../stores/UserConfigStore';

/** "Theme" menu in the header; the choice is saved to the user's settings */
const ThemePicker = () => {
  const { userConfig, setUserConfig } = useUserConfigStore();
  const menu = useRef<HTMLDetailsElement>(null);
  const current = userConfig.stylesheet;

  const choose = async (slug: ThemeSlug) => {
    applyTheme(slug);
    setUserConfig({ ...userConfig, stylesheet: slug });
    if (menu.current) {
      menu.current.open = false;
    }
    const response = await updateUserConfig({ stylesheet: slug });
    if (response.data) {
      setUserConfig(response.data);
    }
  };

  return (
    <details className="theme-picker" ref={menu}>
      <summary>Theme</summary>
      <div className="theme-picker-menu" role="group" aria-label="Theme">
        {THEMES.map(theme => (
          <button
            key={theme.slug}
            type="button"
            aria-pressed={current === theme.slug}
            className={current === theme.slug ? 'theme-option current' : 'theme-option'}
            onClick={() => choose(theme.slug)}
          >
            {theme.label}
          </button>
        ))}
      </div>
    </details>
  );
};

export default ThemePicker;
