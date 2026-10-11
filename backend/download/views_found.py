"""found videos and add dialog API views, /api/found/ and /api/add/"""

from common.serializers import ErrorResponseSerializer
from common.src.index_generic import Pagination
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
from download.src.found import (
    NotBuiltYet,
    found_action,
    found_dicts,
    found_queryset,
    tab_counts,
)
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.response import Response

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
        query = FoundListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        queryset = found_queryset(filters)
        pagination = Pagination(request, page_size=filters.get("page_size"))
        page = pagination.pagination
        start = page["page_from"]
        items = list(queryset[start : start + page["page_size"]])
        pagination.validate(queryset.count())
        return Response(
            {
                "data": found_dicts(items),
                "paginate": page,
                "counts": tab_counts(filters),
            }
        )


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
        serializer = FoundActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = found_action(
                data["action"], ids=data.get("ids"), filters=data.get("filter")
            )
        except NotBuiltYet as err:
            return not_built(str(err))
        except ValueError as err:
            return Response({"error": str(err)}, status=400)
        return Response(result)


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
