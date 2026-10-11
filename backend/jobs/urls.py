"""work queue API urls, /api/queue/"""

from django.urls import path
from jobs import views

urlpatterns = [
    path(
        "summary/",
        views.QueueSummaryView.as_view(),
        name="api-queue-summary",
    ),
    path("jobs/", views.JobListView.as_view(), name="api-queue-jobs"),
    path(
        "jobs/action/",
        views.JobBulkActionView.as_view(),
        name="api-queue-jobs-action",
    ),
    path(
        "jobs/<int:job_id>/",
        views.JobView.as_view(),
        name="api-queue-job",
    ),
    path(
        "jobs/<int:job_id>/action/",
        views.JobActionView.as_view(),
        name="api-queue-job-action",
    ),
    path("lanes/", views.LaneListView.as_view(), name="api-queue-lanes"),
    path(
        "lanes/<slug:lane>/",
        views.LaneView.as_view(),
        name="api-queue-lane",
    ),
    path(
        "scheduled/",
        views.ScheduledListView.as_view(),
        name="api-queue-scheduled",
    ),
    path(
        "scheduled/<slug:name>/",
        views.ScheduledView.as_view(),
        name="api-queue-scheduled-item",
    ),
    path(
        "scheduled/<slug:name>/run/",
        views.ScheduledRunView.as_view(),
        name="api-queue-scheduled-run",
    ),
]
