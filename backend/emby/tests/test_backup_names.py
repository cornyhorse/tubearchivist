"""frozen Emby names survive a backup and restore (review E6)"""

import json

import pytest

from emby.models import EpisodeNumber
from storage.importer import EsImporter, episode_to_doc

pytestmark = pytest.mark.django_db

NUMBER = {
    "series_kind": "channel",
    "series_id": "UC1",
    "scheme": "date",
    "youtube_id": "v1",
    "season": 2025,
    "episode": 31400,
}


def import_numbers(tmp_path, docs):
    """import ta_emby_episode documents from an export dir"""
    (tmp_path / "manifest.json").write_text("{}")
    (tmp_path / "ta_emby_episode.ndjson").write_text(
        "".join(
            json.dumps({"_id": str(n), "_source": doc}) + "\n"
            for n, doc in enumerate(docs)
        )
    )
    EsImporter(tmp_path).import_episode_numbers()


def test_names_round_trip(tmp_path):
    doc = episode_to_doc(
        EpisodeNumber(**NUMBER, name="Old title", series_name="Old channel")
    )

    import_numbers(tmp_path, [doc])

    number = EpisodeNumber.objects.get()
    assert (number.name, number.series_name) == ("Old title", "Old channel")


def test_backup_from_before_frozen_names(tmp_path):
    import_numbers(tmp_path, [NUMBER])

    number = EpisodeNumber.objects.get()
    assert (number.episode, number.name, number.series_name) == (
        31400,
        "",
        "",
    )
