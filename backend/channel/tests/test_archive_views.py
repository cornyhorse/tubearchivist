"""an archived channel can't be asked to talk to YouTube"""

import pytest
from channel import views
from channel.models import Channel, EmbyShow, Source
from channel.src.index import YoutubeChannel
from jobs.src import handlers

pytestmark = pytest.mark.django_db

URL = "/api/channel/arch_music/"


@pytest.fixture(name="archived")
def fixture_archived():
    """an archived channel"""
    return Channel.objects.create(
        channel_id="arch_music", name="Music", source=Source.ARCHIVE
    )


@pytest.fixture(name="delays")
def fixture_delays(monkeypatch, fake_delay):
    """celery tasks the views start"""
    fakes = {
        "subscribe_to": fake_delay(),
        "index_channel_playlists": fake_delay(),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(views, name, fake)
    return fakes


@pytest.mark.parametrize(
    "data",
    [
        {"channel_subscribed": True},
        {"auto_download": True},
        {"channel_overwrites": {"index_playlists": True}},
    ],
)
def test_checks_are_refused(client, archived, delays, data):
    del archived

    response = client.post(URL, data, format="json")

    assert response.status_code == 400
    assert "nothing to check" in response.json()["error"]
    assert not delays["index_channel_playlists"].calls
    assert Channel.objects.get().subscribed is False


def test_emby_settings_are_allowed(client, archived, delays):
    del archived, delays

    response = client.post(
        URL,
        {
            "channel_subscribed": False,
            "emby_show": "never",
            "emby_numbering": "absolute_frozen",
            "emby_tags": ["family"],
        },
        format="json",
    )

    assert response.status_code == 200
    channel = Channel.objects.get()
    assert channel.emby_show == EmbyShow.NEVER
    assert channel.emby_numbering == "absolute_frozen"
    assert channel.emby_tags == ["family"]


def test_subscribing_raises(archived, app_config):
    del archived, app_config

    with pytest.raises(ValueError, match="archived"):
        YoutubeChannel("arch_music").change_subscribe(True)

    YoutubeChannel("arch_music").change_subscribe(False)
    assert Channel.objects.get().subscribed is False


def test_job_helper_skips_archive_targets():
    class Ctx:
        """a job context with a target"""

        def __init__(self, target):
            self.job = type("Job", (), {"target_id": target})()

    assert handlers._archived(Ctx("arch_music")) == {  # noqa: SLF001
        "skipped": "archived items have no YouTube data"
    }
    assert handlers._archived(Ctx("UCreal")) is None  # noqa: SLF001
