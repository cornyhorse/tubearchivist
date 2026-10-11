import { DownloadRuleType, VideoTypeOption } from '../api/actions/updateDownloadRule';
import { EmbyNumberingType } from '../api/actions/updateEmbyNumbering';

const TYPE_NAMES: Record<VideoTypeOption, string> = {
  videos: 'Videos',
  streams: 'Livestreams',
  shorts: 'Shorts',
};

const ALL_TYPES: VideoTypeOption[] = ['videos', 'streams', 'shorts'];

/** 600 -> "10 min", 3600 -> "1 h", 5400 -> "1 h 30 min" */
export const formatDurationShort = (seconds: number): string => {
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${minutes} min`;
  if (rest === 0) return `${hours} h`;
  return `${hours} h ${rest} min`;
};

/** the parts of a rule in plain words, e.g. ["No Shorts", "> 10 min"] */
export const describeRuleParts = (rule: DownloadRuleType): string[] => {
  const parts: string[] = [];
  const types = rule.vid_types ?? [];

  if (types.length > 0 && types.length < ALL_TYPES.length) {
    const left = ALL_TYPES.filter(type => !types.includes(type));
    if (types.length === 1) {
      parts.push(`Only ${TYPE_NAMES[types[0]]}`);
    } else {
      parts.push(`No ${left.map(type => TYPE_NAMES[type]).join(', ')}`);
    }
  }
  if (rule.min_duration) parts.push(`> ${formatDurationShort(rule.min_duration)}`);
  if (rule.max_duration) parts.push(`< ${formatDurationShort(rule.max_duration)}`);
  if (rule.title_include) parts.push(`Title has “${rule.title_include}”`);
  if (rule.title_exclude) parts.push(`Title not “${rule.title_exclude}”`);
  if (rule.published_after) parts.push(`After ${rule.published_after}`);
  return parts;
};

export type RuleSummary = {
  /** short badge text */
  label: string;
  /** one sentence for the tooltip */
  detail: string;
  /** "own" rule, "default" rule inherited, or the rule is switched "off" */
  state: 'own' | 'default' | 'off';
};

/**
 * Badge text for a channel's rule. A null rule means the default rule applies;
 * pass it as defaultRule to describe that one.
 */
export const summarizeRule = (
  rule: DownloadRuleType | null | undefined,
  defaultRule?: DownloadRuleType | null,
): RuleSummary => {
  if (!rule) {
    const inherited = defaultRule && defaultRule.enabled ? describeRuleParts(defaultRule) : [];
    return {
      label: 'Default',
      detail: inherited.length
        ? `Uses the default rule: ${inherited.join(' · ')}.`
        : 'Uses the default rule. Everything found is downloaded.',
      state: 'default',
    };
  }
  if (!rule.enabled) {
    return {
      label: 'All videos',
      detail: 'This rule is switched off, so every video found is downloaded.',
      state: 'off',
    };
  }
  const parts = describeRuleParts(rule);
  const skipped = rule.action === 'skip' ? 'left out' : 'kept as ignored';
  return {
    label: parts.length ? parts.join(' · ') : 'All videos',
    detail: parts.length
      ? `Downloads only videos that match: ${parts.join(', ')}. Others are ${skipped}.`
      : 'This rule matches every video.',
    state: 'own',
  };
};

export const NUMBERING_SHORT: Record<EmbyNumberingType, string> = {
  date: 'By date',
  year_frozen: 'By year',
  absolute_frozen: 'Absolute',
  playlist_frozen: 'Playlist order, frozen',
  playlist_position: 'Playlist order',
};
