# Fork design: Postgres storage and native Emby writer

Status: draft for Matt's review, 2026-10-10. Base: upstream tag `v0.5.12` (1f480944).
Branch: `cornyhorse/postgres`. This is a hard fork; nothing goes back upstream.

Scoping and decisions live in MattsTools `docs/architecture/tubearchivist-fork-scoping.md`.
This note covers *how*.

## Goals and non-goals

Goals:

1. Replace Elasticsearch with Postgres for all seven `ta_*` indices, so the
   2.75 GiB ES container goes away.
2. Keep the REST API and the React frontend unchanged. The API returns the
   same ES-shaped payloads (`_index`, `_source`, `_score`, `hits.total.value`,
   `aggregations`) so the frontend never notices.
3. Move the download queue into Postgres and drop Redis if Celery allows it.
4. Write the Emby library natively, with `<episodedetails>` NFOs and a
   per-series numbering scheme, so every video is its own Emby entry.
5. A one-shot importer from an ES export, so the 776 GB of media is untouched
   and nothing re-downloads.

Non-goals: changing the download pipeline, yt-dlp handling or the UI;
matching ES relevance scores exactly; running both stores at once in
production.

## 1. Storage layer

### 1.1 Shape

```
backend/storage/
  base.py        DocumentStore interface + UnsupportedQuery
  schema.py      index catalog: field types, array fields, search fields
  sql/           DDL migrations (plain SQL, applied in order)
  dsl.py         compiler: the ES query DSL subset TA uses -> SQL
  pg_store.py    Postgres DocumentStore (psycopg 3, JSONB)
  es_store.py    ES DocumentStore (thin wrapper over ElasticWrap)
  repos.py       typed repositories: VideoRepo, ChannelRepo, DownloadRepo, ...
  importer.py    ES export (ndjson) -> Postgres
  es_export.py   read-only ES -> ndjson exporter (stdlib only)
```

`get_store()` picks the backend from `TA_STORAGE=es|postgres` (default `es`
until the fork ships, then `postgres`).

### 1.2 Why a DSL compiler, not a rewrite of every call site

There are about 94 `ElasticWrap` and 24 `IndexPaginate` call sites in 27
files. Rewriting all of them into hand-written SQL at once is a big-bang
change that is hard to test and makes porting upstream fixes painful.

TA uses a small, closed subset of the ES DSL. A grep of the backend gives:
`term` (62), `bool` with `must`/`must_not`/`should`/`filter` (28),
`terms` (18), `match` (17), `aggs` (17, mostly `terms`/`sum`/`value_count`),
`sort` (16), painless `script` (12), `exists` (9), `match_all` (9),
`multi_match` (6), `range` (5), `prefix` (2), one `date_histogram`, one
`highlight`.

So the plan is two layers:

- **Compiler (breadth).** `dsl.py` turns that subset into parameterised SQL
  over JSONB. Anything outside the subset raises `UnsupportedQuery`. It never
  silently returns wrong results. That lets the whole app run on Postgres
  early, with call sites unchanged.
- **Repositories (depth).** Hot paths and anything ES-specific get typed
  methods with hand-written SQL: the download queue, painless-script updates,
  stats aggregations, search. Call sites move from `ElasticWrap` to repos one
  file at a time, each with tests. When the last one moves, `ElasticWrap`,
  `IndexPaginate` and `dsl.py`'s compat entry points are deleted.

During the transition `ElasticWrap` becomes a façade. When
`TA_STORAGE=postgres` it routes by path (`{index}/_doc/{id}`, `_search`,
`_count`, `_update`, `_bulk`, `_update_by_query`, `_delete_by_query`,
`_refresh`, `_pit`) to the store and returns ES-shaped responses. ES admin
endpoints (`_snapshot`, `_mapping`, `_aliases`, `_nodes`, index setup) become
no-ops or are replaced (see 1.9).

### 1.3 Schema: one JSONB table per index

```sql
CREATE TABLE ta_video (
    id         text PRIMARY KEY,           -- ES _id (youtube_id)
    doc        jsonb NOT NULL,             -- the ES _source, unchanged
    search     tsvector GENERATED ALWAYS AS (ta_video_tsv(doc)) STORED,
    updated_at timestamptz NOT NULL DEFAULT now()
);
```

Same shape for `ta_channel`, `ta_playlist`, `ta_download`, `ta_subtitle`,
`ta_comment` and `ta_config`. `doc` keeps the exact ES `_source`, so
serializers, the frontend and the importer need no field mapping.

Indexes, from the queries the code actually runs:

| Table | Index |
|---|---|
| all | `GIN (search)` where the table has one |
| `ta_video` | `(doc #>> '{channel,channel_id}')`, `ta_ts(doc->'published')`, `ta_ts(doc->'date_downloaded')`, `(doc->>'vid_type')`, `GIN (doc jsonb_path_ops)` for array containment (playlist ids, stream heights) |
| `ta_download` | `(doc->>'status', (doc->>'auto_start')::bool DESC, ta_ts(doc->'timestamp'))` for the queue head, `(doc->>'channel_id')` |
| `ta_subtitle` | `(doc->>'youtube_id')`, `(doc->>'subtitle_channel_id')` |
| `ta_playlist` | `(doc->>'playlist_channel_id')` |
| `ta_comment` | `(doc->>'comment_channel_id')` |

`ta_ts(jsonb)` is an `IMMUTABLE` SQL helper that turns ES date values into
`timestamptz`. ES stores dates as epoch seconds or ISO strings
(`published` is `epoch_second||strict_date_optional_time`), and the helper
accepts both, always in UTC.

Why not typed columns: JSONB keeps the import lossless, keeps upstream ports
cheap (a new field upstream needs no migration), and at our size (about
1,700 videos and 47,000 subtitle lines) expression indexes are plenty.
If a field becomes hot later it can become a generated column without
changing callers.

### 1.4 Field semantics in the compiler

`schema.py` holds a catalog per index, derived from
`appsettings/index_mapping.json`: the field type (`keyword`, `text`,
`boolean`, `long`/`short`/`integer`/`float`, `date`, `object`) plus the
list of fields that hold arrays (`tags`, `playlist`, `category`, `streams`,
`subtitles`, `channel_tags`, `channel_tabs`, `playlist_entries`,
`comment_comments`, `sponsorblock.segments`). ES has no array type, so that
list is explicit.

- A `.keyword` suffix is stripped. `term` on `title.keyword` is an exact
  match on `title`.
- Scalar field, exact match: `(doc #>> '{a,b}') = %s`, cast by type
  (`::boolean`, `::numeric`, `ta_ts()` for dates). This can use the btree
  expression indexes.
- Field under an array: `jsonb_path_exists(doc, '$.a.b ? (@ == $v)', vars)`.
  Lax mode unwraps arrays, which matches ES semantics: a term matches if
  any element matches.
- `exists`: the path is present and not null (and not an empty array, as in
  ES).
- `range`: `gte`/`gt`/`lte`/`lt` with type-aware casts. Date math
  `now-Nd` is supported because the code uses it (autodelete, rescans).
- `match` on keyword/boolean/number fields behaves like `term` (ES analyses
  nothing there). `match` on a text field becomes a full-text match
  (1.5).
- `sort`: by field with type-aware casts, plus `_doc`, `_score`, and the one
  painless `_script` sort (playlist position), which becomes
  `array_position()` over a parameter array. Missing values sort last, as in
  ES.
- `from`/`size`: `OFFSET`/`LIMIT`. ES caps results at 10,000 and
  `Pagination` knows that. The compiler applies the same cap so the UI
  behaves the same.
- `hits.total.value`: `count(*) OVER ()` in the same query.
- `_source` filtering: includes/excludes applied in Python on the result.

### 1.5 Full-text search: tsvector replaces multi_match

Each searchable table gets a generated `search` column with weights:

| Table | A | B | C |
|---|---|---|---|
| `ta_video` | `title` | `channel.channel_name`, `tags` | `category`, `description` |
| `ta_channel` | `channel_name` | `channel_tags` | `channel_description` |
| `ta_playlist` | `playlist_name` | `playlist_channel` | `playlist_description` |
| `ta_subtitle` | `subtitle_line` | | |

The text search config is `simple` (no stemming, no stop words). Titles are
multilingual, and ES used the standard analyzer, which also does no stemming.

The search UI sends `multi_match` with `type: bool_prefix`,
`operator: and` and `fuzziness: auto`. The repo method builds:

```sql
WHERE search @@ to_tsquery('simple', 'word1 & word2 & word3:*')
ORDER BY ts_rank_cd(search, q) DESC
```

Every word is required (`operator: and`) and the last word is a prefix
(`bool_prefix`), which is what search-as-you-type gives in ES today.

Fuzziness: if the tsquery finds nothing and fuzziness is not `no`, fall back
to `pg_trgm` `word_similarity()` against the A-weight text (title, channel
name, playlist name) with a threshold near ES `auto` (edit distance 1–2).
That covers typos without the n-gram subfields.

The full-text (`full:`) subtitle search needs highlighting.
`ts_headline('simple', line, q, 'StartSel=<span class="settings-current">,
StopSel=</span>, HighlightAll=true')` gives the same markup the frontend
renders today.

Scores differ from ES BM25. Only the order matters and the UI shows the
score as a number, which is acceptable.

### 1.6 Aggregations (stats and filters)

`stats/src/aggs.py` has 9 aggregation classes, and `views_base.get_aggs`
plus a few views use small `terms` aggs. Each becomes one SQL query in a
`StatsRepo`, returning the same dict shape the `process()` methods produce.
Examples:

```sql
-- Video: totals by vid_type and by active
SELECT doc->>'vid_type' AS key, count(*) AS doc_count,
       sum((doc->>'media_size')::bigint) AS media_size,
       sum((doc#>>'{player,duration}')::bigint) AS duration
FROM ta_video GROUP BY ROLLUP (1);
```

| aggs.py class | SQL |
|---|---|
| `Video` | `GROUP BY GROUPING SETS ((vid_type), (active), ())` with `sum(media_size)`, `sum(player.duration)`, `count(*)` |
| `Channel`, `Playlist` | `count(*)` plus `count(*) FILTER (WHERE active)` / `FILTER (WHERE subscribed)` |
| `Download` | `GROUP BY status, vid_type` |
| `WatchProgress` | `GROUP BY (player.watched)` with `sum(duration)`, plus the partial-progress list from Redis/PG |
| `DownloadHist` (`date_histogram` by day) | `GROUP BY date_trunc('day', ta_ts(date_downloaded) AT TIME ZONE $tz)` over the last N days |
| `BiggestChannel` | `GROUP BY channel.channel_id, channel.channel_name ORDER BY {doc_count|duration|media_size} DESC LIMIT 10` |

The compiler also supports generic `terms`/`sum`/`value_count`/`max`/`min`
aggs for the transition, so the views work before every class is
hand-ported.

### 1.7 Paginated reads (IndexPaginate)

`IndexPaginate` uses ES point-in-time plus `search_after` to walk whole
indices (rescans, backups, reindex, playlist and channel refresh). In Postgres
this becomes keyset pagination on the primary key:

```sql
SELECT id, doc FROM ta_video WHERE <compiled query> AND id > %(after)s
ORDER BY id LIMIT 500;
```

The callback and progress-notification arguments stay as they are. With an
explicit sort, keyset uses `(sort_expr, id)`. There is no PIT to create or
clean up. A `REPEATABLE READ` transaction gives the same snapshot guarantee
when a caller needs it.

### 1.8 Painless scripts

The 12 painless scripts are all simple field sets, list appends or list
removals. Each becomes a repo method that runs one SQL statement:

| Call site | Script | SQL |
|---|---|---|
| `index_generic.deactivate` | `ctx._source.active = false` | `doc = jsonb_set(doc, '{active}', 'false')` |
| `channel.index` sync channel into videos | `ctx._source.channel = params.channel` | `UPDATE ta_video SET doc = jsonb_set(doc,'{channel}',%s) WHERE doc#>>'{channel,channel_id}'=%s` |
| `playlist.index` add/remove playlist id | list append/remove | `jsonb_set(doc,'{playlist}', (doc->'playlist') - %s || ...)` |
| `yt_dlp_handler` reset auto_start | field set | `UPDATE ta_download SET doc = doc || '{"auto_start": false}' WHERE ...` |
| `queue_interact` status changes | field set by query | `doc || jsonb_build_object(...)` |
| `notify.py` URL list add/remove | upsert list | `INSERT ... ON CONFLICT DO UPDATE` |
| `ta_startup` migrations, `index_setup` reindex | one-off migrations | replaced by SQL migrations (1.9) |
| `query_building` playlist sort | sort script | `array_position(%s::text[], id)` |

### 1.9 Things ES did that go away or change

- **Index setup and mapping checks** (`index_setup.py`): replaced by numbered
  SQL migrations in `storage/sql/`, applied at startup by `ta_startup`, with
  a `ta_schema_version` table.
- **ES snapshots** (`snapshot.py`, the snapshot UI): replaced by `pg_dump`
  of the `ta` database to the existing backup directory on the same schedule.
  The snapshot settings UI keeps its toggle and labels it "database backup".
- **Backup/restore to zip** (`backup.py`): keeps the ndjson format, read
  from and written to Postgres. Old ES backups stay restorable.
- **Connection check** (`ta_connection`): checks Postgres instead of ES.
- **`refresh=true`**: Postgres is read-your-writes, so `_refresh` is a
  no-op.

### 1.10 Download queue

Today the queue is `ta_download` in ES. `_get_next` sorts
`auto_start desc, timestamp asc` among `status=pending` items with no
`message`. Redis holds per-download progress, locks and the post-processing
list `ta:download:video`.

In Postgres:

```sql
-- claim the next item; safe with several workers
SELECT id, doc FROM ta_download
WHERE doc->>'status' = 'pending' AND NOT doc ? 'message'
  AND (NOT %(auto_only)s OR (doc->>'auto_start')::boolean)
ORDER BY (doc->>'auto_start')::boolean DESC, ta_ts(doc->'timestamp')
LIMIT 1 FOR UPDATE SKIP LOCKED;
```

TA downloads in one worker, so `SKIP LOCKED` is a safety net, not a
throughput feature. Status changes (`pending`/`priority`/`ignore`, clearing
messages, bulk delete by channel) become `DownloadRepo` methods.

**Redis.** Celery 5 needs a broker. The Postgres options are the
`sqlalchemy` transport (kombu, documented as experimental) or replacing
Celery. TA also uses Redis for task progress messages, locks, user progress
and settings caches (`common/src/ta_redis.py`, about 40 uses). Plan: **keep
Redis as the Celery broker and for those ephemeral keys** in this fork phase.
It is about 10 MB of RAM, nothing worth persisting lives there, and moving
it has real risk with little gain. The download queue state itself (what is
pending, what failed) moves to Postgres, so losing Redis loses nothing that
matters. Revisit after the fork ships.

### 1.11 Django database

Django still uses SQLite for users, sessions, tokens and celery-beat
schedules. With Postgres already present, `DATABASES` switches to the same
Postgres (`TA_PG_*` env), and the importer copies the SQLite rows with
`dumpdata`/`loaddata`. Document tables live in the same database, outside
Django's ORM, managed by `storage/sql/`.

### 1.12 Importer

`python manage.py ta_import_es <export_dir>` reads the ndjson files written
by `storage.es_export` (one per index, `{"_id", "_source"}` per line, plus
`manifest.json` with counts). For each index it:

1. checks the line count against the manifest;
2. `COPY`s into a staging table, then `INSERT ... ON CONFLICT (id) DO
   UPDATE` into the real table, so a rerun is idempotent;
3. verifies row counts plus spot checks: per-channel video counts,
   `sum(media_size)`, pending download count, and a sample of 50 random docs
   compared byte-for-byte after JSON normalisation;
4. prints a report and exits non-zero on any mismatch.

It works offline from a snapshot. Production ES is only read, by
`es_export`. ES stays untouched until the fork is accepted, which is what
makes rollback a single image switch.

### 1.13 Testing

- Unit tests for `dsl.py`: SQL text for every construct in 1.4, plus
  `UnsupportedQuery` for anything else.
- Integration tests against a throwaway Postgres
  (`docker-compose.dev-pg.yml`, port 55432). Each test runs in its own
  schema that is dropped afterwards, so tests never share state.
- Parity fixtures: synthetic documents shaped like real TA docs, the real
  query bodies from the code (search, queue, filters, stats), and asserted
  results.
- Importer test on a small synthetic export, then on the real export
  (counts and spot checks from 1.12).

## 2. Native Emby writer

### 2.1 What goes wrong today

`build_tubearchivist_emby_view.py` (MattsTools) wipes and rebuilds the whole
derived tree every hour, writes `<movie>` NFOs with no season or episode,
and fetches thumbnails from ytimg. Emby reads numbers out of the filenames
as SxxExx, folds clashes into "versions" (CoasterFan2105: 59 files, 42
entries), shows `[id]` in titles, and churns item IDs.

### 2.2 Shape

```
backend/emby/
  numbering.py   numbering schemes, pure functions plus a state file
  nfo.py         <tvshow>/<season>/<episodedetails> XML
  writer.py      reconcile a desired tree against disk, write on change only
  manifest.py    record of owned files (path, kind, sha256, inode)
```

Runs as a Celery task after downloads and metadata refreshes, plus a
manual "rebuild Emby view" action and a `--dry-run` management command that
prints the diff.

### 2.3 Layout

```
<emby_root>/channels/<Channel Name> [<channel_id>]/
    tvshow.nfo
    poster.jpg  fanart.jpg                    # from TA cache
    Season 2024/
        <Channel Name> - S2024E0012 - <safe title>.mp4   # hardlink
        <Channel Name> - S2024E0012 - <safe title>.nfo
        <Channel Name> - S2024E0012 - <safe title>-thumb.jpg
<emby_root>/playlists/<Playlist Name> [<playlist_id>]/...   # same pattern
```

The filename carries the SxxExx that the NFO also states, so Emby agrees
even before it reads the NFO, and two videos can never parse to the same
number. Because the youtube id is in `<uniqueid>`, the folder name keeps
the channel id (renaming a channel does not move the folder), and the file
names keep no id.

### 2.4 Numbering (per series setting)

Stored on the channel/playlist doc as `emby_numbering`, default
`year_frozen`. The assigned numbers live in Postgres table
`emby_episode_number (series_id, youtube_id, scheme, season, episode)`.
Once a number is assigned it is never reassigned, except by an explicit
"renumber series" action.

| Scheme | Season | Episode | Behaviour on backfill |
|---|---|---|---|
| `year_frozen` (default) | upload year | 1, 2, 3… in order of first sighting within the year, ties broken by `published`, then `youtube_id` | an older video gets the next free number in its year; nothing renumbers |
| `date` | upload year | `MMDD*10 + n` (n = 0–9 for same-day uploads, ordered by youtube_id) | stable without state; gaps in numbering |
| `absolute_frozen` | 1 | 1…N across all years, frozen | backfill gets N+1 |
| `playlist_position` (playlists) | 1 | playlist `idx + 1` | follows the playlist; renumbering follows upstream reordering |

Rules for `date`: an 11th upload on the same day overflows to `n = 9`
plus a deterministic bump into the next free slot of that day's block, and
is logged. In practice it never happens. `year_frozen` and
`absolute_frozen` are seeded in `published, youtube_id` order the first
time a series is built, so the first build of an existing channel is in
date order.

Uniqueness is enforced by a `UNIQUE (series_id, scheme, season, episode)`
constraint, so a collision is impossible rather than unlikely.

### 2.5 NFOs

`<episodedetails>` with `title` (the TA title, no `[id]`), `showtitle`,
`season`, `episode`, `aired`, `premiered` (upload date, `YYYY-MM-DD`),
`plot` (description), `runtime` (minutes), `studio` (channel),
`uniqueid type="youtube" default="true"`, and `<thumb>` pointing at the
local sidecar. `tvshow.nfo` carries the channel name, description, a
`uniqueid` with the channel id, and the `lockdata` flag so Emby keeps the
name. Output is deterministic (fixed element order, no timestamps), so the
same input produces the same bytes.

### 2.6 Writes

- **Desired state first.** Build the full list of (path → content source)
  for the series, then reconcile.
- **Media:** hardlink from the TA media dir. If the link exists with the
  same inode, do nothing. If the inode differs (file re-downloaded), relink
  atomically by linking to a temp name, then `rename()`.
- **NFO and thumbs:** render to bytes, compare with what is on disk, and
  write only if they differ, via a temp file in the same directory, `fsync`,
  then `rename()`. Unchanged files keep their mtime and inode, so Emby's
  monitor stays quiet.
- **Thumbs** come from TA's cache (`/cache/videos/<x>/<id>.jpg`,
  `/cache/channels/<id>_thumb.jpg`, `_banner`, `_tvart`). There is never a
  network fetch. A missing cache file means no sidecar plus a warning.
- **Manifest** (`.ta-emby-manifest.json` at the root, also atomic) lists
  every file the writer owns with its kind and hash. Deletion only removes
  manifest-listed files whose video is gone or whose number changed after an
  explicit renumber. It never deletes anything not in the manifest, never
  `rmtree`s, and removes a directory only if it is empty and the writer
  created it.
- **Renames:** when the title changes, the old trio is renamed (`rename()`,
  same inode) to the new name instead of delete plus create. Emby sees a
  move rather than a new item.

### 2.7 Acceptance

Unit-level, no Emby needed: build CoasterFan2105 (59 videos, from the ES
export) into a scratch root with each scheme. Assert 59 distinct
`(season, episode)` pairs, 59 media links, 59 `<episodedetails>` NFOs,
titles with no `[id]`, a second run that changes 0 files (mtimes and inodes
unchanged), and an added backfilled video that changes only its own three
files.

Optional end-to-end: a scratch Emby container on the Mac pointed at the
scratch root (read-only sample media). Assert 59 episodes through the Emby
API. Production Emby is never used for testing.

## 3. Rollout (for later sessions)

1. Fork image `ghcr.io/cornyhorse/tubearchivist:<tag>`, public, because
   GPL-3.0 requires the source to be published alongside it.
2. On apps: the ansible `image:` override plus `TA_STORAGE=postgres`, with
   a Postgres container next to TA. The ES data dir is kept as is.
3. Run `es_export` against stock ES, then `ta_import_es`, then switch the
   image.
4. Point the Emby libraries once at the new writer's root
   (`emby-derived/tubearchivist-v2/`).
5. Rollback: switch the image back to stock. ES is untouched and still
   current up to the cutover. Downloads made on the fork would need a
   rescan, which the "rescan filesystem" task handles.

## 4. Upstream tracking

Pinned at v0.5.12. For each upstream release, a checklist issue in the fork:
port yt-dlp version bumps, bgutil/PO-token changes and YouTube-breakage
fixes. Skip ES-specific changes. Most hot fixes touch `yt_dlp_handler`,
`download/` and `requirements.txt`. The storage layer keeps those files'
diff small.

## Open questions for Matt

1. **Redis:** keep it as the Celery broker for now (recommended), or put
   effort into a Postgres broker?
2. **Default numbering** for new channels: `year_frozen` (recommended,
   matches YT-Approved) or `date`?
3. **Emby folder naming:** include `[channel_id]` in series folder names
   (stable across channel renames, recommended), or plain channel name?
4. **Snapshots UI:** repurpose as `pg_dump` backups (recommended), or remove
   it?
