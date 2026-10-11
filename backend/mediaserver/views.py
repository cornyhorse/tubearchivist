"""media server API views, /api/mediaserver/"""

from common.serializers import ErrorResponseSerializer
from common.views_base import AdminOnly, ApiBaseView
from drf_spectacular.utils import OpenApiResponse, extend_schema
from jobs.models import CreatedBy
from jobs.src.present import job_dict
from mediaserver.serializers import (
    LibraryListSerializer,
    MediaServerJobSerializer,
    MediaServerSerializer,
    MediaServerTestQuerySerializer,
    MediaServerTestSerializer,
    NotifyQuerySerializer,
    RefreshQuerySerializer,
)
from mediaserver.src import settings
from mediaserver.src.client import MediaServerError
from mediaserver.src.jobs import (
    libraries,
    manual_notify,
    queue_refresh,
    test_connection,
)
from rest_framework.response import Response

NOT_CONFIGURED = OpenApiResponse(
    ErrorResponseSerializer(), description="no media server configured"
)
UPSTREAM = OpenApiResponse(
    ErrorResponseSerializer(), description="the media server failed"
)


class MediaServerView(ApiBaseView):
    """resolves to /api/mediaserver/
    GET: settings, never the API key
    PUT: change settings
    """

    permission_classes = [AdminOnly]

    @extend_schema(responses=MediaServerSerializer())
    def get(self, request):
        """settings"""
        return Response(settings.public())

    @extend_schema(
        request=MediaServerSerializer(),
        responses={200: MediaServerSerializer()},
    )
    def put(self, request):
        """save settings; the key is stored encrypted"""
        serializer = MediaServerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(settings.update(serializer.validated_data))


class MediaServerTestView(ApiBaseView):
    """resolves to /api/mediaserver/test/
    POST: test connection, always 200, see ok and error
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=MediaServerTestQuerySerializer(),
        responses=MediaServerTestSerializer(),
    )
    def post(self, request):
        """test connection"""
        serializer = MediaServerTestQuerySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(test_connection(serializer.validated_data))


class LibraryListView(ApiBaseView):
    """resolves to /api/mediaserver/libraries/
    GET: libraries of the configured server
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        responses={
            200: LibraryListSerializer(),
            409: NOT_CONFIGURED,
            502: UPSTREAM,
        }
    )
    def get(self, request):
        """list libraries"""
        try:
            return Response({"data": libraries()})
        except LookupError as err:
            return Response({"error": str(err)}, status=409)
        except MediaServerError as err:
            return Response({"error": str(err)}, status=502)


class NotifyView(ApiBaseView):
    """resolves to /api/mediaserver/notify/
    POST: tell the server these paths changed, as a job
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=NotifyQuerySerializer(),
        responses={201: MediaServerJobSerializer(), 409: NOT_CONFIGURED},
    )
    def post(self, request):
        """queue a notify job"""
        serializer = NotifyQuerySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            job = manual_notify(data.get("paths"), data["all"])
        except LookupError as err:
            return Response({"error": str(err)}, status=409)
        except ValueError as err:
            return Response({"error": str(err)}, status=400)
        return Response({"job": job_dict(job)}, status=201)


class RefreshView(ApiBaseView):
    """resolves to /api/mediaserver/refresh/
    POST: full library refresh, as a job
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=RefreshQuerySerializer(),
        responses={201: MediaServerJobSerializer(), 409: NOT_CONFIGURED},
    )
    def post(self, request):
        """queue a full refresh job"""
        serializer = RefreshQuerySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        library_id = serializer.validated_data.get("library_id")
        try:
            job = queue_refresh(library_id, CreatedBy.USER)
        except LookupError as err:
            return Response({"error": str(err)}, status=409)
        return Response({"job": job_dict(job)}, status=201)
