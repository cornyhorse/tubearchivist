# Emby v0.5.0: shows = channels, playlists = Emby playlists, TA-managed access tags

Status: **design, awaiting Matt's approval** (2026-10-22). Nothing here is built yet. It ships Matt's 2026-10-22
decisions on how TA content appears in Emby. Findings marked *(tested)* come from a scratch `emby/embyserver:4.9.5.0`
container with a throwaway library and fake episodes, never from production.

## 1. Goal

| Emby thing | Made from | Today | v0.5.0 |
|---|---|---|---|
| Show (Season = upload year) | a channel | channels **and** playlists | **channels only** |
| Emby playlist | a TA playlist | does not exist | created and synced through the Emby API |
| Videos of channels TA doesn't follow | those videos | one oddly named one-off show per channel | one hidden catch-all show, reachable through playlists |
| Who may see it | Emby user policy plus tags | hand-tagged | `tvshow.nfo` `<tag>` written by TA, from a per-channel / per-playlist setting |

## 2. What testing on the scratch Emby established

1. **Tags on a series reach its episodes; a tag on an episode alone also works** *(tested)*. With David's policy
   (`AllowTagOrRating`, inclusive `BlockedTags=[David]`, `MaxParentalRating=1`, `BlockUnratedItems=[Series]`) an
   episode tagged `David` inside an *untagged* show is visible to David (item lists, search) and appears in his
   playlist, while the show itself stays out of his Shows list. This is what makes the catch-all show possible
   (section 4).
2. **A playlist belongs to one user** *(tested)*. A playlist created for the admin is not listed for David at all.
   Emby 4.9.5 has no playlist sharing endpoint (`/Playlists/{id}/Users` and similar all 404). A playlist created
   with the API key and `UserId=<david>` is owned by David and listed for him. So TA must create **one copy per
   owning user**.
3. **Items inside a playlist are filtered by the viewer's access** *(tested)*: an admin's playlist with items from a
   tagged and an untagged show shows David only the tagged one. An approved playlist therefore can't leak an
   unapproved video, even when its copy is shared by mistake.
4. **Playlist API that works** *(tested)*: `POST /Playlists?Name&Ids&MediaType=Video&UserId` creates and returns
   the id; `GET /Playlists/{id}/Items?UserId` returns items with `PlaylistItemId`;
   `POST /Playlists/{id}/Items?Ids&UserId` appends (an item already in it is not added twice);
   `DELETE /Playlists/{id}/Items?EntryIds=` removes; `POST /Playlists/{id}/Items/{entry}/Move/{index}` reorders;
   `DELETE /Items/{id}` deletes a playlist. Renaming a playlist through `POST /Items/{id}` failed (400), so a rename
   is done as delete and recreate.
5. **Finding an episode id** *(tested)*: `GET /Items?Recursive&IncludeItemTypes=Episode&Fields=Path` returns every
   episode with its file path. TA knows each path, so the sync lists the library once and maps path to id. (The
   `uniqueid` in the NFO does arrive as a provider id, but path is exact and doesn't depend on that.)
6. **Emby does not re-read an NFO tag on `Library/Media/Updated`** *(tested; folder and file paths both)*. It does
   after `POST /Items/{id}/Refresh`, but **only a full refresh that replaces the metadata changes an existing tag**
   *(tested on the scratch Emby, series and episode, tag NFO changes)*:

   | NFO change | `MetadataRefreshMode=Default`, `ReplaceAllMetadata=false` | `FullRefresh` + `ReplaceAllMetadata=true` |
   |---|---|---|
   | none -> David | David is added | David is added |
   | David -> none | **David stays** | tags emptied |
   | David -> Kids | **David stays, Kids is not added** | Kids replaces David |

   So the follow-up refresh uses `FullRefresh` with `ReplaceAllMetadata=true` and `ReplaceAllImages=false`. On the
   scratch Emby that kept the poster and thumb sidecar images, the play count and resume position, and worked on a
   show with `lockdata`. It does replace anything set by hand in Emby on those items; TA owns these NFOs. It runs
   only on items whose NFO changed. If a library has online metadata providers switched on, a full refresh may ask
   them; Matt's YT library has none that match these shows (no provider ids, series locked).
7. New items are only in Emby after its scan. A playlist sync that runs before the scan simply leaves those
   episodes out, and the next run adds them.

## 3. Slice 1: access tags, written by TA (PR 1)

- `Channel.emby_tags` and `Playlist.emby_tags`: text array, default `[]`. **Empty means hidden from tag-restricted
  users**, which is the allow-list behaviour Matt wants: a new channel is invisible to David until someone ticks
  "Visible to David".
- `render_tvshow` writes one `<tag>` per tag, sorted, so the file is deterministic and write-if-changed stays quiet.
  The tvshow.nfo of a channel carries the channel's tags.
- Playlists don't have a show any more, but their tags matter in two places: the Emby playlist owner (section 5) and
  the episode tags in the catch-all (section 4).
- UI: channel table and channel page get a "Visible to" control. A tag is a free string; the settings page keeps a
  short list of known tags (default `["David"]`) so the control is a set of switches, not a text box. API:
  `emby_tags` on the channel and playlist serializers and bulk actions, and the MCP server's channel tools.
- After a sync that updated `tvshow.nfo` or an episode NFO whose tags changed, the notify job also calls
  `Items/{id}/Refresh` for those items (found by path). A run that would refresh more than 200 items refreshes the
  series recursively instead.
- Needs an API key with admin rights (the key from PR #14 already is one in Matt's setup); no new setting.
- The 14 hand-tagged series are not touched. Matt applies the tags in the UI before the deploy, or a one-time
  `ta_emby_import_tags` command reads the tags off Emby (read-only) and fills `emby_tags` for matching channels.
  **Decision for Matt: import or tick by hand?** Recommendation: the import command, with a dry run.

## 4. Slice 2: shows = channels, plus the catch-all (PR 2)

- `sync_all` stops building playlist series. `playlists/` disappears from `.emby/`. The playlist `emby_numbering`
  column stays (nothing else reads it, and deleting columns isn't worth a migration).
- A channel gets its own show when `Channel.emby_show` allows it: `auto` (default: yes if `subscribed`),
  `always`, `never`. `auto` gives exactly the subscribed channels a show, which matches Matt's rule. A channel Matt
  keeps downloading from without subscribing can be set to `always`.
- Every other downloaded video goes into one show, **"Other videos"** (`channels/Other videos [other]/`,
  Season = upload year, numbering `year_frozen` so later arrivals append). Each episode's `<studio>` stays the
  uploader and its plot starts with "From <channel name>." so the origin is visible.
- **Options considered**
  - **(a) catch-all show.** One more entry in Matt's Shows list. Access works only through episode tags (finding 1),
    which is fine because the *show* is never tagged: David never sees it, each episode carries the union of the
    tags of the playlists it is in (and of its channel, if tagged).
  - **(b) a show per playlist.** Puts playlist-derived shows back in the Shows list, which Matt ruled out, and a
    video in two playlists would be duplicated.
  - **(c) no show at all.** Emby has no library item without a series/folder; the files have to live somewhere.
    A hidden folder is a show too, so (c) is (a) with the show kept out of David's lists, which is what the tag
    rules already give us.
  - **Recommendation: (a).** Matt sees "Other videos"; David sees none of it as a show, and only the episodes he is
    cleared for, inside Emby playlists. A video in no playlist (TheVideoKnight) sits there untagged, so it is
    invisible to David. Emby's Latest and search can still list a tagged catch-all episode for David; that is
    acceptable because the tag means "approved".
- Episode NFOs gain `<tag>` only in the catch-all (channel shows rely on the series tag; this keeps NFO churn
  and refresh work small).

### The one-time layout step

The change removes `playlists/*` and moves episodes of unsubscribed channels, so it uses the same gate as
`emby-layout-migration.md`: `emby_layout` version **3**; until `manage.py ta_emby_migrate_layout` (extended with a
version 2 to 3 step) has run, `emby_sync` and friends refuse with `EmbyLayoutOutdated`. The step is a pure
database/config step plus one sync, with `--dry-run` printing:

- playlist shows to remove, with file counts (about 8 playlist series today are fully duplicate: SciShow Kids
  uploads 530 episodes, 7 Nat Geo Kids, Baby Einstein, 8-bit Music Theory, This Week In Nerd History, Bible Stories
  for Kids, The Airmen of Note);
- one-off channel shows that move into "Other videos" and how many episodes;
- the UTC date renames of section 6;
- `errors 0`.

**Risk to flag: watch history.** An episode moved to another show, or a playlist-show episode that is removed, is a
new item in Emby. Watched state and resume points on the *removed playlist-show copies* are lost; the channel-show
copy keeps its own state. Before the run, `ta_emby_migrate_layout --dry-run` lists which of those items have
played/resume data in Emby (read-only query with the API key and `Fields=UserData` for the users in the playlist
settings), so Matt can see what he'd lose. First production run plan: same ten steps as layout 2 (pg_dump,
no sync running, dry run, run, idempotence check, Emby checks), then slice 3's first playlist sync after Emby has
scanned.

## 5. Slice 3: Emby playlists (PR 3)

- **Settings** (`mediaserver` config): `playlists_enabled` (default off until Matt turns it on),
  `playlist_default_users` (Emby users who get every synced playlist, e.g. Matt), and the existing URL and key.
  New API calls need the same admin key; no new secret.
- **Who owns a copy** (finding 2): the default users, plus every Emby user whose name equals (case-insensitive) one
  of the playlist's `emby_tags`. A playlist tagged `David` is copied for David and for Matt; an untagged one only
  for Matt. Matt's tag names are the user names already, so there is nothing extra to configure.
- **Which playlists**: `Playlist.emby_playlist` (bool, default true, switch in the table/page). Matt turns it off for
  channel "uploads" playlists, which duplicate a channel show.
- **Sync job** `emby_playlists_sync` (jobs engine), queued by `emby_sync` and by `ta_emby_sync` after the file
  sync, and runnable alone. It
  1. lists the library's episodes with paths once and maps path to Emby id;
  2. for each synced playlist and owner computes the ordered list of ids (videos in playlist order that are
     downloaded and present in Emby);
  3. looks the playlist up by the id stored in a small table `EmbyPlaylistLink(playlist_id, user_id, emby_id)`,
     creating it if missing; reads its items; fixes it with the least calls: delete extra entries, append missing
     ones, and rebuild by delete-all-and-add when only the order differs;
  4. deletes the Emby playlists of TA playlists that were removed or switched off, but **only ones TA created**
     (in the link table). Playlists Matt made by hand are never touched.
- Idempotent: an unchanged playlist costs one read. Emby unreachable or a missing user fails that step only; the
  file sync and the notify are unaffected.
- `ta_emby_sync`'s health report adds: playlist sync last success, failures per playlist, episodes not yet in
  Emby. Jellyfin is out of scope (its playlist API differs); the job skips it with a note.
- Tests: a faked `MediaServerClient` for the unit tests (100% coverage) and one scripted check against the scratch
  container that I run by hand, not in CI.

## 6. Slice 4: dates in UTC (PR 4)

Today `aired`, `premiered` **and the numbering date** (so Season and the `MMDD` of the episode number) all use the
local date of `TZ`. An upload at 00:00 to 04:59 UTC is still the previous day in America/New_York, which is the
201 episodes Emby shows a day early. A video published 2025-01-01 00:30 UTC would also sit in Season 2024.

- Decision proposed: **everything uses the UTC date of `published`** (`date_downloaded` fallback also in UTC).
  That is the date YouTube and TA show, and doesn't change if `TZ` does. `EmbyPaths.timezone` stops being used for
  dates.
- Effect on existing files: the `date` scheme recomputes, so the 201 episodes get a different `MMDD` number
  (rename of media, NFO and thumb) and a Jan-1 early video moves seasons. Frozen schemes keep stored numbers and
  only get the right `aired`. The renames are part of the layout step's dry run, so the count is visible before
  anything moves.
- **Decision for Matt:** UTC everywhere with the renames (recommended; one consistent rule), or UTC for `aired` only
  and leave numbers on local dates (no renames, but NFO date and filename `MMDD` disagree for those 201).

## 7. Order, dependencies, tests

1. PR 1 tags (independent).
2. PR 2 shows and catch-all, with the layout 3 gate (uses PR 1's tags for the catch-all episodes).
3. PR 3 playlists (needs PR 2's catch-all path mapping; independent of PR 1 except tag-to-owner).
4. PR 4 UTC dates (independent; its renames fold into the layout 3 step if it lands first, otherwise it adds its
   own dry-run line).

Every PR: backend coverage stays at 100%, frontend tests for new UI, and `docs/ux-api-contract.md` updates for
API changes. Nothing is deployed and nothing touches production Emby; the scratch Emby is a local container
(`scratch-emby-v050`, API on port 18096, library from a temp folder, gone when I'm done).

## 8. Decisions that are Matt's

1. Catch-all "Other videos" (recommended) vs one of the alternatives (section 4).
2. Playlist copies per owning user, owners = default users + users named by the playlist's tags (section 5), since
   Emby 4.9.5 can't share a playlist.
3. `emby_show` default `auto` (subscribed channel = own show).
4. Import the 14 existing tags from Emby, or tick them in the UI.
5. UTC everywhere with renames, or UTC for `aired` only (section 6).
6. Accepting the watch-history loss on removed playlist-show copies after seeing the dry-run list.
