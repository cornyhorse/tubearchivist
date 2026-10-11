import { describe, expect, it } from 'vitest';
import { DownloadRuleType } from '../api/actions/updateDownloadRule';
import { describeRuleParts, formatDurationShort, summarizeRule } from './describeRule';

const rule = (changes: Partial<DownloadRuleType> = {}): DownloadRuleType => ({
  enabled: true,
  min_duration: null,
  max_duration: null,
  vid_types: [],
  title_include: '',
  title_exclude: '',
  published_after: null,
  action: 'ignore',
  ...changes,
});

describe('formatDurationShort', () => {
  it.each([
    [600, '10 min'],
    [3600, '1 h'],
    [5400, '1 h 30 min'],
    [30, '1 min'],
  ])('%i seconds is %s', (seconds, text) => {
    expect(formatDurationShort(seconds)).toBe(text);
  });
});

describe('describeRuleParts', () => {
  it('names left-out types and lengths', () => {
    expect(
      describeRuleParts(rule({ vid_types: ['videos', 'streams'], min_duration: 600 })),
    ).toEqual(['No Shorts', '> 10 min']);
  });

  it('says "Only" for a single type', () => {
    expect(describeRuleParts(rule({ vid_types: ['shorts'] }))).toEqual(['Only Shorts']);
  });

  it('ignores an all-types selection', () => {
    expect(describeRuleParts(rule({ vid_types: ['videos', 'streams', 'shorts'] }))).toEqual([]);
  });

  it('covers titles, max length and date', () => {
    expect(
      describeRuleParts(
        rule({
          max_duration: 3600,
          title_include: 'train',
          title_exclude: 'live',
          published_after: '2024-01-01',
        }),
      ),
    ).toEqual(['< 1 h', 'Title has “train”', 'Title not “live”', 'After 2024-01-01']);
  });
});

describe('summarizeRule', () => {
  it('falls back to the default rule', () => {
    const summary = summarizeRule(null, rule({ vid_types: ['videos'] }));
    expect(summary.state).toBe('default');
    expect(summary.label).toBe('Default');
    expect(summary.detail).toContain('Only Videos');
  });

  it('says everything downloads without any default', () => {
    expect(summarizeRule(undefined, null).detail).toContain('Everything found is downloaded');
  });

  it('marks a switched-off rule', () => {
    expect(summarizeRule(rule({ enabled: false, min_duration: 60 }))).toMatchObject({
      state: 'off',
      label: 'All videos',
    });
  });

  it('labels an own rule and its action', () => {
    const summary = summarizeRule(rule({ max_duration: 3600, action: 'skip' }));
    expect(summary).toMatchObject({ state: 'own', label: '< 1 h' });
    expect(summary.detail).toContain('left out');
  });

  it('labels an empty own rule', () => {
    expect(summarizeRule(rule()).label).toBe('All videos');
  });
});
