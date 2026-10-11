"""numbering API views, /api/numbering/"""

from common.serializers import ErrorResponseSerializer
from common.views_base import AdminOnly, ApiBaseView, not_built
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from emby.serializers import (
    SchemeChangeResultSerializer,
    SchemeChangeSerializer,
    SchemePreviewSerializer,
    SchemeSerializer,
    SeriesNumberingSerializer,
    VideoNumberDeleteQuerySerializer,
    VideoNumberListSerializer,
    VideoNumberSerializer,
)

STEP = "rules API extensions"
NOT_FOUND = OpenApiResponse(ErrorResponseSerializer(), description="not found")
KIND = OpenApiParameter(
    "kind", str, OpenApiParameter.PATH, enum=["channel", "playlist"]
)


class SchemeListView(ApiBaseView):
    """resolves to /api/numbering/schemes/
    GET: the numbering schemes with labels and examples
    """

    @extend_schema(responses=SchemeSerializer(many=True))
    def get(self, request):
        """list schemes"""
        return not_built(STEP)


class SeriesNumberingView(ApiBaseView):
    """resolves to /api/numbering/<kind>/<series_id>/
    GET: scheme and a preview of the Emby names
    PUT: change the scheme
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        parameters=[KIND],
        responses={200: SeriesNumberingSerializer(), 404: NOT_FOUND},
    )
    def get(self, request, kind, series_id):
        """numbering of a channel or playlist"""
        return not_built(STEP)

    @extend_schema(
        parameters=[KIND],
        request=SchemeChangeSerializer(),
        responses={
            200: SchemeChangeResultSerializer(),
            400: OpenApiResponse(
                ErrorResponseSerializer(),
                description="scheme not valid here, e.g. playlist_position "
                "on a channel",
            ),
            404: NOT_FOUND,
        },
    )
    def put(self, request, kind, series_id):
        """set the scheme; queues an Emby write"""
        return not_built(STEP)


class SeriesNumberingPreviewView(ApiBaseView):
    """resolves to /api/numbering/<kind>/<series_id>/preview/
    POST: what changing the scheme would do, nothing is stored
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        parameters=[KIND],
        request=SchemeChangeSerializer(),
        responses={200: SchemePreviewSerializer(), 404: NOT_FOUND},
    )
    def post(self, request, kind, series_id):
        """count and sample of renumbered episodes"""
        return not_built(STEP)


class VideoNumberView(ApiBaseView):
    """resolves to /api/numbering/video/<youtube_id>/
    GET: the video's number in every series it is in
    PUT: set season and episode by hand in one series
    DELETE: back to automatic numbering in one series
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        responses={200: VideoNumberListSerializer(), 404: NOT_FOUND}
    )
    def get(self, request, youtube_id):
        """numbers of a video"""
        return not_built(STEP)

    @extend_schema(
        request=VideoNumberSerializer(),
        responses={
            200: VideoNumberSerializer(),
            404: NOT_FOUND,
            409: OpenApiResponse(
                ErrorResponseSerializer(),
                description="another video already has this S/E",
            ),
        },
    )
    def put(self, request, youtube_id):
        """manual season and episode, kept by every scheme"""
        return not_built(STEP)

    @extend_schema(
        parameters=[VideoNumberDeleteQuerySerializer()],
        responses={204: None, 404: NOT_FOUND},
    )
    def delete(self, request, youtube_id):
        """remove the manual number"""
        return not_built(STEP)
