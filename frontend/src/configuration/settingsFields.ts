import { AppSettingsConfigType } from '../api/loader/loadAppsettingsConfig';

/**
 * Every application setting with plain-language help, grouped by what you're
 * trying to do. Defaults mirror AppConfig.CONFIG_DEFAULTS in
 * backend/appsettings/src/config.py.
 */

type Group = keyof AppSettingsConfigType;
export type SettingPath = {
  [G in Group]: `${G}.${Extract<keyof AppSettingsConfigType[G], string>}`;
}[Group];

export type SettingValue = string | number | boolean | null;

export type SettingDef = {
  path: SettingPath;
  label: string;
  help: string;
  kind: 'number' | 'text' | 'toggle' | 'select';
  options?: { value: string; label: string }[];
  unit?: string;
  placeholder?: string;
  /** what the current value does, in words */
  effect?: (value: SettingValue) => string;
  /** deletes things: shown with a warning */
  danger?: boolean;
  /** only shown when this returns true for the whole config */
  visible?: (config: AppSettingsConfigType) => boolean;
};

export const SETTING_DEFAULTS: Record<SettingPath, SettingValue> = {
  'subscriptions.channel_size': 50,
  'subscriptions.live_channel_size': 50,
  'subscriptions.shorts_channel_size': 50,
  'subscriptions.playlist_size': 50,
  'subscriptions.auto_start': false,
  'subscriptions.extract_flat': false,
  'downloads.limit_speed': null,
  'downloads.sleep_interval': 10,
  'downloads.autodelete_days': null,
  'downloads.format': null,
  'downloads.format_sort': null,
  'downloads.add_metadata': false,
  'downloads.subtitle': null,
  'downloads.subtitle_source': null,
  'downloads.subtitle_index': false,
  'downloads.comment_max': null,
  'downloads.comment_sort': 'top',
  'downloads.cookie_import': false,
  'downloads.pot_provider_url': null,
  'downloads.throttledratelimit': null,
  'downloads.extractor_lang': null,
  'downloads.integrate_ryd': false,
  'downloads.integrate_sponsorblock': false,
  'application.enable_snapshot': true,
  'application.enable_cast': false,
};

export const getSetting = (config: AppSettingsConfigType, path: SettingPath): SettingValue => {
  const [group, key] = path.split('.') as [Group, string];
  const value = (config[group] as Record<string, SettingValue>)[key];
  return value === undefined ? null : value;
};

/** treat "", null and the default as the same "not changed" state */
export const isDefault = (path: SettingPath, value: SettingValue) => {
  const fallback = SETTING_DEFAULTS[path];
  const empty = (v: SettingValue) => v === null || v === '';
  if (empty(value) && empty(fallback)) return true;
  if (typeof fallback === 'boolean' && value === null) return fallback === false;
  return value === fallback;
};

/** the patch the appsettings API expects for one field */
export const settingPatch = (
  path: SettingPath,
  value: SettingValue,
): Partial<AppSettingsConfigType> => {
  const [group, key] = path.split('.');
  return { [group]: { [key]: value } } as Partial<AppSettingsConfigType>;
};

const videosChecked = (kind: string) => (value: SettingValue) => {
  if (value === 0) return `${kind[0].toUpperCase()}${kind.slice(1)} are not checked.`;
  if (value === null) return `Looks at every one of the channel's ${kind}, which is slow.`;
  return `Looks at the newest ${value} ${kind}.`;
};

export const CHECK_FIELDS: SettingDef[] = [
  {
    path: 'subscriptions.channel_size',
    label: 'Videos to look at per channel',
    help: 'How many of the newest videos each channel check reads. 50 is plenty for a daily check; 0 skips regular videos; empty reads them all.',
    kind: 'number',
    unit: 'videos',
    placeholder: 'all',
    effect: videosChecked('videos'),
  },
  {
    path: 'subscriptions.live_channel_size',
    label: 'Livestreams to look at per channel',
    help: 'Same, for the Live tab. 0 skips livestreams entirely.',
    kind: 'number',
    unit: 'streams',
    placeholder: 'all',
    effect: videosChecked('livestreams'),
  },
  {
    path: 'subscriptions.shorts_channel_size',
    label: 'Shorts to look at per channel',
    help: 'Same, for the Shorts tab. 0 skips Shorts entirely.',
    kind: 'number',
    unit: 'shorts',
    placeholder: 'all',
    effect: videosChecked('Shorts'),
  },
  {
    path: 'subscriptions.playlist_size',
    label: 'Videos to look at per playlist',
    help: 'How many of the newest entries each playlist check reads.',
    kind: 'number',
    unit: 'videos',
  },
  {
    path: 'subscriptions.extract_flat',
    label: 'Quick checks',
    help: 'Adds found videos without fetching their details first, which is much faster. Details are fetched when they download.',
    kind: 'toggle',
  },
];

export const FORMAT_FIELDS: SettingDef[] = [
  {
    path: 'downloads.format',
    label: 'Video format',
    help: 'yt-dlp format (-f). Leave empty for the best available. Example: bestvideo[height<=1080]+bestaudio/best[height<=1080]',
    kind: 'text',
    placeholder: 'best available',
    effect: value => (value ? `Uses ${value}.` : 'Best video and audio available.'),
  },
  {
    path: 'downloads.format_sort',
    label: 'Prefer formats by',
    help: 'yt-dlp format sort (-S). Example: res,codec:av1 prefers AV1. Not every browser plays every codec.',
    kind: 'text',
    placeholder: 'yt-dlp default',
  },
  {
    path: 'downloads.extractor_lang',
    label: 'Metadata language',
    help: 'Two-letter language code for titles and descriptions, when the uploader provides translations.',
    kind: 'text',
    placeholder: 'e.g. en',
  },
  {
    path: 'downloads.add_metadata',
    label: 'Embed metadata in files',
    help: 'Writes title, description and thumbnail into the video file itself.',
    kind: 'toggle',
  },
];

export const SUBTITLE_FIELDS: SettingDef[] = [
  {
    path: 'downloads.subtitle',
    label: 'Subtitle languages',
    help: 'Comma-separated language codes to download, e.g. en, de, zh-Hans. Leave empty for none.',
    kind: 'text',
    placeholder: 'none',
    effect: value => (value ? `Downloads subtitles in ${value}.` : 'No subtitles are downloaded.'),
  },
  {
    path: 'downloads.subtitle_source',
    label: 'Use auto-generated subtitles',
    help: 'Falls back to YouTube’s less accurate automatic subtitles when the uploader didn’t provide any.',
    kind: 'select',
    options: [
      { value: 'user', label: 'No, only uploaded subtitles' },
      { value: 'auto', label: 'Yes, fall back to automatic ones' },
    ],
    visible: config => Boolean(config.downloads.subtitle),
  },
  {
    path: 'downloads.subtitle_index',
    label: 'Make subtitles searchable',
    help: 'Indexes subtitle text so search finds words spoken in videos.',
    kind: 'toggle',
    visible: config => Boolean(config.downloads.subtitle),
  },
];

export const COMMENT_FIELDS: SettingDef[] = [
  {
    path: 'downloads.comment_max',
    label: 'Comments to download',
    help: 'max-comments,max-parents,max-replies,max-replies-per-thread,max-depth. Example: all,100,all,30,all. Empty downloads none. Comments are slow to fetch.',
    kind: 'text',
    placeholder: 'none',
    effect: value => (value ? `Downloads comments: ${value}.` : 'No comments are downloaded.'),
  },
  {
    path: 'downloads.comment_sort',
    label: 'Comment order',
    help: 'Which comments come first when the limit is reached.',
    kind: 'select',
    options: [
      { value: 'top', label: 'Top comments' },
      { value: 'new', label: 'Newest comments' },
    ],
    visible: config => Boolean(config.downloads.comment_max),
  },
];

export const EXTRA_FIELDS: SettingDef[] = [
  {
    path: 'downloads.integrate_sponsorblock',
    label: 'SponsorBlock',
    help: 'Marks sponsor segments in the player, using the crowdsourced SponsorBlock database.',
    kind: 'toggle',
  },
  {
    path: 'downloads.integrate_ryd',
    label: 'Dislike counts',
    help: 'Fetches dislike counts from the Return YouTube Dislike project.',
    kind: 'toggle',
  },
];

export const SPEED_FIELDS: SettingDef[] = [
  {
    path: 'downloads.limit_speed',
    label: 'Download speed limit',
    help: 'Caps download speed. Helps avoid being blocked by YouTube. Empty means no limit.',
    kind: 'number',
    unit: 'KB/s',
    placeholder: 'no limit',
    effect: value => (value ? `At most ${value} KB/s.` : 'No limit.'),
  },
  {
    path: 'downloads.throttledratelimit',
    label: 'Restart slow downloads below',
    help: 'Restarts a download when its speed drops below this, which often gets a faster server.',
    kind: 'number',
    unit: 'KB/s',
    placeholder: 'off',
  },
  {
    path: 'downloads.sleep_interval',
    label: 'Pause between requests',
    help: 'Seconds to wait between requests to YouTube, randomised ±50%. 10 or more is recommended.',
    kind: 'number',
    unit: 'seconds',
  },
  {
    path: 'downloads.autodelete_days',
    label: 'Delete watched videos after',
    help: 'Deletes video files this many days after they are marked watched. Channels can override it. Empty keeps everything.',
    kind: 'number',
    unit: 'days',
    placeholder: 'never',
    danger: true,
    effect: value =>
      value ? `Watched videos are deleted after ${value} days.` : 'Nothing is deleted.',
  },
];

export const WHEN_FIELDS: SettingDef[] = [
  {
    path: 'subscriptions.auto_start',
    label: 'Download found videos straight away',
    help: 'When a channel check finds new videos your rules allow, start downloading them at once instead of waiting for the download schedule or your click.',
    kind: 'toggle',
  },
];

export const YOUTUBE_FIELDS: SettingDef[] = [
  {
    path: 'downloads.pot_provider_url',
    label: 'PO token provider',
    help: 'URL of a PO token provider running next to TA. Some videos need it to download.',
    kind: 'text',
    placeholder: 'not used',
  },
];

export const PLAYER_FIELDS: SettingDef[] = [
  {
    path: 'application.enable_cast',
    label: 'Cast to TVs',
    help: 'Shows a Cast button in the video player. Needs TA served over HTTPS.',
    kind: 'toggle',
  },
];

export const SNAPSHOT_FIELDS: SettingDef[] = [
  {
    path: 'application.enable_snapshot',
    label: 'Daily database snapshots',
    help: 'Keeps automatic snapshots of the database so you can roll back after a mistake.',
    kind: 'toggle',
  },
];
