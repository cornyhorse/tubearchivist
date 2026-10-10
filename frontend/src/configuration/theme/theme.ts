/**
 * Themes: the user setting `stylesheet` holds one of these slugs.
 * "device" means no data-theme attribute: follow prefers-color-scheme.
 */
export type ThemeSlug = 'device' | 'light' | 'dark' | 'paper' | 'contrast' | 'colourblind';

export const THEMES: { slug: ThemeSlug; label: string; scheme: 'light dark' | 'light' | 'dark' }[] =
  [
    { slug: 'device', label: 'Like my device', scheme: 'light dark' },
    { slug: 'light', label: 'Light', scheme: 'light' },
    { slug: 'dark', label: 'Dark', scheme: 'dark' },
    { slug: 'paper', label: 'Off-white', scheme: 'light' },
    { slug: 'contrast', label: 'High contrast', scheme: 'dark' },
    { slug: 'colourblind', label: 'Colour-blind friendly', scheme: 'light' },
  ];

export const DEFAULT_THEME: ThemeSlug = 'device';

/** the same cache key the inline script in index.html reads */
export const THEME_CACHE_KEY = 'ta-theme';

export const isTheme = (value: unknown): value is ThemeSlug =>
  THEMES.some(theme => theme.slug === value);

/** set data-theme and the color-scheme meta; unknown values mean "device" */
export const applyTheme = (value: unknown): ThemeSlug => {
  const slug = isTheme(value) ? value : DEFAULT_THEME;
  const root = document.documentElement;
  if (slug === 'device') {
    root.removeAttribute('data-theme');
  } else {
    root.setAttribute('data-theme', slug);
  }

  let meta = document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]');
  if (!meta) {
    meta = document.createElement('meta');
    meta.name = 'color-scheme';
    document.head.appendChild(meta);
  }
  meta.content = THEMES.find(theme => theme.slug === slug)!.scheme;

  try {
    if (slug === 'device') {
      localStorage.removeItem(THEME_CACHE_KEY);
    } else {
      localStorage.setItem(THEME_CACHE_KEY, slug);
    }
  } catch {
    // private mode or storage blocked: the attribute is still set
  }

  return slug;
};
