"""config URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from mcp_server.views import McpView

urlpatterns = [
    path("api/", include("common.urls")),
    path("api/video/", include("video.urls")),
    path("api/channel/", include("channel.urls")),
    path("api/playlist/", include("playlist.urls")),
    path("api/download/", include("download.urls")),
    path("api/task/", include("task.urls")),
    path("api/appsettings/", include("appsettings.urls")),
    path("api/stats/", include("stats.urls")),
    path("api/user/", include("user.urls")),
    path("api/queue/", include("jobs.urls")),
    path("api/found/", include("download.urls_found")),
    path("api/add/", include("download.urls_add")),
    path("api/rules/", include("download.urls_rules")),
    path("api/numbering/", include("emby.urls")),
    path("api/emby/", include("emby.urls_tags")),
    path("api/mediaserver/", include("mediaserver.urls")),
    path("api/mcp", McpView.as_view(), name="api-mcp"),
    path("api/mcp/", McpView.as_view()),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("admin/", admin.site.urls),
]
