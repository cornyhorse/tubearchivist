"""tests for Download.to_doc variants and DownloadRule"""

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, download_doc
from download.models import Download, DownloadRule

pytestmark = pytest.mark.django_db


def test_to_doc_channel_indexed_lookup():
    """without annotation or argument the channel is looked up"""
    item = Download.upsert_doc(download_doc(youtube_id="a"))
    assert item.to_doc()["channel_indexed"] is False

    Channel.save_doc(channel_doc())
    assert item.to_doc()["channel_indexed"] is True


def test_to_doc_uses_annotation():
    """with_channel_indexed annotation is used by to_doc"""
    Channel.save_doc(channel_doc())
    Download.upsert_doc(download_doc(youtube_id="a"))
    Download.upsert_doc(download_doc(youtube_id="b", channel_id="UCother"))

    docs = {
        i.youtube_id: i.to_doc()
        for i in Download.objects.with_channel_indexed()
    }
    assert docs["a"]["channel_indexed"] is True
    assert docs["b"]["channel_indexed"] is False


def test_to_doc_optional_fields():
    """duration_seconds and ignore_reason only when set"""
    item = Download.upsert_doc(
        download_doc(
            youtube_id="a", duration_seconds=90, ignore_reason="too short"
        )
    )
    doc = item.to_doc(channel_indexed=False)
    assert doc["duration_seconds"] == 90
    assert doc["ignore_reason"] == "too short"
    assert str(item) == "a (pending)"


def test_rule_str():
    """default rule and channel rule names"""
    channel = Channel.save_doc(channel_doc())
    assert str(DownloadRule(channel=None)) == "rule for all channels"
    assert str(DownloadRule(channel=channel)) == (
        f"rule for {channel.channel_id}"
    )
