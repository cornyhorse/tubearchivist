"""rules API views, /api/rules/"""

from common.serializers import ErrorResponseSerializer
from common.views_base import AdminOnly, ApiBaseView
from django.http import JsonResponse
from download.models import VideoOverride
from download.serializers import DownloadRuleSerializer
from download.serializers_rules import (
    EffectiveRuleQuerySerializer,
    EffectiveRuleSerializer,
    RuleListSerializer,
    RulePresetSerializer,
    RulePreviewQuerySerializer,
    RulePreviewSerializer,
    RuleSerializer,
    VideoOverrideListSerializer,
    VideoOverrideSerializer,
)
from download.src.rules_api import (
    PRESETS,
    delete_override,
    effective_rule,
    get_target_rule,
    list_overrides,
    override_dict,
    preview_rule,
    rule_rows,
    set_override,
    set_target_rule,
    target_exists,
)
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework.response import Response

NOT_FOUND = OpenApiResponse(ErrorResponseSerializer(), description="not found")


def rule_response(rule: dict | None):
    """the rule, or JSON null so the frontend can parse 'no rule'"""
    if rule is None:
        return JsonResponse(None, safe=False)
    return Response(rule)


SCOPE = OpenApiParameter(
    "scope", str, OpenApiParameter.PATH, enum=["channel", "playlist"]
)


class RuleListView(ApiBaseView):
    """resolves to /api/rules/
    GET: the default rule and every channel and playlist rule
    """

    permission_classes = [AdminOnly]

    @extend_schema(operation_id="rules_list", responses=RuleListSerializer())
    def get(self, request):
        """all rules"""
        return Response({"data": rule_rows()})


class RulePresetView(ApiBaseView):
    """resolves to /api/rules/presets/
    GET: the one-click chips of the rule editor
    """

    @extend_schema(responses=RulePresetSerializer(many=True))
    def get(self, request):
        """list presets"""
        return Response(PRESETS)


class RuleDefaultView(ApiBaseView):
    """resolves to /api/rules/default/
    GET, PUT, DELETE the default rule; GET returns null when there is none
    """

    permission_classes = [AdminOnly]

    @extend_schema(responses={200: RuleSerializer()})
    def get(self, request):
        """default rule or null"""
        return rule_response(get_target_rule("default", None))

    @extend_schema(
        request=DownloadRuleSerializer(),
        responses={200: RuleSerializer()},
    )
    def put(self, request):
        """create or replace"""
        serializer = DownloadRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            set_target_rule("default", None, serializer.validated_data)
        )

    @extend_schema(responses={204: None})
    def delete(self, request):
        """remove: everything downloads unless a channel rule says else"""
        set_target_rule("default", None, None)
        return Response(status=204)


class RuleTargetView(ApiBaseView):
    """resolves to /api/rules/<scope>/<target_id>/
    GET, PUT, DELETE the rule of one channel or playlist
    """

    permission_classes = [AdminOnly]

    @staticmethod
    def _check(scope: str, target_id: str) -> Response | None:
        """404 for an unknown scope or target"""
        if scope not in ("channel", "playlist"):
            return Response({"error": "unknown scope"}, status=404)
        if not target_exists(scope, target_id):
            return Response({"error": f"{scope} not found"}, status=404)
        return None

    @extend_schema(
        operation_id="rules_target_retrieve",
        parameters=[SCOPE],
        responses={200: RuleSerializer(), 404: NOT_FOUND},
    )
    def get(self, request, scope, target_id):
        """the target's own rule, or null if it uses the default"""
        if error := self._check(scope, target_id):
            return error
        return rule_response(get_target_rule(scope, target_id))

    @extend_schema(
        parameters=[SCOPE],
        request=DownloadRuleSerializer(),
        responses={200: RuleSerializer(), 404: NOT_FOUND},
    )
    def put(self, request, scope, target_id):
        """create or replace"""
        if error := self._check(scope, target_id):
            return error
        serializer = DownloadRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = set_target_rule(scope, target_id, serializer.validated_data)
        return Response(rule)

    @extend_schema(parameters=[SCOPE], responses={204: None, 404: NOT_FOUND})
    def delete(self, request, scope, target_id):
        """remove: the target falls back to the default rule"""
        if error := self._check(scope, target_id):
            return error
        set_target_rule(scope, target_id, None)
        return Response(status=204)


class RulePreviewView(ApiBaseView):
    """resolves to /api/rules/preview/
    POST: run a saved or draft rule against the last N known videos
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=RulePreviewQuerySerializer(),
        responses={200: RulePreviewSerializer(), 404: NOT_FOUND},
    )
    def post(self, request):
        """preview a rule"""
        serializer = RulePreviewQuerySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target_id = data.get("target_id")
        if data["scope"] != "default":
            if not target_id or not target_exists(data["scope"], target_id):
                return Response({"error": "target not found"}, status=404)
        draft = data["rule"] if "rule" in data else False
        return Response(
            preview_rule(data["scope"], target_id, draft, data["limit"])
        )


class RuleEffectiveView(ApiBaseView):
    """resolves to /api/rules/effective/
    GET: which rule applies to a video, channel or playlist, and from where
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        parameters=[EffectiveRuleQuerySerializer()],
        responses={200: EffectiveRuleSerializer()},
    )
    def get(self, request):
        """effective rule"""
        serializer = EffectiveRuleQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(effective_rule(**serializer.validated_data))


class VideoOverrideListView(ApiBaseView):
    """resolves to /api/rules/videos/
    GET: every per-video override
    """

    permission_classes = [AdminOnly]

    @extend_schema(responses=VideoOverrideListSerializer())
    def get(self, request):
        """list overrides"""
        return Response({"data": list_overrides()})


class VideoOverrideView(ApiBaseView):
    """resolves to /api/rules/videos/<youtube_id>/
    PUT: 'download anyway' or 'always ignore'; DELETE: rules decide again
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=VideoOverrideSerializer(),
        responses={200: VideoOverrideSerializer()},
    )
    def put(self, request, youtube_id):
        """set override; download queues a job, ignore cancels queued"""
        serializer = VideoOverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        set_override([youtube_id], data["override"], data["note"])
        item = VideoOverride.objects.get(pk=youtube_id)
        return Response(override_dict(item))

    @extend_schema(responses={204: None, 404: NOT_FOUND})
    def delete(self, request, youtube_id):
        """remove override"""
        if not delete_override(youtube_id):
            return Response({"error": "no override"}, status=404)
        return Response(status=204)
