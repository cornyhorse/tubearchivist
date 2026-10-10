"""test embedding metadata and restoring from it"""

import json
import shutil
from unittest import mock

import pytest
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    playlist_doc,
    subtitle_doc,
    video_doc,
)
from mutagen.mp4 import MP4
from playlist.models import Playlist
from video.models import Comment, CommentRefresh, SubtitleLine, Video
from video.src.comments import Comments
from video.src.index import YoutubeVideo
from video.src.meta_embed import IndexFromEmbed, MetadataEmbed
from video.src.subtitle import save_subtitle_lines
from video.tests.conftest import set_config, tiny_mp4

pytestmark = pytest.mark.django_db

YOUTUBE_ID = "vid00000001"
CHANNEL_ID = "UCchannel000000000000001"
TA_KEY = "----:com.tubearchivist:ta"

COMMENT = {
    "comment_id": "c1",
    "comment_parent": "root",
    "comment_author": "someone",
    "comment_author_id": "UCsomeone",
    "comment_author_is_uploader": False,
    "comment_author_thumbnail": "https://yt3.example/a",
    "comment_text": "nice ride",
    "comment_time_text": "1 year ago",
    "comment_timestamp": 1785000000,
    "comment_likecount": 3,
    "comment_is_favorited": True,
}


def _write_art(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"art-" + path.name.encode())


@pytest.fixture(name="embedded")
def fixture_embedded(app_config, video, dirs, tmp_path):
    """
    video with comments, subtitles, playlists and artwork embedded into
    a file outside the archive, then everything removed from the index
    """
    del app_config
    set_config(add_metadata=True)
    youtube_id = video.youtube_id
    Video.objects.filter(pk=youtube_id).update(
        comment_count=1,
        subtitles=[
            {
                "ext": "json3",
                "lang": "en",
                "media_url": f"{CHANNEL_ID}/{youtube_id}.en.vtt",
                "name": "English",
                "source": "auto",
                "url": None,
            }
        ],
    )
    comments = Comments(youtube_id)
    comments.json_data = {
        "comment_comments": [COMMENT],
        "comment_last_refresh": 1785000000,
    }
    comments.upload_comments()
    save_subtitle_lines(
        youtube_id,
        [
            dict(subtitle_doc(youtube_id, 2, "two"), subtitle_end="00:00:04"),
            subtitle_doc(youtube_id, 1, "one"),
        ],
    )
    Playlist.save_doc(playlist_doc("PL1", [youtube_id]))
    Playlist.save_doc(
        playlist_doc("PLcustom", [youtube_id], playlist_type="custom")
    )

    cache = dirs["cache"]
    _write_art(cache / "videos" / "v" / f"{youtube_id}.jpg")
    for kind in ("banner", "thumb", "tvart"):
        _write_art(cache / "channels" / f"{CHANNEL_ID}_{kind}.jpg")
    _write_art(cache / "playlists" / "PL1.jpg")

    archived = tiny_mp4(dirs["media"] / video.media_url)
    YoutubeVideo(youtube_id).embed_metadata()

    source = tmp_path / "import" / f"{youtube_id}.mp4"
    source.parent.mkdir()
    shutil.move(archived, source)
    shutil.rmtree(cache)
    cache.mkdir()
    shutil.rmtree(dirs["media"] / CHANNEL_ID)

    Video.objects.all().delete()
    Playlist.objects.all().delete()
    Channel.objects.all().delete()
    return str(source)


class TestMetadataEmbed:
    """bulk embed"""

    def test_embed(self, app_config, channel):
        del app_config, channel
        for youtube_id in ("b0000000001", "a0000000001"):
            Video.save_doc(video_doc(youtube_id=youtube_id))
        task = mock.Mock()
        with mock.patch.object(YoutubeVideo, "embed_metadata") as embed:
            MetadataEmbed(task=task).embed()
            MetadataEmbed().embed()

        assert embed.call_count == 4
        task.send_progress.assert_called_once_with(
            ["Processing Videos 2/2"], progress=1.0
        )


class TestRoundTrip:
    """restore everything from the embedded metadata"""

    def test_restore(self, embedded, dirs):
        set_config(comment_max="10", subtitle="de", subtitle_source="user")
        result = IndexFromEmbed(embedded).run_index()

        assert result["youtube_id"] == YOUTUBE_ID
        video = Video.objects.get()
        assert video.channel.name == "Test Channel"
        assert video.channel.subscribed is True
        assert Comment.objects.get().text == "nice ride"
        assert CommentRefresh.objects.exists()
        lines = SubtitleLine.objects.order_by("index")
        assert [i.line for i in lines] == ["one", "two"]
        vtt = dirs["media"] / CHANNEL_ID / f"{YOUTUBE_ID}.en.vtt"
        assert "1\n00:00:01.000 --> 00:00:02.000\none" in vtt.read_text()
        assert sorted(Playlist.objects.values_list("pk", flat=True)) == [
            "PL1",
            "PLcustom",
        ]

        archived = dirs["media"] / CHANNEL_ID / f"{YOUTUBE_ID}.mp4"
        assert archived.exists()
        cache = dirs["cache"]
        assert (cache / "videos" / "v" / f"{YOUTUBE_ID}.jpg").exists()
        for kind in ("banner", "thumb", "tvart"):
            art = cache / "channels" / f"{CHANNEL_ID}_{kind}.jpg"
            assert art.read_bytes() == b"art-" + art.name.encode()
        playlist_art = cache / "playlists" / "PL1.jpg"
        assert playlist_art.read_bytes() == b"art-PL1.jpg"
        assert not (cache / "playlists" / "PLcustom.jpg").exists()

        # run again from the archive: nothing changes, file stays
        assert IndexFromEmbed(str(archived)).run_index()
        assert archived.exists()

    def test_restore_without_user_conf(self, embedded):
        IndexFromEmbed(embedded, use_user_conf=False).run_index()

        video = Video.objects.get()
        assert video.watched is False
        assert video.channel.subscribed is False
        assert list(Playlist.objects.values_list("pk", flat=True)) == ["PL1"]
        playlist = Playlist.objects.get()
        assert playlist.subscribed is False
        assert playlist.sort_order == "top"
        assert SubtitleLine.objects.count() == 2
        assert Comment.objects.exists()

    def test_comments_disabled_existing_items(self, embedded, monkeypatch):
        """existing channel stays, comments off keeps comments out"""
        monkeypatch.setattr(IndexFromEmbed, "HOST_UID", 1000)
        monkeypatch.setattr(IndexFromEmbed, "HOST_GID", 1000)
        Channel.objects.create(
            **Channel.fields_from_doc(channel_doc(channel_name="Kept"))
        )
        with mock.patch("video.src.meta_embed.os.chown") as chown:
            IndexFromEmbed(embedded).run_index()

        # new channel folder and the moved file
        assert chown.call_count == 2
        assert Video.objects.get().channel.name == "Kept"
        assert not Comment.objects.exists()

    def test_existing_video_comments_subtitles(self, embedded, dirs):
        """already indexed parts are not overwritten"""
        set_config(comment_max="10", subtitle="de", subtitle_source="user")
        IndexFromEmbed(embedded).run_index()
        Comment.objects.update(text="kept")
        SubtitleLine.objects.update(line="kept")
        Video.objects.update(title="kept")
        archived = dirs["media"] / CHANNEL_ID / f"{YOUTUBE_ID}.mp4"

        IndexFromEmbed(str(archived)).run_index()

        assert Video.objects.get().title == "kept"
        assert set(Comment.objects.values_list("text", flat=True)) == {
            "kept"
        }
        assert set(SubtitleLine.objects.values_list("line", flat=True)) == {
            "kept"
        }


class TestGetEmbedded:
    """reading the embedded json, potentially untrusted"""

    def _run(self, tags):
        with mock.patch("video.src.meta_embed.MP4", return_value=tags):
            return IndexFromEmbed("/x.mp4", config={"a": 1}).run_index()

    def test_not_embedded(self, tmp_path):
        path = tiny_mp4(tmp_path / "x.mp4")
        with mock.patch("video.src.meta_embed.AppConfig") as app_config:
            app_config.return_value.config = {"downloads": {}}
            assert IndexFromEmbed(path).run_index() is None

    @pytest.mark.parametrize(
        "value,message",
        [
            ("text", "unexpected embedded data"),
            (["text"], "unexpected embedded data"),
        ],
    )
    def test_unexpected(self, value, message):
        with pytest.raises(ValueError, match=message):
            self._run({TA_KEY: value})

    def test_bad_json(self, tmp_path):
        path = tiny_mp4(tmp_path / "x.mp4")
        tags = MP4(path)
        tags[TA_KEY] = [b"{not json"]
        tags.save()
        with pytest.raises(ValueError, match="decoding failed"):
            IndexFromEmbed(path, config={"a": 1}).run_index()

    def test_no_video(self, tmp_path):
        path = tiny_mp4(tmp_path / "x.mp4")
        tags = MP4(path)
        tags[TA_KEY] = [json.dumps({"video": None}).encode()]
        tags.save()
        with pytest.raises(ValueError, match="does not contain video"):
            IndexFromEmbed(path, config={"a": 1}).run_index()


class TestValidation:
    """invalid embedded parts raise"""

    def test_channel_missing(self):
        with pytest.raises(ValueError, match="missing channel"):
            IndexFromEmbed("/x").index_channel({"video": {"title": "x"}})

    def test_channel_invalid(self):
        with pytest.raises(ValueError, match="channel serializer failed"):
            IndexFromEmbed("/x").index_channel(
                {"video": {"channel": {"channel_id": "x"}}}
            )

    def test_video_invalid(self):
        with pytest.raises(ValueError, match="video serializer failed"):
            IndexFromEmbed("/x").index_video(
                {"video": {"channel": {}, "title": "x"}}, {}
            )

    def test_playlists_invalid(self):
        with pytest.raises(ValueError, match="playlist serializer failed"):
            IndexFromEmbed("/x").index_playlists(
                {"playlists": [{"playlist_id": "x"}]}, None
            )

    @pytest.mark.parametrize("playlists", [None, {"a": 1}])
    def test_playlists_skipped(self, playlists):
        IndexFromEmbed("/x").index_playlists({"playlists": playlists}, None)

    def test_subtitles_invalid(self):
        with pytest.raises(ValueError, match="subtitle serializer failed"):
            IndexFromEmbed("/x").index_subtitles(
                {"subtitles": [{"youtube_id": "x"}]}, None
            )

    def test_comments_invalid(self):
        with pytest.raises(ValueError, match="comments serializer failed"):
            IndexFromEmbed("/x").index_comments(
                {"comments": {"youtube_id": "x"}}
            )


class TestProcessSubtitle:
    """decide if an embedded subtitle is restored"""

    INDEXED = {("en", "auto")}

    def _check(self, lang="de", use_user_conf=True, config=None):
        handler = IndexFromEmbed(
            "/x", use_user_conf=use_user_conf, config=config
        )
        return handler._process_subtitle(  # pylint: disable=W0212
            self.INDEXED, lang, "user"
        )

    @staticmethod
    def _config(subtitle, source="user"):
        return {"downloads": {"subtitle": subtitle, "subtitle_source": source}}

    def test_already_indexed(self):
        handler = IndexFromEmbed("/x", use_user_conf=False)
        assert not handler._process_subtitle(  # pylint: disable=W0212
            self.INDEXED, "en", "auto"
        )

    def test_rules(self):
        assert self._check(use_user_conf=False)
        assert not self._check()
        assert not self._check(config=self._config(None))
        assert not self._check(config=self._config("de", None))
        assert self._check(config=self._config("en, fr"))
        assert not self._check(config=self._config("en, de"))


def test_restore_art_item(tmp_path):
    handler = IndexFromEmbed("/x")
    existing = tmp_path / "existing.jpg"
    existing.write_bytes(b"old")
    tags = {"covr": [b"new"]}

    handler._restore_art_item(tags, "covr", str(existing))  # noqa: W0212
    assert existing.read_bytes() == b"old"

    target = tmp_path / "new" / "art.jpg"
    handler._restore_art_item(tags, "missing", str(target))  # noqa: W0212
    assert not target.exists()

    handler._restore_art_item(tags, "covr", str(target))  # noqa: W0212
    assert target.read_bytes() == b"new"


class TestParts:
    """single restore steps"""

    def test_channel_overwrites_dropped(self, app_config):
        del app_config
        doc = channel_doc(channel_overwrites={"index_playlists": True})
        handler = IndexFromEmbed("/x", use_user_conf=False)
        channel = handler.index_channel({"video": {"channel": doc}})
        assert "channel_overwrites" not in channel
        assert Channel.objects.get().subscribed is False

    def test_archive_new_folder(self, app_config, dirs, tmp_path):
        del app_config
        source = tmp_path / "x.mp4"
        source.write_bytes(b"x")
        video = YoutubeVideo(YOUTUBE_ID)
        video.json_data = video_doc()
        IndexFromEmbed(str(source)).archive_video(video)
        assert (dirs["media"] / CHANNEL_ID / f"{YOUTUBE_ID}.mp4").exists()

    def test_playlist_not_expected(self, app_config, tmp_path):
        del app_config
        path = tiny_mp4(tmp_path / "x.mp4")
        video = YoutubeVideo(YOUTUBE_ID)
        video.json_data = video_doc()
        doc = playlist_doc("PL1", [YOUTUBE_ID])
        IndexFromEmbed(path).index_playlists({"playlists": [doc]}, video)
        assert not Playlist.objects.exists()

    def test_nothing_embedded(self):
        handler = IndexFromEmbed("/x")
        handler.index_subtitles({"subtitles": []}, None)
        handler.index_comments({})
        assert not SubtitleLine.objects.exists()
        assert not Comment.objects.exists()
