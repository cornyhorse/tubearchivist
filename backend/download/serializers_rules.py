"""
rules API serializers, the contract of /api/rules/ and of the rule
verdicts shown on found videos and in the add dialog
see docs/ux-api-contract.md
"""

# pylint: disable=abstract-method

from download.serializers import DownloadRuleSerializer
from rest_framework import serializers
from video.src.constants import VideoTypeEnum

RULE_SCOPES = ["default", "channel", "playlist"]
RULE_REF_SCOPES = RULE_SCOPES + ["video"]
VERDICT_ACTIONS = ["download", "ignore", "skip"]
OVERRIDES = ["download", "ignore"]
# why a rule skipped a video, stable keys for filter chips and grouping
REASON_KEYS = [
    "too_short",
    "too_long",
    "vid_type",
    "title_include",
    "title_exclude",
    "published_before",
    "always_ignore",
]


class RuleRefSerializer(serializers.Serializer):
    """which rule decided, so a badge can open the right editor"""

    scope = serializers.ChoiceField(choices=RULE_REF_SCOPES)
    target_id = serializers.CharField(
        allow_null=True, help_text="channel/playlist/video id, null=default"
    )
    summary = serializers.CharField(
        help_text="badge text, e.g. 'No Shorts · >10 min'"
    )


class VerdictSerializer(serializers.Serializer):
    """what the rules do with one video"""

    allowed = serializers.BooleanField()
    action = serializers.ChoiceField(
        choices=VERDICT_ACTIONS,
        help_text="skip = not added to found videos at all",
    )
    reason_key = serializers.ChoiceField(choices=REASON_KEYS, allow_null=True)
    reason = serializers.CharField(
        allow_null=True, help_text="plain words, e.g. 'longer than 1 h'"
    )
    rule = RuleRefSerializer(
        allow_null=True, help_text="null when no rule applies"
    )
    override = serializers.ChoiceField(
        choices=OVERRIDES,
        allow_null=True,
        help_text="per-video override that decided, if any",
    )


class RuleSerializer(DownloadRuleSerializer):
    """a rule body plus its read-only badge summary"""

    summary = serializers.CharField(read_only=True)


class RuleRowSerializer(serializers.Serializer):
    """one row of the Rules page"""

    scope = serializers.ChoiceField(choices=RULE_SCOPES)
    target_id = serializers.CharField(allow_null=True)
    target_title = serializers.CharField(allow_null=True)
    target_subscribed = serializers.BooleanField(allow_null=True)
    rule = RuleSerializer()


class RuleListSerializer(serializers.Serializer):
    """GET /api/rules/"""

    data = RuleRowSerializer(many=True)


class RulePresetSerializer(serializers.Serializer):
    """one-click rule chips"""

    key = serializers.CharField(help_text="e.g. 'no_shorts'")
    label = serializers.CharField(help_text="e.g. 'No Shorts'")
    description = serializers.CharField()
    patch = serializers.DictField(
        help_text="fields to merge into the rule being edited"
    )
    needs_value = serializers.ChoiceField(
        choices=["date", "text", "seconds"],
        allow_null=True,
        required=False,
        help_text="the chip asks for a value, e.g. 'Only after date...'",
    )
    value_field = serializers.CharField(
        allow_null=True,
        required=False,
        help_text="rule field the value goes into",
    )


class RulePreviewQuerySerializer(serializers.Serializer):
    """POST /api/rules/preview/"""

    scope = serializers.ChoiceField(choices=RULE_SCOPES)
    target_id = serializers.CharField(required=False, allow_null=True)
    rule = DownloadRuleSerializer(
        required=False,
        allow_null=True,
        help_text="unsaved draft; omit to preview the saved rule",
    )
    limit = serializers.IntegerField(
        required=False, default=50, min_value=1, max_value=500
    )


class ReasonCountSerializer(serializers.Serializer):
    """'Shorts 20'"""

    key = serializers.ChoiceField(choices=REASON_KEYS)
    label = serializers.CharField()
    count = serializers.IntegerField()


class PreviewVideoSerializer(serializers.Serializer):
    """a known video and what the rule does with it"""

    youtube_id = serializers.CharField()
    title = serializers.CharField()
    channel_id = serializers.CharField()
    vid_type = serializers.ChoiceField(choices=VideoTypeEnum.values())
    duration_seconds = serializers.IntegerField(allow_null=True)
    published = serializers.DateTimeField(allow_null=True)
    verdict = VerdictSerializer()


class RulePreviewSerializer(serializers.Serializer):
    """'38 download / 12 skipped', with the reasons"""

    total = serializers.IntegerField()
    download = serializers.IntegerField()
    skipped = serializers.IntegerField()
    reasons = ReasonCountSerializer(many=True)
    videos = PreviewVideoSerializer(many=True)


class VideoOverrideSerializer(serializers.Serializer):
    """per-video 'download anyway' or 'always ignore'"""

    youtube_id = serializers.CharField(read_only=True)
    override = serializers.ChoiceField(choices=OVERRIDES)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    title = serializers.CharField(read_only=True, allow_null=True)
    channel_id = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class VideoOverrideListSerializer(serializers.Serializer):
    """GET /api/rules/videos/"""

    data = VideoOverrideSerializer(many=True)


class EffectiveRuleQuerySerializer(serializers.Serializer):
    """GET /api/rules/effective/, give one of them"""

    video = serializers.CharField(required=False)
    channel = serializers.CharField(required=False)
    playlist = serializers.CharField(required=False)

    def validate(self, attrs):
        """exactly one target"""
        if len(attrs) != 1:
            raise serializers.ValidationError(
                "give exactly one of video, channel, playlist"
            )
        return attrs


class EffectiveRuleSerializer(serializers.Serializer):
    """the rule that applies to a target and where it comes from"""

    source = RuleRefSerializer(
        allow_null=True, help_text="null: no rule, everything downloads"
    )
    rule = RuleSerializer(allow_null=True)
    override = serializers.ChoiceField(choices=OVERRIDES, allow_null=True)
