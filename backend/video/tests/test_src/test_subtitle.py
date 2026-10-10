"""test subtitle download, parsing and indexing"""

import io
import json
from types import SimpleNamespace
from unittest import mock

import pytest
from common.src.env_settings import EnvironmentSettings
from common.tests.docs import subtitle_doc, video_doc
from video.models import SubtitleLine
from video.src.index import YoutubeVideo
from video.src.subtitle import (
    SubtitleParser,
    YoutubeSubtitle,
    save_subtitle_lines,
)
from video.tests.conftest import set_config

pytestmark = pytest.mark.django_db

JSON3 = {"ext": "json3", "url": "https://sub.example/en", "name": "English"}


def _video(app_config, youtube_meta=None):
    """youtube video with json_data from the test doc"""
    del app_config
    video = YoutubeVideo("vid00000001")
    video.json_data = video_doc()
    video.youtube_meta = youtube_meta or {}
    return video


def _events(*texts, start=0, step=1000):
    """json3 events with one seg each"""
    return [
        {
            "tStartMs": start + idx * step,
            "dDurationMs": step,
            "segs": [{"utf8": text}],
        }
        for idx, text in enumerate(texts)
    ]


def _response(text="", ok=True):
    return SimpleNamespace(ok=ok, text=text)


class TestGetSubtitles:
    """find relevant subtitles from youtube metadata"""

    def test_disabled(self, app_config):
        assert not YoutubeSubtitle(_video(app_config)).get_subtitles()

    def test_user_and_auto(self, app_config):
        set_config(subtitle="en, de", subtitle_source="auto")
        meta = {
            "subtitles": {
                "en": [dict(JSON3), {"ext": "vtt"}],
                "live_chat": [{"ext": "json3"}],
                "fr": [],
                "es": [{"ext": "vtt"}],
            },
            "automatic_captions": {
                "en": [dict(JSON3, url="auto-en")],
                "de": [dict(JSON3, url="auto-de")],
            },
        }
        video = _video(app_config, meta)
        result = YoutubeSubtitle(video).get_subtitles()

        assert [(i["lang"], i["source"], i["url"]) for i in result] == [
            ("en", "user", "https://sub.example/en"),
            ("de", "auto", "auto-de"),
        ]
        assert result[0]["media_url"] == (
            "UCchannel000000000000001/vid00000001.en.vtt"
        )

    def test_user_only_without_subtitles(self, app_config):
        set_config(subtitle="en", subtitle_source="user")
        video = _video(app_config, {"automatic_captions": {"en": [JSON3]}})
        assert not YoutubeSubtitle(video).get_subtitles()

    def test_bad_regex(self, app_config):
        set_config(subtitle="en(", subtitle_source="user")
        video = _video(app_config, {"subtitles": {"en": [dict(JSON3)]}})
        with pytest.raises(ValueError, match="wrong regex"):
            YoutubeSubtitle(video).get_subtitles()

    def test_unknown_source(self, app_config):
        handler = YoutubeSubtitle(_video(app_config))
        with pytest.raises(ValueError, match="unknown subtitles source"):
            handler._get_all_subtitles("other")  # pylint: disable=W0212


def test_get_indexed_subtitles(video):
    save_subtitle_lines(
        video.youtube_id,
        [
            subtitle_doc(video.youtube_id, 2, "second"),
            subtitle_doc(video.youtube_id, 1, "first"),
        ],
    )
    handler = YoutubeSubtitle(SimpleNamespace(youtube_id=video.youtube_id))
    lines = handler.get_indexed_subtitles()
    assert [i["subtitle_line"] for i in lines] == ["first", "second"]
    assert lines[0]["subtitle_channel"] == "Test Channel"


class TestDownloadSubtitles:
    """download, write and queue subtitles"""

    @pytest.fixture(name="relevant")
    def fixture_relevant(self):
        return [
            dict(JSON3, lang=lang, source="user", media_url=f"C/v.{lang}.vtt")
            for lang in ("en", "de", "fr")
        ]

    def test_download(self, app_config, dirs, relevant):
        set_config(subtitle_index=True)
        video = _video(app_config)
        responses = {
            "en": json.dumps({"events": _events("a", "b", "c", "d", "e")}),
            "de": None,
            "fr": json.dumps({"events": []}),
        }
        handler = YoutubeSubtitle(video)
        with mock.patch.object(
            handler, "_make_request", side_effect=lambda u, lang: responses[lang]
        ):
            indexed = handler.download_subtitles(relevant)

        assert indexed == [
            {
                "ext": "json3",
                "name": "English",
                "source": "user",
                "lang": "en",
                "media_url": "C/v.en.vtt",
                "url": "https://sub.example/en",
            }
        ]
        vtt = (dirs["media"] / "C" / "v.en.vtt").read_text()
        assert vtt.startswith("WEBVTT\nKind: captions\nLanguage: en")
        assert "1\n00:00:00.000 --> 00:00:01.000\na" in vtt
        assert len(video.pending_subtitle_lines) == 1
        assert video.pending_subtitle_lines[0]["subtitle_lang"] == "en"

    def test_download_no_index(self, app_config, dirs, relevant):
        video = _video(app_config)
        handler = YoutubeSubtitle(video)
        body = json.dumps({"events": _events("a")})
        with mock.patch.object(handler, "_make_request", return_value=body):
            indexed = handler.download_subtitles(relevant[:1])

        assert len(indexed) == 1
        assert not video.pending_subtitle_lines
        assert (dirs["media"] / "C" / "v.en.vtt").exists()


class TestMakeRequest:
    """subtitle http request"""

    def _request(self, app_config, response, **config):
        set_config(**config)
        video = _video(app_config)
        with mock.patch("video.src.subtitle.requests.get") as get:
            get.return_value = response
            result = YoutubeSubtitle(video)._make_request(  # noqa: W0212
                "https://sub.example", "en"
            )
        return result, get.call_args.kwargs

    def test_ok(self, app_config):
        result, kwargs = self._request(app_config, _response("body"))
        assert result == "body"
        assert "cookies" not in kwargs
        assert kwargs["timeout"] == 30

    def test_not_ok(self, app_config):
        result, _ = self._request(app_config, _response("err", ok=False))
        assert result is None

    def test_empty(self, app_config):
        result, _ = self._request(app_config, _response(""))
        assert result is None

    def test_cookie(self, app_config):
        cookie = io.StringIO(
            "# Netscape HTTP Cookie File\n"
            "# a b c d e f\n"
            ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n"
            "short line\n"
        )
        with mock.patch("video.src.subtitle.CookieHandler") as handler:
            handler.return_value.get.return_value = cookie
            _, kwargs = self._request(
                app_config, _response("body"), cookie_import=True
            )

        jar = kwargs["cookies"]
        assert [(c.name, c.value, c.domain) for c in jar] == [
            ("SID", "secret", ".youtube.com")
        ]

    def test_cookie_missing(self, app_config):
        with mock.patch("video.src.subtitle.CookieHandler") as handler:
            handler.return_value.get.return_value = None
            _, kwargs = self._request(
                app_config, _response("body"), cookie_import=True
            )
        assert "cookies" not in kwargs


def test_write_subtitle_file_chown(dirs, monkeypatch):
    monkeypatch.setattr(EnvironmentSettings, "HOST_UID", 1000)
    monkeypatch.setattr(EnvironmentSettings, "HOST_GID", 1000)
    dest = dirs["media"] / "new" / "v.en.vtt"
    with mock.patch("video.src.subtitle.os.chown") as chown:
        YoutubeSubtitle(None).write_subtitle_file(str(dest), "WEBVTT")

    assert dest.read_text() == "WEBVTT"
    chown.assert_called_once_with(str(dest), 1000, 1000)


def test_index_subtitle_without_pending_attribute():
    video = SimpleNamespace()
    YoutubeSubtitle(video).index_subtitle([{"a": 1}])
    YoutubeSubtitle(video).index_subtitle([{"b": 2}])
    assert video.pending_subtitle_lines == [{"a": 1}, {"b": 2}]


class TestDelete:
    """delete subtitle files and lines"""

    def _setup(self, video, dirs):
        save_subtitle_lines(
            video.youtube_id, [subtitle_doc(video.youtube_id, 1, "x")]
        )
        sub = dirs["media"] / "v.en.vtt"
        sub.write_text("WEBVTT")
        return sub

    def test_given_subtitles(self, video, dirs):
        sub = self._setup(video, dirs)
        target = SimpleNamespace(youtube_id=video.youtube_id, json_data={})
        subtitles = [{"media_url": "v.en.vtt"}, {"media_url": "gone.vtt"}]
        YoutubeSubtitle(target).delete(subtitles=subtitles)

        assert not sub.exists()
        assert not SubtitleLine.objects.exists()

    def test_from_json_data(self, video, dirs):
        sub = self._setup(video, dirs)
        target = SimpleNamespace(
            youtube_id=video.youtube_id,
            json_data={"subtitles": [{"media_url": "v.en.vtt"}]},
        )
        YoutubeSubtitle(target).delete()
        assert not sub.exists()
        assert not SubtitleLine.objects.exists()

    def test_nothing_to_delete(self, video, dirs):
        sub = self._setup(video, dirs)
        target = SimpleNamespace(youtube_id=video.youtube_id, json_data={})
        YoutubeSubtitle(target).delete()
        assert sub.exists()
        assert SubtitleLine.objects.exists()


def test_save_subtitle_lines(video):
    youtube_id = video.youtube_id
    save_subtitle_lines(youtube_id, [])
    assert not SubtitleLine.objects.exists()

    german = dict(subtitle_doc(youtube_id, 1, "hallo"), subtitle_lang="de")
    german["subtitle_fragment_id"] = f"{youtube_id}-de-1"
    save_subtitle_lines(youtube_id, [subtitle_doc(youtube_id, 1, "old")])
    save_subtitle_lines(youtube_id, [german])
    save_subtitle_lines(youtube_id, [subtitle_doc(youtube_id, 1, "new")])

    lines = SubtitleLine.objects.order_by("lang")
    assert [(i.lang, i.line) for i in lines] == [("de", "hallo"), ("en", "new")]


class TestSubtitleParser:
    """parse json3 subtitles"""

    def test_no_events(self):
        parser = SubtitleParser("{}", "en", "user")
        parser.process()
        assert not parser.all_cues

    def test_user(self):
        events = _events("a", "b")
        events.insert(1, {"tStartMs": 0})
        parser = SubtitleParser(json.dumps({"events": events}), "en", "user")
        parser.process()
        assert parser.all_cues == [
            {"start": "00:00:00.000", "end": "00:00:01.000", "text": "a", "idx": 1},
            {"start": "00:00:01.000", "end": "00:00:02.000", "text": "b", "idx": 3},
        ]

    def test_auto_flatten(self):
        events = [
            {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "a"}, {"utf8": "b"}]},
            {"tStartMs": 500},
            {"tStartMs": 500, "dDurationMs": 100, "segs": [{"utf8": " "}]},
            {"tStartMs": 1000, "dDurationMs": 100, "segs": [{"utf8": "c"}]},
            {"tStartMs": 3000, "dDurationMs": 1000, "segs": [{"utf8": "d"}]},
        ]
        parser = SubtitleParser(json.dumps({"events": events}), "en", "auto")
        parser.process()
        assert [i["text"] for i in parser.all_cues] == ["ab\nc", "d"]

    def test_auto_flatten_last_without_duration(self):
        events = [
            {"tStartMs": 0, "segs": [{"utf8": "a"}]},
            {"tStartMs": 10, "dDurationMs": 5, "segs": [{"utf8": "b"}]},
        ]
        flat = SubtitleParser._flat_auto_caption(events)  # noqa: W0212
        assert flat == [{"tStartMs": 0, "segs": [{"utf8": "a"}]}]

    def test_ms_conv(self):
        ms = ((1 * 60 + 2) * 60 + 3) * 1000 + 4
        assert SubtitleParser._ms_conv(ms) == "01:02:03.004"  # noqa: W0212

    def test_create_documents(self):
        texts = [str(i) for i in range(1, 8)]
        body = json.dumps({"events": _events(*texts)})
        parser = SubtitleParser(body, "en", "user")
        parser.process()
        video = SimpleNamespace(youtube_id="vid1", json_data=video_doc())
        documents = parser.create_documents(video, "user")

        assert [i["subtitle_fragment_id"] for i in documents] == [
            "vid1-en-1",
            "vid1-en-2",
        ]
        first, last = documents
        assert first["subtitle_line"] == "1 2\n 3\n 4\n 5\n"
        assert first["subtitle_start"] == "00:00:00.000"
        assert first["subtitle_end"] == "00:00:05.000"
        assert first["subtitle_channel"] == "Test Channel"
        assert first["title"] == "Riding the Big Coaster"
        # trailing cues after the last full chunk are kept
        assert last["subtitle_line"] == "6 7\n"
        assert last["subtitle_end"] == "00:00:07.000"
