"""tests for manual import, ffmpeg/ffprobe and metadata mocked"""

import json
import os

import pytest
from appsettings.src import manual as manual_module
from appsettings.src.manual import ImportFolderScanner, ManualImport
from common.src.env_settings import EnvironmentSettings
from common.tests.docs import download_doc
from download.models import Download
from PIL import Image

pytestmark = pytest.mark.django_db

YT_ID = "abcdefghij4"


class FakeCompleted:
    """subprocess.CompletedProcess stand in"""

    def __init__(self, stdout=b""):
        self.stdout = stdout


@pytest.fixture(name="ffmpeg")
def fixture_ffmpeg(monkeypatch):
    """fake ffprobe answers from a dict, ffmpeg writes its output file"""
    state = {"streams": {}, "calls": []}

    def fake_run(args, **kwargs):
        state["calls"].append((args, kwargs))
        if args[0] == "ffprobe":
            streams = state["streams"].get(args[-1], [])
            return FakeCompleted(json.dumps({"streams": streams}).encode())
        if "-i" in args and args.index("-i") > 3:
            out = args[args.index("-i") - 1]  # dump_attachment target
        elif args[3] == "-map":
            out = args[-1]
        else:
            out = args[3]
        with open(out, "wb") as f:
            f.write(b"converted")
        return FakeCompleted()

    monkeypatch.setattr(manual_module.subprocess, "run", fake_run)
    return state


@pytest.fixture(name="import_dir")
def fixture_import_dir(tmp_path, monkeypatch):
    """scratch /cache/import"""
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    monkeypatch.setattr(ImportFolderScanner, "IMPORT_DIR", str(import_dir))
    return import_dir


def touch(path, data=b"x"):
    """write a file, return its str path"""
    path.write_bytes(data)
    return str(path)


def png(path):
    """write a tiny real png"""
    Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(path)
    return str(path)


# scanning and matching


def test_match_files_groups_by_base_name(import_dir):
    names = [
        f"Clip [{YT_ID}].mp4",
        f"Clip [{YT_ID}].info.json",
        f"Clip [{YT_ID}].en.vtt",
        f"Clip [{YT_ID}].de.vtt",
        f"Clip [{YT_ID}].jpg",
        f"Clip [{YT_ID}].txt",
        "other.xq.webm",
        ".DS_Store",
    ]
    for name in names:
        touch(import_dir / name)
    scanner = ImportFolderScanner()

    scanner.match_files(scanner.get_all_files())

    base = str(import_dir / f"Clip [{YT_ID}]")
    assert scanner.to_import == [
        {
            "media": f"{base}.mp4",
            "video_id": False,
            "metadata": f"{base}.info.json",
            "thumb": f"{base}.jpg",
            "subtitle": [f"{base}.de.vtt", f"{base}.en.vtt"],
        },
        {
            "media": str(import_dir / "other.xq.webm"),
            "video_id": False,
            "metadata": False,
            "thumb": False,
            "subtitle": [],
        },
    ]


def test_match_files_last_group_without_media_dropped(import_dir):
    touch(import_dir / "only.json")
    scanner = ImportFolderScanner()

    scanner.match_files(scanner.get_all_files())

    assert scanner.to_import == []


def test_group_without_media_fails(import_dir, app_config):
    # pylint: disable=unused-argument
    touch(import_dir / "a.json")
    touch(import_dir / f"b{YT_ID}.mp4")
    scanner = ImportFolderScanner()

    with pytest.raises(ValueError):
        scanner.scan()


def test_scan_runs_manual_import(
    import_dir, app_config, ffmpeg, monkeypatch, task
):
    # pylint: disable=unused-argument
    long_name = "x" * 60 + f" [{YT_ID}].mp4"
    touch(import_dir / long_name)
    touch(import_dir / f"short [{YT_ID[::-1]}].mp4")
    touch(import_dir / f"short [{YT_ID[::-1]}].jpg")
    runs = []

    def fake_run(self):
        runs.append((self.current_video, self.ignore_error, self.prefer_local))

    monkeypatch.setattr(ManualImport, "run", fake_run)
    scanner = ImportFolderScanner(
        task=task, ignore_error=True, prefer_local=True
    )

    result = scanner.scan()

    assert [r[0]["video_id"] for r in runs] == [YT_ID[::-1], YT_ID]
    assert runs[0][1:] == (True, True)
    assert result == [r[0] for r in runs]
    assert task.messages == [
        (["Scanning your import folder."], False, "info"),
        (
            ["Import queue processing video 1/2", f"short [{YT_ID[::-1]}].mp4"],
            0.5,
            "info",
        ),
        (
            ["Import queue processing video 2/2", "x" * 50 + "..."],
            1.0,
            "info",
        ),
    ]


# youtube id detection


@pytest.mark.parametrize(
    "file_name, expected",
    [
        (f"/import/Title [{YT_ID}].mp4", YT_ID),
        (f"/import/{YT_ID}.mkv", YT_ID),
        ("/import/short.mp4", False),
    ],
)
def test_extract_id_from_filename(file_name, expected):
    # pylint: disable=protected-access
    assert ImportFolderScanner._extract_id_from_filename(file_name) == expected


def test_detect_id_from_json(import_dir):
    current = {
        "media": str(import_dir / "noid.mp4"),
        "metadata": touch(import_dir / "noid.json", b'{"id": "fromjson123"}'),
    }

    ImportFolderScanner()._detect_youtube_id(current)  # noqa

    assert current["video_id"] == "fromjson123"


def test_detect_id_fails(import_dir):
    current = {"media": str(import_dir / "noid.mp4"), "metadata": False}

    with pytest.raises(ValueError, match="failed to find video id"):
        ImportFolderScanner()._detect_youtube_id(current)  # noqa


# thumbnails


def test_dump_thumb_keeps_existing():
    current = {"media": "/x.mkv", "thumb": "/x.jpg"}
    ImportFolderScanner()._dump_thumb(current)  # pylint: disable=W0212
    assert current["thumb"] == "/x.jpg"


def test_dump_thumb_mkv_attachment(import_dir, ffmpeg):
    media = touch(import_dir / f"{YT_ID}.mkv")
    ffmpeg["streams"][media] = [
        {"codec_type": "video", "codec_name": "vp9"},
        {"codec_type": "attachment", "tags": {"filename": "notes.txt"}},
        {
            "codec_type": "attachment",
            "tags": {"mimetype": "image/png", "filename": "cover.png"},
        },
    ]
    current = {"media": media, "thumb": False}

    ImportFolderScanner()._dump_thumb(current)  # pylint: disable=W0212

    assert current["thumb"] == str(import_dir / f"{YT_ID}.png")
    args, _ = ffmpeg["calls"][-1]
    assert args[3] == "-dump_attachment:t:1"


def test_dump_thumb_mkv_without_cover(import_dir, ffmpeg):
    media = touch(import_dir / "clip.mkv")
    ffmpeg["streams"][media] = [{"codec_type": "video"}]
    current = {"media": media, "thumb": False}

    ImportFolderScanner()._dump_thumb(current)  # pylint: disable=W0212

    assert current["thumb"] is False
    assert len(ffmpeg["calls"]) == 1


def test_dump_thumb_mp4(import_dir, ffmpeg):
    media = touch(import_dir / f"{YT_ID}.mp4")
    ffmpeg["streams"][media] = [
        {"codec_type": "video", "codec_name": "h264"},
        {"codec_type": "video", "codec_name": "png"},
    ]
    current = {"media": media, "thumb": False}

    ImportFolderScanner()._dump_thumb(current)  # pylint: disable=W0212

    assert current["thumb"] == str(import_dir / f"{YT_ID}.png")
    args, kwargs = ffmpeg["calls"][-1]
    assert args[:3] == ["ffmpeg", "-i", media] and kwargs["check"]


@pytest.mark.parametrize("name", ["plain.mp4", "clip.webm"])
def test_dump_thumb_nothing_embedded(import_dir, ffmpeg, name):
    media = touch(import_dir / name)
    ffmpeg["streams"][media] = [{"codec_type": "video", "codec_name": "h264"}]
    current = {"media": media, "thumb": False}

    ImportFolderScanner()._dump_thumb(current)  # pylint: disable=W0212

    assert current["thumb"] is False


def test_convert_thumb(import_dir):
    scanner = ImportFolderScanner()
    jpg = {"thumb": touch(import_dir / "a.jpg")}
    none = {"thumb": False}
    other = {"thumb": png(import_dir / "b.png")}

    for current in (jpg, none, other):
        scanner._convert_thumb(current)  # pylint: disable=W0212

    assert jpg["thumb"] == str(import_dir / "a.jpg")
    assert none["thumb"] is False
    assert other["thumb"] == str(import_dir / "b.jpg")
    assert Image.open(other["thumb"]).format == "JPEG"
    assert not (import_dir / "b.png").exists()


# subtitles and conversion


def test_get_subtitles_from_streams(import_dir, ffmpeg):
    media = touch(import_dir / f"{YT_ID}.mkv")
    ffmpeg["streams"][media] = [
        {"codec_type": "video"},
        {"codec_type": "subtitle", "tags": {"language": "eng"}},
        {"codec_type": "subtitle", "tags": {"language": "deu"}},
    ]
    current = {"media": media, "subtitle": []}

    ImportFolderScanner()._get_subtitles(current)  # pylint: disable=W0212

    base = str(import_dir / YT_ID)
    assert current["subtitle"] == [f"{base}.en.vtt", f"{base}.de.vtt"]
    args, _ = ffmpeg["calls"][1]
    assert args == ["ffmpeg", "-i", media, "-map", "0:1", f"{base}.en.vtt"]


def test_get_subtitles_keeps_files_and_skips_webm(import_dir, ffmpeg):
    scanner = ImportFolderScanner()
    existing = {"media": "/x.mkv", "subtitle": ["/x.en.vtt"]}
    webm = {"media": touch(import_dir / "c.webm"), "subtitle": []}

    scanner._get_subtitles(existing)  # pylint: disable=W0212
    scanner._get_subtitles(webm)  # pylint: disable=W0212

    assert existing["subtitle"] == ["/x.en.vtt"]
    assert webm["subtitle"] == []
    assert len(ffmpeg["calls"]) == 1


def test_convert_video(import_dir, ffmpeg):
    scanner = ImportFolderScanner()
    mp4 = {"media": touch(import_dir / "a.mp4")}
    mkv = {"media": touch(import_dir / "b.mkv")}

    scanner._convert_video(mp4)  # pylint: disable=W0212
    scanner._convert_video(mkv)  # pylint: disable=W0212

    assert mp4["media"] == str(import_dir / "a.mp4")
    assert mkv["media"] == str(import_dir / "b.mp4")
    assert sorted(os.listdir(import_dir)) == ["a.mp4", "b.mp4"]
    assert len(ffmpeg["calls"]) == 1


# ManualImport


@pytest.fixture(name="archive")
def fixture_archive(tmp_path, monkeypatch):
    """scratch media dir, owned by the current user"""
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(EnvironmentSettings, "MEDIA_DIR", str(media))
    return media


@pytest.fixture(name="meta")
def fixture_meta(monkeypatch):
    """fake YoutubeVideo, ThumbManager, Comments, IndexFromEmbed"""
    state = {
        "json_data": {
            "media_url": f"UCa/{YT_ID}.mp4",
            "vid_thumb_url": "https://i.ytimg.com/x.jpg",
        },
        "offline": False,
        "embed": None,
        "build_raises": False,
        "calls": [],
    }
    calls = state["calls"]

    class FakeYoutubeVideo:
        """youtube video with canned metadata"""

        def __init__(self, youtube_id):
            self.youtube_id = youtube_id
            self.json_data = None
            self.offline_import = state["offline"]

        def build_json(self, youtube_meta_overwrite, media_path):
            """canned build"""
            calls.append(("build", youtube_meta_overwrite, media_path))
            if state["build_raises"]:
                raise ValueError("remote failed")
            self.json_data = state["json_data"]

        def check_subtitles(self, subtitle_files):
            """record"""
            calls.append(("subtitles", subtitle_files))

        def save_to_db(self):
            """record"""
            calls.append(("save", self.youtube_id))

        def embed_metadata(self):
            """record"""
            calls.append(("embed", self.youtube_id))

    class FakeThumbManager:
        """thumb paths into tmp"""

        def __init__(self, youtube_id):
            self.youtube_id = youtube_id

        def vid_thumb_path(self, absolute, create_folder):
            """target in the archive dir"""
            assert absolute and create_folder
            return os.path.join(EnvironmentSettings.MEDIA_DIR, "thumb.jpg")

        def download_video_thumb(self, url):
            """record"""
            calls.append(("download_thumb", url))

    class FakeComments:
        """record comments"""

        def __init__(self, youtube_id):
            self.youtube_id = youtube_id

        def build_json(self, upload):
            """record"""
            calls.append(("comments", self.youtube_id, upload))

    class FakeIndexFromEmbed:
        """canned embedded metadata"""

        def __init__(self, media, use_user_conf, config):
            assert use_user_conf is False and config is not None
            self.media = media

        def run_index(self):
            """record"""
            calls.append(("from_embed", self.media))
            return state["embed"]

    monkeypatch.setattr(manual_module, "YoutubeVideo", FakeYoutubeVideo)
    monkeypatch.setattr(manual_module, "ThumbManager", FakeThumbManager)
    monkeypatch.setattr(manual_module, "Comments", FakeComments)
    monkeypatch.setattr(manual_module, "IndexFromEmbed", FakeIndexFromEmbed)
    return state


@pytest.fixture(name="video_files")
def fixture_video_files(import_dir):
    """identified video with metadata, thumb and a subtitle"""
    Download.objects.create(
        **Download.fields_from_doc(download_doc(youtube_id=YT_ID))
    )
    return {
        "media": touch(import_dir / f"{YT_ID}.mp4"),
        "video_id": YT_ID,
        "metadata": touch(import_dir / f"{YT_ID}.info.json", b'{"id": 1}'),
        "thumb": touch(import_dir / f"{YT_ID}.jpg"),
        "subtitle": [touch(import_dir / f"{YT_ID}.en.vtt")],
    }


def run_import(video_files, ignore_error=False, prefer_local=False):
    """run ManualImport with an empty config"""
    ManualImport(
        video_files,
        {"downloads": {}},
        ignore_error=ignore_error,
        prefer_local=prefer_local,
    ).run()


def test_manual_import(video_files, meta, archive, import_dir, monkeypatch):
    monkeypatch.setattr(EnvironmentSettings, "HOST_UID", os.getuid())
    monkeypatch.setattr(EnvironmentSettings, "HOST_GID", os.getgid())

    run_import(video_files)

    assert sorted(p.name for p in (archive / "UCa").iterdir()) == [
        f"{YT_ID}.en.vtt",
        f"{YT_ID}.mp4",
    ]
    assert not os.listdir(import_dir)
    assert not Download.objects.exists()
    assert meta["calls"] == [
        ("build", {"id": 1}, video_files["media"]),
        ("subtitles", video_files["subtitle"]),
        ("save", YT_ID),
        ("download_thumb", "https://i.ytimg.com/x.jpg"),
        ("comments", YT_ID, True),
        ("embed", YT_ID),
    ]


def test_manual_import_offline_moves_thumb(
    video_files, meta, archive, import_dir
):
    meta["offline"] = True
    (archive / "UCa").mkdir()
    video_files["metadata"] = False
    os.remove(import_dir / f"{YT_ID}.info.json")

    run_import(video_files)

    assert (archive / "thumb.jpg").exists()
    assert meta["calls"][0] == ("build", False, video_files["media"])
    assert not os.listdir(import_dir)


def test_prefer_local_embedded(video_files, meta, archive, import_dir):
    # pylint: disable=unused-argument
    meta["embed"] = {"youtube_id": YT_ID}

    run_import(video_files, prefer_local=True)

    assert meta["calls"] == [("from_embed", video_files["media"])]
    assert os.listdir(import_dir) == [f"{YT_ID}.mp4"]
    assert not Download.objects.exists()


def test_prefer_local_falls_back_to_remote(video_files, meta, archive):
    run_import(video_files, prefer_local=True)

    assert meta["calls"][0] == ("from_embed", video_files["media"])
    assert meta["calls"][1][0] == "build"
    assert (archive / "UCa" / f"{YT_ID}.mp4").exists()


def test_remote_fails_embedded_used(video_files, meta, archive):
    meta["build_raises"] = True
    meta["embed"] = dict(meta["json_data"])

    run_import(video_files)

    assert (archive / "UCa" / f"{YT_ID}.mp4").exists()


def test_remote_fails_raises(video_files, meta, archive):
    # pylint: disable=unused-argument
    meta["build_raises"] = True

    with pytest.raises(ValueError):
        run_import(video_files)


def test_remote_fails_ignored(video_files, meta, archive, import_dir):
    # pylint: disable=unused-argument
    meta["build_raises"] = True

    run_import(video_files, ignore_error=True)

    assert len(os.listdir(import_dir)) == 4


def test_no_metadata_raises(video_files, meta, archive):
    # pylint: disable=unused-argument
    meta["json_data"] = None

    with pytest.raises(ValueError) as err:
        run_import(video_files)

    assert "no metadata found" in str(err.value.__cause__)


def test_no_metadata_ignored(video_files, meta, archive, import_dir):
    # pylint: disable=unused-argument
    meta["json_data"] = None

    run_import(video_files, ignore_error=True)

    assert len(os.listdir(import_dir)) == 4
    assert [c[0] for c in meta["calls"]] == ["build"]


def test_cleanup_tolerates_missing_files(video_files, meta):
    for key in ("metadata", "thumb"):
        os.remove(video_files[key])
    os.remove(video_files["subtitle"][0])

    ManualImport(video_files, {}, False, False)._cleanup()  # noqa

    assert not Download.objects.exists()
    assert not meta["calls"]


def test_process_videos_without_task(import_dir, app_config, monkeypatch):
    # pylint: disable=unused-argument
    touch(import_dir / f"{YT_ID}.mp4")
    touch(import_dir / f"{YT_ID}.jpg")
    touch(import_dir / f"{YT_ID}.en.vtt")
    runs = []
    monkeypatch.setattr(ManualImport, "run", lambda self: runs.append(self))

    ImportFolderScanner().scan()

    assert runs[0].current_video["video_id"] == YT_ID


def test_dump_mpv_thumb_keeps_name(import_dir, ffmpeg):
    # pylint: disable=unused-argument
    media = touch(import_dir / "trackmkv.mkv")

    new_path = ImportFolderScanner.dump_mpv_thumb(media, 0, ".png")

    assert new_path == str(import_dir / "trackmkv.png")
