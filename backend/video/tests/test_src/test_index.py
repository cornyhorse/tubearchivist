"""test indexing a single video"""

import json
from types import SimpleNamespace
from unittest import mock

import pytest
import requests
from channel.models import Channel
from common.tests.docs import playlist_doc, subtitle_doc, video_doc
from download.src.thumbnails import ThumbManager
from mutagen.mp4 import MP4, MP4MetadataError
from playlist.models import Playlist
from user.models import UserConfig as UserConfigModel
from video.models import SubtitleLine, Video
from video.src import index
from video.src.comments import Comments
from video.src.constants import VideoTypeEnum
from video.src.index import SponsorBlock, YoutubeVideo, index_new_video
from video.src.subtitle import save_subtitle_lines
from video.tests.conftest import set_config, tiny_mp4

pytestmark = pytest.mark.django_db

YOUTUBE_ID = "vid00000001"
CHANNEL_ID = "UCchannel000000000000001"

META = {
    "id": YOUTUBE_ID,
    "channel_id": CHANNEL_ID,
    "title": "Riding the Big Coaster",
    "thumbnail": "https://i.ytimg.com/vi/x/maxresdefault.jpg",
    "categories": ["Travel & Events"],
    "tags": ["pov"],
    "timestamp": 1700000000,
    "description": "a ride",
    "view_count": 10,
    "like_count": None,
}


def _response(status_code=200, payload=None):
    return SimpleNamespace(
        ok=status_code < 400,
        status_code=status_code,
        json=lambda: payload,
    )


class TestSponsorBlock:
    """sponsorblock api client"""

    def test_get_sb_id(self, django_user_model):
        with pytest.raises(ValueError):
            SponsorBlock().get_sb_id()

        user = django_user_model.objects.create(name="sb", password="x")
        sb_id = SponsorBlock(user.id).get_sb_id()
        assert len(sb_id) == 32
        assert SponsorBlock(user.id).get_sb_id() == sb_id
        stored = UserConfigModel.objects.get(user_id=user.id).config
        assert stored["sponsorblock_id"] == sb_id

    def test_post_and_vote(self, django_user_model):
        user = django_user_model.objects.create(name="sb", password="x")
        sponsor = SponsorBlock(user.id)
        assert sponsor.post_timestamps("v1", 1, 2) == ({"success": True}, 200)
        assert sponsor.vote_on_segment("uuid", 1) == ({"success": True}, 200)

    @pytest.mark.parametrize(
        "error", [requests.ReadTimeout, requests.ConnectionError]
    )
    def test_get_timestamps_error(self, error):
        with mock.patch("video.src.index.requests.get", side_effect=error):
            assert SponsorBlock().get_timestamps("v1") is False

    def test_get_timestamps_unavailable(self):
        with mock.patch("video.src.index.requests.get") as get:
            get.return_value = _response(503)
            assert SponsorBlock().get_timestamps("v1") is False

    def test_get_timestamps_not_found(self):
        sponsor = SponsorBlock()
        with mock.patch("video.src.index.requests.get") as get:
            get.return_value = _response(404)
            result = sponsor.get_timestamps("v1")

        assert result == {
            "last_refresh": sponsor.last_refresh,
            "is_enabled": True,
            "segments": [],
        }

    def test_get_timestamps(self):
        segments = [
            {"category": "sponsor", "locked": 0},
            {"category": "intro", "locked": 1},
        ]
        with mock.patch("video.src.index.requests.get") as get:
            get.return_value = _response(200, segments)
            result = SponsorBlock().get_timestamps("v1")

        assert get.call_args.kwargs["params"]["videoID"] == "v1"
        assert result["has_unlocked"] is False
        assert [i["actionType"] for i in result["segments"]] == [
            "skip",
            "none",
        ]


def test_save_to_db_writes_pending_lines(app_config, channel):
    del app_config, channel
    video = YoutubeVideo(YOUTUBE_ID)
    video.json_data = video_doc()
    video.pending_subtitle_lines = [subtitle_doc(YOUTUBE_ID, 1, "hello")]
    video.save_to_db()

    assert SubtitleLine.objects.get().line == "hello"
    assert not video.pending_subtitle_lines


class TestBuildJson:
    """build the video document from youtube metadata"""

    @pytest.fixture(name="probe")
    def fixture_probe(self):
        """fake ffprobe for duration and streams"""
        with (
            mock.patch("video.src.index.get_duration_sec", return_value=90),
            mock.patch("video.src.media_streams.subprocess.run") as run,
        ):
            run.return_value = SimpleNamespace(
                returncode=0, stdout=json.dumps({"streams": []})
            )
            yield

    def _build(self, meta, **kwargs):
        video = YoutubeVideo(YOUTUBE_ID, video_type=VideoTypeEnum.SHORTS)
        with mock.patch("common.src.index_generic.YtWrap") as yt_wrap:
            yt_wrap.return_value.extract.return_value = (meta, None)
            video.build_json(**kwargs)
        return video

    def test_no_metadata(self, app_config):
        del app_config
        video = self._build(False)
        assert not video.json_data
        assert not video.offline_import

    def test_build(self, app_config, channel, dirs, probe):
        del app_config, channel, probe
        set_config(integrate_ryd=True, integrate_sponsorblock=True)
        cached = dirs["cache"] / "download" / f"{YOUTUBE_ID}.mp4"
        cached.parent.mkdir()
        cached.write_bytes(b"1234")

        with (
            mock.patch.object(index.ryd_client, "get") as ryd,
            mock.patch.object(SponsorBlock, "get_timestamps") as sponsor,
        ):
            ryd.return_value = {"status": 200, "dislikes": 3, "rating": 4.0}
            sponsor.return_value = {"segments": []}
            video = self._build(dict(META))

        data = video.json_data
        assert data["vid_type"] == "shorts"
        assert data["description"] == "a ride"
        assert data["published"] == 1700000000
        assert data["channel"]["channel_id"] == CHANNEL_ID
        assert data["media_url"] == f"{CHANNEL_ID}/{YOUTUBE_ID}.mp4"
        assert data["player"]["duration"] == 90
        assert data["streams"] == [] and data["media_size"] == 4
        assert data["stats"] == {
            "view_count": 10,
            "like_count": 0,
            "dislike_count": 3,
            "average_rating": 4.0,
        }
        assert data["sponsorblock"] == {"segments": []}
        assert not video.offline_import

    def test_offline_overwrite(self, app_config, channel, tmp_path, probe):
        del app_config, channel, probe
        media = tmp_path / "file.mp4"
        media.write_bytes(b"12")
        meta = dict(META)
        meta.pop("description")
        video = self._build(
            False, youtube_meta_overwrite=meta, media_path=str(media)
        )
        assert video.offline_import
        assert "description" not in video.json_data
        assert "sponsorblock" not in video.json_data
        assert video.json_data["media_size"] == 2


class TestCheckGetSb:
    """channel overwrite wins over the global sponsorblock setting"""

    @pytest.mark.parametrize(
        "overwrites,expected",
        [
            (None, True),
            ({}, True),
            ({"download_format": "x"}, True),
            ({"integrate_sponsorblock": False}, False),
        ],
    )
    def test_check(self, app_config, overwrites, expected):
        del app_config
        set_config(integrate_sponsorblock=True)
        video = YoutubeVideo(YOUTUBE_ID)
        video.json_data = {"channel": {"channel_overwrites": overwrites}}
        assert video._check_get_sb() is expected  # pylint: disable=W0212


class TestPublished:
    """published from timestamp or upload date"""

    @pytest.mark.parametrize(
        "meta,expected",
        [
            ({"timestamp": 5}, 5),
            ({"timestamp": 5.7}, 5),
            ({"timestamp": "1.7e9"}, 1700000000),
            ({"timestamp": "bad", "upload_date": "20231114"}, "2023-11-14"),
            ({"upload_date": "20231114"}, "2023-11-14"),
        ],
    )
    def test_published(self, app_config, meta, expected):
        del app_config
        video = YoutubeVideo(YOUTUBE_ID)
        video.youtube_meta = meta
        assert video._build_published() == expected  # pylint: disable=W0212

    def test_no_date(self, app_config):
        del app_config
        video = YoutubeVideo(YOUTUBE_ID)
        video.youtube_meta = {"upload_date": None}
        with pytest.raises(ValueError, match="published date"):
            video._build_published()  # pylint: disable=W0212


def test_validate_id(app_config):
    del app_config
    video = YoutubeVideo(YOUTUBE_ID)
    video.youtube_meta = dict(META, id="otherid0000")
    with pytest.raises(ValueError, match="unexpected redirect"):
        video.process_youtube_meta()


class TestDlCachePath:
    """find the media file of a new video"""

    def _video(self):
        video = YoutubeVideo(YOUTUBE_ID)
        video.json_data = video_doc()
        return video

    def test_channel_path(self, app_config, dirs):
        del app_config
        media = dirs["media"] / CHANNEL_ID / f"{YOUTUBE_ID}.mp4"
        media.parent.mkdir()
        media.write_bytes(b"")
        assert self._video().build_dl_cache_path() == str(media)

    def test_missing(self, app_config, dirs):
        del app_config, dirs
        with pytest.raises(FileNotFoundError):
            self._video().build_dl_cache_path()


class TestDelete:
    """delete a video with its file, playlists, subtitles, comments"""

    def test_not_indexed(self, app_config):
        del app_config
        with pytest.raises(FileNotFoundError):
            YoutubeVideo("missing0000").delete_media_file()

    def test_delete(self, app_config, video, dirs):
        del app_config
        set_config(comment_max="10")
        youtube_id = video.youtube_id
        Video.objects.filter(pk=youtube_id).update(
            subtitles=[{"media_url": "sub.en.vtt"}]
        )
        media = dirs["media"] / video.media_url
        media.parent.mkdir()
        media.write_bytes(b"")
        (dirs["media"] / "sub.en.vtt").write_text("WEBVTT")
        save_subtitle_lines(youtube_id, [subtitle_doc(youtube_id, 1, "x")])
        comments = Comments(youtube_id)
        comments.json_data = {
            "comment_comments": [{"comment_id": "c1"}],
            "comment_last_refresh": 1,
        }
        comments.upload_comments()
        Playlist.save_doc(
            playlist_doc("PLcustom", [youtube_id], playlist_type="custom")
        )
        Playlist.save_doc(playlist_doc("PLregular", [youtube_id]))

        with mock.patch.object(ThumbManager, "download_playlist_thumb"):
            YoutubeVideo(youtube_id).delete_media_file()

        assert not media.exists()
        assert not (dirs["media"] / "sub.en.vtt").exists()
        assert not Video.objects.exists()
        assert not SubtitleLine.objects.exists()
        assert Comments(youtube_id).get_comments() is False
        custom = Playlist.objects.get(playlist_id="PLcustom")
        assert not custom.entries.exists()
        regular = Playlist.objects.get(playlist_id="PLregular")
        assert regular.entries.get().youtube_id == youtube_id

    def test_delete_missing_file_comments_off(self, app_config, video, dirs):
        del app_config, dirs
        with mock.patch.object(Comments, "delete_comments") as delete:
            YoutubeVideo(video.youtube_id).delete_media_file()
        delete.assert_not_called()
        assert not Video.objects.exists()


class TestRyd:
    """return youtube dislike stats"""

    def _video(self):
        video = YoutubeVideo(YOUTUBE_ID)
        video.json_data = {"stats": {"dislike_count": 0}}
        return video

    def test_error(self, app_config):
        del app_config
        video = self._video()
        with mock.patch.object(index.ryd_client, "get", side_effect=OSError):
            video._get_ryd_stats()  # pylint: disable=W0212
        assert video.json_data["stats"] == {"dislike_count": 0}

    def test_not_found(self, app_config):
        del app_config
        video = self._video()
        with mock.patch.object(index.ryd_client, "get") as get:
            get.return_value = {"status": 404}
            video._get_ryd_stats()  # pylint: disable=W0212
        assert video.json_data["stats"] == {"dislike_count": 0}


def test_sponsorblock_unavailable(app_config):
    del app_config
    video = YoutubeVideo(YOUTUBE_ID)
    video.json_data = {}
    with mock.patch.object(SponsorBlock, "get_timestamps", return_value=False):
        video._get_sponsorblock()  # pylint: disable=W0212
    assert "sponsorblock" not in video.json_data


class TestCheckSubtitles:
    """add subtitles while indexing"""

    def _video(self, meta):
        video = YoutubeVideo(YOUTUBE_ID)
        video.json_data = video_doc()
        video.youtube_meta = meta
        return video

    def test_offline(self, app_config):
        del app_config
        video = self._video(False)
        video.offline_import = True
        video.check_subtitles(subtitle_files=["/a/x.en.vtt", "/a/x.de.vtt"])
        assert [i["media_url"] for i in video.json_data["subtitles"]] == [
            f"{CHANNEL_ID}/{YOUTUBE_ID}.en.vtt",
            f"{CHANNEL_ID}/{YOUTUBE_ID}.de.vtt",
        ]
        assert video.json_data["subtitles"][0]["source"] == "file"

    def test_no_metadata(self, app_config):
        del app_config
        video = self._video(False)
        video.check_subtitles()
        assert "subtitles" not in video.json_data

    def test_none_relevant(self, app_config):
        del app_config
        video = self._video({"subtitles": {}})
        video.check_subtitles()
        assert "subtitles" not in video.json_data

    def test_download(self, app_config):
        del app_config
        set_config(subtitle="en", subtitle_source="user")
        meta = {"subtitles": {"en": [{"ext": "json3", "url": "u"}]}}
        video = self._video(meta)
        with mock.patch(
            "video.src.index.YoutubeSubtitle.download_subtitles"
        ) as download:
            download.return_value = [{"lang": "en"}]
            video.check_subtitles()
        assert video.json_data["subtitles"] == [{"lang": "en"}]
        assert download.call_args.kwargs["relevant_subtitles"][0]["lang"] == (
            "en"
        )


class TestEmbed:
    """embed metadata into the media file"""

    def test_not_indexed(self, app_config):
        del app_config
        video = YoutubeVideo("missing0000")
        video.embed_metadata()
        assert video.json_data is None

    def test_disabled(self, app_config, video, dirs):
        del app_config
        path = tiny_mp4(dirs["media"] / video.media_url)
        YoutubeVideo(video.youtube_id).embed_metadata()
        assert not MP4(path).tags

    def test_file_missing(self, app_config, video, dirs):
        del app_config, dirs
        set_config(add_metadata=True)
        with mock.patch.object(ThumbManager, "embed_video_art") as art:
            YoutubeVideo(video.youtube_id).embed_metadata()
        art.assert_called_once()

    def test_mp4_error(self, app_config, video, dirs):
        del app_config
        set_config(add_metadata=True)
        tiny_mp4(dirs["media"] / video.media_url)
        with mock.patch(
            "video.src.index.MP4", side_effect=MP4MetadataError("broken")
        ):
            YoutubeVideo(video.youtube_id).embed_metadata()

    def test_embed(self, app_config, video, dirs):
        del app_config
        set_config(add_metadata=True)
        youtube_id = video.youtube_id
        path = tiny_mp4(dirs["media"] / video.media_url)
        Video.objects.filter(pk=youtube_id).update(
            comment_count=1, subtitles=[{"media_url": "x.en.vtt"}]
        )
        save_subtitle_lines(youtube_id, [subtitle_doc(youtube_id, 1, "x")])
        Playlist.save_doc(playlist_doc("PL1", [youtube_id]))
        Channel.objects.update(description="")

        to_embed = YoutubeVideo(youtube_id)
        to_embed.get_from_db()
        to_embed.json_data["description"] = ""
        to_embed.embed_metadata()

        tags = MP4(path).tags
        assert tags["\xa9nam"] == [video.title]
        assert tags["\xa9ART"] == ["Test Channel"]
        assert "desc" not in tags
        embedded = json.loads(bytes(tags["----:com.tubearchivist:ta"][0]))
        assert embedded["video"]["youtube_id"] == youtube_id
        assert embedded["comments"] is False
        assert embedded["subtitles"][0]["subtitle_line"] == "x"
        assert embedded["playlists"][0]["playlist_id"] == "PL1"

    def test_embed_description(self, app_config, video, dirs):
        del app_config
        set_config(add_metadata=True)
        path = tiny_mp4(dirs["media"] / video.media_url)
        YoutubeVideo(video.youtube_id).embed_metadata()

        tags = MP4(path).tags
        assert tags["desc"] == [video.description]
        assert tags["ldes"] == [video.description]
        embedded = json.loads(bytes(tags["----:com.tubearchivist:ta"][0]))
        assert embedded["comments"] is None
        assert embedded["subtitles"] is None
        assert embedded["playlists"] is None


class TestIndexNewVideo:
    """index a newly downloaded video"""

    def test_existing_reindexes(self, app_config, video):
        del app_config
        reindexed = SimpleNamespace(json_data={"reindexed": True})
        with mock.patch("appsettings.src.reindex.Reindex") as reindex:
            reindex.return_value.reindex_single_video.return_value = reindexed
            assert index_new_video(video.youtube_id) == {"reindexed": True}
        reindex.return_value.reindex_single_video.assert_called_once_with(
            youtube_id=video.youtube_id, from_download=True
        )

    def test_no_metadata(self, app_config):
        del app_config
        with mock.patch.object(YoutubeVideo, "build_json"):
            with pytest.raises(ValueError, match="failed to get metadata"):
                index_new_video("missing0000")

    def test_new(self, app_config, channel):
        del app_config, channel

        def build_json(self):
            self.json_data = video_doc()

        with (
            mock.patch.object(YoutubeVideo, "build_json", build_json),
            mock.patch.object(ThumbManager, "download_video_thumb") as thumb,
        ):
            result = index_new_video(YOUTUBE_ID, VideoTypeEnum.STREAMS)

        thumb.assert_called_once_with(url=video_doc()["vid_thumb_url"])
        assert result["youtube_id"] == YOUTUBE_ID
        assert Video.objects.filter(pk=YOUTUBE_ID).exists()
