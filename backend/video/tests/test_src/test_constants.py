"""test video enums"""

from video.src.constants import (
    OrderEnum,
    SortEnum,
    VideoTypeEnum,
    WatchedEnum,
)


def test_video_type_enum():
    assert str(VideoTypeEnum.SHORTS) == "shorts"
    assert VideoTypeEnum.values() == ["videos", "streams", "shorts", "unknown"]
    assert "unknown" not in VideoTypeEnum.values_known()
    assert VideoTypeEnum.UNKNOWN not in VideoTypeEnum.known()


def test_value_lists():
    assert SortEnum.values()[0] == "published"
    assert SortEnum.names()[-1] == "height"
    assert OrderEnum.values() == ["asc", "desc"]
    assert WatchedEnum.values() == ["watched", "unwatched", "continue"]
