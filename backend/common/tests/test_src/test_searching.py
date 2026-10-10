"""tests for search: parsing, Postgres full text ranking, trigram fallback"""

import pytest
from channel.models import Channel
from common.src import searching
from common.src.searching import (
    QueryBuilder,
    SearchForm,
    SearchParser,
    build_tsquery,
)
from common.tests.docs import (
    channel_doc,
    playlist_doc,
    subtitle_doc,
    video_doc,
)
from playlist.models import Playlist
from video.models import SubtitleLine, Video

COASTER_ID = "UCchannel000000000000001"
RAIL_ID = "UCchannel000000000000002"
HIGHLIGHT = '<span class="settings-current">{}</span>'


def save_channel(channel_id, name, **overrides) -> dict:
    """save channel, return its doc"""
    doc = channel_doc(
        channel_id=channel_id,
        channel_name=name,
        channel_description="",
        channel_tags=[],
        **overrides,
    )
    Channel.save_doc(doc)
    return doc


def save_video(youtube_id, title, channel, **overrides):
    """save video without the tags/category of the base builder"""
    doc = video_doc(
        channel=channel,
        youtube_id=youtube_id,
        title=title,
        description=overrides.pop("description", ""),
        tags=overrides.pop("tags", []),
        category=overrides.pop("category", []),
        **overrides,
    )
    return Video.save_doc(doc)


def save_line(youtube_id, index, line, **overrides):
    """save subtitle line"""
    doc = subtitle_doc(youtube_id, index, line)
    doc.update(overrides)
    return SubtitleLine.objects.create(**SubtitleLine.fields_from_doc(doc))


@pytest.fixture(name="library")
def fixture_library(db):  # pylint: disable=unused-argument
    """two channels with videos, playlists and subtitle lines"""
    coaster = save_channel(COASTER_ID, "Coaster Fans")
    rail = save_channel(
        RAIL_ID,
        "Railway Adventures",
        channel_active=False,
        channel_subscribed=False,
    )
    save_video("coaster0001", "Riding the Big Coaster", coaster)
    save_video("coaster0002", "Wooden Coaster Review", coaster, active=False)
    save_video("rail0000001", "Steam Train Journey", rail)
    save_video(
        "rail0000002", "Night Ride", rail, description="steam in the fog"
    )

    Playlist.save_doc(
        playlist_doc(
            "PLcoaster",
            ["coaster0001"],
            playlist_name="Best Coaster Rides",
            playlist_description="",
            playlist_channel="Coaster Fans",
        )
    )
    Playlist.save_doc(
        playlist_doc(
            "PLrail",
            ["rail0000001"],
            playlist_name="Rail Coaster Mix",
            playlist_description="",
            playlist_channel="Railway Adventures",
            playlist_channel_id=RAIL_ID,
            playlist_active=False,
            playlist_subscribed=True,
        )
    )

    save_line("coaster0001", 0, "here comes the big drop")
    save_line("coaster0001", 1, "the big drop again, big fun")
    save_line(
        "coaster0001", 2, "big drop in german", subtitle_lang="de"
    )
    save_line(
        "coaster0001", 3, "big drop by a human", subtitle_source="user"
    )
    save_line("rail0000001", 0, "the steam whistle before the big drop")


def search(query: str) -> dict:
    """run search, return results"""
    return SearchForm().multi_search(query)


def ids(results: list[dict], key: str) -> list[str]:
    """id field of each processed result"""
    return [result[key] for result in results]


# parser


@pytest.mark.parametrize(
    "query, expected_map, expected_type",
    [
        (
            "Big Coaster",
            {"index": "ta_video,ta_channel,ta_playlist", "term": "big coaster"},
            "simple",
        ),
        (
            "video:coaster active:yes channel:coaster fans",
            {
                "index": "ta_video",
                "term": "coaster",
                "active": True,
                "channel": "coaster fans",
            },
            "video",
        ),
        (
            "channel:rail active:no subscribed:yes",
            {
                "index": "ta_channel",
                "term": "rail",
                "active": False,
                "subscribed": True,
            },
            "channel",
        ),
        (
            "playlist:mix subscribed:no",
            {"index": "ta_playlist", "term": "mix", "subscribed": False},
            "playlist",
        ),
        (
            "full:drop lang:en source:auto fuzzy:no",
            {
                "index": "ta_subtitle",
                "term": "drop",
                "lang": ["en"],
                "source": ["auto"],
                "fuzzy": ["no"],
            },
            "full",
        ),
        (
            "unknown:word other:thing",
            {
                "index": "ta_video,ta_channel,ta_playlist",
                "term": "unknown:word other:thing",
            },
            "simple",
        ),
        ("video:", {"index": "ta_video"}, "video"),
        (
            "10:30:00 video:clock:tower",
            {
                "index": "ta_video,ta_channel,ta_playlist",
                "term": "10:30:00 video:clock:tower",
            },
            "simple",
        ),
        (
            "coaster index:ta_channel",
            {
                "index": "ta_video,ta_channel,ta_playlist",
                "term": "coaster index:ta_channel",
            },
            "simple",
        ),
        (
            "video:a:b",
            {"index": "ta_video", "term": "a:b"},
            "video",
        ),
    ],
)
def test_parser(query, expected_map, expected_type):
    query_map, query_type = SearchParser(query).run()
    assert query_type == expected_type
    assert query_map == expected_map


# tsquery


def test_build_tsquery_reduces_words():
    assert build_tsquery("") is None
    assert build_tsquery("?!. --") is None
    assert build_tsquery("It's, big!") is not None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "term, prefix_last, matches",
    [
        ("riding big coa", True, True),
        ("riding big coa", False, False),
        ("coaster riding", True, True),
        ("coaster banana", True, False),
        ("RIDING, the big-coaster!", True, True),
        ("ride", True, False),
    ],
)
def test_build_tsquery_matching(library, term, prefix_last, matches):
    """every word required, last word only as prefix when asked"""
    del library
    query = build_tsquery(term, prefix_last=prefix_last)
    hit = Video.objects.filter(pk="coaster0001", search=query).exists()
    assert hit is matches


# simple search


@pytest.mark.django_db
def test_simple_search_across_indexes(library):
    del library
    response = search("coaster")
    results = response["results"]

    assert response["queryType"] == "simple"
    assert ids(results["video_results"], "youtube_id") == [
        "coaster0001",
        "coaster0002",
    ]
    assert ids(results["channel_results"], "channel_id") == [COASTER_ID]
    assert sorted(ids(results["playlist_results"], "playlist_id")) == [
        "PLcoaster",
        "PLrail",
    ]
    assert results["fulltext_results"] == []


@pytest.mark.django_db
def test_simple_search_prefix_on_last_word(library):
    del library
    results = search("riding big coa")["results"]
    assert ids(results["video_results"], "youtube_id") == ["coaster0001"]
    assert results["channel_results"] == []


@pytest.mark.django_db
def test_simple_search_every_word_required(library):
    del library
    results = search("steam journey")["results"]
    assert ids(results["video_results"], "youtube_id") == ["rail0000001"]


@pytest.mark.django_db
def test_simple_search_ordered_by_score(library):
    """title (weight A) ranks above description (weight D)"""
    del library
    results = search("steam")["results"]
    videos = results["video_results"]

    assert ids(videos, "youtube_id") == ["rail0000001", "rail0000002"]
    assert videos[0]["_score"] > videos[1]["_score"] > 0


@pytest.mark.django_db
def test_simple_search_sorts_hits_of_all_indexes_by_score(library):
    del library
    hits = QueryBuilder({"term": "coaster"}, "simple").run()
    scores = [hit[2] for hit in hits]

    assert scores == sorted(scores, reverse=True)
    assert {hit[0] for hit in hits} == {"ta_video", "ta_channel", "ta_playlist"}
    assert all(hit[3] is None for hit in hits)


@pytest.mark.django_db
def test_search_without_word_characters_finds_nothing(library):
    del library
    results = search("?!")["results"]
    assert results == {
        "video_results": [],
        "channel_results": [],
        "playlist_results": [],
        "fulltext_results": [],
    }


# trigram fallback


@pytest.mark.django_db
def test_trigram_fallback_on_misspelling(library):
    del library
    results = search("coastr")["results"]

    assert ids(results["video_results"], "youtube_id") == [
        "coaster0001",
        "coaster0002",
    ]
    assert ids(results["channel_results"], "channel_id") == [COASTER_ID]


@pytest.mark.django_db
@pytest.mark.parametrize("fuzzy", ["no", "0"])
def test_no_trigram_fallback_when_fuzzy_off(library, fuzzy):
    del library
    results = search(f"video:coastr fuzzy:{fuzzy}")["results"]
    assert results["video_results"] == []


@pytest.mark.django_db
def test_no_trigram_fallback_when_tsquery_matches(library):
    """fuzzy only kicks in when the full text search finds nothing"""
    del library
    results = search("video:review")["results"]
    assert ids(results["video_results"], "youtube_id") == ["coaster0002"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "word, fuzzy, found",
    [
        # word similarity to "coaster": coasting 0.56, coazter 0.45
        ("coasting", "", True),
        ("coasting", " fuzzy:1", False),
        ("coasting", " fuzzy:2", True),
        ("coazter", "", False),
        ("coazter", " fuzzy:1", False),
        ("coazter", " fuzzy:2", True),
        ("coazter", " fuzzy:9", True),
    ],
)
def test_trigram_fallback_respects_threshold(library, word, fuzzy, found):
    """auto needs 0.5 word similarity, 1 needs 0.6, 2 and above 0.4"""
    del library
    videos = search(f"video:{word}{fuzzy}")["results"]["video_results"]
    expected = ["coaster0001", "coaster0002"] if found else []
    assert ids(videos, "youtube_id") == expected


@pytest.mark.parametrize(
    "fuzzy, expected",
    [
        (None, "auto"),
        (["no"], None),
        (["0"], None),
        (["auto"], "auto"),
        (["yes"], "auto"),
        (["1"], "1"),
        (["2"], "2"),
        (["7"], "2"),
    ],
)
def test_get_fuzzy(fuzzy, expected):
    query_map = {"term": "x"}
    if fuzzy is not None:
        query_map["fuzzy"] = fuzzy
    assert QueryBuilder(query_map, "simple")._get_fuzzy() == expected


# video filters


@pytest.mark.django_db
def test_video_search_active_filter(library):
    del library
    active = search("video:coaster active:yes")["results"]["video_results"]
    inactive = search("video:coaster active:no")["results"]["video_results"]

    assert ids(active, "youtube_id") == ["coaster0001"]
    assert ids(inactive, "youtube_id") == ["coaster0002"]


@pytest.mark.django_db
def test_video_search_channel_filter(library):
    del library
    by_rail = search("video:steam channel:railway")["results"]
    by_coaster = search("video:steam channel:coaster")["results"]
    misspelled = search("video:ride channel:railwy")["results"]

    assert ids(by_rail["video_results"], "youtube_id") == [
        "rail0000001",
        "rail0000002",
    ]
    assert by_coaster["video_results"] == []
    assert ids(misspelled["video_results"], "youtube_id") == ["rail0000002"]


@pytest.mark.django_db
def test_video_search_without_term_lists_filtered(library):
    del library
    videos = search("video: channel:coaster")["results"]["video_results"]

    assert sorted(ids(videos, "youtube_id")) == ["coaster0001", "coaster0002"]
    assert all(video["_score"] == 0 for video in videos)


@pytest.mark.django_db
def test_video_search_result_size_cap(library):
    del library
    coaster = channel_doc(channel_id=COASTER_ID, channel_name="Coaster Fans")
    Video.objects.bulk_create(
        [
            Video.from_doc(
                video_doc(
                    channel=coaster,
                    youtube_id=f"bulk{idx:07d}",
                    title=f"Loop number {idx}",
                )
            )
            for idx in range(searching.RESULT_SIZE + 5)
        ]
    )

    results = search("loop")["results"]
    assert len(results["video_results"]) == searching.RESULT_SIZE

    simple = QueryBuilder({"term": "coaster"}, "simple").run()
    assert len(simple) == searching.RESULT_SIZE


# channel and playlist filters


@pytest.mark.django_db
@pytest.mark.parametrize(
    "query, expected",
    [
        ("channel:fans", [COASTER_ID]),
        ("channel:adventures active:yes", []),
        ("channel:adventures active:no", [RAIL_ID]),
        ("channel:adventures subscribed:no", [RAIL_ID]),
        ("channel:fans subscribed:no", []),
        ("channel: subscribed:yes", [COASTER_ID]),
    ],
)
def test_channel_search_filters(library, query, expected):
    del library
    results = search(query)["results"]
    assert ids(results["channel_results"], "channel_id") == expected
    assert results["video_results"] == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "query, expected",
    [
        ("playlist:coaster", ["PLcoaster", "PLrail"]),
        ("playlist:coaster active:no", ["PLrail"]),
        ("playlist:coaster active:yes", ["PLcoaster"]),
        ("playlist:coaster subscribed:yes", ["PLrail"]),
        ("playlist:railway subscribed:yes", ["PLrail"]),
        ("playlist:mix subscribed:no", []),
    ],
)
def test_playlist_search_filters(library, query, expected):
    del library
    results = search(query)["results"]
    found = ids(results["playlist_results"], "playlist_id")
    assert sorted(found) == expected


# full text


@pytest.mark.django_db
def test_fulltext_highlights_and_orders(library):
    del library
    results = search("full:big drop")["results"]["fulltext_results"]

    fragments = ids(results, "subtitle_fragment_id")
    # "big" twice in line 1 ranks it first
    assert fragments[0] == "coaster0001-en-1"
    assert set(fragments) == {
        "coaster0001-en-0",
        "coaster0001-en-1",
        "coaster0001-en-2",
        "coaster0001-en-3",
        "rail0000001-en-0",
    }
    big, drop = HIGHLIGHT.format("big"), HIGHLIGHT.format("drop")
    assert results[0]["subtitle_line"] == (
        f"the {big} {drop} again, {big} fun"
    )
    assert results[0]["vid_thumb_url"] == "/cache/videos/c/coaster0001.jpg"
    scores = [result["_score"] for result in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.django_db
def test_fulltext_needs_whole_words(library):
    """no prefix matching for subtitle lines"""
    del library
    assert search("full:dro")["results"]["fulltext_results"] == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "query, expected",
    [
        ("full:drop lang:de", ["coaster0001-en-2"]),
        ("full:drop source:user", ["coaster0001-en-3"]),
        ("full:whistle channel:railway", ["rail0000001-en-0"]),
        ("full:whistle channel:coaster", []),
        ("full:drop lang:en source:auto channel:fans", None),
    ],
)
def test_fulltext_filters(library, query, expected):
    del library
    results = search(query)["results"]["fulltext_results"]
    found = ids(results, "subtitle_fragment_id")
    if expected is None:
        assert sorted(found) == ["coaster0001-en-0", "coaster0001-en-1"]
    else:
        assert found == expected


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["full: lang:en", "full:?!"])
def test_fulltext_without_term_finds_nothing(library, query):
    del library
    assert search(query)["results"]["fulltext_results"] == []


@pytest.mark.django_db
def test_fulltext_result_size_cap(library):
    del library
    SubtitleLine.objects.bulk_create(
        [
            SubtitleLine(
                **SubtitleLine.fields_from_doc(
                    subtitle_doc("rail0000001", idx + 10, "yet another loop")
                )
            )
            for idx in range(searching.RESULT_SIZE + 5)
        ]
    )
    results = search("full:loop")["results"]["fulltext_results"]
    assert len(results) == searching.RESULT_SIZE


# result grouping


def test_build_results_groups_by_index():
    results = [
        {"_index": "ta_video", "id": 1},
        {"_index": "ta_channel", "id": 2},
        {"_index": "ta_playlist", "id": 3},
        {"_index": "ta_subtitle", "id": 4},
        {"_index": "ta_download", "id": 5},
    ]
    grouped = SearchForm.build_results(results)

    assert grouped == {
        "video_results": [results[0]],
        "channel_results": [results[1]],
        "playlist_results": [results[2]],
        "fulltext_results": [results[3]],
    }
    assert SearchForm.build_results([]) == {
        "video_results": [],
        "channel_results": [],
        "playlist_results": [],
        "fulltext_results": [],
    }
