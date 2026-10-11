"""rules API views, /api/rules/"""

from common.serializers import ErrorResponseSerializer
from common.views_base import AdminOnly, ApiBaseView, not_built
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
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)

STEP = "rules API extensions"
NOT_FOUND = OpenApiResponse(ErrorResponseSerializer(), description="not found")
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
        return not_built(STEP)


class RulePresetView(ApiBaseView):
    """resolves to /api/rules/presets/
    GET: the one-click chips of the rule editor
    """

    @extend_schema(responses=RulePresetSerializer(many=True))
    def get(self, request):
        """list presets"""
        return not_built(STEP)


class RuleDefaultView(ApiBaseView):
    """resolves to /api/rules/default/
    GET, PUT, DELETE the default rule; GET returns null when there is none
    """

    permission_classes = [AdminOnly]

    @extend_schema(responses={200: RuleSerializer()})
    def get(self, request):
        """default rule or null"""
        return not_built(STEP)

    @extend_schema(
        request=DownloadRuleSerializer(),
        responses={200: RuleSerializer()},
    )
    def put(self, request):
        """create or replace"""
        return not_built(STEP)

    @extend_schema(responses={204: None})
    def delete(self, request):
        """remove: everything downloads unless a channel rule says else"""
        return not_built(STEP)


class RuleTargetView(ApiBaseView):
    """resolves to /api/rules/<scope>/<target_id>/
    GET, PUT, DELETE the rule of one channel or playlist
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        operation_id="rules_target_retrieve",
        parameters=[SCOPE],
        responses={200: RuleSerializer(), 404: NOT_FOUND},
    )
    def get(self, request, scope, target_id):
        """the target's own rule, or null if it uses the default"""
        return not_built(STEP)

    @extend_schema(
        parameters=[SCOPE],
        request=DownloadRuleSerializer(),
        responses={200: RuleSerializer(), 404: NOT_FOUND},
    )
    def put(self, request, scope, target_id):
        """create or replace"""
        return not_built(STEP)

    @extend_schema(parameters=[SCOPE], responses={204: None, 404: NOT_FOUND})
    def delete(self, request, scope, target_id):
        """remove: the target falls back to the default rule"""
        return not_built(STEP)


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
        return not_built(STEP)


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
        return not_built(STEP)


class VideoOverrideListView(ApiBaseView):
    """resolves to /api/rules/videos/
    GET: every per-video override
    """

    permission_classes = [AdminOnly]

    @extend_schema(responses=VideoOverrideListSerializer())
    def get(self, request):
        """list overrides"""
        return not_built(STEP)


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
        return not_built(STEP)

    @extend_schema(responses={204: None, 404: NOT_FOUND})
    def delete(self, request, youtube_id):
        """remove override"""
        return not_built(STEP)
