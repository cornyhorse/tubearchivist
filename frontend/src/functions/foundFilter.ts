import { FoundFilter, FoundTab } from '../api/ux/client';

export type Filters = {
  q: string;
  channel: string;
  vid_type: string;
  min_minutes: string;
  max_minutes: string;
  published_from: string;
  published_to: string;
  skipped_by_rule: boolean;
  error: boolean;
};

export const NO_FILTERS: Filters = {
  q: '',
  channel: '',
  vid_type: '',
  min_minutes: '',
  max_minutes: '',
  published_from: '',
  published_to: '',
  skipped_by_rule: false,
  error: false,
};

/** the filter object the API takes, for the list and for "all matching" */
export const toApiFilter = (tab: FoundTab, filters: Filters): Partial<FoundFilter> => {
  const filter: Partial<FoundFilter> = { tab };
  if (filters.q.trim()) filter.q = filters.q.trim();
  if (filters.channel) filter.channel = filters.channel;
  if (filters.vid_type) filter.vid_type = filters.vid_type;
  if (filters.min_minutes) filter.min_duration = Math.round(Number(filters.min_minutes) * 60);
  if (filters.max_minutes) filter.max_duration = Math.round(Number(filters.max_minutes) * 60);
  if (filters.published_from) filter.published_from = filters.published_from;
  if (filters.published_to) filter.published_to = filters.published_to;
  if (filters.skipped_by_rule) filter.skipped_by_rule = true;
  if (filters.error) filter.error = true;
  return filter;
};
