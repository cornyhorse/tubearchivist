import { describe, expect, it } from 'vitest';
import { describeSchedule, formatNextRun, nextRun, parseSchedule, presetToCron } from './cron';

// Saturday 10 October 2026, 14:20 local time
const now = new Date(2026, 9, 10, 14, 20, 30);

describe('parseSchedule', () => {
  it.each([
    ['', { preset: 'off' }],
    [null, { preset: 'off' }],
    ['false', { preset: 'off' }],
    ['auto', { preset: 'auto' }],
    ['0 * *', { preset: 'hourly' }],
    ['0 */6 *', { preset: 'every6h' }],
    ['30 3 *', { preset: 'daily', time: '03:30' }],
    ['0 18 *', { preset: 'daily', time: '18:00' }],
    ['30 8 */2', { preset: 'custom' }],
    ['0 */2 *', { preset: 'custom' }],
    ['* * * * *', { preset: 'custom' }],
  ])('%s', (schedule, parsed) => {
    expect(parseSchedule(schedule)).toEqual(parsed);
  });
});

describe('presetToCron', () => {
  it('builds each preset', () => {
    expect(presetToCron('off')).toBeNull();
    expect(presetToCron('hourly')).toBe('0 * *');
    expect(presetToCron('every6h')).toBe('0 */6 *');
    expect(presetToCron('auto')).toBe('auto');
    expect(presetToCron('daily', '18:05')).toBe('5 18 *');
    expect(presetToCron('daily')).toBe('0 3 *');
    expect(presetToCron('custom')).toBeNull();
  });

  it('round-trips daily times', () => {
    expect(parseSchedule(presetToCron('daily', '07:45'))).toEqual({
      preset: 'daily',
      time: '07:45',
    });
  });
});

describe('nextRun', () => {
  it('finds the next daily run today or tomorrow', () => {
    expect(nextRun('0 18 *', now)).toEqual(new Date(2026, 9, 10, 18, 0));
    expect(nextRun('0 3 *', now)).toEqual(new Date(2026, 9, 11, 3, 0));
  });

  it('handles steps, ranges, lists and weekdays', () => {
    expect(nextRun('0 */6 *', now)).toEqual(new Date(2026, 9, 10, 18, 0));
    expect(nextRun('0 * *', now)).toEqual(new Date(2026, 9, 10, 15, 0));
    expect(nextRun('15,45 9-10 1', now)).toEqual(new Date(2026, 9, 12, 9, 15));
    // 7 is Sunday too
    expect(nextRun('0 8 7', now)).toEqual(new Date(2026, 9, 11, 8, 0));
    expect(nextRun('30 8 */2', now)).toEqual(new Date(2026, 9, 11, 8, 30));
  });

  it('is never the current minute', () => {
    expect(nextRun('20 14 *', now)).toEqual(new Date(2026, 9, 11, 14, 20));
  });

  it('returns null for off, auto and nonsense', () => {
    expect(nextRun(null, now)).toBeNull();
    expect(nextRun('auto', now)).toBeNull();
    expect(nextRun('false', now)).toBeNull();
    expect(nextRun('0 0', now)).toBeNull();
    expect(nextRun('x * *', now)).toBeNull();
    expect(nextRun('61 * *', now)).toBeNull();
    expect(nextRun('5-1 * *', now)).toBeNull();
    expect(nextRun('*/0 * *', now)).toBeNull();
  });

  it('uses the current time by default', () => {
    expect(nextRun('0 * *')).toBeInstanceOf(Date);
  });
});

describe('formatNextRun', () => {
  it('names the day', () => {
    expect(formatNextRun(new Date(2026, 9, 10, 18, 0), now)).toBe('today 18:00');
    expect(formatNextRun(new Date(2026, 9, 11, 3, 5), now)).toBe('tomorrow 03:05');
    expect(formatNextRun(new Date(2026, 9, 13, 8, 30), now)).toBe('Tuesday 08:30');
    expect(formatNextRun(new Date(2026, 9, 30, 8, 30), now)).toContain('08:30');
  });

  it('defaults to now', () => {
    expect(formatNextRun(new Date())).toMatch(/^today/);
  });
});

describe('describeSchedule', () => {
  it.each([
    [null, 'Off'],
    ['auto', 'Automatic (TA’s default)'],
    ['0 * *', 'Every hour'],
    ['0 */6 *', 'Every 6 hours'],
    ['0 3 *', 'Daily at 03:00'],
    ['30 8 */2', 'Custom: 30 8 */2'],
  ])('%s', (schedule, text) => {
    expect(describeSchedule(schedule)).toBe(text);
  });
});
