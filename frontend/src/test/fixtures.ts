import { FoundVideo, Job, PreviewItem, Verdict } from '../api/ux/client';

export const verdict = (changes: Partial<Verdict> = {}): Verdict => ({
  allowed: true,
  action: 'download',
  reason_key: null,
  reason: null,
  rule: { scope: 'channel', target_id: 'UC1', summary: 'No Shorts' },
  override: null,
  ...changes,
});

export const found = (id: string, changes: Partial<FoundVideo> = {}): FoundVideo => ({
  youtube_id: id,
  title: `Video ${id}`,
  channel_id: 'UC1',
  channel_name: 'Trains',
  channel_indexed: true,
  vid_type: 'videos',
  duration: '10:00',
  duration_seconds: 600,
  published: '2026-10-01T10:00:00+00:00',
  thumb_url: '/t.jpg',
  found_at: '2026-10-09T10:00:00+00:00',
  found_by: { kind: 'channel_check', label: 'Channel check Oct 9', job_id: 1, playlist_id: null },
  verdict: verdict(),
  state: 'waiting',
  job: null,
  error: null,
  downloaded_at: null,
  ...changes,
});

export const job = (id: number, changes: Partial<Job> = {}): Job => ({
  id,
  type: 'download_video',
  type_label: 'Download',
  label: '',
  lane: 'download',
  target: { kind: 'video', id: `v${id}`, title: `Video ${id}`, url: null },
  status: 'queued',
  priority: 50,
  position: 1,
  attempts: 0,
  max_attempts: 3,
  progress: null,
  error: null,
  result: null,
  created_by: 'schedule',
  created_at: new Date(Date.now() - 600000).toISOString(),
  started_at: null,
  finished_at: null,
  duration_seconds: null,
  depends_on: null,
  parent: null,
  children: 0,
  merged: 0,
  actions: ['cancel', 'pause', 'move_to_top'],
  ...changes,
});

export const previewItem = (changes: Partial<PreviewItem> = {}): PreviewItem => ({
  url: 'https://youtube.com/@trains',
  kind: 'channel',
  id: 'UC1',
  title: 'Trains',
  channel_id: 'UC1',
  channel_name: 'Trains',
  thumb_url: null,
  video_count: 142,
  subscribed: false,
  known: { downloaded: 0, waiting: 0, ignored: 0 },
  rule: { scope: 'default', target_id: '', summary: 'No Shorts' },
  numbering: { scheme: 'date', source: 'channel' },
  verdict: {
    total: 142,
    download: 118,
    skipped: 24,
    already_have: 0,
    reasons: [
      { key: 'vid_type', label: 'Shorts', count: 20 },
      { key: 'too_long', label: '>1 h', count: 4 },
    ],
  },
  sample: [],
  ...changes,
});

/** an ApiResponseType */
export const ok = <T>(data: T, status = 200) => ({ data, status });
export const notYet = { status: 501, error: { error: 'not built yet: jobs engine' } };
