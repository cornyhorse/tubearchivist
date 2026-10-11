/**
 * TA's schedules are a short cron: "minute hour day-of-week" (0 = Sunday),
 * or "auto" for the backend's default. The scheduler won't run anything
 * more often than once an hour.
 */

export type SchedulePreset = 'off' | 'hourly' | 'every6h' | 'daily' | 'auto' | 'custom';

export type ParsedSchedule = { preset: SchedulePreset; time?: string };

const pad = (value: number) => String(value).padStart(2, '0');

/** recognise a schedule as one of the presets */
export const parseSchedule = (schedule: string | null | undefined): ParsedSchedule => {
  const value = (schedule ?? '').trim();
  if (!value || value === 'false') return { preset: 'off' };
  if (value === 'auto') return { preset: 'auto' };
  const parts = value.split(/\s+/);
  if (parts.length !== 3) return { preset: 'custom' };
  const [minute, hour, weekday] = parts;
  if (weekday !== '*') return { preset: 'custom' };
  if (minute === '0' && hour === '*') return { preset: 'hourly' };
  if (minute === '0' && hour === '*/6') return { preset: 'every6h' };
  if (/^\d{1,2}$/.test(minute) && /^\d{1,2}$/.test(hour)) {
    return { preset: 'daily', time: `${pad(Number(hour))}:${pad(Number(minute))}` };
  }
  return { preset: 'custom' };
};

/** the cron for a preset; null means remove the schedule */
export const presetToCron = (preset: SchedulePreset, time = '03:00'): string | null => {
  switch (preset) {
    case 'off':
      return null;
    case 'hourly':
      return '0 * *';
    case 'every6h':
      return '0 */6 *';
    case 'auto':
      return 'auto';
    case 'daily': {
      const [hour, minute] = time.split(':').map(Number);
      return `${minute} ${hour} *`;
    }
    default:
      return null;
  }
};

/** expand one cron field into the values it allows */
const expandField = (field: string, min: number, max: number): number[] | null => {
  const values = new Set<number>();
  for (const item of field.split(',')) {
    const match = item.match(/^(\*|\d+(?:-\d+)?)(?:\/(\d+))?$/);
    if (!match) return null;
    const [, range, stepText] = match;
    const step = stepText ? Number(stepText) : 1;
    if (step < 1) return null;
    let start = min;
    let end = max;
    if (range !== '*') {
      const [from, to] = range.split('-').map(Number);
      start = from;
      end = to ?? (stepText ? max : from);
    }
    if (start < min || end > max || start > end) return null;
    for (let value = start; value <= end; value += step) values.add(value);
  }
  return Array.from(values).sort((a, b) => a - b);
};

/** next time the schedule fires after `from`, in local time; null if off or unknown */
export const nextRun = (schedule: string | null | undefined, from = new Date()): Date | null => {
  const value = (schedule ?? '').trim();
  if (!value || value === 'auto' || value === 'false') return null;
  const parts = value.split(/\s+/);
  if (parts.length !== 3) return null;
  const minutes = expandField(parts[0], 0, 59);
  const hours = expandField(parts[1], 0, 23);
  const weekdays = expandField(parts[2].replace(/\b7\b/g, '0'), 0, 6);
  if (!minutes || !hours || !weekdays) return null;

  const candidate = new Date(from);
  candidate.setSeconds(0, 0);
  candidate.setMinutes(candidate.getMinutes() + 1);
  // a week of minutes covers every pattern
  for (let step = 0; step < 7 * 24 * 60; step++) {
    if (
      weekdays.includes(candidate.getDay()) &&
      hours.includes(candidate.getHours()) &&
      minutes.includes(candidate.getMinutes())
    ) {
      return candidate;
    }
    candidate.setMinutes(candidate.getMinutes() + 1);
  }
  return null;
};

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

/** "today 18:00", "tomorrow 03:00", "Saturday 08:30", or a date beyond a week */
export const formatNextRun = (when: Date, now = new Date()): string => {
  const time = `${pad(when.getHours())}:${pad(when.getMinutes())}`;
  const startOfDay = (date: Date) =>
    new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const days = Math.round((startOfDay(when) - startOfDay(now)) / 86400000);
  if (days === 0) return `today ${time}`;
  if (days === 1) return `tomorrow ${time}`;
  if (days < 7) return `${WEEKDAYS[when.getDay()]} ${time}`;
  return `${when.toLocaleDateString()} ${time}`;
};

/** a schedule in plain words, e.g. "Every 6 hours", "Daily at 03:00" */
export const describeSchedule = (schedule: string | null | undefined): string => {
  const parsed = parseSchedule(schedule);
  switch (parsed.preset) {
    case 'off':
      return 'Off';
    case 'auto':
      return 'Automatic (TA’s default)';
    case 'hourly':
      return 'Every hour';
    case 'every6h':
      return 'Every 6 hours';
    case 'daily':
      return `Daily at ${parsed.time}`;
    default:
      return `Custom: ${schedule}`;
  }
};
