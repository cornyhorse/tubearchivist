import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { applyTheme, isTheme, THEME_CACHE_KEY, THEMES } from './theme';
import page from '../../../index.html?raw';

const html = document.documentElement;
const scheme = () => document.querySelector('meta[name="color-scheme"]')?.getAttribute('content');

beforeEach(() => {
  document.head.innerHTML = '<meta name="color-scheme" content="light dark" />';
  html.removeAttribute('data-theme');
  localStorage.clear();
});

describe('applyTheme', () => {
  it.each([
    ['light', 'light'],
    ['dark', 'dark'],
    ['paper', 'light'],
    ['contrast', 'dark'],
    ['colourblind', 'light'],
  ])('sets data-theme=%s with color-scheme %s', (slug, expected) => {
    expect(applyTheme(slug)).toBe(slug);
    expect(html.getAttribute('data-theme')).toBe(slug);
    expect(scheme()).toBe(expected);
    expect(localStorage.getItem(THEME_CACHE_KEY)).toBe(slug);
  });

  it('follows the device: no attribute, both schemes, no cache', () => {
    applyTheme('dark');
    expect(applyTheme('device')).toBe('device');
    expect(html.hasAttribute('data-theme')).toBe(false);
    expect(scheme()).toBe('light dark');
    expect(localStorage.getItem(THEME_CACHE_KEY)).toBeNull();
  });

  it.each(['neon', 'dark.css', '', null, undefined, 3, '<script>'])(
    'treats unknown value %s as the device default',
    value => {
      applyTheme('paper');
      expect(applyTheme(value)).toBe('device');
      expect(html.hasAttribute('data-theme')).toBe(false);
    },
  );

  it('creates the color-scheme meta when missing', () => {
    document.head.innerHTML = '';
    applyTheme('contrast');
    expect(scheme()).toBe('dark');
  });

  it('still sets the attribute when storage is blocked', () => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error('blocked');
    };
    try {
      applyTheme('paper');
      expect(html.getAttribute('data-theme')).toBe('paper');
    } finally {
      Storage.prototype.setItem = original;
    }
  });
});

describe('isTheme and labels', () => {
  it('knows exactly the six themes', () => {
    expect(THEMES.map(t => t.slug)).toEqual([
      'device',
      'light',
      'dark',
      'paper',
      'contrast',
      'colourblind',
    ]);
    expect(THEMES.map(t => t.label)).toEqual([
      'Like my device',
      'Light',
      'Dark',
      'Off-white',
      'High contrast',
      'Colour-blind friendly',
    ]);
    expect(isTheme('dark')).toBe(true);
    expect(isTheme('dark.css')).toBe(false);
  });
});

describe('no-flash script in index.html', () => {
  const script = page.match(/<script>([\s\S]*?)<\/script>/)![1];
  const run = () => new Function(script)();

  it('comes before any stylesheet', () => {
    expect(page.indexOf('<script>')).toBeLessThan(page.indexOf('<link rel="stylesheet"') >>> 0);
  });

  it.each([
    ['dark', 'dark'],
    ['contrast', 'dark'],
    ['paper', 'light'],
  ])('applies cached %s before the app loads', (slug, expected) => {
    localStorage.setItem(THEME_CACHE_KEY, slug);
    run();
    expect(html.getAttribute('data-theme')).toBe(slug);
    expect(scheme()).toBe(expected);
  });

  it('ignores a tampered cache', () => {
    localStorage.setItem(THEME_CACHE_KEY, '"><img src=x onerror=alert(1)>');
    run();
    expect(html.hasAttribute('data-theme')).toBe(false);
    expect(scheme()).toBe('light dark');
  });
});

afterEach(() => {
  html.removeAttribute('data-theme');
});
