"""numbering API urls, /api/numbering/"""

from django.urls import path

from emby import views

urlpatterns = [
    path(
        "schemes/",
        views.SchemeListView.as_view(),
        name="api-numbering-schemes",
    ),
    path(
        "video/<slug:youtube_id>/",
        views.VideoNumberView.as_view(),
        name="api-numbering-video",
    ),
    path(
        "<slug:kind>/<slug:series_id>/",
        views.SeriesNumberingView.as_view(),
        name="api-numbering-series",
    ),
    path(
        "<slug:kind>/<slug:series_id>/preview/",
        views.SeriesNumberingPreviewView.as_view(),
        name="api-numbering-series-preview",
    ),
]
