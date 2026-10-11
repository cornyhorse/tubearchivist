"""rules API urls, /api/rules/"""

from django.urls import path
from download import views_rules

urlpatterns = [
    path("", views_rules.RuleListView.as_view(), name="api-rules"),
    path(
        "presets/",
        views_rules.RulePresetView.as_view(),
        name="api-rules-presets",
    ),
    path(
        "default/",
        views_rules.RuleDefaultView.as_view(),
        name="api-rules-default",
    ),
    path(
        "preview/",
        views_rules.RulePreviewView.as_view(),
        name="api-rules-preview",
    ),
    path(
        "effective/",
        views_rules.RuleEffectiveView.as_view(),
        name="api-rules-effective",
    ),
    path(
        "videos/",
        views_rules.VideoOverrideListView.as_view(),
        name="api-rules-videos",
    ),
    path(
        "videos/<slug:youtube_id>/",
        views_rules.VideoOverrideView.as_view(),
        name="api-rules-video",
    ),
    path(
        "<slug:scope>/<slug:target_id>/",
        views_rules.RuleTargetView.as_view(),
        name="api-rules-target",
    ),
]
