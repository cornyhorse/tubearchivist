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
| `--dry-run` | Runs everything inside a transaction, including a dry-run sync, prints the counts, then rolls back. Nothing changes on disk or in the database. |
| `--verbose-changes` | Prints every rename, create and delete. |
| `--no-sync` | Migrates the database only, and the next `emby_sync` does the renames. |

After it has run, every later sync of an unchanged library reports `renamed 0`.

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
