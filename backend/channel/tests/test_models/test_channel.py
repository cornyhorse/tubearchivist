"""tests for channel and video models"""

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, video_doc
from django.contrib.postgres.search import SearchQuery
from video.models import Video

pytestmark = pytest.mark.django_db


def test_channel_doc_round_trip():
    doc = channel_doc()
    channel = Channel.from_doc(doc)
    channel.save()

    assert Channel.objects.get().to_doc() == doc


def test_channel_overwrites_round_trip():
    doc = channel_doc(
        channel_overwrites={"autodelete_days": 30, "index_playlists": False}
    )
    Channel.from_doc(doc).save()

    channel = Channel.objects.get()
    assert channel.autodelete_days == 30
    assert channel.to_doc()["channel_overwrites"] == {
        "autodelete_days": 30,
        "index_playlists": False,
    }


def test_video_doc_round_trip():
    Channel.from_doc(channel_doc()).save()
    doc = video_doc()
    Video.from_doc(doc).save()

    rebuilt = Video.objects.select_related("channel").get().to_doc()

    assert rebuilt == doc


def test_search_vector_weights_title_over_description():
    Channel.from_doc(channel_doc()).save()
    Video.from_doc(
        video_doc(youtube_id="vid_title01", title="steam locomotive")
    ).save()
    Video.from_doc(
        video_doc(
            youtube_id="vid_descr01",
            title="something else",
            description="a steam locomotive appears",
        )
    ).save()

    query = SearchQuery("steam & locomotive", search_type="raw", config="simple")
    found = Video.objects.filter(search=query).values_list("pk", flat=True)

    assert set(found) == {"vid_title01", "vid_descr01"}


def test_channel_search_on_tags():
    Channel.from_doc(channel_doc()).save()

    query = SearchQuery("coasters", config="simple")
    assert Channel.objects.filter(search=query).count() == 1
