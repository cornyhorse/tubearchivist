import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SettingField from './SettingField';
import ScheduleField from './ScheduleField';
import { AppSettingsConfigType } from '../../api/loader/loadAppsettingsConfig';
import {
  getSetting,
  isDefault,
  SETTING_DEFAULTS,
  SettingDef,
  settingPatch,
  SPEED_FIELDS,
  SUBTITLE_FIELDS,
  CHECK_FIELDS,
} from '../../configuration/settingsFields';

const config = (): AppSettingsConfigType => ({
  subscriptions: {
    channel_size: 50,
    live_channel_size: 0,
    shorts_channel_size: 50,
    playlist_size: 50,
    auto_download_default: false,
    extract_flat: true,
  },
  downloads: {
    limit_speed: null,
    sleep_interval: 10,
    autodelete_days: null,
    format: null,
    format_sort: null,
    add_metadata: false,
    subtitle: null,
    subtitle_source: null,
    subtitle_index: false,
    comment_max: null,
    comment_sort: 'top',
    cookie_import: false,
    pot_provider_url: null,
    throttledratelimit: null,
    extractor_lang: null,
    integrate_ryd: false,
    integrate_sponsorblock: false,
  },
  application: { enable_snapshot: true, enable_cast: false },
});

const field = (path: string, list: SettingDef[]) => list.find(def => def.path === path)!;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('settings registry', () => {
  it('reads, compares to defaults and builds patches', () => {
    const value = config();
    expect(getSetting(value, 'downloads.sleep_interval')).toBe(10);
    expect(getSetting({ ...value, downloads: {} } as never, 'downloads.format')).toBeNull();
    expect(isDefault('downloads.sleep_interval', 10)).toBe(true);
    expect(isDefault('downloads.sleep_interval', 5)).toBe(false);
    expect(isDefault('downloads.format', '')).toBe(true);
    expect(isDefault('downloads.integrate_ryd', null)).toBe(true);
    expect(isDefault('application.enable_snapshot', null)).toBe(false);
    expect(settingPatch('downloads.format', 'best')).toEqual({ downloads: { format: 'best' } });
  });

  it('has a default for every field', () => {
    for (const def of [...SPEED_FIELDS, ...SUBTITLE_FIELDS, ...CHECK_FIELDS]) {
      expect(def.path in SETTING_DEFAULTS).toBe(true);
    }
  });
});

describe('SettingField', () => {
  it('labels a number field, shows the effect and the default badge', () => {
    render(
      <SettingField
        def={field('downloads.limit_speed', SPEED_FIELDS)}
        config={config()}
        onSave={vi.fn()}
      />,
    );
    const input = screen.getByRole('spinbutton', { name: /Download speed limit/ });
    expect(input.getAttribute('aria-describedby')).toBeTruthy();
    expect(screen.getByText('default')).toBeTruthy();
    expect(screen.getByText('No limit.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });

  it('saves a changed value, then can cancel and reset', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const value = config();
    value.downloads.sleep_interval = 3;
    render(
      <SettingField
        def={field('downloads.sleep_interval', SPEED_FIELDS)}
        config={value}
        onSave={onSave}
      />,
    );
    expect(screen.queryByText('default')).toBeNull();
    const input = screen.getByRole('spinbutton', { name: /Pause between requests/ });

    fireEvent.change(input, { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect((input as HTMLInputElement).value).toBe('3');

    fireEvent.change(input, { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByText('Saved')).toBeTruthy());
    expect(onSave).toHaveBeenCalledWith('downloads.sleep_interval', 20);

    fireEvent.change(input, { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reset to default' }));
    await waitFor(() => expect(onSave).toHaveBeenLastCalledWith('downloads.sleep_interval', 10));
  });

  it('saves empty text as null and reports errors', async () => {
    const onSave = vi.fn().mockRejectedValue({ message: 'bad value' });
    const value = config();
    value.downloads.subtitle = 'en';
    render(
      <SettingField
        def={field('downloads.subtitle', SUBTITLE_FIELDS)}
        config={value}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByRole('textbox', { name: /Subtitle languages/ }), {
      target: { value: '  ' },
    });
    fireEvent.submit(screen.getByRole('textbox', { name: /Subtitle languages/ }).closest('form')!);
    await waitFor(() => expect(screen.getByText('Not saved: bad value')).toBeTruthy());
    expect(onSave).toHaveBeenCalledWith('downloads.subtitle', null);
  });

  it('saves toggles and selects at once, and hides fields that need another', async () => {
    vi.useFakeTimers();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const value = config();
    const { rerender } = render(
      <SettingField
        def={field('downloads.subtitle_source', SUBTITLE_FIELDS)}
        config={value}
        onSave={onSave}
      />,
    );
    expect(screen.queryByRole('combobox')).toBeNull();

    value.downloads.subtitle = 'en';
    rerender(
      <SettingField
        def={field('downloads.subtitle_source', SUBTITLE_FIELDS)}
        config={{ ...value }}
        onSave={onSave}
      />,
    );
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox', { name: /auto-generated/ }), {
        target: { value: 'auto' },
      });
    });
    expect(onSave).toHaveBeenCalledWith('downloads.subtitle_source', 'auto');

    rerender(
      <SettingField
        def={field('subscriptions.extract_flat', CHECK_FIELDS)}
        config={value}
        onSave={onSave}
      />,
    );
    const toggle = screen.getByRole('switch', { name: /Quick checks/ });
    expect((toggle as HTMLInputElement).checked).toBe(true);
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(onSave).toHaveBeenLastCalledWith('subscriptions.extract_flat', false);
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.queryByText('Saved')).toBeNull();
  });

  it('marks dangerous fields', () => {
    const { container } = render(
      <SettingField
        def={field('downloads.autodelete_days', SPEED_FIELDS)}
        config={config()}
        onSave={vi.fn()}
      />,
    );
    expect(container.querySelector('.ux-setting.danger')).toBeTruthy();
  });
});

// Saturday 10 October 2026, 14:20
const now = new Date(2026, 9, 10, 14, 20);

describe('ScheduleField', () => {
  it('shows what happens while off', () => {
    render(
      <ScheduleField
        label="Download queued videos automatically"
        help="Downloads found videos."
        offText="Off. Found videos wait until you press Download."
        schedule={null}
        onSave={vi.fn()}
        now={now}
      />,
    );
    expect(screen.getByText('Off. Found videos wait until you press Download.')).toBeTruthy();
    expect(
      (screen.getByRole('combobox', { name: /Download queued/ }) as HTMLSelectElement).value,
    ).toBe('off');
    expect(screen.getByText('default')).toBeTruthy();
  });

  it('shows the next run from the cron, or from the server', () => {
    const { rerender } = render(
      <ScheduleField
        label="Check"
        help="h"
        offText="off"
        schedule="0 18 *"
        onSave={vi.fn()}
        now={now}
      />,
    );
    expect(screen.getByText('Daily at 18:00. Next run: today 18:00.')).toBeTruthy();
    rerender(
      <ScheduleField
        label="Check"
        help="h"
        offText="off"
        schedule="0 18 *"
        serverNextRun={new Date(2026, 9, 11, 3, 0).toISOString()}
        onSave={vi.fn()}
        now={now}
      />,
    );
    expect(screen.getByText('Daily at 18:00. Next run: tomorrow 03:00.')).toBeTruthy();
  });

  it('says when TA picks the time', () => {
    render(
      <ScheduleField label="C" help="h" offText="off" schedule="auto" onSave={vi.fn()} now={now} />,
    );
    expect(screen.getByText('Automatic (TA’s default). Next run time is set by TA.')).toBeTruthy();
  });

  it('saves a daily preset with its time', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <ScheduleField
        label="Check"
        help="h"
        offText="off"
        schedule={null}
        onSave={onSave}
        now={now}
      />,
    );
    fireEvent.change(screen.getByRole('combobox', { name: /Check/ }), {
      target: { value: 'daily' },
    });
    fireEvent.change(screen.getByLabelText('Check: time of day'), { target: { value: '06:30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByText('Saved')).toBeTruthy());
    expect(onSave).toHaveBeenCalledWith('30 6 *', undefined);
  });

  it('turns a schedule off and saves custom crons with extra config', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { unmount } = render(
      <ScheduleField label="Back up" help="h" offText="off" schedule="0 3 *" onSave={onSave} />,
    );
    fireEvent.change(screen.getByRole('combobox', { name: /Back up/ }), {
      target: { value: 'off' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith(null, undefined));
    unmount();

    render(
      <ScheduleField
        label="Refresh"
        help="h"
        offText="off"
        schedule="30 8 */2"
        extra={{ key: 'days', label: 'Older than', unit: 'days', value: 90 }}
        onSave={onSave}
      />,
    );
    expect(screen.getByText(/minute, hour and weekday/i)).toBeTruthy();
    const days = screen.getByRole('spinbutton', { name: /Older than/ });
    fireEvent.change(days, { target: { value: '30' } });
    fireEvent.change(screen.getByLabelText('Refresh: custom schedule'), {
      target: { value: '0 4 1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSave).toHaveBeenLastCalledWith('0 4 1', { days: 30 }));

    fireEvent.change(days, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSave).toHaveBeenLastCalledWith('0 4 1', undefined));
  });

  it('shows a save error', async () => {
    const onSave = vi.fn().mockRejectedValue({ message: 'more than once per hour' });
    render(<ScheduleField label="C" help="h" offText="off" schedule={null} onSave={onSave} />);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'hourly' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(screen.getByText('Not saved: more than once per hour')).toBeTruthy(),
    );
  });
});

describe('channel check wording', () => {
  it('explains 0, empty and a number', () => {
    const effect = field('subscriptions.live_channel_size', CHECK_FIELDS).effect!;
    expect(effect(0)).toBe('Livestreams are not checked.');
    expect(effect(null)).toContain('every one');
    expect(effect(20)).toBe('Looks at the newest 20 livestreams.');
  });
});
