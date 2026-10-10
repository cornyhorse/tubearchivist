"""tests for the filesystem rescan, metadata extraction mocked"""

import pytest
from appsettings.src import filesystem as fs_module
from appsettings.src.filesystem import Scanner
from channel.models import Channel
from common.tests.docs import channel_doc, download_doc, video_doc
from download.models import Download
from video.models import Video

pytestmark = pytest.mark.django_db


@pytest.fixture(name="media")
def fixture_media(tmp_path, monkeypatch):
    """fake media tree: UCa has v1 (indexed) and new1, UCb has new2"""
    root = tmp_path / "media"
    for rel in [
        "UCa/v1.mp4",
        "UCa/new00000001.mp4",
        "UCa/new00000001.en.vtt",
        "UCa/.hidden.mp4",
        "UCb/new00000002.mp4",
    ]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"media")
    (root / "@eaDir").mkdir()
    monkeypatch.setattr(Scanner, "VIDEOS", str(root))
    channel = channel_doc(channel_id="UCa")
    Channel.save_doc(channel)
    Video.save_doc(video_doc(channel=channel, youtube_id="v1"))
    Video.save_doc(video_doc(channel=channel, youtube_id="gone0000001"))
    return root


@pytest.fixture(name="calls")
def fixture_calls(monkeypatch):
    """replace every external effect of indexing with a recorder"""
    calls = []
    state = {"remote_fails": set(), "embed_ok": set()}

    class FakeYoutubeVideo:
        """records delete and embed"""

        def __init__(self, youtube_id):
            self.youtube_id = youtube_id

        def delete_media_file(self):
            """record delete"""
            calls.append(("delete", self.youtube_id))

        def embed_metadata(self):
            """record embed"""
            calls.append(("embed", self.youtube_id))

    class FakeComments:
        """records comment build"""

        def __init__(self, youtube_id):
            self.youtube_id = youtube_id

        def build_json(self, upload):
            """record comments"""
            calls.append(("comments", self.youtube_id, upload))

    class FakeIndexFromEmbed:
        """index from embedded metadata, succeeds for embed_ok ids"""

        def __init__(self, file_path, use_user_conf, config):
            assert use_user_conf is True
            assert config is not None
            self.file_path = file_path

        def run_index(self):
            """record and maybe succeed"""
            calls.append(("from_embed", self.file_path))
            for youtube_id in state["embed_ok"]:
                if youtube_id in self.file_path:
                    return {"youtube_id": youtube_id}
            return None

    def fake_index_new_video(youtube_id):
        calls.append(("remote", youtube_id))
        if youtube_id in state["remote_fails"]:
            raise ValueError("no metadata")

    monkeypatch.setattr(fs_module, "YoutubeVideo", FakeYoutubeVideo)
    monkeypatch.setattr(fs_module, "Comments", FakeComments)
    monkeypatch.setattr(fs_module, "IndexFromEmbed", FakeIndexFromEmbed)
    monkeypatch.setattr(fs_module, "index_new_video", fake_index_new_video)
    monkeypatch.setattr(
        fs_module, "rand_sleep", lambda config: calls.append(("sleep",))
    )
    calls_state = calls, state
    return calls_state


def test_scan(media, task):
    # pylint: disable=unused-argument
    scanner = Scanner(task=task)

    scanner.scan()

    assert scanner.to_index == {
        ("new00000001", "UCa/new00000001.mp4"),
        ("new00000002", "UCb/new00000002.mp4"),
    }
    assert scanner.to_delete == {
        ("gone0000001", "UCa/gone0000001.mp4")
    }
    assert task.lines == [
        "Scan your filesystem for videos.",
        "Get all videos indexed.",
    ]


def test_apply_deletes_and_indexes(media, calls, app_config, task):
    # pylint: disable=unused-argument
    recorded, _ = calls
    Download.objects.create(
        **Download.fields_from_doc(download_doc(youtube_id="new00000001"))
    )
    scanner = Scanner(task=task)
    scanner.scan()

    scanner.apply()

    assert scanner.config == app_config.config
    assert ("delete", "gone0000001") in recorded
    for youtube_id in ("new00000001", "new00000002"):
        assert ("remote", youtube_id) in recorded
        assert ("comments", youtube_id, True) in recorded
        assert ("embed", youtube_id) in recorded
    assert recorded.count(("sleep",)) == 2
    assert not Download.objects.exists()
    assert "Remove 1 videos from index." in task.lines
    progress = [p for lines, p, _ in task.messages if "Index missing" in lines[0]]
    assert progress == [0.5, 1.0]


def test_apply_nothing_to_do(calls, app_config):
    # pylint: disable=unused-argument
    recorded, _ = calls
    scanner = Scanner()
    scanner.config = {"downloads": {}}

    scanner.apply()

    assert not recorded


def test_prefer_local(media, calls, app_config):
    # pylint: disable=unused-argument
    recorded, state = calls
    state["embed_ok"] = {"new00000001"}
    scanner = Scanner(prefer_local=True)
    scanner.scan()

    scanner.apply()

    assert ("delete", "gone0000001") in recorded
    assert ("remote", "new00000001") not in recorded
    assert ("remote", "new00000002") in recorded
    assert ("from_embed", str(media / "UCb/new00000002.mp4")) in recorded


def test_remote_failure_falls_back_to_embed(media, calls, app_config):
    # pylint: disable=unused-argument
    recorded, state = calls
    state["remote_fails"] = {"new00000001"}
    state["embed_ok"] = {"new00000001"}
    scanner = Scanner()
    scanner.to_index = {("new00000001", "UCa/new00000001.mp4")}

    scanner.apply()

    assert recorded == [
        ("remote", "new00000001"),
        ("from_embed", str(media / "UCa/new00000001.mp4")),
    ]


def test_remote_failure_ignored(media, calls, app_config, task):
    # pylint: disable=unused-argument
    recorded, state = calls
    state["remote_fails"] = {"new00000001"}
    scanner = Scanner(task=task, ignore_error=True)
    scanner.to_index = {("new00000001", "UCa/new00000001.mp4")}

    scanner.apply()

    assert recorded[-1] == ("sleep",)
    assert task.messages[-1] == (
        ["[scanner] Failed to index new00000001, no metadata", "Continue..."],
        False,
        "error",
    )


def test_remote_failure_ignored_without_task(media, calls, app_config):
    # pylint: disable=unused-argument
    _, state = calls
    state["remote_fails"] = {"new00000001"}
    scanner = Scanner(ignore_error=True)
    scanner.to_index = {("new00000001", "UCa/new00000001.mp4")}

    scanner.apply()


def test_remote_failure_raises(media, calls, app_config):
    # pylint: disable=unused-argument
    _, state = calls
    state["remote_fails"] = {"new00000001"}
    scanner = Scanner()
    scanner.to_index = {("new00000001", "UCa/new00000001.mp4")}

    with pytest.raises(ValueError):
        scanner.apply()
