# Emby playlists (fork-v0.5.0)

TA playlists become real Emby playlists that point at the episodes of the channel shows and of the Other videos
show (`emby-v050-design.md` section 5). Emby only; with Jellyfin the job skips.

## What Emby does (tested on 4.9.5.0)

- A playlist belongs to one user and cannot be shared; another user does not see it. So TA creates one copy per owner.
- Items inside a playlist are filtered by the viewer's access rules, so David sees only the items he may see.
- Emby answers 500 (not 404) when a playlist is gone; the client checks the id to tell that from a real error.
- A playlist cannot be renamed through the API TA uses, so a rename recreates it.

## Settings

| Where | Setting | Meaning |
|---|---|---|
| Media server (`/api/mediaserver/`) | `playlists_enabled` (default off) | switch the sync on |
| Media server | `playlist_default_users` | Emby users who get every playlist (e.g. Matt) |
| Playlist | `emby_tags` | the playlist is also created for every Emby user whose name equals a tag (case blind), e.g. tag `David` |
| Playlist | `emby_playlist` (default on) | off: no Emby playlist (use it for a channel's "uploads" playlist) |

The API key must be an administrator's (the key from the media server settings already is).

## The job

`emby_playlists` (queue, postprocess lane) runs after every `emby_write` (debounce + 60 s, so Emby has scanned the
files first) and by hand with `manage.py ta_emby_playlists [--dry-run]`. It

1. reads the writer's manifest for the file of each video, and Emby's episode list once (path to id);
2. for each playlist and owner computes the ordered item ids; a video Emby has not scanned yet is left out and
   counted as `not_in_emby_yet`;
3. creates the playlist, or fixes it with the fewest calls (remove extras and duplicates, append, or rebuild when
   the order differs); an unchanged playlist costs one read. The reader is an administrator so items the owner
   can't see don't look "missing" every run;
4. deletes playlists TA created whose TA playlist is gone, switched off or no longer has the owner. Only rows of
   `EmbyPlaylistLink` are touched: playlists made by hand are never changed.

A failing playlist is reported in the job result and the others still sync. The hourly `emby_health` check adds a
problem when the playlist sync is on and has not succeeded for 6 hours or its last run failed.

## First production run

1. Deploy, then in Settings > Media server set the default users and switch `playlists_enabled` on.
2. Set `emby_playlist` off on the playlists that only repeat a channel (uploads).
3. `manage.py ta_emby_playlists --dry-run` lists what it would create; check owners and counts.
4. Run it without `--dry-run` (or wait for the next `emby_write`), then look at David's and Matt's playlists in
   Emby. Matt's own playlists are untouched.
5. To undo: switch `playlists_enabled` off and set every `emby_playlist` off, then run the command: the playlists TA
   created are deleted.
