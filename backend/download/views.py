"""all download API views"""

from common.serializers import (
    AsyncTaskResponseSerializer,
    ErrorResponseSerializer,
)
from common.views_base import AdminOnly, ApiBaseView
from django.db.models import Count
from download.models import Download
from download.serializers import (
    AddToDownloadListSerializer,
    AddToDownloadQuerySerializer,
    BulkUpdateDowloadDataSerializer,
    BulkUpdateDowloadQuerySerializer,
    DownloadAggsSerializer,
    DownloadItemSerializer,
    DownloadListQuerySerializer,
    DownloadListQueueDeleteQuerySerializer,
    DownloadListSerializer,
    DownloadQueueItemUpdateSerializer,
    DownloadRuleSerializer,
)
from download.src.queue_interact import PendingInteract
from download.src.rules import get_rule, set_rule
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.response import Response
from task.tasks import download_pending, extrac_dl


class DownloadApiListView(ApiBaseView):
    """resolves to /api/download/
    GET: returns latest videos in the download queue
    POST: add a list of videos to download queue
    DELETE: remove items based on query filter
    """

    valid_filter = ["pending", "ignore"]
    permission_classes = [AdminOnly]

    @extend_schema(
        responses={
            200: OpenApiResponse(DownloadListSerializer()),
        },
        parameters=[DownloadListQuerySerializer()],
    )
    def get(self, request):
        """get download queue list"""
        serializer = DownloadListQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        queue = Download.objects.matching(
            status=validated_data.get("filter"),
            channel_id=validated_data.get("channel"),
            vid_type=validated_data.get("vid_type"),
            error=validated_data.get("error"),
        )
        search_query = validated_data.get("q")
        if search_query:
            queue = queue.filter(title__icontains=search_query)

        queue = queue.with_channel_indexed().queue_order()
        self.get_document_list(request, "ta_download", queue)
        serializer = DownloadListSerializer(self.response)

        return Response(serializer.data)

    @staticmethod
    @extend_schema(
        request=AddToDownloadListSerializer(),
        parameters=[AddToDownloadQuerySerializer()],
        responses={
            200: OpenApiResponse(
                AsyncTaskResponseSerializer(),
                description="New async task started",
            ),
            400: OpenApiResponse(
                ErrorResponseSerializer(), description="Bad request"
            ),
        },
    )
    def post(request):
        """add list of videos to download queue"""
        data_serializer = AddToDownloadListSerializer(data=request.data)
        data_serializer.is_valid(raise_exception=True)
        validated_data = data_serializer.validated_data

        query_serializer = AddToDownloadQuerySerializer(
            data=request.query_params
        )
        query_serializer.is_valid(raise_exception=True)
        validated_query = query_serializer.validated_data

        auto_start = validated_query.get("autostart")
        flat = validated_query.get("flat", False)
        force = validated_query.get("force", False)
        print(f"auto_start: {auto_start}, flat: {flat}, force: {force}")
        to_add = validated_data["data"]

        pending = [i["youtube_id"] for i in to_add if i["status"] == "pending"]
        url_str = " ".join(pending)
        print(f"url_str: {url_str}")
        task = extrac_dl.delay(
            url_str, auto_start=auto_start, flat=flat, force=force
        )

        message = {
            "message": "add to queue task started",
            "task_id": task.id,
        }
        response_serializer = AsyncTaskResponseSerializer(message)

        return Response(response_serializer.data)

    @staticmethod
    @extend_schema(
        request=BulkUpdateDowloadDataSerializer(),
        parameters=[BulkUpdateDowloadQuerySerializer()],
        responses={204: OpenApiResponse(description="Status updated")},
    )
    def patch(request):
        """bulk update status"""
        data_serializer = BulkUpdateDowloadDataSerializer(data=request.data)
        data_serializer.is_valid(raise_exception=True)
        validated_data = data_serializer.validated_data

        new_status = validated_data["status"]

        query_serializer = BulkUpdateDowloadQuerySerializer(
            data=request.query_params
        )
        query_serializer.is_valid(raise_exception=True)
        validated_query = query_serializer.validated_data
        status_filter = validated_query.get("filter")

        PendingInteract(status=status_filter).update_bulk(
            channel_id=validated_query.get("channel"),
            vid_type=validated_query.get("vid_type"),
            new_status=validated_data["status"],
            error=validated_query.get("error"),
        )

        if new_status == "priority":
            download_pending.delay(auto_only=True)

        return Response(status=204)

    @extend_schema(
        parameters=[DownloadListQueueDeleteQuerySerializer()],
        responses={
            204: OpenApiResponse(description="Download items deleted"),
            400: OpenApiResponse(
                ErrorResponseSerializer(), description="Bad request"
            ),
        },
    )
    def delete(self, request):
        """bulk delete download queue items by filter"""
        serializer = DownloadListQueueDeleteQuerySerializer(
            data=request.query_params
        )
        serializer.is_valid(raise_exception=True)
        validated_query = serializer.validated_data

        query_filter = validated_query["filter"]
        channel = validated_query.get("channel")
        vid_type = validated_query.get("vid_type")
        message = f"delete queue by status: {query_filter}"
        if channel:
            message += f" - filter by channel: {channel}"
        if vid_type:
            message += f" - filter by vid_type: {vid_type}"

        print(message)
        PendingInteract(status=query_filter).delete_bulk(
            channel_id=channel, vid_type=vid_type
        )

        return Response(status=204)


class DownloadApiView(ApiBaseView):
    """resolves to /api/download/<video_id>/
    GET: returns metadata dict of an item in the download queue
    POST: update status of item to pending or ignore
    DELETE: forget from download queue
    """

    valid_status = ["pending", "ignore", "ignore-force", "priority"]
    permission_classes = [AdminOnly]

    @extend_schema(
        responses={
            200: OpenApiResponse(DownloadItemSerializer()),
            404: OpenApiResponse(
                ErrorResponseSerializer(),
                description="Download item not found",
            ),
        },
    )
    def get(self, request, video_id):
        # pylint: disable=unused-argument
        """get download queue item"""
        item, _ = PendingInteract(video_id).get_item()
        self.get_document("ta_download", item)
        if not self.response:
            error = ErrorResponseSerializer(
                {"error": "Download item not found"}
            )
            return Response(error.data, status=404)

        response_serializer = DownloadItemSerializer(self.response)

        return Response(response_serializer.data, status=self.status_code)

    @extend_schema(
        request=DownloadQueueItemUpdateSerializer(),
        responses={
            200: OpenApiResponse(
                DownloadQueueItemUpdateSerializer(),
                description="Download item update",
            ),
            400: OpenApiResponse(
                ErrorResponseSerializer(), description="Bad request"
            ),
            404: OpenApiResponse(
                ErrorResponseSerializer(),
                description="Download item not found",
            ),
        },
    )
    def post(self, request, video_id):
        """post to video to change status"""
        data_serializer = DownloadQueueItemUpdateSerializer(data=request.data)
        data_serializer.is_valid(raise_exception=True)
        validated_data = data_serializer.validated_data
        item_status = validated_data["status"]

        if item_status == "ignore-force":
            extrac_dl.delay(video_id, status="ignore")
            return Response(data_serializer.data)

        _, status_code = PendingInteract(video_id).get_item()
        if status_code == 404:
            error = ErrorResponseSerializer(
                {"error": "Download item not found"}
            )
            return Response(error.data, status=404)

        print(f"{video_id}: change status to {item_status}")
        PendingInteract(video_id, item_status).update_status()
        if item_status == "priority":
            download_pending.delay(auto_only=True)

        return Response(data_serializer.data)

    @staticmethod
    @extend_schema(
        responses={
            204: OpenApiResponse(description="delete download item"),
            404: OpenApiResponse(
                ErrorResponseSerializer(),
                description="Download item not found",
            ),
        },
    )
    def delete(request, video_id):
        # pylint: disable=unused-argument
        """delete single video from queue"""
        print(f"{video_id}: delete from queue")
        PendingInteract(video_id).delete_item()

        return Response(status=204)


class DownloadAggsApiView(ApiBaseView):
    """resolves to /api/download/aggs/
    GET: get download aggregations
    """

    valid_filter_view = ["ignore", "pending"]

    @extend_schema(
        parameters=[DownloadListQueueDeleteQuerySerializer()],
        responses={
            200: OpenApiResponse(DownloadAggsSerializer()),
            400: OpenApiResponse(
                ErrorResponseSerializer(), description="bad request"
            ),
        },
    )
    def get(self, request):
        """get aggs"""
        serializer = DownloadListQueueDeleteQuerySerializer(
            data=request.query_params
        )
        serializer.is_valid(raise_exception=True)
        validated_query = serializer.validated_data

        filter_view = validated_query.get("filter")
        queue = Download.objects.all()
        if filter_view:
            if filter_view not in self.valid_filter_view:
                message = f"invalid filter: {filter_view}"
                return Response({"message": message}, status=400)

            queue = queue.filter(status=filter_view)

        counts = list(
            queue.values("channel_name", "channel_id")
            .annotate(doc_count=Count("youtube_id"))
            .order_by("-doc_count", "channel_name", "channel_id")
        )
        top = counts[:30]
        response = {
            "doc_count_error_upper_bound": 0,
            "sum_other_doc_count": sum(i["doc_count"] for i in counts[30:]),
            "buckets": [
                {
                    "key": [i["channel_name"], i["channel_id"]],
                    "key_as_string": f"{i['channel_name']}|{i['channel_id']}",
                    "doc_count": i["doc_count"],
                }
                for i in top
            ],
        }
        serializer = DownloadAggsSerializer(response)

        return Response(serializer.data)


class DownloadRuleDefaultView(ApiBaseView):
    """resolves to /api/download/rule/
    GET: the default download rule, for channels without their own
    POST: set it
    DELETE: remove it, download everything
    """

    permission_classes = [AdminOnly]

    @staticmethod
    @extend_schema(responses={200: OpenApiResponse(DownloadRuleSerializer())})
    def get(request):
        """get default rule"""
        # pylint: disable=unused-argument
        rule = get_rule(None)
        if rule is None:
            return Response(None)
        return Response(DownloadRuleSerializer(rule).data)

    @staticmethod
    @extend_schema(
        request=DownloadRuleSerializer(),
        responses={200: OpenApiResponse(DownloadRuleSerializer())},
    )
    def post(request):
        """set default rule"""
        serializer = DownloadRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = set_rule(None, dict(serializer.validated_data))
        return Response(DownloadRuleSerializer(rule).data)

    @staticmethod
    @extend_schema(responses={204: OpenApiResponse(description="deleted")})
    def delete(request):
        """remove default rule"""
        # pylint: disable=unused-argument
        set_rule(None, None)
        return Response(status=204)
