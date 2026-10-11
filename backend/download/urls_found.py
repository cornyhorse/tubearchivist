"""found videos API urls, /api/found/"""

from django.urls import path
from download import views_found

urlpatterns = [
    path("", views_found.FoundListView.as_view(), name="api-found"),
    path(
        "actions/",
        views_found.FoundActionView.as_view(),
        name="api-found-actions",
    ),
]
