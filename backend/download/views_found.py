"""found videos and add dialog API views, /api/found/ and /api/add/"""

from common.serializers import ErrorResponseSerializer
from common.views_base import AdminOnly, ApiBaseView, not_built
from download.serializers_found import (
    AddResultSerializer,
    AddSerializer,
    FoundActionResponseSerializer,
    FoundActionSerializer,
    FoundListQuerySerializer,
    FoundListSerializer,
    PreviewQuerySerializer,
    PreviewSerializer,
)
from drf_spectacular.utils import OpenApiResponse, extend_schema

BAD_REQUEST = OpenApiResponse(
    ErrorResponseSerializer(), description="bad request"
)


class FoundListView(ApiBaseView):
    """resolves to /api/found/
    GET: found videos, filtered, searched and paginated, with tab counts
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        parameters=[FoundListQuerySerializer()],
        responses=FoundListSerializer(),
    )
    def get(self, request):
        """list found videos"""
        return not_built("jobs engine")


class FoundActionView(ApiBaseView):
    """resolves to /api/found/actions/
    POST: download, ignore, forget... on explicit ids or a filter
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=FoundActionSerializer(),
        responses={200: FoundActionResponseSerializer(), 400: BAD_REQUEST},
    )
    def post(self, request):
        """bulk action"""
        return not_built("jobs engine")


class AddPreviewView(ApiBaseView):
    """resolves to /api/add/preview/
    POST: resolve links and show what the rules would do; stores nothing
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=PreviewQuerySerializer(),
        responses={200: PreviewSerializer(), 400: BAD_REQUEST},
    )
    def post(self, request):
        """
        preview links; reads YouTube (flat), so it can take a few seconds
        per channel; results are cached for 10 minutes per link
        """
        return not_built("scoped scan and add")


class AddView(ApiBaseView):
    """resolves to /api/add/
    POST: add links: subscribe, download once, or only the newest N
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=AddSerializer(),
        responses={201: AddResultSerializer(), 400: BAD_REQUEST},
    )
    def post(self, request):
        """queue one high priority scan job per link"""
        return not_built("scoped scan and add")
