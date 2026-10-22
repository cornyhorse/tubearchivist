"""
media server API serializers, the contract of /api/mediaserver/
see docs/ux-api-contract.md
"""

# pylint: disable=abstract-method

from jobs.serializers import JobSerializer
from rest_framework import serializers

SERVER_TYPES = ["emby", "jellyfin"]
KEY_SOURCES = ["env", "db"]


class LastNotifySerializer(serializers.Serializer):
    """the most recent notify call"""

    at = serializers.DateTimeField()
    status_code = serializers.IntegerField(allow_null=True)
    paths = serializers.IntegerField(help_text="number of paths sent")
    job_id = serializers.IntegerField()
    error = serializers.CharField(allow_null=True)


class MediaServerSerializer(serializers.Serializer):
    """
    GET/PUT /api/mediaserver/
    api_key is write-only: omit it to keep the stored key, null or "" to
    clear it; when TA_MEDIASERVER_API_KEY is set, the env var wins and
    api_key_source is 'env'
    """

    type = serializers.ChoiceField(
        choices=SERVER_TYPES, allow_null=True, help_text="null: disabled"
    )
    url = serializers.URLField(
        allow_blank=True, help_text="e.g. http://192.0.2.10:8096"
    )
    api_key = serializers.CharField(
        write_only=True, required=False, allow_null=True, allow_blank=True
    )
    api_key_set = serializers.BooleanField(read_only=True)
    api_key_source = serializers.ChoiceField(
        choices=KEY_SOURCES, read_only=True, allow_null=True
    )
    library_id = serializers.CharField(
        allow_blank=True,
        required=False,
        help_text="library to refresh on full refresh",
    )
    path_from = serializers.CharField(
        read_only=True, help_text="TA's Emby root, TA_EMBY_ROOT"
    )
    path_to = serializers.CharField(
        allow_blank=True,
        required=False,
        help_text="the same folder as the media server sees it",
    )
    notify_enabled = serializers.BooleanField(required=False)
    debounce_seconds = serializers.IntegerField(
        required=False, min_value=0, max_value=3600
    )
    refresh_batch_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=1000,
        help_text="item refreshes per run after a sync (each re-reads files)",
    )
    refresh_pause_seconds = serializers.IntegerField(
        required=False,
        min_value=0,
        max_value=3600,
        help_text="pause between runs of item refreshes",
    )
    known_tags = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        max_length=20,
        help_text="Emby tags offered in the UI, e.g. ['David']",
    )
    playlists_enabled = serializers.BooleanField(
        required=False,
        help_text="keep Emby playlists in step with the TA playlists (Emby)",
    )
    playlist_default_users = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        max_length=20,
        help_text="Emby users who get every playlist, e.g. ['Matt']; users "
        "named by a playlist's tags get it too",
    )
    last_notify = LastNotifySerializer(read_only=True, allow_null=True)


class MediaServerTestQuerySerializer(serializers.Serializer):
    """
    POST /api/mediaserver/test/: test the stored settings, or the values
    given here (unsaved form values); the stored key is used if no api_key
    """

    type = serializers.ChoiceField(choices=SERVER_TYPES, required=False)
    url = serializers.URLField(required=False)
    api_key = serializers.CharField(required=False, write_only=True)


class MediaServerTestSerializer(serializers.Serializer):
    """test connection result"""

    ok = serializers.BooleanField()
    server_name = serializers.CharField(allow_null=True)
    product = serializers.CharField(allow_null=True)
    version = serializers.CharField(allow_null=True)
    error = serializers.CharField(allow_null=True)


class LibrarySerializer(serializers.Serializer):
    """a library of the media server"""

    id = serializers.CharField()
    name = serializers.CharField()
    collection_type = serializers.CharField(allow_null=True)
    paths = serializers.ListField(child=serializers.CharField())
    contains_ta = serializers.BooleanField(
        help_text="one of its paths is inside path_to"
    )


class LibraryListSerializer(serializers.Serializer):
    """GET /api/mediaserver/libraries/"""

    data = LibrarySerializer(many=True)


class NotifyQuerySerializer(serializers.Serializer):
    """POST /api/mediaserver/notify/: paths are TA-side, under path_from"""

    paths = serializers.ListField(
        child=serializers.CharField(), required=False, min_length=1
    )
    all = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        """paths or all"""
        if bool(attrs.get("paths")) == bool(attrs.get("all")):
            raise serializers.ValidationError("give either paths or all")
        return attrs


class RefreshQuerySerializer(serializers.Serializer):
    """POST /api/mediaserver/refresh/: full library refresh, for repairs"""

    library_id = serializers.CharField(
        required=False, help_text="default: the configured library"
    )


class MediaServerJobSerializer(serializers.Serializer):
    """the queued post-process job"""

    job = JobSerializer()
