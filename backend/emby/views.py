"""numbering API views, /api/numbering/"""

from django.core.exceptions import ObjectDoesNotExist
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework.response import Response

from common.serializers import ErrorResponseSerializer
from common.views_base import AdminOnly, ApiBaseView
from emby.serializers import (
    EmbyTagsSerializer,
    SchemeChangeResultSerializer,
    SchemeChangeSerializer,
    SchemePreviewSerializer,
    SchemeSerializer,
    SeriesNumberingSerializer,
    VideoNumberDeleteQuerySerializer,
    VideoNumberListSerializer,
    VideoNumberSerializer,
)
from emby.src.numbering_api import (
    NumberConflict,
    delete_manual,
    preview_scheme,
    schemes,
    series_numbering,
    set_manual,
    set_scheme,
    video_numbers,
)
from emby.src.tags import known_tags

NOT_FOUND = OpenApiResponse(ErrorResponseSerializer(), description="not found")


def _not_found() -> Response:
    return Response({"error": "not found"}, status=404)


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
        return Response(schemes())


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
        try:
            return Response(series_numbering(kind, series_id))
        except (KeyError, ObjectDoesNotExist):
            return _not_found()

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
        serializer = SchemeChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scheme = serializer.validated_data["scheme"]
        try:
            return Response(set_scheme(kind, series_id, scheme))
        except (KeyError, ObjectDoesNotExist):
            return _not_found()
        except ValueError as err:
            return Response({"error": str(err)}, status=400)


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
        serializer = SchemeChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scheme = serializer.validated_data["scheme"]
        try:
            return Response(preview_scheme(kind, series_id, scheme))
        except (KeyError, ObjectDoesNotExist):
            return _not_found()
        except ValueError as err:
            return Response({"error": str(err)}, status=400)


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
        try:
            return Response({"data": video_numbers(youtube_id)})
        except ObjectDoesNotExist:
            return _not_found()

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
        serializer = VideoNumberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            item = set_manual(
                youtube_id,
                data["series_kind"],
                data["series_id"],
                data["season"],
                data["episode"],
            )
        except (LookupError, ObjectDoesNotExist):
            return _not_found()
        except NumberConflict as err:
            return Response({"error": str(err)}, status=409)
        return Response(item)

    @extend_schema(
        parameters=[VideoNumberDeleteQuerySerializer()],
        responses={204: None, 404: NOT_FOUND},
    )
    def delete(self, request, youtube_id):
        """remove the manual number"""
        serializer = VideoNumberDeleteQuerySerializer(
            data=request.query_params
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not delete_manual(
            youtube_id, data["series_kind"], data["series_id"]
        ):
            return _not_found()
        return Response(status=204)


class EmbyTagsView(ApiBaseView):
    """resolves to /api/emby/tags/
    GET: Emby tags in use or configured, to offer on channels and playlists
    """

    @extend_schema(responses=EmbyTagsSerializer())
    def get(self, request):
        """list tags"""
        return Response({"tags": known_tags()})
