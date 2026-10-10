"""
Functionality:
- handle search to populate results to view
- cache youtube video thumbnails and channel artwork
- parse values in hit_cleanup for frontend
- calculate pagination values
"""

import re

from channel.models import Channel
from common.src.search_processor import SearchProcess
from django.contrib.postgres.search import (
    SearchHeadline,
    SearchQuery,
    SearchRank,
    TrigramWordSimilarity,
)
from django.db.models import F, FloatField, Q, Value
from playlist.models import Playlist
from video.models import SubtitleLine, Video

RESULT_SIZE = 30
TRIGRAM_THRESHOLD = {"auto": 0.5, "1": 0.6, "2": 0.4}


class SearchForm:
    """build query from search form data"""

    def multi_search(self, search_query):
        """searching through everything"""
        query_map, query_type = SearchParser(search_query).run()
        hits = QueryBuilder(query_map, query_type).run()
        processor = SearchProcess()
        search_results = [
            processor.process_one(index, doc, score, highlight)
            for index, doc, score, highlight in hits
        ]
        all_results = self.build_results(search_results)

        return {"results": all_results, "queryType": query_type}

    @staticmethod
    def build_results(search_results):
        """build the all_results dict"""
        video_results = []
        channel_results = []
        playlist_results = []
        fulltext_results = []
        if search_results:
            for result in search_results:
                if result["_index"].startswith("ta_video"):
                    video_results.append(result)
                elif result["_index"].startswith("ta_channel"):
                    channel_results.append(result)
                elif result["_index"].startswith("ta_playlist"):
                    playlist_results.append(result)
                elif result["_index"].startswith("ta_subtitle"):
                    fulltext_results.append(result)

        all_results = {
            "video_results": video_results,
            "channel_results": channel_results,
            "playlist_results": playlist_results,
            "fulltext_results": fulltext_results,
        }

        return all_results


class SearchParser:
    """handle structured searches"""

    def __init__(self, search_query):
        self.query_words = search_query.lower().split()
        self.query_map = {"term": [], "fuzzy": []}
        self.append_to = "term"

    def run(self):
        """parse the search string, return query map and query type"""
        print(f"query words: {self.query_words}")
        query_type = self._find_map()
        self._run_words()
        self._delete_unset()
        self._match_data_types()

        return self.query_map, query_type

    def _find_map(self):
        """find query in keyword map"""
        first_word = self.query_words[0]
        key_word_map = self._get_map()

        if ":" in first_word:
            index_match, query_string = first_word.split(":", 1)
            if index_match in key_word_map:
                self.query_map.update(key_word_map.get(index_match))
                self.query_words[0] = query_string
                return index_match

        self.query_map.update(key_word_map.get("simple"))
        print(f"query_map: {self.query_map}")

        return "simple"

    @staticmethod
    def _get_map():
        """return map to build on"""
        return {
            "simple": {
                "index": "ta_video,ta_channel,ta_playlist",
            },
            "video": {
                "index": "ta_video",
                "channel": [],
                "active": [],
            },
            "channel": {
                "index": "ta_channel",
                "active": [],
                "subscribed": [],
            },
            "playlist": {
                "index": "ta_playlist",
                "active": [],
                "subscribed": [],
            },
            "full": {
                "index": "ta_subtitle",
                "lang": [],
                "source": [],
                "channel": [],
            },
        }

    def _run_words(self):
        """append word by word"""
        for word in self.query_words:
            if ":" in word:
                keyword, search_string = word.split(":", 1)
                if isinstance(self.query_map.get(keyword), list):
                    self.append_to = keyword
                    word = search_string

            if word:
                self.query_map[self.append_to].append(word)

    def _delete_unset(self):
        """delete unset keys"""
        new_query_map = {}
        for key, value in self.query_map.items():
            if value:
                new_query_map.update({key: value})
        self.query_map = new_query_map

    def _match_data_types(self):
        """match values with data types"""
        for key, value in self.query_map.items():
            if key in ["term", "channel"]:
                self.query_map[key] = " ".join(self.query_map[key])
            if key in ["active", "subscribed"]:
                self.query_map[key] = "yes" in value


def build_tsquery(term: str, prefix_last: bool = True) -> SearchQuery | None:
    """
    every word required, last word as prefix (like ES bool_prefix with
    operator and); words are reduced to word characters, so the raw
    tsquery can't be malformed
    """
    words = re.sub(r"[^\w]+", " ", term.lower()).split()
    if not words:
        return None
    parts = [f"'{w}'" for w in words]
    if prefix_last:
        parts[-1] += ":*"
    return SearchQuery(" & ".join(parts), search_type="raw", config="simple")


class QueryBuilder:
    """run the parsed query against the database"""

    def __init__(self, query_map, query_type):
        self.query_map = query_map
        self.query_type = query_type

    def run(self) -> list[tuple]:
        """[(index, document, score, highlight)], best first"""
        exec_map = {
            "simple": self._build_simple,
            "video": self._build_video,
            "channel": self._build_channel,
            "playlist": self._build_playlist,
            "full": self._build_fulltext,
        }
        return exec_map[self.query_type]()

    def _get_fuzzy(self) -> str | None:
        """trigram fallback setting: None for off"""
        fuzzy_value = self.query_map.get("fuzzy", ["auto"])[0]
        if fuzzy_value in ("no", "0"):
            return None

        if not fuzzy_value.isdigit():
            return "auto"

        return "2" if int(fuzzy_value) > 2 else fuzzy_value

    def _ranked(self, queryset, name_field: str, term: str | None):
        """
        queryset filtered and ranked by term on its search vector; falls back
        to trigram similarity on name_field when nothing matches
        """
        if term is None:
            return queryset.annotate(score=Value(0.0, FloatField())), True

        query = build_tsquery(term)
        if query is None:
            return queryset.none(), True

        matched = queryset.filter(search=query).annotate(
            score=SearchRank(F("search"), query)
        )
        fuzzy = self._get_fuzzy()
        if matched.exists() or not fuzzy:
            return matched.order_by("-score", "pk"), True

        similar = (
            queryset.annotate(score=TrigramWordSimilarity(term, name_field))
            .filter(score__gte=TRIGRAM_THRESHOLD[fuzzy])
            .order_by("-score", "pk")
        )
        return similar, False

    @staticmethod
    def _hits(index: str, objects, to_doc=None) -> list[tuple]:
        to_doc = to_doc or (lambda obj: obj.to_doc())
        return [(index, to_doc(o), float(o.score or 0), None) for o in objects]

    def _videos(self, term, extra: Q | None = None):
        queryset = Video.objects.select_related("channel")
        if extra is not None:
            queryset = queryset.filter(extra)
        videos, _ = self._ranked(queryset, "title", term)
        return self._hits("ta_video", videos[:RESULT_SIZE])

    def _channels(self, term, extra: Q | None = None):
        queryset = Channel.objects.all()
        if extra is not None:
            queryset = queryset.filter(extra)
        channels, _ = self._ranked(queryset, "name", term)
        return self._hits("ta_channel", channels[:RESULT_SIZE])

    def _playlists(self, term, extra: Q | None = None):
        queryset = Playlist.objects.all()
        if extra is not None:
            queryset = queryset.filter(extra)
        playlists, _ = self._ranked(queryset, "name", term)
        return self._hits("ta_playlist", playlists[:RESULT_SIZE])

    def _build_simple(self):
        """cross index search, best RESULT_SIZE overall"""
        term = self.query_map.get("term")
        hits = (
            self._videos(term) + self._channels(term) + self._playlists(term)
        )
        hits.sort(key=lambda hit: -hit[2])
        return hits[:RESULT_SIZE]

    def _channel_filter(self, field: str) -> Q | None:
        """videos or lines of channels whose name matches channel:"""
        channel = self.query_map.get("channel")
        if channel is None:
            return None
        channels, _ = self._ranked(Channel.objects.all(), "name", channel)
        return Q(**{f"{field}__in": channels.values("channel_id")})

    def _build_video(self):
        """build video query"""
        extra = Q()
        if (active := self.query_map.get("active")) is not None:
            extra &= Q(active=active)
        if (channel_q := self._channel_filter("channel_id")) is not None:
            extra &= channel_q
        return self._videos(self.query_map.get("term"), extra)

    def _build_channel(self):
        """build query for channel"""
        extra = Q()
        if (active := self.query_map.get("active")) is not None:
            extra &= Q(active=active)
        if (subscribed := self.query_map.get("subscribed")) is not None:
            extra &= Q(subscribed=subscribed)
        return self._channels(self.query_map.get("term"), extra)

    def _build_playlist(self):
        """build query for playlist"""
        extra = Q()
        if (active := self.query_map.get("active")) is not None:
            extra &= Q(active=active)
        if (subscribed := self.query_map.get("subscribed")) is not None:
            extra &= Q(subscribed=subscribed)
        return self._playlists(self.query_map.get("term"), extra)

    def _build_fulltext(self):
        """subtitle lines, with the matching words highlighted"""
        lines = SubtitleLine.objects.select_related("video__channel")
        if (lang := self.query_map.get("lang")) is not None:
            lines = lines.filter(lang=lang[0])
        if (source := self.query_map.get("source")) is not None:
            lines = lines.filter(source=source[0])
        if (
            channel_q := self._channel_filter("video__channel_id")
        ) is not None:
            lines = lines.filter(channel_q)

        term = self.query_map.get("term")
        query = build_tsquery(term, prefix_last=False) if term else None
        if query is None:
            return []

        lines = (
            lines.filter(search=query)
            .annotate(
                score=SearchRank(F("search"), query),
                headline=SearchHeadline(
                    "line",
                    query,
                    config="simple",
                    start_sel='<span class="settings-current">',
                    stop_sel="</span>",
                    highlight_all=True,
                ),
            )
            .order_by("-score", "fragment_id")[:RESULT_SIZE]
        )
        return [
            (
                "ta_subtitle",
                line.to_doc(),
                float(line.score),
                {"subtitle_line": [line.headline]},
            )
            for line in lines
        ]
