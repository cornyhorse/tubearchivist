"""shared fixtures of the Emby writer tests"""

import pytest


@pytest.fixture(name="media")
def fixture_media(tmp_path):
    """fake archive with three small media files and thumbs"""
    root = tmp_path / "media"
    cache = tmp_path / "cache"
    (root / "UC1").mkdir(parents=True)
    (cache / "videos").mkdir(parents=True)
    for youtube_id in ("aaa", "bbb", "ccc"):
        (root / "UC1" / f"{youtube_id}.mp4").write_bytes(
            b"video " + youtube_id.encode()
        )
        thumb_dir = cache / "videos" / youtube_id[0]
        thumb_dir.mkdir(exist_ok=True)
        (thumb_dir / f"{youtube_id}.jpg").write_bytes(
            b"jpg " + youtube_id.encode()
        )
    return root, cache
