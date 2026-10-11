import { useId, useState } from 'react';
import {
  describeSchedule,
  formatNextRun,
  nextRun,
  parseSchedule,
  presetToCron,
  SchedulePreset,
} from '../../functions/cron';
import { SettingRow } from './SettingField';

export type ScheduleExtra = {
  /** config key sent with the schedule, e.g. "days" */
  key: 'days' | 'rotate';
  label: string;
  unit: string;
  value: number | undefined;
};

type ScheduleFieldProps = {
  label: string;
  help: string;
  /** what happens while the schedule is off */
  offText: string;
  schedule: string | null | undefined;
  /** next run as the server computes it, when the server sends one */
  serverNextRun?: string | null;
  extra?: ScheduleExtra;
  onSave: (schedule: string | null, extra?: Record<string, number>) => Promise<void>;
  /** for tests */
  now?: Date;
};

const PRESETS: { value: SchedulePreset; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'hourly', label: 'Every hour' },
  { value: 'every6h', label: 'Every 6 hours' },
  { value: 'daily', label: 'Daily at…' },
  { value: 'auto', label: 'TA’s default' },
  { value: 'custom', label: 'Custom…' },
];

/** a schedule as presets with the next run time, instead of a bare cron box */
const ScheduleField = ({
  label,
  help,
  offText,
  schedule,
  serverNextRun,
  extra,
  onSave,
  now,
}: ScheduleFieldProps) => {
  const id = useId();
  const helpId = useId();
  const current = parseSchedule(schedule);
  const [preset, setPreset] = useState<SchedulePreset>(current.preset);
  const [time, setTime] = useState(current.time ?? '03:00');
  const [custom, setCustom] = useState(current.preset === 'custom' ? (schedule ?? '') : '');
  const [extraValue, setExtraValue] = useState<number | undefined>(extra?.value);
  const [status, setStatus] = useState('');

  const draftCron = preset === 'custom' ? custom.trim() || null : presetToCron(preset, time);
  const savedCron = current.preset === 'off' ? null : (schedule ?? null);
  const dirty =
    draftCron !== savedCron || (extra !== undefined && (extraValue ?? 0) !== (extra.value ?? 0));

  const save = async () => {
    setStatus('Saving…');
    try {
      await onSave(
        draftCron,
        extra && extraValue !== undefined ? { [extra.key]: extraValue } : undefined,
      );
      setStatus('Saved');
      setTimeout(() => setStatus(''), 2500);
    } catch (error) {
      const message = (error as { message?: string })?.message;
      setStatus(`Not saved: ${message ?? 'something went wrong'}`);
    }
  };

  let effect: string;
  if (current.preset === 'off') {
    effect = offText;
  } else {
    const next = serverNextRun ? new Date(serverNextRun) : nextRun(schedule, now);
    effect = `${describeSchedule(schedule)}. ${
      next ? `Next run: ${formatNextRun(next, now)}.` : 'Next run time is set by TA.'
    }`;
  }

  return (
    <SettingRow
      label={label}
      help={help}
      htmlFor={id}
      isDefault={current.preset === 'off' || current.preset === 'auto'}
      effect={effect}
      status={status}
      helpId={helpId}
    >
      <form
        className="ux-inline-form"
        onSubmit={event => {
          event.preventDefault();
          if (dirty) save();
        }}
      >
        <select
          id={id}
          aria-describedby={helpId}
          value={preset}
          onChange={event => setPreset(event.target.value as SchedulePreset)}
        >
          {PRESETS.map(option => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        {preset === 'daily' && (
          <input
            type="time"
            aria-label={`${label}: time of day`}
            value={time}
            onChange={event => setTime(event.target.value)}
          />
        )}
        {preset === 'custom' && (
          <input
            type="text"
            aria-label={`${label}: custom schedule`}
            placeholder="minute hour weekday, e.g. 30 8 */2"
            value={custom}
            onChange={event => setCustom(event.target.value)}
          />
        )}
        {extra && preset !== 'off' && (
          <label className="ux-extra">
            {extra.label}
            <input
              type="number"
              min={0}
              value={extraValue ?? ''}
              onChange={event =>
                setExtraValue(event.target.value === '' ? undefined : Number(event.target.value))
              }
            />
            {extra.unit}
          </label>
        )}
        {dirty && <button type="submit">Save</button>}
      </form>
      {preset === 'custom' && (
        <p className="ux-setting-help">
          Minute, hour and weekday (0 = Sunday). <code>30 8 */2</code> runs at 08:30 every other
          day. TA won’t run a schedule more than once an hour.
        </p>
      )}
    </SettingRow>
  );
};

export default ScheduleField;
