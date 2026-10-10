"""tests for the Postgres download queue"""

import threading
from datetime import datetime, timezone

import pytest
from common.tests.docs import download_doc
from django.db import connection, transaction
from download.models import Download

pytestmark = pytest.mark.django_db


def add(youtube_id, **overrides):
    """add a queue item from a legacy doc"""
    return Download.upsert_doc(
        download_doc(youtube_id=youtube_id, **overrides)
    )


def test_doc_round_trip():
    doc = download_doc(youtube_id="dl1", message="failed once")
    item = Download.upsert_doc(doc)

    assert item.to_doc(channel_indexed=True) == doc


def test_next_pending_order():
    add("old", timestamp=1000)
    add("new", timestamp=2000)
    add("auto", timestamp=3000, auto_start=True)
    add("ignored", timestamp=1, status="ignore")
    add("failed", timestamp=1, message="error")

    with transaction.atomic():
        assert Download.objects.next_pending().youtube_id == "auto"
    with transaction.atomic():
        assert (
            Download.objects.next_pending(auto_only=True).youtube_id == "auto"
        )

    Download.objects.filter(pk="auto").delete()
    with transaction.atomic():
        assert Download.objects.next_pending().youtube_id == "old"
    with transaction.atomic():
        assert Download.objects.next_pending(auto_only=True) is None


@pytest.mark.django_db(transaction=True)
def test_next_pending_skips_locked():
    add("first", timestamp=1000)
    add("second", timestamp=2000)
    locked = threading.Event()
    release = threading.Event()

    def hold_first():
        with transaction.atomic():
            Download.objects.next_pending()
            locked.set()
            release.wait(5)
        connection.close()

    worker = threading.Thread(target=hold_first)
    worker.start()
    try:
        assert locked.wait(5)
        with transaction.atomic():
            assert Download.objects.next_pending().youtube_id == "second"
    finally:
        release.set()
        worker.join()


def test_set_status_actions():
    add("a", message="boom")
    add("b", status="ignore")

    assert Download.objects.matching(error=True).set_status("priority") == 1
    item = Download.objects.get(pk="a")
    assert (item.status, item.auto_start, item.message) == (
        "pending",
        True,
        None,
    )

    Download.objects.filter(pk="b").update(message="x")
    Download.objects.matching(status="ignore").set_status("clear_error")
    assert Download.objects.get(pk="b").message is None

    with pytest.raises(ValueError):
        Download.objects.all().set_status("bogus")


def test_matching_filters():
    add("a", channel_id="UC1", vid_type="videos")
    add("b", channel_id="UC1", vid_type="shorts")
    add("c", channel_id="UC2", vid_type="videos", status="ignore")

    by_channel = Download.objects.matching(status="pending", channel_id="UC1")
    assert set(by_channel.values_list("pk", flat=True)) == {"a", "b"}
    shorts = Download.objects.matching(vid_type="shorts")
    assert list(shorts.values_list("pk", flat=True)) == ["b"]


def test_reset_auto_start():
    add("a", auto_start=True)
    add("b", auto_start=True)

    assert Download.objects.reset_auto_start() == 2
    assert not Download.objects.filter(auto_start=True).exists()


def test_epoch_fields_are_aware_datetimes():
    item = add("a", timestamp=1785000000)

    assert item.timestamp == datetime.fromtimestamp(
        1785000000, tz=timezone.utc
    )
