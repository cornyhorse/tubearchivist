# Emby layout 2: the one-time rename pass

Layout 2 ships Matt's 2026-10-11 decisions on review items E6, E7 and E8. Together they rename the Emby tree
once, so they are applied in one explicit step that Emby sees as a single rename pass.

| | Layout 1 (fork-v0.1.0, fork-v0.2.0) | Layout 2 |
|---|---|---|
| File and folder names | follow the current titles, so a renamed video or channel moves files | **frozen** at first write, stored with the episode number (`EpisodeNumber.name`, `.series_name`); titles change only in the NFOs |
| `date` scheme | `MMDD*10+n`, so an 11th upload spills into the next day's numbers | `MMDD*100+n`, 100 slots per day |
| Playlist default | `playlist_position`, which renumbers on every reorder | `playlist_frozen`: numbered in playlist order on first sync, later additions are appended, never renumbered |
| Episode file name | uncapped (show and title 120 bytes each) | at most 200 bytes (show part at most 80) |

## The gate

The layout is recorded in the `ConfigDocument` `emby_layout` (`{"version": 2}`), and backups carry it. A database
that holds episode numbers but no layout document is on layout 1. Until it is migrated, `emby_sync`,
`ta_emby_sync` and the API-triggered syncs refuse to run (`EmbyLayoutOutdated`), so the new image can't half-apply
the change. A database without any numbers, such as a fresh install or a stock import, starts on layout 2.

## What `manage.py ta_emby_migrate_layout` does

The database part runs in one transaction:

1. **Date numbers.** `MMDD*10+n` becomes `MMDD*100+n`. A number that spilled into the next day's block moves
   back to the first free slot of its own day. Numbers of deleted videos are converted by the formula and stay
   reserved.
2. **Playlists.** Every playlist on `playlist_position` switches to `playlist_frozen` and keeps the numbers it
   has now, so its files don't move. To keep one on `playlist_position`, pass
   `--keep-playlist-position <playlist_id> ...`.
3. **Names.** Every number gets the current video title and series title as its frozen name. That is what is on
   disk now, assuming the last sync was current, so names don't move.
4. The layout document is set to 2.

Then it runs one `sync_all`. The renames keep inodes, because media are hardlinks and are moved, not
recreated. Only the files of `date`-scheme series move, plus names that changed since the last sync or are over
the 200-byte cap.

| Option | Effect |
|---|---|
| `--dry-run` | Runs everything inside a transaction, including a dry-run sync, prints the counts, then rolls back. Nothing changes on disk or in the database (the hardlink preflight creates a probe and removes it again). |
| `--verbose-changes` | Prints every rename, create and delete. |
| `--no-sync` | Migrates the database only, and the next `emby_sync` does the renames. |

After it has run, every later sync of an unchanged library reports `renamed 0`.

Unless `--no-sync` is given, the command runs the writer's hardlink preflight before it touches the database, in
a dry run as well as a real run. A root that can't take the renames therefore fails the command with the database
still on layout 1, and a dry run can't pass where the real run would fail.

### 2026-10-21: the half-applied production run

On fork-v0.3.0 the preflight compared inode numbers. The CIFS client on apps had autodisabled server inode numbers
on the NAS share ("Hardlinks will not be recognized on this mount"), so the database part committed and the sync
then refused the root with `EmbyRootError` before touching a file. Production was left on database layout 2 with
the files still on layout 1. fork-v0.4.0 tests hardlinks by behaviour and recognises links by size, mtime and
source (see `postgres-storage-design.md` §2.5), so the recovery is not to rerun the migration (it reports
`already on Emby layout 2`) but to run the sync:

1. `ta_emby_sync /youtube/.emby --dry-run`: the renames should match the earlier preview (about 5,244: 5,139
   date-scheme renumbers and 105 over-long playlist names), with `errors 0`, `created 0`, `deleted 0`. The
   version 2 manifest is upgraded in memory only.
2. `ta_emby_sync /youtube/.emby`: does the renames and writes a version 3 manifest.
3. A second `--dry-run` reports `renamed 0`.

## First production run (needs Matt's OK at each step)

Prerequisites: an image built from `main` with this change, the MattsTools digest bump, and an apps redeploy, each
with Matt's OK. Until the migration runs, the hourly `emby_sync` fails with the gate message. That is harmless,
because the tree stays as it is.

1. **Snapshot first.** Take a `pg_dump` of the database (D9 in the 2026-10-11 review), and save a listing of the
   tree:
   `ssh apps 'find /mnt/nas-media/tubearchivist/.emby -type f -printf "%i %p\n"' > emby-tree-before.txt`.
2. **Make sure no sync is running.** Check the TA task list for no running `emby_sync`. The writer is not safe to
   run twice at once (review T1).
3. **Preview:**
   `ssh apps docker exec tubearchivist python manage.py ta_emby_migrate_layout --dry-run --verbose-changes > migrate-preview.txt`.
   Check that:
   - `errors 0`, `created 0`, and `deleted 0` except for media that is really gone;
   - every rename stays inside its own series folder and only changes `SxxEyy` (or a name over the cap);
   - `moved to own day` lists only the days that had more than 10 uploads.
4. **Run it:** `ssh apps docker exec tubearchivist python manage.py ta_emby_migrate_layout --verbose-changes > migrate-run.txt`.
   Its counts should match the preview.
5. **Check idempotence:** run
   `ssh apps docker exec tubearchivist python manage.py ta_emby_sync /youtube/.emby --dry-run`.
   It should report `renamed 0, created 0, deleted 0`.
6. **Check in Emby** (David's YT libraries), after a library scan has run:
   - For each channel, Emby's episode count matches the files: one `<episodedetails>` NFO per media file, and
     no multi-version entries.
   - A channel that had more than 10 uploads on one day shows those episodes in upload order within that day.
   - Watched state and resume points are still there on a few episodes that had them before. Note them before
     step 4. Emby keys user data to the item, and a moved file normally keeps its item through Emby's
     rename detection. This is the main thing to confirm.
   - Playlists show the same episode order as before.
7. **If something is wrong,** restore the `pg_dump` and redeploy the previous image digest. Files that were
   renamed stay renamed, but the old image's next `emby_sync` renames them back, because layout 1 derives names
   live. That is one more rename pass, so check with Matt before doing it.

## Tests

`backend/emby/tests/test_migrate_layout.py` builds a layout-1 tree and database (old slot count, live names,
`playlist_position`, no layout document) and checks the following:

- One migration renames the date-scheme files and keeps media inodes.
- A second sync changes nothing, and later title changes rename nothing.
- A spilled number goes back to its own day.
- The dry run leaves the database and the disk untouched.


# Layout 3: shows are channels, the rest is in "Other videos" (fork-v0.5.0)

Design: `emby-v050-design.md` sections 4 and 6. The same command, `manage.py ta_emby_migrate_layout`, takes a
database on layout 2 to layout 3 (and one on layout 1 through both).

| | Layout 2 | Layout 3 |
|---|---|---|
| Shows in `.emby/` | one per channel **and** one per playlist (`playlists/`) | one per channel that has a show, plus **Other videos** |
| Which channel has a show | all | `Channel.emby_show`: `auto` = subscribed (default), `always`, `never` |
| Videos of channels without a show | a one-off show named after the channel | `channels/Other videos [other]/`, Season = upload year, numbering `year_frozen`; each episode's studio is the uploader and its plot starts "From <channel>." |
| Access tags | none | channel shows carry `emby_tags` in `tvshow.nfo`; the Other videos show carries none; episodes (in every show) carry the tags of their playlists and channel that the show's tag does not give |
| Playlists | shows | not written to `.emby/`; Emby playlists come from a later change |

The gate is the same: until the command has run, `emby_sync` and the API-triggered syncs refuse
(`EmbyLayoutOutdated`). 2 to 3 changes no stored number. The sync that follows removes `playlists/*` and the
shows of channels without a show, and creates Other videos. Stored numbers of those channels are kept, so
switching a channel back to `always` returns its show with the same names.

## Who can see what (tags in layout 3)

A video is visible to a tag's users when its channel carries the tag or any playlist it is in does. The channel show
carries the channel's tags; every episode also carries the tags of its playlists (and, in Other videos, of its
channel) that the show's own tag does not give, so a tagged playlist approves its videos even inside an untagged
channel show. Both `ta_emby_import_tags --dry-run` and `ta_emby_migrate_layout --dry-run` print, per tag:

    visible to David: 3100 videos now, 3112 after, lost 0, gained 12, 4 Emby items outside the TA tree not compared
      LOST <youtube_id> <title>        (first 30)

"now" is what Emby shows for the tag today (the shows carrying it, read only, matched to videos through the writer's
manifest), "after" is what TA will tag. Anything on a LOST line is access the person has today and would lose: tag
the channel or playlist (or `ta_emby_import_tags --map`) before the run. Items outside the TA tree (the hand made
YT-Approved folder) can't be compared and are only counted.

## What the dry run prints

`ta_emby_migrate_layout --dry-run` runs the whole thing in a transaction, prints, and rolls back:

- the visibility lines above;
- `show removed: <folder> (N files)` for every playlist show and every channel show that goes;
- `playlist shows removed N, channel shows moved to Other videos N, episodes created in Other videos N`;
- **watch history** (read only, uses the media server settings): for each Emby user, the removed media files they
  have played or started, listed by path. A removed file is a new item in Emby, so that state is lost (Matt has
  accepted that loss; the list is printed for the record). The playlist-show copies of channel episodes are separate
  items from the channel-show ones; the channel-show copy keeps its state. Files that are only **renamed** are
  counted separately (`renamed files with watch history`): Emby normally keeps the state of a renamed file, so
  check a few after the run. If the server can't be read it says `watch history not read: <why>`;
- the usual `renamed/created/updated/deleted` counts.

## First production run (needs Matt's OK at each step)

Prerequisites: the images of PRs 26, 27, 29 and 28 deployed together (merge order below), Matt's OK to deploy, and
the Emby tags set on the channels David may see. Until the migration runs, the hourly `emby_write` fails with the
gate message (a sync on layout 2 is refused on purpose) and the tree stays as it is.

1. **Snapshot:** `pg_dump`, and `find .../.emby -type f -printf "%i %p\n" > emby-tree-before.txt`.
2. **No sync running** (TA task list). Expect the counts to **drift**: the CoasterFan2105 indexing (about 156
   files) and the orphan videos being indexed (about 534) change the library while you work. Run the preview
   right before the real run, pause those jobs if you can, and compare the real run with that preview, not an
   older one.
3. **Import the tags** from Emby: `ta_emby_import_tags --dry-run`, check every show maps (the YT-Approved
   CoasterFan2105 maps to the TA channel) and read the visibility lines, then run it. Do not sync before the
   migration: the image refuses on layout 2.
4. **CoasterFan2105:** check the channel is subscribed (else its videos land in Other videos; set `emby_show` to
   `always` if it should keep its show). After the run, remove the hand-made YT-Approved CoasterFan show/folder
   in Emby (its videos now come from the TA channel show).
5. **Preview:** `ta_emby_migrate_layout --dry-run --verbose-changes > preview.txt`. Check:
   - `errors 0`;
   - **no LOST lines** for any tag (or each one is intended);
   - the removed shows are exactly the playlist shows and the channels expected to lose their show;
   - `episodes created in Other videos` equals the media in the removed channel shows plus the videos of
     unfollowed channels;
   - the watch history lists (for the record).
6. **Run it:** `ta_emby_migrate_layout --verbose-changes > run.txt`; counts match the preview (within the drift).
7. **If the sync fails after the database part committed** (the 2026-10-21 case): the command reports
   `already on Emby layout`; fix the cause and run `ta_emby_sync .../.emby --dry-run`, then without
   `--dry-run`. It finishes the same renames.
8. **Idempotence:** `ta_emby_sync .../.emby --dry-run` reports `renamed 0, created 0, deleted 0`.
9. **Tell Emby and check:** the notify and item-refresh jobs run after the sync; then in Emby (as admin and as
   David) the YT library shows channel shows and "Other videos" for admin, David sees only tagged shows plus
   tagged episodes (playlists), and "Other videos" is not in his list.
10. **If wrong:** if the Emby playlist sync (PR 28) has run, undo it first (see docs/fork/emby-playlists.md:
    `playlists_enabled` off, then `ta_emby_playlists`), then restore the `pg_dump` and redeploy the previous
    image; the old image's next sync recreates the playlist shows and the one-off channel shows (one more change
    pass; ask Matt first).

## Merge and deploy order of the v0.5.0 PRs

`delete_branch_on_merge` is off, so a merged parent does not retarget its children. Use merge commits (no squash):
#26 into main; `gh pr edit 27 --base main`, check its diff, merge; the same for #29 and then #28 (its base
is the UTC branch). Run the tests on main with all four before building the image, and deploy all four together:
the layout migration (2 to 3 to 4, one command run) must run once, on the image that has all of them.

## Layout 4: dates are UTC (fork-v0.5.0)

Before layout 4, `aired`, `premiered`, the season and the `MMDD` of a date number used the date in `TZ` (America/New_York
on Matt's server). A video published at 00:00 to 04:59 UTC is still the previous day there, so Emby showed its
premiere date one day early (201 episodes), and a video published just after midnight UTC on 1 January sat in the
previous year's season. Now every date is the **UTC date of the upload**: it is what YouTube and TA show, and it
does not change when `TZ` does.

This is a layout step of its own (3 to 4), so it happens once, whichever image first ran an earlier layout step: a
database on layout 2 or 3 is brought to 4 by one `ta_emby_migrate_layout` run. The command moves the stored numbers
of the affected episodes in the same transaction, and the following sync renames their files (media, NFO, thumb).
Its **first output line** is the count:

    [dry-run] UTC dates: N episodes renumbered, M of them change season

- Only the series the writer writes are counted (channels with a show, Other videos), so N is the number of
  episodes whose files are renamed. Numbers of channels without a show are kept consistent but not counted;
  playlist series are left alone.
- `date` scheme: the episode keeps its slot within the day when it is free on the new day, else takes the first free
  one; one that already lies on its UTC day is not touched. The season becomes the UTC year. Within a day the slot
  order of a moved episode may no longer follow upload order.
- `year_frozen`: only an episode whose year changes moves, to the next number of the new season.
- `absolute_frozen`, playlist schemes and numbers set by hand are not touched.
- `--verbose-changes` lists every renumbered episode (`series/youtube_id`).

The renames keep the media inodes (files are moved, not recreated) and Emby's rename detection, as in the layout 2 pass.
