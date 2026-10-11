"""test playlist query building"""

import pytest
from common.tests.docs import playlist_doc
from playlist.models import Playlist
from playlist.src.query_building import QueryBuilder

pytestmark = pytest.mark.django_db


def test_build_queryset():
    """filters and name order"""
    Playlist.save_doc(playlist_doc("PL1", [], playlist_name="b list"))
    Playlist.save_doc(
        playlist_doc(
            "PL2", [], playlist_name="a list", playlist_subscribed=True
        )
    )
    Playlist.save_doc(
        playlist_doc(
            "PL3", [], playlist_channel_id="UCx", playlist_type="custom"
        )
    )

    def ids(**kwargs):
        queryset = QueryBuilder(**kwargs).build_queryset()
        return list(queryset.values_list("playlist_id", flat=True))

    # case insensitive like the ES to_lower keyword sort
    assert ids() == ["PL2", "PL1", "PL3"]
    assert ids(subscribed=True) == ["PL2"]
    assert ids(channel="UCx") == ["PL3"]
    assert ids(type="custom") == ["PL3"]


def test_parse_type():
    """test type is parsed"""
    qb = QueryBuilder(type="regular")
    with pytest.raises(ValueError):
        qb.parse_type("invalid")

    assert qb.parse_type("regular") == "regular"
