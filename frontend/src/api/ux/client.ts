/**
 * Typed calls for the fork's UX API (docs/ux-api-contract.md).
 * Types come from schema.ts, generated from docs/ux-api-schema.yaml:
 *   npm run gen:api
 */
import APIClient, { ApiResponseType } from '../../functions/APIClient';
import { components } from './schema';
import { DownloadRuleType } from '../actions/updateDownloadRule';

export type Schemas = components['schemas'];
export type Job = Schemas['Job'];
export type JobAction = Schemas['JobActionEnum'];
export type JobStatus = Schemas['JobStatusEnum'];
export type Lane = Schemas['Lane'];
export type LaneName = Schemas['LaneEnum'];
export type QueueSummary = Schemas['QueueSummary'];
export type ScheduledJob = Schemas['ScheduledJob'];
export type FoundVideo = Schemas['FoundVideo'];
export type FoundList = Schemas['FoundList'];
export type FoundTab = Schemas['FoundTabEnum'];
export type FoundFilter = Schemas['FoundFilter'];
export type FoundActionName = Schemas['FoundActionEnum'];
export type FoundActionResult = Schemas['FoundActionResponse'];
export type Verdict = Schemas['Verdict'];
export type PreviewResult = Schemas['Preview'];
export type PreviewItem = Schemas['PreviewItem'];
export type AddRequest = Schemas['Add'];
export type AddResult = Schemas['AddResult'];
export type Rule = Schemas['Rule'];
/** the rule fields the editor works with (the schema marks some optional) */
export type RuleBody = DownloadRuleType;
export type RuleRow = Schemas['RuleRow'];
export type RuleScope = Schemas['RuleScopeEnum'];
export type RulePreset = Schemas['RulePreset'];
export type RulePreview = Schemas['RulePreview'];
export type EffectiveRule = Schemas['EffectiveRule'];
export type VideoOverride = Schemas['VideoOverride'];
/** playlist_frozen arrives with the Emby review fixes (cornyhorse/review-fixes-emby) */
export type NumberingScheme = Schemas['NumberingSchemeEnum'] | 'playlist_frozen';
export type Scheme = Schemas['Scheme'];
export type SchemePreview = Schemas['SchemePreview'];
export type VideoNumber = Schemas['VideoNumber'];
export type MediaServer = Schemas['MediaServer'];
export type MediaServerTest = Schemas['MediaServerTest'];
export type Library = Schemas['Library'];

/** fill the fields the schema marks optional */
export const toRuleBody = (rule: Partial<Rule> | null | undefined): RuleBody | null =>
  rule
    ? {
        enabled: rule.enabled ?? true,
        min_duration: rule.min_duration ?? null,
        max_duration: rule.max_duration ?? null,
        vid_types: rule.vid_types ?? [],
        title_include: rule.title_include ?? '',
        title_exclude: rule.title_exclude ?? '',
        published_after: rule.published_after ?? null,
        action: rule.action ?? 'ignore',
      }
    : null;

/** the endpoint exists in the contract but its PR hasn't landed (501), or the server predates it (404) */
export const notBuilt = (response: { status: number }) =>
  response.status === 501 || response.status === 404;

type Params = Record<string, string | number | boolean | null | undefined>;

const query = (params: Params = {}) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.append(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
};

const get = <T>(path: string, params?: Params): Promise<ApiResponseType<T>> =>
  APIClient<T>(`${path}${query(params)}`);

const send = <T>(
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: object,
): Promise<ApiResponseType<T>> =>
  APIClient<T>(path, { method, body: body as Record<string, unknown> | undefined });

// --- queue ---

export const getQueueSummary = () => get<QueueSummary>('/api/queue/summary/');
export const listJobs = (params: {
  status?: string;
  lane?: LaneName;
  type?: string;
  page?: number;
  page_size?: number;
}) => get<Schemas['JobList']>('/api/queue/jobs/', params);
export const createJob = (
  body: Omit<Schemas['JobCreate'], 'priority'> & { priority?: Schemas['JobCreate']['priority'] },
) => send<Schemas['JobCreateResponse']>('POST', '/api/queue/jobs/', { priority: 'high', ...body });
export const jobAction = (id: number, action: JobAction) =>
  send<Job>('POST', `/api/queue/jobs/${id}/action/`, { action });
export const listLanes = () => get<Lane[]>('/api/queue/lanes/');
export const updateLane = (lane: LaneName, body: Schemas['PatchedLaneUpdate']) =>
  send<Lane>('PATCH', `/api/queue/lanes/${lane}/`, body);
export const listScheduled = () => get<ScheduledJob[]>('/api/queue/scheduled/');
export const updateScheduled = (
  name: string,
  body: { enabled?: boolean; schedule?: string | null },
) => send<ScheduledJob>('PATCH', `/api/queue/scheduled/${name}/`, body);
export const runScheduled = (name: string) =>
  send<Schemas['JobCreateResponse']>('POST', `/api/queue/scheduled/${name}/run/`);

// --- found videos ---

export const listFound = (params: Params) => get<FoundList>('/api/found/', params);
export const foundAction = (
  body: { action: FoundActionName } & ({ ids: string[] } | { filter: Partial<FoundFilter> }),
) => send<FoundActionResult>('POST', '/api/found/actions/', body);

// --- add ---

export const previewAdd = (body: { urls: string; rule?: RuleBody | null; newest_n?: number }) =>
  send<PreviewResult>('POST', '/api/add/preview/', body);
export const addUrls = (body: AddRequest) => send<AddResult>('POST', '/api/add/', body);

// --- rules ---

const rulePath = (scope: RuleScope, id?: string) =>
  scope === 'default' ? '/api/rules/default/' : `/api/rules/${scope}/${id}/`;

export const listRules = () => get<Schemas['RuleList']>('/api/rules/');
export const listRulePresets = () => get<RulePreset[]>('/api/rules/presets/');
export const getRule = (scope: RuleScope, id?: string) => get<Rule | null>(rulePath(scope, id));
export const saveRule = (scope: RuleScope, id: string | undefined, rule: RuleBody) =>
  send<Rule>('PUT', rulePath(scope, id), rule);
export const deleteRule = (scope: RuleScope, id?: string) =>
  send<null>('DELETE', rulePath(scope, id));
export const previewRule = (body: {
  scope: RuleScope;
  target_id?: string;
  rule?: RuleBody | null;
  limit?: number;
}) => send<RulePreview>('POST', '/api/rules/preview/', body);
export const getEffectiveRule = (params: { video?: string; channel?: string; playlist?: string }) =>
  get<EffectiveRule>('/api/rules/effective/', params);
export const listVideoOverrides = () => get<Schemas['VideoOverrideList']>('/api/rules/videos/');
export const setVideoOverride = (youtubeId: string, override: 'download' | 'ignore', note = '') =>
  send<VideoOverride>('PUT', `/api/rules/videos/${youtubeId}/`, { override, note });
export const clearVideoOverride = (youtubeId: string) =>
  send<null>('DELETE', `/api/rules/videos/${youtubeId}/`);

// --- numbering ---

export const listSchemes = () => get<Scheme[]>('/api/numbering/schemes/');
export const previewScheme = (kind: 'channel' | 'playlist', id: string, scheme: NumberingScheme) =>
  send<SchemePreview>('POST', `/api/numbering/${kind}/${id}/preview/`, { scheme });
export const setScheme = (kind: 'channel' | 'playlist', id: string, scheme: NumberingScheme) =>
  send<Schemas['SchemeChangeResult']>('PUT', `/api/numbering/${kind}/${id}/`, { scheme });
export const getVideoNumbering = (youtubeId: string) =>
  get<Schemas['VideoNumberList']>(`/api/numbering/video/${youtubeId}/`);
export const setVideoNumbering = (
  youtubeId: string,
  body: { series_kind: 'channel' | 'playlist'; series_id: string; season: number; episode: number },
) => send<VideoNumber>('PUT', `/api/numbering/video/${youtubeId}/`, body);
export const clearVideoNumbering = (
  youtubeId: string,
  seriesKind: 'channel' | 'playlist',
  seriesId: string,
) =>
  send<null>(
    'DELETE',
    `/api/numbering/video/${youtubeId}/${query({ series_kind: seriesKind, series_id: seriesId })}`,
  );

// --- media server ---

export const getMediaServer = () => get<MediaServer>('/api/mediaserver/');
export const saveMediaServer = (body: Partial<MediaServer> & { api_key?: string | null }) =>
  send<MediaServer>('PUT', '/api/mediaserver/', body);
export const testMediaServer = (body: { type?: string | null; url?: string; api_key?: string }) =>
  send<MediaServerTest>('POST', '/api/mediaserver/test/', body);
export const listLibraries = () => get<Schemas['LibraryList']>('/api/mediaserver/libraries/');
export const notifyMediaServer = (body: { paths?: string[]; all?: boolean }) =>
  send<{ job: Job }>('POST', '/api/mediaserver/notify/', body);
export const refreshMediaServer = (libraryId?: string) =>
  send<{ job: Job }>(
    'POST',
    '/api/mediaserver/refresh/',
    libraryId ? { library_id: libraryId } : {},
  );
