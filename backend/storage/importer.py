"""
Functionality:
- one-shot import of an ES export (storage.es_export) into the
  relational Postgres schema
- idempotent: rerunning replaces rows from the export
- verifies counts and spot checks, reports every mismatch
"""

import json
import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterator

from appsettings.models import ConfigDocument
from channel.models import Channel
from common.src.db import from_epoch
from django.db import transaction
from django.db.models import Sum
from download.models import Download, DownloadRule
from emby.models import EpisodeNumber
from playlist.models import Playlist, PlaylistEntry
from user.models import Account, UserConfig
from video.models import Comment, CommentRefresh, SubtitleLine, Video

BATCH_SIZE = 2000
RULE_DOC_FIELDS = [
    "enabled",
    "min_duration",
    "max_duration",
    "vid_types",
    "title_include",
    "title_exclude",
    "published_after",
    "action",
]
EPISODE_DOC_FIELDS = [
    "series_kind",
    "series_id",
    "scheme",
    "youtube_id",
    "season",
    "episode",
]
# list values with set semantics, rebuilt in sorted order
UNORDERED_KEYS = {"playlist"}


@dataclass
class ImportReport:
    """what was imported and what did not check out"""

    counts: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)

    def skip(self, index: str, reason: str) -> None:
        """record a document that could not be imported"""
        self.skipped.setdefault(index, []).append(reason)

    @property
    def ok(self) -> bool:
        """no mismatches"""
        return not self.mismatches

    def lines(self) -> list[str]:
        """human readable report"""
        out = [f"{k}: {v}" for k, v in self.counts.items()]
        for index, reasons in self.skipped.items():
            out.append(f"{index}: skipped {len(reasons)}")
            out.extend(f"  - {r}" for r in reasons[:20])
        out.extend(f"MISMATCH {m}" for m in self.mismatches)
        out.append("OK" if self.ok else "FAILED")
        return out


class EsImporter:
    """import ndjson export dir into postgres"""

    def __init__(self, export_dir: Path, sample_size: int = 50):
        self.export_dir = Path(export_dir)
        self.sample_size = sample_size
        self.manifest = json.loads(
            (self.export_dir / "manifest.json").read_text()
        )
        self.report = ImportReport()

    def docs(self, index: str) -> Iterator[tuple[str, dict]]:
        """yield (_id, _source) of an exported index"""
        path = self.export_dir / f"{index}.ndjson"
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            for line in f:
                hit = json.loads(line)
                yield hit["_id"], hit["_source"]

    def run(self) -> ImportReport:
        """import everything in dependency order, then verify"""
        self._check_manifest()
        with transaction.atomic():
            self.import_channels()
            self.import_videos()
            self.import_playlists()
            self.import_downloads()
            self.import_subtitles()
            self.import_comments()
            self.import_config()
            self.import_download_rules()
            self.import_episode_numbers()

        self.verify()
        return self.report

    def _check_manifest(self):
        for index, expected in self.manifest.items():
            found = sum(1 for _ in self.docs(index))
            if found != expected:
                self.report.mismatches.append(
                    f"{index}: export has {found} lines, manifest {expected}"
                )

    @staticmethod
    def _upsert(model, objs: list, unique_fields: list[str]) -> None:
        """bulk insert, replacing rows that exist"""
        if not objs:
            return
        update_fields = [
            f.name
            for f in model._meta.concrete_fields
            if not f.primary_key
            and f.name not in unique_fields
            and not getattr(f, "generated", False)
        ]
        for start in range(0, len(objs), BATCH_SIZE):
            model.objects.bulk_create(
                objs[start : start + BATCH_SIZE],
                update_conflicts=True,
                unique_fields=unique_fields,
                update_fields=update_fields,
            )

    def import_channels(self):
        """ta_channel, plus channels only known from embedded video docs"""
        channels = {
            doc["channel_id"]: Channel.from_doc(doc)
            for _, doc in self.docs("ta_channel")
        }
        for _, doc in self.docs("ta_video"):
            embedded = doc.get("channel") or {}
            channel_id = embedded.get("channel_id")
            if channel_id and channel_id not in channels:
                channels[channel_id] = Channel.from_doc(embedded)
                self.report.skip(
                    "ta_channel",
                    f"{channel_id}: missing, rebuilt from video "
                    f"{doc['youtube_id']}",
                )

        self._upsert(Channel, list(channels.values()), ["channel_id"])
        self.report.counts["channel"] = len(channels)

    def import_videos(self):
        """ta_video"""
        videos = []
        for _id, doc in self.docs("ta_video"):
            if not (doc.get("channel") or {}).get("channel_id"):
                self.report.skip("ta_video", f"{_id}: no channel")
                continue
            videos.append(Video.from_doc(doc))

        self._upsert(Video, videos, ["youtube_id"])
        self.report.counts["video"] = len(videos)

    def import_playlists(self):
        """ta_playlist, entries replaced wholesale per playlist"""
        playlists, entries = [], []
        for _, doc in self.docs("ta_playlist"):
            playlists.append(Playlist(**Playlist.fields_from_doc(doc)))
            entries.extend(Playlist.entries_from_doc(doc))

        self._upsert(Playlist, playlists, ["playlist_id"])
        PlaylistEntry.objects.filter(
            playlist_id__in=[p.playlist_id for p in playlists]
        ).delete()
        PlaylistEntry.objects.bulk_create(entries, batch_size=BATCH_SIZE)
        self.report.counts["playlist"] = len(playlists)
        self.report.counts["playlist_entry"] = len(entries)

    def import_downloads(self):
        """ta_download"""
        items = [Download.from_doc(doc) for _, doc in self.docs("ta_download")]
        self._upsert(Download, items, ["youtube_id"])
        self.report.counts["download"] = len(items)

    def import_subtitles(self):
        """ta_subtitle, lines of videos that are not indexed are skipped"""
        known = set(Video.objects.values_list("youtube_id", flat=True))
        lines = []
        for _id, doc in self.docs("ta_subtitle"):
            if doc.get("youtube_id") not in known:
                self.report.skip("ta_subtitle", f"{_id}: video not indexed")
                continue
            lines.append(SubtitleLine(**SubtitleLine.fields_from_doc(doc)))

        self._upsert(SubtitleLine, lines, ["fragment_id"])
        self.report.counts["subtitle_line"] = len(lines)

    def import_comments(self):
        """ta_comment, one doc per video, replaced wholesale per video"""
        known = set(Video.objects.values_list("youtube_id", flat=True))
        comments, refreshes, video_ids = [], [], []
        for _id, doc in self.docs("ta_comment"):
            youtube_id = doc.get("youtube_id") or _id
            if youtube_id not in known:
                self.report.skip("ta_comment", f"{_id}: video not indexed")
                continue
            video_ids.append(youtube_id)
            for position, comment in enumerate(
                Comment.unique_docs(doc.get("comment_comments") or [])
            ):
                comments.append(
                    Comment(
                        video_id=youtube_id,
                        **Comment.fields_from_doc(comment, position),
                    )
                )
            refreshes.append(
                CommentRefresh(
                    video_id=youtube_id,
                    last_refresh=from_epoch(doc.get("comment_last_refresh")),
                )
            )

        Comment.objects.filter(video_id__in=video_ids).delete()
        Comment.objects.bulk_create(comments, batch_size=BATCH_SIZE)
        self._upsert(CommentRefresh, refreshes, ["video"])
        self.report.counts["comment"] = len(comments)

    def import_config(self):
        """ta_config: appsettings, notify, user_<id>"""
        imported = 0
        for _id, doc in self.docs("ta_config"):
            if _id.startswith("user_"):
                user_id = int(_id.removeprefix("user_"))
                if not Account.objects.filter(id=user_id).exists():
                    self.report.skip(
                        "ta_config", f"{_id}: user does not exist yet"
                    )
                    continue
                UserConfig.objects.update_or_create(
                    user_id=user_id, defaults={"config": doc.get("config", {})}
                )
            else:
                ConfigDocument.objects.update_or_create(
                    key=_id, defaults={"value": doc}
                )
            imported += 1

        self.report.counts["config"] = imported

    def import_download_rules(self):
        """ta_download_rule: fork backups only"""
        rules = []
        for _id, doc in self.docs("ta_download_rule"):
            channel_id = doc.get("channel_id")
            if (
                channel_id
                and not Channel.objects.filter(pk=channel_id).exists()
            ):
                self.report.skip("ta_download_rule", f"{_id}: channel gone")
                continue
            fields = {k: doc.get(k) for k in RULE_DOC_FIELDS if k in doc}
            if fields.get("published_after"):
                fields["published_after"] = date.fromisoformat(
                    fields["published_after"]
                )
            DownloadRule.objects.update_or_create(
                channel_id=channel_id, defaults=fields
            )
            rules.append(_id)
        if rules:
            self.report.counts["download_rule"] = len(rules)

    def import_episode_numbers(self):
        """ta_emby_episode: fork backups only, numbers are kept as they were"""
        numbers = [
            EpisodeNumber(
                **{k: doc[k] for k in EPISODE_DOC_FIELDS},
            )
            for _, doc in self.docs("ta_emby_episode")
        ]
        if numbers:
            EpisodeNumber.objects.bulk_create(numbers, ignore_conflicts=True)
            self.report.counts["emby_episode"] = len(numbers)

    def verify(self):
        """compare the database against the export"""
        self._verify_count(
            "ta_channel", Channel.objects.count(), at_least=True
        )
        self._verify_count("ta_video", Video.objects.count())
        self._verify_count("ta_playlist", Playlist.objects.count())
        self._verify_count("ta_download", Download.objects.count())
        self._verify_count("ta_subtitle", SubtitleLine.objects.count())
        self._verify_videos()
        self._verify_downloads()
        self._verify_playlists()

    def _verify_count(self, index: str, found: int, at_least=False):
        expected = self.manifest.get(index, 0) - len(
            self.report.skipped.get(index, [])
        )
        if at_least:
            expected = self.manifest.get(index, 0)
            if found < expected:
                self.report.mismatches.append(
                    f"{index}: {found} rows < {expected} docs"
                )
            return
        if found != expected:
            self.report.mismatches.append(
                f"{index}: {found} rows != {expected} expected"
            )

    def _verify_videos(self):
        per_channel: dict[str, int] = {}
        total_size = 0
        docs = {}
        for _id, doc in self.docs("ta_video"):
            channel_id = (doc.get("channel") or {}).get("channel_id")
            if not channel_id:
                continue  # skipped on import
            per_channel[channel_id] = per_channel.get(channel_id, 0) + 1
            total_size += doc.get("media_size") or 0
            docs[_id] = doc

        for channel_id, expected in per_channel.items():
            found = Video.objects.filter(channel_id=channel_id).count()
            if found != expected:
                self.report.mismatches.append(
                    f"channel {channel_id}: {found} videos != {expected}"
                )

        db_size = Video.objects.aggregate(s=Sum("media_size"))["s"] or 0
        if db_size != total_size:
            self.report.mismatches.append(
                f"video media_size sum {db_size} != {total_size}"
            )

        sample = random.Random(0).sample(
            sorted(docs), min(self.sample_size, len(docs))
        )
        for youtube_id in sample:
            video = (
                Video.objects.select_related("channel")
                .filter(pk=youtube_id)
                .first()
            )
            if not video:
                self.report.mismatches.append(f"video {youtube_id}: missing")
                continue
            self._compare_doc(
                f"video {youtube_id}",
                docs[youtube_id],
                video.to_doc(),
                ignore={"channel"},
            )

    def _verify_downloads(self):
        expected = sum(
            1 for _, d in self.docs("ta_download") if d["status"] == "pending"
        )
        found = Download.objects.filter(status="pending").count()
        if found != expected:
            self.report.mismatches.append(
                f"pending downloads {found} != {expected}"
            )

    def _verify_playlists(self):
        for _, doc in self.docs("ta_playlist"):
            playlist = Playlist.objects.get(pk=doc["playlist_id"])
            self._compare_doc(
                f"playlist {doc['playlist_id']}",
                doc,
                playlist.to_doc(),
                ignore={"playlist_entries"},
            )
            exported = [
                (e["idx"], e["youtube_id"])
                for e in doc.get("playlist_entries") or []
            ]
            stored = list(
                playlist.entries.order_by("idx").values_list(
                    "idx", "youtube_id"
                )
            )
            if sorted(exported) != stored:
                self.report.mismatches.append(
                    f"playlist {doc['playlist_id']}: entries differ"
                )

    def _compare_doc(self, label: str, source: dict, rebuilt: dict, ignore):
        """values present in the source doc must round trip"""
        for key, value in source.items():
            if key in ignore:
                continue
            if key in UNORDERED_KEYS:
                value = sorted(value or [])
            if _normalize(rebuilt.get(key)) != _normalize(value):
                self.report.mismatches.append(
                    f"{label}: {key} {rebuilt.get(key)!r} != {value!r}"
                )


def _normalize(value):
    """treat None, empty list and empty string alike, sort dict keys"""
    if value in (None, [], "", {}):
        return None
    return json.loads(json.dumps(value, sort_keys=True))


def rule_to_doc(rule) -> dict:
    """DownloadRule as a backup document"""
    doc = {key: getattr(rule, key) for key in RULE_DOC_FIELDS}
    doc["channel_id"] = rule.channel_id
    if rule.published_after:
        doc["published_after"] = rule.published_after.isoformat()
    return doc


def episode_to_doc(number) -> dict:
    """EpisodeNumber as a backup document"""
    return {key: getattr(number, key) for key in EPISODE_DOC_FIELDS}
