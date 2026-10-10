"""tests for PendingInteract"""

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, download_doc
from download.models import Download
from download.src.queue_interact import PendingInteract

pytestmark = pytest.mark.django_db


def add(youtube_id, **overrides):
    """add a queue item from a legacy doc"""
    return Download.upsert_doc(download_doc(youtube_id=youtube_id, **overrides))


def test_delete_item():
    """deletes only the one item"""
    add("a")
    add("b")
    PendingInteract("a").delete_item()
    assert list(Download.objects.values_list("pk", flat=True)) == ["b"]


def test_delete_bulk():
    """deletes matching status, channel and type"""
    add("a", channel_id="UC1")
    add("b", channel_id="UC2")
    add("c", channel_id="UC1", status="ignore")
    add("d", channel_id="UC1", vid_type="shorts")

    PendingInteract(status="pending").delete_bulk(
        channel_id="UC1", vid_type="videos"
    )
    assert set(Download.objects.values_list("pk", flat=True)) == {
        "b",
        "c",
        "d",
    }


def test_update_bulk_and_status():
    """bulk and single status changes"""
    add("a", status="ignore")
    add("b", status="ignore", message="failed")
    add("c")

    PendingInteract(status="ignore").update_bulk(
        channel_id=None, vid_type=None, new_status="pending", error=False
    )
    assert Download.objects.get(pk="a").status == "pending"
    assert Download.objects.get(pk="b").status == "ignore"

    PendingInteract("c", "ignore").update_status()
    assert Download.objects.get(pk="c").status == "ignore"


def test_get_item():
    """document and 200, or None and 404"""
    Channel.save_doc(channel_doc())
    add("a")

    item, status = PendingInteract("a").get_item()
    assert status == 200
    assert item["youtube_id"] == "a"
    assert item["channel_indexed"] is True

    assert PendingInteract("missing").get_item() == (None, 404)


def test_get_channel():
    """channel name from the queue, NA when not queued"""
    add("a", channel_id="UC1", channel_name="Queued Channel")

    assert PendingInteract("UC1").get_channel() == {
        "channel_id": "UC1",
        "channel_name": "Queued Channel",
    }
    assert PendingInteract("UC9").get_channel()["channel_name"] == "NA"
