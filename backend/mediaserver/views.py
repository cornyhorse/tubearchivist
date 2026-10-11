"""media server API views, /api/mediaserver/"""

from common.serializers import ErrorResponseSerializer
from common.views_base import AdminOnly, ApiBaseView, not_built
from drf_spectacular.utils import OpenApiResponse, extend_schema
from mediaserver.serializers import (
    LibraryListSerializer,
    MediaServerJobSerializer,
    MediaServerSerializer,
    MediaServerTestQuerySerializer,
    MediaServerTestSerializer,
    NotifyQuerySerializer,
    RefreshQuerySerializer,
)

STEP = "media server integration"
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
        return not_built(STEP)

    @extend_schema(
        request=MediaServerSerializer(),
        responses={200: MediaServerSerializer()},
    )
    def put(self, request):
        """save settings; the key is stored encrypted"""
        return not_built(STEP)


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
        return not_built(STEP)


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
        return not_built(STEP)


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
        return not_built(STEP)


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
        return not_built(STEP)
