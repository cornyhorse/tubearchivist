import { useCallback, useEffect, useState } from 'react';
import Notifications from '../components/Notifications';
import PaginationDummy from '../components/PaginationDummy';
import SettingsNavigation from '../components/SettingsNavigation';
import SettingsSection from '../components/settings/SettingsSection';
import ScheduleField, { ScheduleExtra } from '../components/settings/ScheduleField';
import loadSchedule, { ScheduleResponseType } from '../api/loader/loadSchedule';
import loadScheduledJobs, { ScheduledJobType } from '../api/loader/loadScheduledJobs';
import createTaskSchedule, { TaskScheduleNameType } from '../api/actions/createTaskSchedule';
import deleteTaskSchedule from '../api/actions/deleteTaskSchedule';
import useAppSettings from '../functions/useAppSettings';
import { WHEN_FIELDS } from '../configuration/settingsFields';

type ScheduleDef = {
  name: TaskScheduleNameType;
  label: string;
  help: string;
  offText: string;
  extra?: Omit<ScheduleExtra, 'value'>;
};

const SCHEDULES: ScheduleDef[] = [
  {
    name: 'update_subscribed',
    label: 'Check channels for new videos',
    help: 'Checks every subscribed channel and playlist for new uploads. Found videos your rules allow are queued for download.',
    offText:
      'Off. Channels are only checked when you press “Check channels for new videos” on the Downloads page.',
  },
  {
    name: 'download_pending',
    label: 'Download queued videos automatically',
    help: 'Downloads every found video that isn’t ignored.',
    offText:
      'Off. Videos found by channel checks wait on the Downloads page until you press “Download all allowed”.',
  },
  {
    name: 'check_reindex',
    label: 'Refresh old videos',
    help: 'Re-reads titles, descriptions, thumbnails and counts from YouTube for videos and channels not refreshed for a while.',
    offText: 'Off. Metadata is only refreshed when you ask for it on a video, channel or playlist.',
    extra: { key: 'days', label: 'Refresh items older than', unit: 'days' },
  },
  {
    name: 'thumbnail_check',
    label: 'Check thumbnails',
    help: 'Finds missing thumbnails and downloads them again, and removes ones no longer used.',
    offText: 'Off. Missing thumbnails stay missing until you run it from Maintenance.',
  },
  {
    name: 'run_backup',
    label: 'Back up the database to a zip file',
    help: 'Writes a zip backup to cache/backup. Snapshots (in Maintenance) are faster and usually better.',
    offText: 'Off. Zip backups are only made when you start one from Maintenance.',
    extra: { key: 'rotate', label: 'Keep the newest', unit: 'backups' },
  },
];

const SettingsWhen = () => {
  const { config, save } = useAppSettings();
  const [schedules, setSchedules] = useState<ScheduleResponseType>();
  const [serverJobs, setServerJobs] = useState<ScheduledJobType[]>([]);

  const reload = useCallback(async () => {
    const { data } = await loadSchedule();
    setSchedules(data ?? []);
    try {
      const jobs = await loadScheduledJobs();
      setServerJobs(Array.isArray(jobs.data) ? jobs.data : []);
    } catch {
      setServerJobs([]);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload();
  }, [reload]);

  const saveSchedule =
    (name: TaskScheduleNameType) =>
    async (schedule: string | null, extra?: Record<string, number>) => {
      if (schedule === null) {
        await deleteTaskSchedule(name);
      } else {
        await createTaskSchedule(name, { schedule, ...(extra ? { config: extra } : {}) });
      }
      await reload();
    };

  return (
    <>
      <title>TA | When</title>
      <div className="boxed-content">
        <SettingsNavigation />
        <Notifications pageName={'all'} />
        <div className="title-bar">
          <h1>When</h1>
          <p className="ux-help">
            What TA does on its own, and when. Times are in your browser’s time zone. Frequent
            checks risk being blocked by YouTube; once or twice a day is usually enough.
          </p>
        </div>

        <SettingsSection
          id="after-checks"
          title="After a check"
          fields={WHEN_FIELDS}
          config={config}
          onSave={save}
        />

        <SettingsSection id="schedules" title="Schedules">
          {schedules === undefined && <p>Loading…</p>}
          {schedules !== undefined &&
            SCHEDULES.map(def => {
              const current = schedules.find(item => item.name === def.name);
              const server = serverJobs.find(item => item.name === def.name);
              return (
                <ScheduleField
                  key={def.name}
                  label={def.label}
                  help={def.help}
                  offText={def.offText}
                  schedule={current?.schedule}
                  serverNextRun={server?.next_run}
                  extra={
                    def.extra && {
                      ...def.extra,
                      value: current?.config?.[def.extra.key],
                    }
                  }
                  onSave={saveSchedule(def.name)}
                />
              );
            })}
        </SettingsSection>
      </div>
      <PaginationDummy />
    </>
  );
};

export default SettingsWhen;
