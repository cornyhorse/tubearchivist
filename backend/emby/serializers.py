"""
numbering API serializers, the contract of /api/numbering/
see docs/ux-api-contract.md
"""

# pylint: disable=abstract-method

from rest_framework import serializers

NUMBERING_SCHEMES = [
    "date",
    "year_frozen",
    "absolute_frozen",
    "playlist_frozen",
    "playlist_position",
]
SERIES_KINDS = ["channel", "playlist"]


class SchemeSerializer(serializers.Serializer):
    """one numbering scheme"""

    key = serializers.ChoiceField(choices=NUMBERING_SCHEMES)
    label = serializers.CharField(help_text="e.g. 'By date'")
    description = serializers.CharField()
    frozen = serializers.BooleanField(
        help_text="numbers never change once assigned"
    )
    playlist_only = serializers.BooleanField()
    example = serializers.CharField(help_text="e.g. 'S2025E6200'")


class EpisodeNameSerializer(serializers.Serializer):
    """a video's place in a series and its Emby name"""

    youtube_id = serializers.CharField()
    title = serializers.CharField()
    published = serializers.DateTimeField(allow_null=True)
    season = serializers.IntegerField()
    episode = serializers.IntegerField()
    name = serializers.CharField(
        help_text="e.g. 'S2025E6200 - Southern California Trains 2025'"
    )
    manual = serializers.BooleanField(help_text="set by hand")


class SeriesNumberingSerializer(serializers.Serializer):
    """GET /api/numbering/<kind>/<id>/"""

    kind = serializers.ChoiceField(choices=SERIES_KINDS)
    id = serializers.CharField()
    title = serializers.CharField()
    scheme = serializers.ChoiceField(choices=NUMBERING_SCHEMES)
    episodes = serializers.IntegerField(help_text="downloaded videos")
    manual = serializers.IntegerField(help_text="numbers set by hand")
    sample = EpisodeNameSerializer(
        many=True, help_text="newest episodes, up to 20"
    )


class SchemeChangeSerializer(serializers.Serializer):
    """body of preview and PUT on /api/numbering/<kind>/<id>/"""

    scheme = serializers.ChoiceField(choices=NUMBERING_SCHEMES)


class RenumberSampleSerializer(EpisodeNameSerializer):
    """an episode with its number before the change"""

    old_season = serializers.IntegerField(allow_null=True)
    old_episode = serializers.IntegerField(allow_null=True)
    old_name = serializers.CharField(allow_null=True)


class SchemePreviewSerializer(serializers.Serializer):
    """'this renumbers N episodes in Emby'"""

    scheme = serializers.ChoiceField(choices=NUMBERING_SCHEMES)
    episodes = serializers.IntegerField()
    renumbers = serializers.IntegerField(
        help_text="episodes whose S/E changes; manual numbers are kept"
    )
    sample = RenumberSampleSerializer(many=True)


class SchemeChangeResultSerializer(serializers.Serializer):
    """PUT result"""

    scheme = serializers.ChoiceField(choices=NUMBERING_SCHEMES)
    renumbered = serializers.IntegerField()
    job_id = serializers.IntegerField(
        allow_null=True, help_text="the Emby write job, if queued"
    )


class VideoNumberSerializer(serializers.Serializer):
    """a video's number in one series"""

    series_kind = serializers.ChoiceField(choices=SERIES_KINDS)
    series_id = serializers.CharField()
    series_title = serializers.CharField(read_only=True)
    scheme = serializers.ChoiceField(choices=NUMBERING_SCHEMES, read_only=True)
    season = serializers.IntegerField(min_value=0)
    episode = serializers.IntegerField(min_value=0)
    name = serializers.CharField(read_only=True)
    manual = serializers.BooleanField(read_only=True)


class VideoNumberListSerializer(serializers.Serializer):
    """GET /api/numbering/video/<youtube_id>/: every series it is in"""

    data = VideoNumberSerializer(many=True)


class VideoNumberDeleteQuerySerializer(serializers.Serializer):
    """DELETE /api/numbering/video/<youtube_id>/: back to automatic"""

    series_kind = serializers.ChoiceField(choices=SERIES_KINDS)
    series_id = serializers.CharField()


class EmbyTagsSerializer(serializers.Serializer):
    """GET /api/emby/tags/: the tags to offer on channels and playlists"""

    tags = serializers.ListField(child=serializers.CharField())
