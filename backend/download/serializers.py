"""download serializers"""

# pylint: disable=abstract-method

import re

from common.serializers import PaginationSerializer, ValidateUnknownFieldsMixin
from rest_framework import serializers
from video.src.constants import VideoTypeEnum

RULE_ACTIONS = ["ignore", "skip"]


class DownloadItemSerializer(serializers.Serializer):
    """serialize download item"""

    auto_start = serializers.BooleanField(required=False)
    channel_id = serializers.CharField()
    channel_indexed = serializers.BooleanField()
    channel_name = serializers.CharField()
    duration = serializers.CharField()
    duration_seconds = serializers.IntegerField(
        required=False, allow_null=True
    )
    ignore_reason = serializers.CharField(required=False, allow_null=True)
    message = serializers.CharField(required=False, allow_null=True)
    published = serializers.CharField(allow_null=True)
    status = serializers.ChoiceField(
        choices=["pending", "ignore"], required=False
    )
    timestamp = serializers.IntegerField(allow_null=True)
    title = serializers.CharField()
    vid_thumb_url = serializers.CharField(allow_null=True)
    vid_type = serializers.ChoiceField(choices=VideoTypeEnum.values())
    youtube_id = serializers.CharField()
    _index = serializers.CharField(required=False)
    _score = serializers.IntegerField(required=False)


class DownloadListSerializer(serializers.Serializer):
    """serialize download list"""

    data = DownloadItemSerializer(many=True)
    paginate = PaginationSerializer()


class DownloadListQuerySerializer(
    ValidateUnknownFieldsMixin, serializers.Serializer
):
    """serialize query params for download list"""

    filter = serializers.ChoiceField(
        choices=["pending", "ignore"], required=False
    )
    vid_type = serializers.ChoiceField(
        choices=VideoTypeEnum.values_known(), required=False
    )
    channel = serializers.CharField(required=False, help_text="channel ID")
    page = serializers.IntegerField(required=False)
    q = serializers.CharField(required=False, help_text="Search Query")
    error = serializers.BooleanField(required=False, allow_null=True)


class DownloadListQueueDeleteQuerySerializer(serializers.Serializer):
    """serialize bulk delete download queue query string"""

    filter = serializers.ChoiceField(choices=["pending", "ignore"])
    channel = serializers.CharField(required=False, help_text="channel ID")
    vid_type = serializers.ChoiceField(
        choices=VideoTypeEnum.values_known(), required=False
    )


class AddDownloadItemSerializer(serializers.Serializer):
    """serialize single item to add"""

    youtube_id = serializers.CharField()
    status = serializers.ChoiceField(choices=["pending", "ignore-force"])


class AddToDownloadListSerializer(serializers.Serializer):
    """serialize add to download queue data"""

    data = AddDownloadItemSerializer(many=True)


class AddToDownloadQuerySerializer(serializers.Serializer):
    """add to queue query serializer"""

    autostart = serializers.BooleanField(required=False)
    flat = serializers.BooleanField(required=False)
    force = serializers.BooleanField(required=False)


class BulkUpdateDowloadQuerySerializer(serializers.Serializer):
    """serialize bulk update query"""

    filter = serializers.ChoiceField(choices=["pending", "ignore", "priority"])
    channel = serializers.CharField(required=False)
    vid_type = serializers.ChoiceField(
        choices=VideoTypeEnum.values_known(), required=False
    )
    error = serializers.BooleanField(required=False, allow_null=True)


class BulkUpdateDowloadDataSerializer(serializers.Serializer):
    """serialize data"""

    status = serializers.ChoiceField(
        choices=["pending", "ignore", "priority", "clear_error"]
    )


class DownloadQueueItemUpdateSerializer(serializers.Serializer):
    """update single download queue item"""

    status = serializers.ChoiceField(
        choices=["pending", "ignore", "ignore-force", "priority"]
    )


class DownloadAggBucketSerializer(serializers.Serializer):
    """serialize bucket"""

    key = serializers.ListField(child=serializers.CharField())
    key_as_string = serializers.CharField()
    doc_count = serializers.IntegerField()


class DownloadAggsSerializer(serializers.Serializer):
    """serialize download channel bucket aggregations"""

    doc_count_error_upper_bound = serializers.IntegerField()
    sum_other_doc_count = serializers.IntegerField()
    buckets = DownloadAggBucketSerializer(many=True)


class DownloadRuleSerializer(serializers.Serializer):
    """which videos of a channel to download; empty conditions match all"""

    enabled = serializers.BooleanField(default=True)
    min_duration = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, help_text="seconds"
    )
    max_duration = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, help_text="seconds"
    )
    vid_types = serializers.ListField(
        child=serializers.ChoiceField(choices=VideoTypeEnum.values_known()),
        required=False,
        default=list,
    )
    title_include = serializers.CharField(
        required=False, allow_blank=True, default="", help_text="regex"
    )
    title_exclude = serializers.CharField(
        required=False, allow_blank=True, default="", help_text="regex"
    )
    published_after = serializers.DateField(required=False, allow_null=True)
    action = serializers.ChoiceField(
        choices=RULE_ACTIONS, required=False, default="ignore"
    )

    @staticmethod
    def _validate_regex(value: str) -> str:
        try:
            re.compile(value)
        except re.error as err:
            raise serializers.ValidationError(f"invalid regex: {err}") from err
        return value

    def validate_title_include(self, value):
        """must compile"""
        return self._validate_regex(value)

    def validate_title_exclude(self, value):
        """must compile"""
        return self._validate_regex(value)

    def validate(self, attrs):
        """min below max"""
        low, high = attrs.get("min_duration"), attrs.get("max_duration")
        if low is not None and high is not None and low > high:
            raise serializers.ValidationError(
                "min_duration must not exceed max_duration"
            )
        return attrs
