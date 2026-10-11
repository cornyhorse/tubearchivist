"""
found videos and add dialog serializers, the contract of /api/found/ and
/api/add/
see docs/ux-api-contract.md
"""

# pylint: disable=abstract-method

from common.serializers import PaginationSerializer
from download.serializers import DownloadRuleSerializer
from download.serializers_rules import (
    ReasonCountSerializer,
    RuleRefSerializer,
    VerdictSerializer,
)
from emby.serializers import NUMBERING_SCHEMES
from jobs.serializers import PRIORITY_NAMES, JobSerializer
from rest_framework import serializers
from video.src.constants import VideoTypeEnum

FOUND_TABS = ["waiting", "ignored", "failed", "downloaded"]
FOUND_STATES = [
    "waiting",
    "queued",
    "downloading",
    "ignored",
    "failed",
    "downloaded",
]
FOUND_BY = [
    "channel_check",
    "playlist_check",
    "user",
    "agent",
    "auto_delete",
    "import",
    "unknown",
]
FOUND_SORT = [
    "published",
    "-published",
    "found",
    "-found",
    "duration",
    "-duration",
    "title",
    "-title",
    "channel",
    "-channel",
]
FOUND_ACTIONS = [
    "download_now",
    "download",
    "ignore",
    "forget",
    "download_anyway",
    "always_ignore",
    "clear_error",
]
URL_KINDS = ["video", "channel", "playlist"]
ADD_MODES = ["subscribe", "once", "newest_n"]
NUMBERING_SOURCES = ["channel", "playlist", "default"]


class FoundBySerializer(serializers.Serializer):
    """how TA came to know about the video"""

    kind = serializers.ChoiceField(choices=FOUND_BY)
    label = serializers.CharField(
        help_text="e.g. 'Channel check Oct 9' or 'Added by you'"
    )
    job_id = serializers.IntegerField(allow_null=True)
    playlist_id = serializers.CharField(
        allow_null=True, help_text="set when found by a playlist check"
    )


class FoundJobSerializer(serializers.Serializer):
    """the download job of a found video, if one is queued or running"""

    id = serializers.IntegerField()
    status = serializers.CharField()
    position = serializers.IntegerField(allow_null=True)
    progress = serializers.FloatField(allow_null=True)


class FoundVideoSerializer(serializers.Serializer):
    """a video TA knows about but hasn't downloaded (or just did)"""

    youtube_id = serializers.CharField()
    title = serializers.CharField()
    channel_id = serializers.CharField()
    channel_name = serializers.CharField()
    channel_indexed = serializers.BooleanField()
    vid_type = serializers.ChoiceField(choices=VideoTypeEnum.values())
    duration = serializers.CharField(help_text="e.g. '12:04'")
    duration_seconds = serializers.IntegerField(allow_null=True)
    published = serializers.DateTimeField(allow_null=True)
    thumb_url = serializers.CharField(
        allow_null=True, help_text="TA cache path, or the YouTube url"
    )
    found_at = serializers.DateTimeField()
    found_by = FoundBySerializer()
    verdict = VerdictSerializer()
    state = serializers.ChoiceField(choices=FOUND_STATES)
    job = FoundJobSerializer(allow_null=True)
    error = serializers.CharField(allow_null=True)
    downloaded_at = serializers.DateTimeField(allow_null=True)


class FoundTabCountsSerializer(serializers.Serializer):
    """counts on the tabs, with the current filters applied"""

    waiting = serializers.IntegerField()
    ignored = serializers.IntegerField()
    failed = serializers.IntegerField()
    downloaded = serializers.IntegerField()


class FoundListSerializer(serializers.Serializer):
    """GET /api/found/"""

    data = FoundVideoSerializer(many=True)
    paginate = PaginationSerializer()
    counts = FoundTabCountsSerializer()


class FoundFilterSerializer(serializers.Serializer):
    """
    filters of the found videos list; the same object selects
    'all matching' in bulk actions
    """

    tab = serializers.ChoiceField(
        choices=FOUND_TABS, required=False, default="waiting"
    )
    q = serializers.CharField(
        required=False, help_text="search title, channel name, video id"
    )
    channel = serializers.CharField(
        required=False, help_text="comma separated channel ids"
    )
    vid_type = serializers.CharField(
        required=False, help_text="comma separated: videos,streams,shorts"
    )
    min_duration = serializers.IntegerField(
        required=False, min_value=0, help_text="seconds"
    )
    max_duration = serializers.IntegerField(
        required=False, min_value=0, help_text="seconds"
    )
    published_from = serializers.DateField(required=False)
    published_to = serializers.DateField(required=False)
    error = serializers.BooleanField(required=False, allow_null=True)
    skipped_by_rule = serializers.BooleanField(
        required=False,
        allow_null=True,
        help_text="true: only rule-ignored, false: only not rule-ignored",
    )
    reason_key = serializers.CharField(
        required=False, help_text="only items skipped for this reason"
    )
    found_by = serializers.ChoiceField(choices=FOUND_BY, required=False)
    downloaded_days = serializers.IntegerField(
        required=False,
        default=7,
        min_value=1,
        max_value=365,
        help_text="window of the 'downloaded' tab",
    )


class FoundListQuerySerializer(FoundFilterSerializer):
    """GET /api/found/ query"""

    sort = serializers.ChoiceField(
        choices=FOUND_SORT, required=False, default="-found"
    )
    page = serializers.IntegerField(required=False)
    page_size = serializers.IntegerField(
        required=False, min_value=1, max_value=500
    )


class FoundActionSerializer(serializers.Serializer):
    """
    POST /api/found/actions/: give ids, or filter for 'select all matching'
    download_now: high priority jobs; download: normal priority jobs
    """

    action = serializers.ChoiceField(choices=FOUND_ACTIONS)
    ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        min_length=1,
        max_length=5000,
    )
    filter = FoundFilterSerializer(required=False)

    def validate(self, attrs):
        """exactly one of ids, filter"""
        if ("ids" in attrs) == ("filter" in attrs):
            raise serializers.ValidationError("give either ids or filter")
        return attrs


class FoundActionResponseSerializer(serializers.Serializer):
    """what the bulk action did"""

    matched = serializers.IntegerField()
    changed = serializers.IntegerField()
    job_ids = serializers.ListField(child=serializers.IntegerField())


class UrlInputSerializer(serializers.Serializer):
    """one or more pasted links, separated by whitespace or commas"""

    urls = serializers.CharField(
        help_text="video, channel or playlist links or ids"
    )


class PreviewQuerySerializer(UrlInputSerializer):
    """POST /api/add/preview/"""

    rule = DownloadRuleSerializer(
        required=False,
        allow_null=True,
        help_text="draft rule to preview instead of the effective one",
    )
    newest_n = serializers.IntegerField(
        required=False, min_value=1, help_text="limit the verdict to these"
    )


class KnownCountsSerializer(serializers.Serializer):
    """what TA already has of this item"""

    downloaded = serializers.IntegerField()
    waiting = serializers.IntegerField()
    ignored = serializers.IntegerField()


class PreviewVerdictSerializer(serializers.Serializer):
    """'142 videos: 118 will download, 24 skipped (Shorts 20, >1 h 4)'"""

    total = serializers.IntegerField()
    download = serializers.IntegerField()
    skipped = serializers.IntegerField()
    already_have = serializers.IntegerField(
        help_text="downloaded or already found, not counted above"
    )
    reasons = ReasonCountSerializer(many=True)


class PreviewSampleSerializer(serializers.Serializer):
    """newest videos of the item with their verdict"""

    youtube_id = serializers.CharField()
    title = serializers.CharField()
    vid_type = serializers.ChoiceField(choices=VideoTypeEnum.values())
    duration_seconds = serializers.IntegerField(allow_null=True)
    published = serializers.DateTimeField(allow_null=True)
    known = serializers.ChoiceField(
        choices=["downloaded", "waiting", "ignored"], allow_null=True
    )
    verdict = VerdictSerializer()


class PreviewNumberingSerializer(serializers.Serializer):
    """numbering the item would get"""

    scheme = serializers.ChoiceField(choices=NUMBERING_SCHEMES)
    source = serializers.ChoiceField(choices=NUMBERING_SOURCES)


class PreviewItemSerializer(serializers.Serializer):
    """one resolved link"""

    url = serializers.CharField(help_text="the input as pasted")
    kind = serializers.ChoiceField(choices=URL_KINDS)
    id = serializers.CharField()
    title = serializers.CharField()
    channel_id = serializers.CharField(allow_null=True)
    channel_name = serializers.CharField(allow_null=True)
    thumb_url = serializers.CharField(allow_null=True)
    video_count = serializers.IntegerField(
        allow_null=True, help_text="videos on YouTube, 1 for a video"
    )
    subscribed = serializers.BooleanField()
    known = KnownCountsSerializer()
    rule = RuleRefSerializer(
        allow_null=True, help_text="effective rule, null: everything"
    )
    numbering = PreviewNumberingSerializer()
    verdict = PreviewVerdictSerializer()
    sample = PreviewSampleSerializer(many=True)


class UrlErrorSerializer(serializers.Serializer):
    """a link that couldn't be resolved"""

    url = serializers.CharField()
    error = serializers.CharField()


class PreviewSerializer(serializers.Serializer):
    """POST /api/add/preview/ response"""

    items = PreviewItemSerializer(many=True)
    errors = UrlErrorSerializer(many=True)


class AddSerializer(UrlInputSerializer):
    """POST /api/add/"""

    mode = serializers.ChoiceField(choices=ADD_MODES)
    newest_n = serializers.IntegerField(
        required=False, min_value=1, help_text="required for newest_n"
    )
    rule = DownloadRuleSerializer(
        required=False,
        allow_null=True,
        help_text="rule for these items; omit to keep the effective rule",
    )
    save_rule = serializers.BooleanField(
        required=False,
        help_text=(
            "store the rule on the channel or playlist; default true for "
            "subscribe, false otherwise (then it applies to this add only)"
        ),
    )
    numbering = serializers.ChoiceField(
        choices=NUMBERING_SCHEMES, required=False
    )
    download_now = serializers.BooleanField(
        required=False,
        default=True,
        help_text="queue downloads for allowed videos as they're found",
    )
    priority = serializers.ChoiceField(
        choices=PRIORITY_NAMES, required=False, default="high"
    )

    def validate(self, attrs):
        """newest_n needs its count"""
        if attrs["mode"] == "newest_n" and not attrs.get("newest_n"):
            raise serializers.ValidationError(
                {"newest_n": "required for mode newest_n"}
            )
        return attrs


class AddResultSerializer(serializers.Serializer):
    """POST /api/add/ response: one scan job per resolved link"""

    jobs = JobSerializer(many=True)
    errors = UrlErrorSerializer(many=True)
