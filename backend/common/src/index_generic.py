"""
functionality:
- generic base class to inherit from for video, channel and playlist
"""

import math

from appsettings.src.config import AppConfig
from download.src.yt_dlp_base import YtWrap
from user.src.user_config import UserConfig


class YouTubeItem:
    """base class for youtube"""

    # model with load_doc/save_doc, set by subclasses
    model = None
    yt_base = ""
    yt_obs: dict[str, bool | str] = {
        "skip_download": True,
        "noplaylist": True,
    }

    def __init__(self, youtube_id):
        self.youtube_id = youtube_id
        self.config = AppConfig().config
        self.error = None
        self.youtube_meta = False
        self.json_data = False

    def build_yt_url(self):
        """build youtube url"""
        return self.yt_base + self.youtube_id

    def get_from_youtube(self, obs_overwrite: dict | None = None):
        """use yt-dlp to get meta data from youtube"""
        print(f"{self.youtube_id}: get metadata from youtube")
        obs_request = self.yt_obs.copy()
        if self.config["downloads"]["extractor_lang"]:
            langs = self.config["downloads"]["extractor_lang"]
            langs_list = [i.strip() for i in langs.split(",")]
            obs_request["extractor_args"] = {
                "youtube": {"lang": langs_list}
            }  # type: ignore

        if obs_overwrite:
            obs_request.update(obs_overwrite)

        url = self.build_yt_url()
        self.youtube_meta, self.error = YtWrap(
            obs_request, self.config
        ).extract(url)

    def get_from_db(self) -> None:
        """load the indexed document, None if not indexed"""
        self.json_data = self.model.load_doc(self.youtube_id)

    def save_to_db(self):
        """create or replace from json_data"""
        self.model.save_doc(self.json_data)

    def deactivate(self):
        """mark inactive, the item is gone from youtube"""
        print(f"{self.youtube_id}: deactivate document")
        self.model.objects.filter(pk=self.youtube_id).update(active=False)

    def delete_from_db(self):
        """delete item from the database"""
        print(f"{self.youtube_id}: delete from database")
        self.model.objects.filter(pk=self.youtube_id).delete()


class Pagination:
    """
    figure out the pagination based on page size and total_hits
    """

    def __init__(self, request):
        self.request = request
        self.page_get = False
        self.params = False
        self.get_params()
        self.page_size = self.get_page_size()
        self.pagination = self.first_guess()

    def get_params(self):
        """process url query parameters"""
        query_dict = self.request.GET.copy()
        try:
            self.page_get = int(query_dict.get("page", 0))
        except ValueError:
            self.page_get = 0

        _ = query_dict.pop("page", False)
        self.params = query_dict.urlencode()

    def get_page_size(self):
        """get default or user modified page_size"""
        return UserConfig(self.request.user.id).get_value("page_size")

    def first_guess(self):
        """build first guess before api call"""
        page_get = self.page_get
        page_from = 0
        prev_pages = None
        if page_get > 1:
            page_from = (page_get - 1) * self.page_size
            prev_pages = [
                i for i in range(page_get - 1, page_get - 6, -1) if i > 1
            ]
            prev_pages.reverse()
        pagination = {
            "page_size": self.page_size,
            "page_from": page_from,
            "prev_pages": prev_pages,
            "current_page": page_get,
            "max_hits": False,
            "params": self.params,
        }

        return pagination

    def validate(self, total_hits):
        """validate pagination with total_hits after making api call"""
        page_get = self.page_get
        max_pages = math.ceil(total_hits / self.page_size)

        if page_get < max_pages and max_pages > 1:
            self.pagination["last_page"] = max_pages
        else:
            self.pagination["last_page"] = False
        next_pages = [
            i for i in range(page_get + 1, page_get + 6) if 1 < i < max_pages
        ]

        self.pagination["next_pages"] = next_pages
        self.pagination["total_hits"] = total_hits
