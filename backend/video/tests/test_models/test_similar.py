"""test video lookups by raw sql and by id"""

import pytest
from common.tests.docs import video_doc
from video.models import Video

pytestmark = pytest.mark.django_db


def test_similar_to(channel):
    del channel

    def doc(youtube_id, title, tags):
        return video_doc(
            youtube_id=youtube_id, title=title, tags=tags, description=None
        )

    Video.save_doc(doc("a", "big coaster", []))
    Video.save_doc(doc("b", "coaster coaster ride", []))
    Video.save_doc(doc("c", "small train", []))
    Video.save_doc(doc("d", "big", ["coaster"]))

    similar = Video.objects.similar_to("a")
    assert [v.youtube_id for v in similar] == ["d", "b"]
    assert similar[0].channel.name == "Test Channel"
    assert [v.youtube_id for v in Video.objects.similar_to("a", 1)] == ["d"]


def test_similar_to_unknown(channel):
    del channel
    Video.save_doc(video_doc(youtube_id="a"))
    assert not Video.objects.similar_to("missing")


def test_load_doc(video):
    assert Video.load_doc(video.youtube_id)["title"] == video.title
    assert Video.load_doc("missing") is None
