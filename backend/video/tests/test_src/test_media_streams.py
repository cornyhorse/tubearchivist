"""test ffprobe stream extraction"""

import json
import subprocess
from unittest import mock

from video.src.media_streams import MediaStreamExtractor

PROBE = {
    "streams": [
        {
            "codec_type": "video",
            "bit_rate": "1000",
            "codec_name": "av1",
            "height": 1080,
            "width": 1920,
            "index": 0,
        },
        # cover art, no bit rate
        {"codec_type": "video", "codec_name": "mjpeg", "index": 1},
        {"codec_type": "audio", "bit_rate": "128", "index": 2},
        {"codec_type": "data", "index": 3},
    ]
}


def _completed(returncode, stdout=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout)


def test_extract_metadata():
    with mock.patch("video.src.media_streams.subprocess.run") as run:
        run.return_value = _completed(0, json.dumps(PROBE))
        result = MediaStreamExtractor("/x.mp4").extract_metadata()

    assert run.call_args.args[0][-1] == "/x.mp4"
    assert result == [
        {
            "bitrate": 1000,
            "codec": "av1",
            "height": 1080,
            "index": 0,
            "type": "video",
            "width": 1920,
        },
        {"bitrate": 128, "codec": "undefined", "index": 2, "type": "audio"},
    ]


def test_extract_metadata_failed():
    with mock.patch("video.src.media_streams.subprocess.run") as run:
        run.return_value = _completed(1)
        assert not MediaStreamExtractor("/x.mp4").extract_metadata()


def test_file_size(tmp_path):
    media = tmp_path / "v.mp4"
    media.write_bytes(b"12345")
    assert MediaStreamExtractor(str(media)).get_file_size() == 5
