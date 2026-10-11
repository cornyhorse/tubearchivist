# Work queue (jobs engine)

Step 1 of the UX build-out. It replaces the batch Celery tasks with a jobs
table in Postgres, so a subscription check no longer blocks downloads and no
request is silently dropped. The API is section 1 of
[`../ux-api-contract.md`](../ux-api-contract.md).

## Why a scan blocked downloads (before)

This was reproduced against `main` at 645132a4 with a characterization test:
3 subscribed channels, `auto_start` on, the Celery broker recorded.

1. `update_subscribed` ran **one task for every channel and playlist**
   (`SubscriptionScanner.scan` → `PendingList.parse_url_list`). Found videos
   were written to the queue after each channel, but no download was
   requested during the scan. The test recorded **zero** broker sends while
   channels 1–3 were scanned.
2. Only after the whole scan returned did it send **one**
   `download_pending(auto_only=True)`, and only if
   `subscriptions.auto_start` was on (it is off by default). With it off,
   nothing downloaded until the 16:00 schedule or a click.
3. A second "Rescan subscriptions" or "Start download" while one ran was
   **dropped**. `TaskManager.is_pending` found the earlier run, printed
   "already running" and returned `None`. Nothing was queued.
4. The workers run `--concurrency 4`, so pool slots weren't the
   bottleneck. Task design was: one batch task per kind of work, a same-name
   guard, and the download hand-off at the very end.
5. Scans sleep `sleep_interval` (default 5–15 s) per extracted video, so a
   check of many channels takes a long time, and downloads waited for all of
   it.

## What replaces it

- **`jobs.Job`**: one row per unit of work. Its fields are type, target, lane, priority, status, attempts, progress, error, result, created_by, parent, depends_on, not_before (retry backoff and debounce), and heartbeat.
- **Lanes** (`jobs.Lane`): `scan` (2 at once), `download` (1, at least 10 s between starts, as the old sleep did), `postprocess` (1) and `maintenance` (1). They run in parallel. Concurrency, pause and rate limit are set per lane.
- **`pump()`** claims due jobs per lane with `SELECT … FOR UPDATE SKIP LOCKED` and sends `run_job(id)` to Celery. It runs after every enqueue, after every job ends, from a 60 s beat entry as a safety net, and as a delayed call when a rate limit or backoff is due.
- **Merge, never drop.** A partial unique index allows one waiting (queued or paused) job per `dedupe_key` (`type:target`). A repeat request merges into it: the priority goes up and `merged` is counted. A request made while the job runs queues a new job behind it.
- **Streaming scans.** `check_subscriptions` queues one `check_channel` or `check_playlist` job per subscription. `PendingList(on_added=…)` stores each found video as soon as it's parsed. The handler then queues its `download_video` job, so downloading starts while the scan goes on. `jobs/tests/test_handlers.py::test_channel_check_downloads_while_scanning` checks this: the first video's download is running before the scan reaches the second video.
- **Post-processing** (playlists, comments, embedded metadata, then the Emby write) is one merged `post_download` job. It waits 60 s after a download, and defers itself while downloads are still queued.
- **The old task names stay.** Schedules, the old `/api/task/` endpoints and the views that call `.delay()` keep working. Each task now only queues jobs: schedules at low priority, people at high priority. Schedules pass `created_by=schedule` through their kwargs, which `ta_startup` sets.
- **Stop, cancel and pause** are cooperative. Worker classes already call `task.is_stopped()`, and `JobContext` provides it along with `send_progress()`. The old UI's redis progress messages keep working, with id `job-<id>`, and its stop button reaches the job.
- **Recovery.** At startup, running jobs go back to the queue. A running job with no heartbeat for 60 minutes is marked failed.
- `docker_assets/run.sh` now starts the worker with `--concurrency 6`. The lanes need 5 slots together, and a spare slot is left for the thin scheduler tasks.

## Not changed here

- `TaskManager`'s pending guard (`is_pending`, `init`) is unused now. It is left in place until the old task UI is gone.
- `Download.auto_start` and the `priority` status still work. "Priority" now means "queue a high priority download job".
