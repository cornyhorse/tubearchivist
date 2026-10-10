# Fork design: Postgres storage and native Emby writer

Status: approach approved by Matt on 2026-10-10 (see Decisions). Base:
upstream tag `v0.5.12` (1f480944). Branch: `cornyhorse/postgres`. This is a
hard fork, and nothing goes back upstream.

Scoping lives in MattsTools `docs/architecture/tubearchivist-fork-scoping.md`.
This note covers *how* the fork is built.

## Decisions (Matt, 2026-10-10)

| Question | Decision |
|---|---|
| Storage design | **Relational Django models**, normalized. No ES-DSL translator and no JSONB document store: every ES call site gets rewritten to model/queryset code. Done right rather than fast. |
| Redis | Kept as the Celery broker and for ephemeral keys. Not questioned in review, so it stays as proposed. |
| Default Emby numbering | **Date-derived** (`season = year`, `episode = MMDD*10+n`). |
| Series folder names | `<Name> [<channel_id>]`. |
| Visibility | The fork stays a public GitHub fork, which also covers the GPL-3.0 requirement to publish source. |

## Goals

1. Replace Elasticsearch with Postgres, so the 2.75 GiB ES container goes away.
2. Keep the REST API contract, so the React frontend is unchanged.
3. Move the download queue into Postgres.
4. Write the Emby library natively, with `<episodedetails>` NFOs and a
   per-series numbering scheme, so every video is its own Emby entry.
5. Provide a one-shot importer from ES, so nothing re-downloads.

## 1. Storage: relational models

### 1.1 Schema

Postgres is Django's only database (`TA_DB_HOST/PORT/NAME/USER/PASSWORD`,
with `TA_DB_PASSWORD_FILE` supported). Users, sessions and celery-beat
move there too, replacing SQLite.

| Model | Replaces | Notes |
|---|---|---|
| `channel.Channel` | `ta_channel` | `channel_overwrites` become nullable columns (null = use the global setting). `emby_numbering` choice. |
| `video.Video` | `ta_video` | FK to `Channel`, so the copy of the channel embedded in every video and the painless "sync channel into videos" script both go away. Real columns for everything filtered or sorted: published, downloaded, type, size, duration, watched, stats, and `width`/`height` from the first video stream. JSONB only for semi-structured data read whole: `streams`, `subtitles` (file list), `sponsorblock`. |
| `video.SubtitleLine` | `ta_subtitle` | FK to `Video`. The denormalized channel and title fields come from the join instead. |
| `video.Comment` + `CommentRefresh` | `ta_comment` | One row per comment, with thread order kept as `position`. |
| `playlist.Playlist` + `PlaylistEntry` | `ta_playlist` + `video.playlist` | Entries are rows `(playlist, idx, youtube_id)`. `downloaded` is computed (`Exists(Video)`), and a video's playlist list is derived from entries, so the two duplicated arrays can't drift apart. |
| `download.Download` | `ta_download` | The queue. `channel_indexed` is computed. |
| `appsettings.ConfigDocument` | `ta_config` `appsettings`, `notify` | Settings stay as validated JSON documents, because their schema belongs to the serializers. |
| `user.UserConfig` | `ta_config` `user_<id>` | OneToOne with `Account`. |
| `emby.EpisodeNumber` | (new) | Frozen episode numbers. |

Every model has `fields_from_doc()` / `from_doc()` and `to_doc()`, which map
to and from the legacy document shape. The importer uses them, and so does
the API during the port: views can keep returning the shape the frontend
expects while their queries move to the ORM.

### 1.2 Full-text search (replaces `multi_match`)

Generated, stored `tsvector` columns with weights, plus GIN indexes:

| Model | A | B | C | D |
|---|---|---|---|---|
| `Video` | title | tags | category | description |
| `Channel` | name | tags | description | |
| `Playlist` | name | channel name | description | |
| `SubtitleLine` | line | | | |

The config is `simple` (no stemming), matching ES's standard analyzer.
Postgres' `array_to_string` is only STABLE, so the arrays go through
`ta_array_text()`, an IMMUTABLE wrapper created in `common/0001`.
`pg_trgm` GIN indexes on the title and name columns cover fuzziness.

The search UI's `bool_prefix` + `operator: and` query becomes
`to_tsquery('simple', 'w1 & w2 & w3:*')` ranked by `ts_rank_cd`. Typos fall
back to `TrigramWordSimilarity` when the tsquery finds nothing.
Video search on a channel name joins through the FK.
Subtitle highlighting uses `ts_headline` with the frontend's existing
`<span class="settings-current">` markup.

### 1.3 Aggregations (stats/src/aggs.py)

Each class becomes an ORM aggregate that returns the same dict:

| Class | ORM |
|---|---|
| `Video` | `values("vid_type").annotate(Count, Sum(media_size), Sum(duration))`, the same by `active`, plus totals |
| `Channel`, `Playlist` | `aggregate(Count("pk"), Count("pk", filter=Q(active=True)), …)` |
| `Download` | `values("status", "vid_type").annotate(Count)` |
| `WatchProgress` | `values("watched").annotate(Count, Sum(duration))` |
| `DownloadHist` | `TruncDay("date_downloaded", tzinfo=TZ)` over the last N days |
| `BiggestChannel` | `Channel.objects.annotate(Count/Sum over videos).order_by(...)[:10]` |

### 1.4 Paginated reads (`IndexPaginate`)

Whole-table walks (rescans, backup, refresh) become `.iterator(chunk_size=500)`
or keyset pagination on the primary key. Progress callbacks keep their
interface. UI pagination is `Paginator`/`LIMIT`/`OFFSET`, without ES's
10k cap.

### 1.5 Painless scripts

Each one becomes an ordinary update:

| Call site | Replacement |
|---|---|
| `index_generic.deactivate` | `.update(active=False)` |
| `channel.index` channel sync into videos | gone: the FK means there is no copy to sync |
| `playlist.index` add/remove the playlist on videos | gone: membership is `PlaylistEntry` rows |
| `yt_dlp_handler` reset auto_start | `Download.objects.reset_auto_start()` |
| `queue_interact` bulk status | `Download.objects.matching(...).set_status(...)` |
| `notify.py` URL list | `ConfigDocument` update in a transaction |
| `query_building` playlist sort | `order_by(PlaylistEntry idx)` via a subquery |
| `ta_startup` ES migrations, `index_setup` | Django migrations |

### 1.6 What ES did that changes

- Index setup and mapping checks are replaced by Django migrations.
- ES snapshots become `pg_dump` on the same schedule, and the UI toggle is
  kept.
- Backup/restore zips keep the ndjson document format via `to_doc` and
  `from_doc`.
- `refresh=true` has no equivalent and isn't needed: Postgres is
  read-your-writes.

### 1.7 Download queue

`Download.objects.next_pending(auto_only)` does
`select_for_update(skip_locked=True)` in the downloader's order
(`-auto_start, timestamp`). Status changes go through `DownloadQuerySet`.
Redis keeps progress messages, locks and the post-download list.

### 1.8 Importer

`storage/es_export.py` (stdlib only, read-only: point-in-time plus
`search_after`) writes `<index>.ndjson` files and `manifest.json`.
`manage.py ta_import_es <dir>` then:

1. checks the line counts against the manifest;
2. upserts in dependency order inside one transaction (idempotent);
3. records skips: subtitle lines of videos that aren't indexed, user
   configs of users that don't exist yet, videos with no channel, and
   channels rebuilt from embedded video docs;
4. verifies row counts, per-channel video counts, `sum(media_size)`, the
   pending downloads, playlist entries, and a round trip of sampled video
   and playlist docs (`--sample-size 5000` checks every video).

Proven on the 2026-10-10 production export: 30 channels, 1,712 videos, 16
playlists with 959 entries, 763 downloads and 46,705 subtitle lines all
round-trip. Postgres holds it in about 75 MB (ES used 30 MB of data and a
2 GB heap).

Still open: copying users and tokens from the old `db.sqlite3`.

## 2. Native Emby writer (`backend/emby/`)

### 2.1 Why

Today's builder writes `<movie>` NFOs with no numbers, so Emby parses
numbers out of the titles ("2025", "2105", "Part 2") as SxxExx and folds
clashes into "versions": CoasterFan2105 has 59 files but shows 42 entries.
It also wipes the tree every hour (about 9k refresh events per run) and
fetches thumbnails from ytimg.

### 2.2 Layout

```
<root>/channels/<Name> [<channel_id>]/
    tvshow.nfo  poster.jpg  fanart.jpg  banner.jpg       # TA cache art
    Season 2025/
        <Name> - S2025E6200 - <Title>.mp4                # hardlink
        <Name> - S2025E6200 - <Title>.nfo                # <episodedetails>
        <Name> - S2025E6200 - <Title>-thumb.jpg          # TA cache thumb
<root>/playlists/<Name> [<playlist_id>]/...
```

The filename always carries the same SxxEyy as the NFO, so Emby's filename
parser can never fold two videos together.

### 2.3 Numbering (`numbering.py`, pure functions)

The scheme is set per channel or playlist (`emby_numbering`).

| Scheme | Season | Episode | Notes |
|---|---|---|---|
| `date` (default) | upload year | `MMDD*10 + n`, where n is the next free slot that day | frozen once assigned. An 11th upload on one day takes the next free number and logs a warning. |
| `year_frozen` | upload year | next number in that year | backfills get the next number, and nothing renumbers |
| `absolute_frozen` | 1 | next number overall | |
| `playlist_position` (playlist default) | 1 | `idx + 1` | follows the playlist on purpose, so it renumbers on reorder |

Frozen numbers live in `EpisodeNumber`. They are never reused, even after a
video is deleted. Unique constraints make collisions impossible.
Dates are in the instance's `TZ`. If `published` is missing, the download
date is used instead.

### 2.4 NFOs

`<episodedetails>` carries title (no `[id]`), showtitle, season, episode,
aired, premiered, year, plot, runtime, studio and `uniqueid type=youtube`.
It has no lockdata, so later NFO edits still apply. `tvshow.nfo` has
lockdata, which stops Emby from matching the series to an online provider
by name. Output is deterministic and strips XML-invalid control characters.

### 2.5 Writer (`writer.py`)

- Media are hardlinks, written by linking to a temp name and then
  `rename()`. Cross-filesystem links fail loudly; there is no silent
  symlink fallback.
- Sidecars are written to a temp file, fsynced and renamed, and only when
  the bytes differ. Unchanged files keep their inode and mtime.
- `.ta-emby-manifest.json` records every owned file (kind, owner, role,
  inode or sha256). Owners are qualified by series, because one video can
  appear in a channel and in several playlists.
- Only manifest files are renamed or deleted, and only if they are
  unchanged since the writer wrote them. Anything else is a warning or a
  conflict and is left alone. Directories are removed only when empty and
  created by the writer.
- Renames run in two phases: everything that moves goes to a hidden temp
  name first, then into place. Swaps and cycles (a reordered playlist) are
  therefore safe and keep inodes.
- An identical existing file is adopted, so a lost manifest rebuilds with
  zero writes.
- Dry-run simulates moves and removals, so its report matches a real run.

`manage.py ta_emby_sync <root> [--channel ID | --playlist ID] [--dry-run]
[--verbose-changes]` refuses an output root inside the media dir.
`sync_all` reconciles the whole root, which also cleans up deleted series.

### 2.6 Acceptance

`emby/tests/test_acceptance_coasterfan.py` uses the 59 real upload times
of CoasterFan2105, with synthetic titles that carry the troublesome number
patterns and 8 identical titles. It checks:

- 59 `<episodedetails>` NFOs, 59 distinct season/episode pairs, 59 distinct
  media inodes, and 59 distinct SxxEyy filename stems, for every frozen
  scheme;
- that a rerun changes nothing (same inodes and mtimes);
- that a backfilled old video adds exactly its 3 files and touches nothing
  else.

With `TA_ACCEPTANCE_EXPORT=<export dir>`, the same assertions run on the
real export (real titles) through the full importer and `sync_all`.
That passed on 2026-10-10.

## 3. Testing

- `docker compose -f docker-compose.dev-pg.yml up -d` starts Postgres 17 on
  127.0.0.1:55432 and Redis on 56379. `backend/.env` (gitignored) points
  at them.
- `pytest backend --cov --cov-config=.coveragerc` enforces **100% line and
  branch coverage of fork-owned code** (the list is in `.coveragerc`). Each
  upstream file joins the list when it is ported. CI runs the same command
  against a Postgres service.

## 4. Download rules

`download.DownloadRule`: one rule per channel, plus an optional default
(channel null) for channels without their own. A disabled channel rule
switches the default off for that channel.

The conditions are min/max duration, allowed video types, a title
include regex, a title exclude regex, and an earliest upload date. Each
empty condition matches everything, and unknown values pass.

Rules apply to channel extractions: channel URLs and subscription scans.
They do not apply to single videos added by URL or to playlists, which
are explicit choices. A video that fails the rule is either queued as
`ignore` with `ignore_reason` (shown in the queue) or left out of the
queue (`skip`).

The UI is on the channel About page, with the default rule in the
application settings. The API is the channel endpoint (`download_rule`)
and `/api/download/rule/`.

## 5. Emby tree location

Hardlinks cannot cross mount points, even on the same disk, so the tree
lives inside TA's media mount: `TA_EMBY_ROOT=/youtube/.emby`. TA's
scanners skip dot folders. Any other path inside the media dir is
refused.

On apps the media is a CIFS mount of the NAS share. `link()`, atomic
replace and rename were verified over SMB3 with `seal` against Samba on
2026-10-10: one inode, two links, and the inode is kept through renames.

## 6. Status (2026-10-10)

- Elasticsearch is gone from the code. Every call site has been ported,
  and `es_connect.py`, index setup and the mapping file are deleted.
- `ta_import_sqlite`, then `ta_import_es`, make up the migration. Both
  were proven on the real export and in a full stack running the built
  image.
- Backups keep the ES bulk zip format. They also carry fork-only data:
  `emby_numbering`, download rules and frozen episode numbers.
- Snapshots are `pg_dump` runs on a daily beat schedule.
- **Acceptance in a real Emby 4.10** (scratch server, test media):
  CoasterFan2105 is 59 episodes for both an admin and a non-admin
  viewer. There are 0 multi-version entries and 0 titles with `[id]`.
- 1,113 tests. CI enforces 100% line and branch coverage of the whole
  backend, with no `pragma: no cover` anywhere.
- The `fork_image` workflow publishes `ghcr.io/cornyhorse/tubearchivist`
  on `fork-v*` tags.
- Deployment is in MattsTools: the apps playbook `flavour`, and
  `docker-compose.fork.yaml`.

## 7. Upstream tracking

Pinned at v0.5.12. For each upstream release, port by hand the yt-dlp
bumps, bgutil/PO-token changes and YouTube-breakage fixes. Skip anything
ES-specific.
