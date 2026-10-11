import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getQueueSummary, notBuilt, QueueSummary } from '../api/ux/client';
import Routes from '../configuration/routes/RouteList';

/** "⟳ 2 running · 14 queued" in the header; opens the Queue page */
const QueueIndicator = ({ interval = 10000 }: { interval?: number }) => {
  const [summary, setSummary] = useState<QueueSummary | null>(null);

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const response = await getQueueSummary();
        if (notBuilt(response)) {
          stopped = true;
          return;
        }
        setSummary(response.data ?? null);
      } catch {
        // keep the last known numbers
      }
    };
    load();
    const timer = setInterval(() => {
      if (!stopped && document.visibilityState !== 'hidden') load();
    }, interval);
    return () => clearInterval(timer);
  }, [interval]);

  if (!summary) return null;
  const busy = summary.running > 0;
  const text =
    busy || summary.queued ? `${summary.running} running · ${summary.queued} queued` : 'Idle';

  return (
    <Link
      to={Routes.Queue}
      className={`ux-queue-indicator ${busy ? 'busy' : ''}`}
      title="Open the queue: what TA is doing now and next"
    >
      <span aria-hidden="true" className={busy ? 'spin' : ''}>
        ⟳
      </span>{' '}
      {text}
      {summary.failed_24h > 0 && (
        <span className="ux-queue-failed" title="Jobs that failed in the last 24 hours">
          {' '}
          · {summary.failed_24h} failed
        </span>
      )}
    </Link>
  );
};

export default QueueIndicator;
