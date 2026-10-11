import { describe, expect, it } from 'vitest';
import { ago, duration, inFuture } from './timeText';

const now = new Date(2026, 9, 10, 14, 0).getTime();
const minutes = (m: number) => now - m * 60000;

describe('ago', () => {
  it.each([
    [null, ''],
    [undefined, ''],
    [minutes(0.5), 'just now'],
    [minutes(5), '5 min ago'],
    [minutes(180), '3 h ago'],
    [minutes(60 * 24), 'yesterday'],
    [minutes(60 * 24 * 4), '4 days ago'],
  ])('%s', (when, text) => {
    expect(ago(when as number, now)).toBe(text);
  });

  it('shows a date after a week, and future times', () => {
    expect(ago(minutes(60 * 24 * 10), now)).toContain('2026');
    expect(ago(now + 5 * 60000, now)).toBe('in 5 min');
    expect(ago(new Date())).toBe('just now');
  });
});

describe('inFuture', () => {
  it('names the time', () => {
    expect(inFuture(now + 20000, now)).toBe('any moment');
    expect(inFuture(now + 3 * 3600000, now)).toBe('today 17:00');
    expect(inFuture(new Date(2026, 9, 11, 3, 5).getTime(), now)).toBe('tomorrow 03:05');
    expect(inFuture(new Date(2026, 9, 20, 3, 5).getTime(), now)).toContain('03:05');
    expect(inFuture(Date.now() + 30 * 60000)).toBe('in 30 min');
  });
});

describe('duration', () => {
  it.each([
    [null, ''],
    [undefined, ''],
    [42, '42 s'],
    [95, '1 min 35 s'],
    [120, '2 min'],
    [3720, '1 h 2 min'],
    [7200, '2 h'],
  ])('%s', (seconds, text) => {
    expect(duration(seconds as number)).toBe(text);
  });
});
