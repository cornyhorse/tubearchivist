"""media server API urls, /api/mediaserver/"""

from django.urls import path
from mediaserver import views

urlpatterns = [
    path("", views.MediaServerView.as_view(), name="api-mediaserver"),
    path(
        "test/",
        views.MediaServerTestView.as_view(),
        name="api-mediaserver-test",
    ),
    path(
        "libraries/",
        views.LibraryListView.as_view(),
        name="api-mediaserver-libraries",
    ),
    path(
        "notify/",
        views.NotifyView.as_view(),
        name="api-mediaserver-notify",
    ),
    path(
        "refresh/",
        views.RefreshView.as_view(),
        name="api-mediaserver-refresh",
    ),
]
