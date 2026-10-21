# Auto download per channel and playlist

Auto download is **off by default** and is switched on per channel or per
playlist (`auto_download` on `channel.Channel` and `playlist.Playlist`,
migrations `channel.0003_auto_download` and `playlist.0003_auto_download`;
existing rows get `false`).

## What a check does

A check of a channel or playlist (scheduled `check_subscriptions`, "Check
now", the queue API) that doesn't say `download_now` itself:

- **opted in**: each new video the download rules allow (no Shorts, length
  limits and so on, see [`../ux-api-contract.md`](../ux-api-contract.md)
  section 4) gets a `download_video` job at once, as the jobs engine
  already did for the add dialog. Ignored videos stay ignored.
- **not opted in**: new videos land in Found videos, waiting, exactly as
  before.

A video found through two sources downloads if any opted in source finds
it and it passes that source's rules. When an opted in channel or playlist
lists a video that is already waiting because **another** source found it
(another channel, or a playlist), that video gets its download job too.
What the same channel or playlist found before it was opted in stays
waiting: switching auto download on never pulls a backlog.

An explicit `download_now` still wins: the add dialog's "download now"
downloads for any channel, and `download_now: false` never downloads.

## The old global switch

`subscriptions.auto_start` was one global switch: on, every check
downloaded everything the rules allowed, for every subscription. It was
switched on by mistake once and pulled 147 videos.

It is retired. Its place is `subscriptions.auto_download_default`
(default `false`): the `auto_download` a channel or playlist gets **when
it is subscribed** (a new subscription, not a re-save of one). It never
downloads anything by itself and changes nothing already subscribed.

A stored `auto_start: true` cannot come back as a global switch: no code
reads `auto_start` any more, and `ta_startup` removes the key
(`clear_old_keys`) and adds `auto_download_default: false`
(`add_new_defaults`). The old value is not carried over.

The per-video `Download.auto_start` flag ("download this one first") is a
different thing and is unchanged.

## Where to switch it

- REST: `auto_download` on `GET`/`POST /api/channel/<id>/` and
  `GET`/`POST /api/playlist/<id>/`; list filter `?auto_download=true|false`
  on `/api/channel/` and `/api/playlist/`; `auto_download` on
  `POST /api/add/`.
- MCP: `set_rule` with `scope` channel or playlist and `auto_download`;
  `add_url` with `auto_download`.
- UI: the toggle on the channel and playlist pages, the column and bulk
  action of the channel and playlist tables, the "Auto-download" filter,
  and the default under Settings → When.
