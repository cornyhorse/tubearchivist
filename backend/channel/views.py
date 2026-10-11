"""all channel API views"""

from channel.models import Channel
from channel.serializers import (
    ChannelAggSerializer,
    ChannelListQuerySerializer,
    ChannelListSerializer,
    ChannelNavSerializer,
    ChannelSearchQuerySerializer,
    ChannelSerializer,
    ChannelUpdateSerializer,
)
from channel.src.index import YoutubeChannel, channel_overwrites
from channel.src.listing import channel_list, table_doc
from channel.src.nav import ChannelNav
from common.serializers import ErrorResponseSerializer
from common.src.helper import get_duration_str
from common.src.urlparser import Parser
from common.views_base import AdminWriteOnly, ApiBaseView
from django.db.models import Count, Sum
from download.src.rules import get_rule, set_rule
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from jobs.src.handlers import request_emby_write
from rest_framework.response import Response
from task.tasks import index_channel_playlists, subscribe_to
from video.models import Video


class ChannelApiListView(ApiBaseView):
    """resolves to /api/channel/
    GET: returns list of channels
    POST: edit a list of channels
    """

    valid_filter = ["subscribed"]
    permission_classes = [AdminWriteOnly]

    @extend_schema(
        responses={
            200: OpenApiResponse(ChannelListSerializer()),
        },
        parameters=[ChannelListQuerySerializer()],
    )
    def get(self, request):
        """get request"""
        serializer = ChannelListQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        self.get_document_list(
            request,
            "ta_channel",
            channel_list(validated_data),
            to_doc=table_doc,
            page_size=validated_data.get("page_size"),
        )
        serializer = ChannelListSerializer(self.response)

        return Response(serializer.data)

    def post(self, request):
        """subscribe/unsubscribe to list of channels"""
        data = request.data
        try:
            to_add = data["data"]
        except KeyError:
            message = "missing expected data key"
            print(message)
            return Response({"message": message}, status=400)

        pending = []
        for channel_item in to_add:
            channel_id = channel_item["channel_id"]
            if channel_item["channel_subscribed"]:
                pending.append(channel_id)
            else:
                self._unsubscribe(channel_id)

        if pending:
            url_str = " ".join(pending)
            subscribe_to.delay(url_str, expected_type="channel")

        return Response(data)

    @staticmethod
    def _unsubscribe(channel_id: str):
        """unsubscribe"""
        print(f"[{channel_id}] unsubscribe from channel")
        YoutubeChannel(channel_id).change_subscribe(new_subscribe_state=False)


class ChannelApiView(ApiBaseView):
    """resolves to /api/channel/<channel_id>/
    GET: returns metadata dict of channel
    """

    permission_classes = [AdminWriteOnly]

    @extend_schema(
        responses={
            200: OpenApiResponse(ChannelSerializer()),
            404: OpenApiResponse(
                ErrorResponseSerializer(), description="Channel not found"
            ),
        }
    )
    def get(self, request, channel_id):
        # pylint: disable=unused-argument
        """get channel detail"""
        self.get_document("ta_channel", Channel.load_doc(channel_id))
        if not self.response:
            error = ErrorResponseSerializer({"error": "channel not found"})
            return Response(error.data, status=404)

        self.response.update(self._fork_settings(channel_id))

        response_serializer = ChannelSerializer(self.response)
        return Response(response_serializer.data, status=self.status_code)

    @extend_schema(
        request=ChannelUpdateSerializer(),
        responses={
            200: OpenApiResponse(ChannelUpdateSerializer()),
            400: OpenApiResponse(
                ErrorResponseSerializer(), description="Bad request"
            ),
            404: OpenApiResponse(
                ErrorResponseSerializer(), description="Channel not found"
            ),
        },
    )
    def post(self, request, channel_id):
        """modify channel"""
        self.get_document("ta_channel", Channel.load_doc(channel_id))
        if not self.response:
            error = ErrorResponseSerializer({"error": "channel not found"})
            return Response(error.data, status=404)

        serializer = ChannelUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        subscribed = validated_data.get("channel_subscribed")
        if subscribed is not None:
            YoutubeChannel(channel_id).change_subscribe(
                new_subscribe_state=subscribed
            )

        overwrites = validated_data.get("channel_overwrites")
        if overwrites:
            channel_overwrites(channel_id, overwrites)
            if overwrites.get("index_playlists"):
                index_channel_playlists.delay(channel_id)

        if numbering := validated_data.get("emby_numbering"):
            Channel.objects.filter(channel_id=channel_id).update(
                emby_numbering=numbering
            )
            request_emby_write()

        if "download_rule" in validated_data:
            set_rule(channel_id, validated_data["download_rule"])

        return Response(serializer.data, status=200)

    @staticmethod
    def _fork_settings(channel_id: str) -> dict:
        """emby numbering and download rule of the channel"""
        numbering = (
            Channel.objects.filter(channel_id=channel_id)
            .values_list("emby_numbering", flat=True)
            .first()
        )
        return {
            "emby_numbering": numbering,
            "download_rule": get_rule(channel_id),
        }

    @extend_schema(
        responses={
            204: OpenApiResponse(description="Channel deleted"),
            404: OpenApiResponse(
                ErrorResponseSerializer(), description="Channel not found"
            ),
        },
    )
    def delete(self, request, channel_id):
        # pylint: disable=unused-argument
        """delete channel"""
        try:
            YoutubeChannel(channel_id).delete_channel()
            # the series and its links go from the Emby tree (review T2)
            request_emby_write()
            return Response(status=204)
        except FileNotFoundError:
            pass

        error = ErrorResponseSerializer({"error": "channel not found"})
        return Response(error.data, status=404)


class ChannelAggsApiView(ApiBaseView):
    """resolves to /api/channel/<channel_id>/aggs/
    GET: get channel aggregations
    """

    @extend_schema(
        responses={
            200: OpenApiResponse(ChannelAggSerializer()),
        },
    )
    def get(self, request, channel_id):
        """get channel aggregations"""
        # pylint: disable=unused-argument
        totals = Video.objects.filter(channel_id=channel_id).aggregate(
            items=Count("youtube_id"),
            size=Sum("media_size", default=0),
            duration=Sum("duration", default=0),
        )
        response = {
            "total_items": {"value": totals["items"]},
            "total_size": {"value": totals["size"]},
            "total_duration": {
                "value": totals["duration"],
                "value_str": get_duration_str(totals["duration"]),
            },
        }
        serializer = ChannelAggSerializer(response)

        return Response(serializer.data)


class ChannelNavApiView(ApiBaseView):
    """resolves to /api/channel/<channel_id>/nav/
    GET: get channel nav
    """

    @extend_schema(
        responses={
            200: OpenApiResponse(ChannelNavSerializer()),
        },
    )
    def get(self, request, channel_id):
        """get navigation"""

        nav = ChannelNav(channel_id).get_nav()
        serializer = ChannelNavSerializer(nav)
        return Response(serializer.data)


class ChannelApiSearchView(ApiBaseView):
    """resolves to /api/channel/search/
    search for channel
    """

    @extend_schema(
        responses={
            200: OpenApiResponse(ChannelSerializer()),
            400: OpenApiResponse(description="Bad Request"),
            404: OpenApiResponse(
                ErrorResponseSerializer(), description="Channel not found"
            ),
        },
        parameters=[
            OpenApiParameter(
                name="q",
                description="Search query string",
                required=True,
                type=str,
            ),
        ],
    )
    def get(self, request):
        """search for local channel ID"""

        serializer = ChannelSearchQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        # required non blank q, the serializer answers 400 without it
        query = validated_data["q"]
        try:
            parsed = Parser(query).parse()[0]
        except (ValueError, IndexError, AttributeError):
            error = ErrorResponseSerializer(
                {"error": f"channel not found: {query}"}
            )
            return Response(error.data, status=404)

        if not parsed["type"] == "channel":
            error = ErrorResponseSerializer({"error": "expected channel data"})
            return Response(error.data, status=400)

        self.get_document("ta_channel", Channel.load_doc(parsed["url"]))
        if not self.response:
            error = ErrorResponseSerializer(
                {"error": f"channel not found: {query}"}
            )
            return Response(error.data, status=404)

        serializer = ChannelSerializer(self.response)

        return Response(serializer.data, status=self.status_code)
