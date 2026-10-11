import { RuleBody, RulePreset } from '../../api/ux/client';

export const EMPTY_RULE: RuleBody = {
  enabled: true,
  min_duration: null,
  max_duration: null,
  vid_types: [],
  title_include: '',
  title_exclude: '',
  published_after: null,
  action: 'ignore',
};

/** used when the server doesn't list presets yet */
export const FALLBACK_PRESETS: RulePreset[] = [
  {
    key: 'no_shorts',
    label: 'No Shorts',
    description: 'Skip Shorts',
    patch: { vid_types: ['videos', 'streams'] },
    needs_value: null,
    value_field: null,
  },
  {
    key: 'no_livestreams',
    label: 'No livestreams',
    description: 'Skip livestreams',
    patch: { vid_types: ['videos', 'shorts'] },
    needs_value: null,
    value_field: null,
  },
  {
    key: 'skip_over_1h',
    label: 'Skip over 1 hour',
    description: 'Only videos up to an hour',
    patch: { max_duration: 3600 },
    needs_value: null,
    value_field: null,
  },
  {
    key: 'only_over_1h',
    label: 'Only over 1 hour',
    description: 'Only videos longer than an hour',
    patch: { min_duration: 3600 },
    needs_value: null,
    value_field: null,
  },
  {
    key: 'only_after_date',
    label: 'Only after date…',
    description: 'Only videos uploaded on or after a date',
    patch: {},
    needs_value: 'date',
    value_field: 'published_after',
  },
  {
    key: 'title_contains',
    label: 'Title contains…',
    description: 'Only titles matching',
    patch: {},
    needs_value: 'text',
    value_field: 'title_include',
  },
  {
    key: 'title_excludes',
    label: 'Title doesn’t contain…',
    description: 'Skip titles matching',
    patch: {},
    needs_value: 'text',
    value_field: 'title_exclude',
  },
];
