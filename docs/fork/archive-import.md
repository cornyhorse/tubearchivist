# Archived channels: videos that exist only as files

Status: implemented on `claude/archive-import`. Reads with
[postgres-storage-design.md](postgres-storage-design.md).

Some old YouTube archives can't be indexed the normal way: the channel is
gone, the cuts were re-edited, or there is no matching YouTube id. The
filesystem scanner needs `<youtube_id>.mp4` and YouTube metadata, so such files
failed with "no YouTube metadata". An **archived** channel and its videos
live in the database and in the media tree like any others, but TubeArchivist
never asks YouTube about them.

## Data model

| Where | What |
|---|---|
| `Channel.source`, `Video.source` | `youtube` (default) or `archive` |
| Channel id | `arch_<slug of the name>`, e.g. `arch_tyler-jenkins-music` |
| Video id | `arch_` + 16 hex digits of `sha256(channel id, file name)` |

A YouTube video id is 11 characters and a channel id starts with `UC`, so an
`arch_` id can never collide with one. The id depends on the channel and the
file *name* only, never on the path or on how the file was placed, so running
the import again finds the same ids. (Two files with the same name in one
folder tree are reported and the second skipped.) `video_video.youtube_id`
and the `/api/video/<id>/` routes keep working as they are, the id is just a
`slug`.

API documents (`/api/video/…`, `/api/channel/…`, backups) carry
`"source": "archive"` for archived items and nothing for YouTube ones, so the
legacy shape is unchanged. The fields in the UX schema are optional.

## Import

```bash
docker exec -it tubearchivist python backend/manage.py ta_import_archive \
    /import/Tyler\ Jenkins\ Music --channel-name "Tyler Jenkins Music" --dry-run
```

```text
ta_import_archive <root> [--channel-name NAME] [--channel-id ID]
                  [--mode auto|hardlink|copy|move] [--dry-run]
                  [--queue] [--no-emby]
```

- `<root>` is a folder of media files (searched recursively, hidden folders
  and `@eaDir` skipped). Extensions: mp4 m4v mkv webm mov avi flv wmv mpg mpeg
  ts m2ts 3gp. It must be readable inside the container, so mount the NAS
  folder read-only, e.g. `-v /mnt/wd10tb/media/yt:/import:ro` (a `move` needs
  it writable).
- One run is one channel. The name is `--channel-name`, else the `<title>` of
  `tvshow.nfo` in the folder, else the folder name. `--channel-id UC…` or
  `arch_…` adds the files to a channel that is already indexed instead (for
  example the real "Key to the Geyt" channel); the videos are still archived
  ones.
- **No network access at all.** Nothing is looked up, downloaded or queued.
- `--dry-run` probes the files and prints what it would do, writes nothing.
- `--queue` runs it as an `archive_import` job in the `maintenance` lane
  instead of in this process (progress and stop work like other jobs). The job
  is not in the user job list on purpose: the folder is a server path, so only
  this command can start it.
- When something was imported an Emby library write is queued, unless
  `--no-emby`.

### What it reads

| Field | From |
|---|---|
| title | `<name>.nfo` `<title>`, else `<name>.info.json` `title`, else the file name |
| description | `<plot>` / `<outline>`, else `description` |
| published | `<aired>`, `<premiered>`, `<releasedate>`, `<year>`, else `timestamp` / `upload_date`, else the file's modification time |
| tags | `<genre>`, `<tag>`, else `tags` |
| duration, streams, size, width, height | ffprobe on the file |
| thumbnail | `<name>.jpg\|png\|webp`, `<name>-thumb.jpg`, else a frame at 10% of the video (ffmpeg), else TA's default image |
| channel art | `poster`, `folder`, `cover` or `logo` for the icon, `banner`, `fanart` or `background` for the TV art (`.jpg .jpeg .png .webp`), else TA's defaults |

The NFO wins over `.info.json` field by field. Thumbnails are cropped to 16:9
and cached like any video thumbnail.

### Where the file goes (decision)

Each file is placed at `<media>/<channel id>/<video id>.<ext>` (extension
lowercased, container kept), the same layout as every other video, so the
player, downloads, deletion and the Emby writer need no special case.
`--mode` decides how:

- `auto` (default): **hardlink**; if the folder is on another filesystem, copy.
- `hardlink`: hardlink or fail.
- `copy`: copy (to a hidden `.part` file, then renamed, so a stopped copy is
  never mistaken for a video).
- `move`: move the file; sidecars stay behind.

The original is not renamed, edited or deleted (except `move`). Deleting an
archived video in TA removes only TA's link or copy. A file already at the
destination with the same size is taken as an earlier, interrupted attempt
and reused; one of another size is an error and is skipped.

### Idempotent

A file whose id is already indexed is left alone, including hand edits of its
title. Only new files are added; nothing is renamed or deleted. Problems
(ffprobe fails, no video stream, duplicate names) are listed per file and the
run goes on; the command exits non-zero and the job fails if any file failed.

## Downstream

| Area | Behaviour for archived items |
|---|---|
| Lists, detail, search | Normal. Video title/tags and channel name are full-text searched like any other. |
| Playback | The placed file is served from the media dir. Browsers play only some containers: mp4 and webm are fine, mkv often is not. |
| Filesystem rescan | `Scanner` skips them in both directions: it never deletes an archived video (its file may be `.mkv`) and never tries to index an `arch_*.mp4` as a YouTube id. |
| Reindex (jobs and API) | Never queued; ids that get queued by hand are dropped; `reindex_single_video` returns without deactivating. |
| Subscribe / checks | `channel_subscribed`, `auto_download` and `index_playlists` set to true return 400 for an archived channel; `check_channel`, `fetch_video` and `index_playlists` jobs skip `arch_` targets. `download_video` already skips a video that is indexed. They never enter the download queue. |
| Metadata embedding | `resync_metadata` and the per-video embed skip them: **TA never writes into an archived file**, which may be hardlinked to the original. |
| Thumbnail check | Skipped for archived channels and videos, so nothing tries to fetch art; cleanup treats `arch_` channel art correctly (channel ids are no longer assumed to be 24 characters). |
| Emby | Channels get `emby_show = always` (nothing subscribes them, so `auto` would hide them), `emby_numbering` default `date` (changeable per channel as usual). Episodes use the published date, NFO and thumbnail; the media link keeps the container's extension. The `uniqueid` type is `archive`, not `youtube`, so Emby's YouTube provider doesn't look it up. |
| Playlists | Custom playlists work as usual. Deleting a video removes it from custom playlists as before. |
| Deletion | `DELETE /api/video/<id>/` and deleting the channel remove TA's copies and rows only. |
| UI | No "open on YouTube" link, no "last refreshed", views/likes/rating, Reindex, Subscribe, Subscribers, channel customization or download rules; the Emby settings stay. |

## Not covered

- Subtitle files next to the media (`.vtt`/`.srt`) and comments are not
  imported.
- Playlists are not made from the folder structure.
- The scanner does not discover orphaned `arch_*` files. A `move` that is
  interrupted between moving a file and indexing it leaves that file in the
  channel folder without a video; copy it back to the source folder under its
  old name and run the import again. `auto`, `hardlink` and `copy` have no
  such window: the source stays where it is.
- Files are probed with ffprobe directly (not the downloader's stream
  extractor), because a Matroska or WebM video stream has no bit rate of its
  own and would be mistaken for cover art.
