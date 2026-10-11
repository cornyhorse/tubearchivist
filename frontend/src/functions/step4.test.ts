import { describe, expect, it } from 'vitest';
import { toApiFilter, NO_FILTERS } from './foundFilter';
import { verdictSentence } from './verdictSentence';
import { jobLabel } from './jobLabel';
import { job, previewItem } from '../test/fixtures';

describe('toApiFilter', () => {
  it('sends only what is set, durations in seconds', () => {
    expect(toApiFilter('waiting', NO_FILTERS)).toEqual({ tab: 'waiting' });
    expect(
      toApiFilter('ignored', {
        q: ' train ',
        channel: 'UC1',
        vid_type: 'shorts',
        min_minutes: '10',
        max_minutes: '60',
        published_from: '2026-01-01',
        published_to: '2026-02-01',
        skipped_by_rule: true,
        error: true,
      }),
    ).toEqual({
      tab: 'ignored',
      q: 'train',
      channel: 'UC1',
      vid_type: 'shorts',
      min_duration: 600,
      max_duration: 3600,
      published_from: '2026-01-01',
      published_to: '2026-02-01',
      skipped_by_rule: true,
      error: true,
    });
  });
});

describe('verdictSentence', () => {
  it('summarises a channel the way the spec says', () => {
    expect(verdictSentence(previewItem())).toBe(
      '142 videos: 118 will download, 24 skipped (Shorts 20, >1 h 4).',
    );
    expect(
      verdictSentence(
        previewItem({
          verdict: { total: 10, download: 8, skipped: 0, already_have: 2, reasons: [] },
        }),
      ),
    ).toBe('10 videos: 8 will download, 0 skipped, 2 already downloaded.');
  });

  it('talks about one video', () => {
    const video = (download: number, have: number, reasons = previewItem().verdict.reasons) =>
      verdictSentence(
        previewItem({
          kind: 'video',
          verdict: {
            total: 1,
            download,
            skipped: 1 - download - have,
            already_have: have,
            reasons,
          },
        }),
      );
    expect(video(1, 0, [])).toBe('This video will download.');
    expect(video(0, 1, [])).toBe('You already have this video.');
    expect(video(0, 0)).toBe('This video is skipped: Shorts.');
  });
});

describe('jobLabel', () => {
  it('quotes videos, names channels, and falls back to the type', () => {
    expect(jobLabel(job(1))).toBe('Download “Video 1”');
    expect(
      jobLabel(
        job(2, {
          type_label: 'Check channel',
          target: { kind: 'channel', id: 'UC1', title: 'Trains' },
        }),
      ),
    ).toBe('Check channel: Trains');
    expect(
      jobLabel(job(3, { type_label: 'Back up', target: { kind: 'none', id: null, title: null } })),
    ).toBe('Back up');
  });
});
