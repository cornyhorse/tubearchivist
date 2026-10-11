"""add dialog API urls, /api/add/"""

from django.urls import path
from download import views_found

urlpatterns = [
    path("", views_found.AddView.as_view(), name="api-add"),
    path(
        "preview/",
        views_found.AddPreviewView.as_view(),
        name="api-add-preview",
    ),
]
