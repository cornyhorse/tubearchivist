import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Job,
  jobAction,
  JobAction,
  Lane,
  listJobs,
  listLanes,
  listScheduled,
  notBuilt,
  runScheduled,
  ScheduledJob,
  updateLane,
  updateScheduled,
} from '../api/ux/client';
import Routes from '../configuration/routes/RouteList';
import { jobLabel } from '../functions/jobLabel';
import { ago, duration, inFuture } from '../functions/timeText';
import useIsAdmin from '../functions/useIsAdmin';
import { FilterChips } from '../components/ux/Controls';
import ScrollToTopOnNavigate from '../components/ScrollToTop';
import LoadingIndicator from '../components/LoadingIndicator';

const targetLink = (job: Job) => {
  if (job.target.kind === 'channel' && job.target.id) return Routes.Channel(job.target.id);
  if (job.target.kind === 'playlist' && job.target.id) return Routes.Playlist(job.target.id);
  return null;
};

/** the job's name, linked to its channel or playlist here, or to the video on YouTube */
const JobTitle = ({ job }: { job: Job }) => {
  const inside = targetLink(job);
  if (inside) return <Link to={inside}>{jobLabel(job)}</Link>;
  if (job.target.url) {
    return (
      <a href={job.target.url} target="_blank" rel="noreferrer">
        {jobLabel(job)}
      </a>
    );
  }
  return <>{jobLabel(job)}</>;
};

const ACTION_LABEL: Partial<Record<JobAction | string, [string, string]>> = {
  stop: ['Stop', 'Stop this job now'],
  cancel: ['Cancel', 'Take it out of the queue'],
  pause: ['Pause', 'Keep it in the queue, but don’t start it'],
  resume: ['Resume', 'Let it run again'],
  retry: ['Retry', 'Run it again'],
  move_to_top: ['To top', 'Run this next in its lane'],
  move_up: ['Up', 'Move up one place'],
  move_down: ['Down', 'Move down one place'],
  move_to_bottom: ['To bottom', 'Run this last in its lane'],
};

const RESULT_TEXT = (job: Job) => {
  if (job.error) return job.error;
  const result = job.result as Record<string, unknown> | null;
  if (!result) return '';
  if ('found' in result) {
    return `Found ${result.found}: ${result.allowed} allowed, ${result.ignored} ignored`;
  }
  if ('status_code' in result)
    return `Media server answered ${result.status_code} for ${result.paths} paths`;
  return Object.entries(result)
    .map(([key, value]) => `${key}: ${value}`)
    .join(', ');
};

const JobButtons = ({ job, only, onDone }: { job: Job; only: string[]; onDone: () => void }) => (
  <div className="ux-row-actions">
    {only
      .filter(action => job.actions.includes(action as JobAction))
      .map(action => {
        const [label, help] = ACTION_LABEL[action] ?? [action, action];
        return (
          <button
            key={action}
            type="button"
            className={`ux-row-button ${action === 'stop' ? 'danger' : ''}`}
            title={help}
            aria-label={`${label}: ${jobLabel(job)}`}
            onClick={async () => {
              await jobAction(job.id, action as JobAction);
              onDone();
            }}
          >
            {label}
          </button>
        );
      })}
  </div>
);

const Progress = ({ job }: { job: Job }) => {
  const progress = job.progress;
  if (!progress) return null;
  const percent = progress.fraction === null ? null : Math.round(progress.fraction * 100);
  return (
    <div className="ux-progress-row">
      <div
        className={`ux-progress ${percent === null ? 'indeterminate' : ''}`}
        role="progressbar"
        aria-label={jobLabel(job)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
        aria-valuetext={progress.message}
      >
        <span style={{ width: `${percent ?? 100}%` }} />
      </div>
      <span className="ux-muted">
        {progress.message}
        {percent !== null && ` · ${percent}%`}
        {progress.speed && ` · ${progress.speed}`}
        {progress.eta_seconds ? ` · ${duration(progress.eta_seconds)} left` : ''}
      </span>
    </div>
  );
};

type LaneFilter = 'all' | Lane['name'];

const Queue = () => {
  const isAdmin = useIsAdmin();
  const [running, setRunning] = useState<Job[]>();
  const [upNext, setUpNext] = useState<Job[]>([]);
  const [history, setHistory] = useState<Job[]>([]);
  const [lanes, setLanes] = useState<Lane[]>([]);
  const [scheduled, setScheduled] = useState<ScheduledJob[]>([]);
  const [lane, setLane] = useState<LaneFilter>('all');
  const [legacy, setLegacy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const laneParam = lane === 'all' ? undefined : lane;

  const loadRunning = useCallback(async () => {
    const response = await listJobs({ status: 'running', lane: laneParam, page_size: 50 });
    if (notBuilt(response)) {
      setLegacy(true);
      return;
    }
    setRunning(response.data?.data ?? []);
  }, [laneParam]);

  const loadRest = useCallback(async () => {
    const [next, done, laneList, schedule] = await Promise.all([
      listJobs({ status: 'queued,paused', lane: laneParam, page_size: 200 }),
      listJobs({ status: 'history', lane: laneParam, page_size: 50 }),
      listLanes(),
      listScheduled(),
    ]);
    setUpNext(next.data?.data ?? []);
    setHistory(done.data?.data ?? []);
    if (Array.isArray(laneList.data)) setLanes(laneList.data);
    if (Array.isArray(schedule.data)) setScheduled(schedule.data);
  }, [laneParam]);

  const reloadAll = useCallback(() => {
    loadRunning();
    loadRest();
  }, [loadRunning, loadRest]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reloadAll();
    const fast = setInterval(loadRunning, 2000);
    const slow = setInterval(loadRest, 10000);
    return () => {
      clearInterval(fast);
      clearInterval(slow);
    };
  }, [reloadAll, loadRunning, loadRest]);

  if (legacy) {
    return (
      <div className="boxed-content">
        <h1>Queue</h1>
        <p>
          This server doesn’t have the work queue yet. Running tasks show in the notifications on
          the <Link to={Routes.Downloads}>Found videos</Link> page.
        </p>
      </div>
    );
  }

  const visibleNext = showAll ? upNext : upNext.slice(0, 50);

  return (
    <>
      <title>TA | Queue</title>
      <ScrollToTopOnNavigate />
      <div className="boxed-content ux-wide">
        <div className="title-bar">
          <h1>Queue</h1>
          <p className="ux-help">
            Everything TA is doing or will do on its own. Checks, downloads and Emby updates run in
            separate lanes at the same time, so a long check doesn’t hold up downloads.
          </p>
        </div>

        {lanes.length > 0 && (
          <div className="ux-lanes">
            {lanes.map(item => (
              <div key={item.name} className={`ux-lane ${item.paused ? 'paused' : ''}`}>
                <strong>{item.label}</strong>
                <span className="ux-muted" title={item.description}>
                  {item.running} running · {item.queued} waiting
                  {item.paused && ' · paused'}
                </span>
                {isAdmin && (
                  <button
                    type="button"
                    className="ux-row-button"
                    aria-label={`${item.paused ? 'Resume' : 'Pause'} ${item.label}`}
                    title={
                      item.paused
                        ? 'Start picking up jobs in this lane again'
                        : 'Finish what’s running, then wait'
                    }
                    onClick={async () => {
                      await updateLane(item.name, { paused: !item.paused });
                      loadRest();
                    }}
                  >
                    {item.paused ? 'Resume this lane' : 'Pause this lane'}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        <FilterChips
          label="Lane"
          value={lane}
          onChange={setLane}
          options={[
            { value: 'all', label: 'All lanes' },
            ...lanes.map(item => ({ value: item.name, label: item.label })),
          ]}
        />

        <section className="ux-queue-section" aria-labelledby="q-running">
          <h2 id="q-running">Running now</h2>
          {running === undefined && <LoadingIndicator />}
          {running?.length === 0 && <p className="ux-muted">Nothing is running.</p>}
          <ul className="ux-job-list">
            {running?.map(job => (
              <li key={job.id} className="ux-job running">
                <div className="ux-job-main">
                  <span className="ux-job-title">
                    <JobTitle job={job} />
                  </span>
                  <span className="ux-muted">
                    started {ago(job.started_at)} ·{' '}
                    {job.created_by === 'user' ? 'you asked' : `by ${job.created_by}`}
                    {job.children > 0 && ` · ${job.children} parts`}
                  </span>
                  <Progress job={job} />
                </div>
                {isAdmin && <JobButtons job={job} only={['stop']} onDone={reloadAll} />}
              </li>
            ))}
          </ul>
        </section>

        <section className="ux-queue-section" aria-labelledby="q-next">
          <h2 id="q-next">
            Up next <span className="ux-muted">· {upNext.length}</span>
          </h2>
          {upNext.length === 0 && <p className="ux-muted">The queue is empty.</p>}
          <ol className="ux-job-list">
            {visibleNext.map(job => (
              <li key={job.id} className={`ux-job ${job.status}`}>
                <span
                  className="ux-job-pos"
                  aria-label={job.position ? `Position ${job.position}` : 'Paused'}
                >
                  {job.status === 'paused' ? '‖' : job.position}
                </span>
                <div className="ux-job-main">
                  <span className="ux-job-title">
                    <JobTitle job={job} />
                  </span>
                  <span className="ux-muted">
                    {lanes.find(item => item.name === job.lane)?.label ?? job.lane}
                    {job.priority >= 100 && ' · high priority'}
                    {job.merged > 0 && ` · asked for ${job.merged + 1} times`}
                    {job.status === 'paused' && ' · paused'}
                    {' · '}added {ago(job.created_at)}
                  </span>
                </div>
                {isAdmin && (
                  <JobButtons
                    job={job}
                    only={['move_to_top', 'move_up', 'move_down', 'pause', 'resume', 'cancel']}
                    onDone={loadRest}
                  />
                )}
              </li>
            ))}
          </ol>
          {upNext.length > 50 && (
            <button type="button" className="ux-link-button" onClick={() => setShowAll(!showAll)}>
              {showAll ? 'Show fewer' : `Show all ${upNext.length}`}
            </button>
          )}
        </section>

        <section className="ux-queue-section" aria-labelledby="q-scheduled">
          <h2 id="q-scheduled">Scheduled</h2>
          <p className="ux-help">
            Change times in <Link to={Routes.SettingsWhen}>Settings → When</Link>.
          </p>
          <table className="ux-table ux-compact">
            <caption className="sr-only">Scheduled jobs</caption>
            <thead>
              <tr>
                <th scope="col">What</th>
                <th scope="col">When</th>
                <th scope="col">Next run</th>
                <th scope="col">Last run</th>
                {isAdmin && (
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {scheduled.map(item => (
                <tr key={item.name}>
                  <td>{item.label}</td>
                  <td>{item.enabled ? item.schedule_human : 'Off'}</td>
                  <td>
                    {item.enabled && item.next_run
                      ? inFuture(new Date(item.next_run).getTime())
                      : '—'}
                  </td>
                  <td>{item.last_run ? ago(item.last_run) : '—'}</td>
                  {isAdmin && (
                    <td>
                      <div className="ux-row-actions">
                        <button
                          type="button"
                          className="ux-row-button"
                          title="Queue it now, at high priority"
                          aria-label={`Run now: ${item.label}`}
                          onClick={async () => {
                            await runScheduled(item.name);
                            reloadAll();
                          }}
                        >
                          Run now
                        </button>
                        {item.schedule && (
                          <button
                            type="button"
                            className="ux-row-button"
                            title={
                              item.enabled
                                ? 'Skip the schedule until you resume it'
                                : 'Follow the schedule again'
                            }
                            aria-label={`${item.enabled ? 'Pause' : 'Resume'}: ${item.label}`}
                            onClick={async () => {
                              await updateScheduled(item.name, { enabled: !item.enabled });
                              loadRest();
                            }}
                          >
                            {item.enabled ? 'Pause' : 'Resume'}
                          </button>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="ux-queue-section" aria-labelledby="q-history">
          <h2 id="q-history">History</h2>
          <table className="ux-table ux-compact">
            <caption className="sr-only">Finished jobs</caption>
            <thead>
              <tr>
                <th scope="col">Job</th>
                <th scope="col">Result</th>
                <th scope="col">Took</th>
                <th scope="col">Finished</th>
                {isAdmin && (
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {history.map(job => (
                <tr key={job.id} className={job.status === 'failed' ? 'failed' : undefined}>
                  <td className="ux-col-title">{jobLabel(job)}</td>
                  <td className={job.status === 'failed' ? 'ux-failed' : undefined}>
                    {job.status === 'failed'
                      ? 'Failed: '
                      : job.status === 'cancelled'
                        ? 'Cancelled. '
                        : ''}
                    {RESULT_TEXT(job)}
                  </td>
                  <td>{duration(job.duration_seconds)}</td>
                  <td>{ago(job.finished_at)}</td>
                  {isAdmin && (
                    <td>
                      <JobButtons job={job} only={['retry']} onDone={reloadAll} />
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </>
  );
};

export default Queue;
