"""
Functionality:
- format stored documents for the frontend
"""

import urllib.parse

from common.src.env_settings import EnvironmentSettings
from common.src.helper import date_parser, get_duration_str
from common.src.ta_redis import RedisArchivist
from download.src.thumbnails import ThumbManager


class SearchProcess:
    """process search results"""

    def __init__(self, match_video_user_progress: None | int = None):
        self.position_index = self.get_user_progress(match_video_user_progress)

    def process_one(
        self,
        index: str,
        source: dict,
        score: float | None = None,
        highlight: dict | None = None,
    ):
        """format one document of index (ta_video, ta_channel, ...)"""
        result = {"_index": index, "_source": source, "_score": score}
        if highlight:
            result["highlight"] = highlight
        return self._process_result(result)

    def process_many(self, index: str, sources: list[dict]) -> list:
        """format documents of one index"""
        return [self.process_one(index, source) for source in sources]

    def get_user_progress(self, match_video_user_progress) -> dict | None:
        """get user video watch progress"""
        if not match_video_user_progress:
            return None

        query = f"{match_video_user_progress}:progress:*"
        all_positions = RedisArchivist().list_items(query)
        if not all_positions:
            return None

        pos_index = {
            i["youtube_id"]: i["position"]
            for i in all_positions
            if not i.get("watched")
        }
        return pos_index

    def _process_result(self, result):
        """detect which type of data to process"""
        index = result["_index"]
        processed = False
        if index.startswith("ta_video"):
            processed = self._process_video(result["_source"])
        if index.startswith("ta_channel"):
            processed = self._process_channel(result["_source"])
        if index.startswith("ta_playlist"):
            processed = self._process_playlist(result["_source"])
        if index.startswith("ta_download"):
            processed = self._process_download(result["_source"])
        if index.startswith("ta_comment"):
            processed = self._process_comment(result["_source"])
        if index.startswith("ta_subtitle"):
            processed = self._process_subtitle(result)

        if isinstance(processed, dict):
            processed.update(
                {
                    "_index": index,
                    "_score": round(result.get("_score") or 0, 2),
                }
            )

        return processed

    @staticmethod
    def _process_channel(channel_dict):
        """run on single channel"""
        channel_id = channel_dict["channel_id"]
        cache_root = EnvironmentSettings().get_cache_root()
        art_base = f"{cache_root}/channels/{channel_id}"
        date_str = date_parser(channel_dict["channel_last_refresh"])
        channel_dict.update(
            {
                "channel_last_refresh": date_str,
                "channel_description": channel_dict.get("channel_description"),
            }
        )

        if channel_dict.get("channel_banner_url"):
            channel_dict["channel_banner_url"] = f"{art_base}_banner.jpg"
        else:
            channel_dict["channel_banner_url"] = None

        if channel_dict.get("channel_thumb_url"):
            channel_dict["channel_thumb_url"] = f"{art_base}_thumb.jpg"
        else:
            channel_dict["channel_thumb_url"] = None

        if channel_dict.get("channel_tvart_url"):
            channel_dict["channel_tvart_url"] = f"{art_base}_tvart.jpg"
        else:
            channel_dict["channel_tvart_url"] = None

        return dict(sorted(channel_dict.items()))

    def _process_video(self, video_dict):
        """run on single video dict"""
        video_id = video_dict["youtube_id"]
        media_url = urllib.parse.quote(video_dict["media_url"])
        vid_last_refresh = date_parser(video_dict["vid_last_refresh"])
        published = date_parser(video_dict["published"])
        vid_thumb_url = ThumbManager(video_id).vid_thumb_path()
        channel = self._process_channel(video_dict["channel"])

        cache_root = EnvironmentSettings().get_cache_root()
        media_root = EnvironmentSettings().get_media_root()

        if "subtitles" in video_dict:
            for idx, _ in enumerate(video_dict["subtitles"]):
                url = video_dict["subtitles"][idx]["media_url"]
                video_dict["subtitles"][idx][
                    "media_url"
                ] = f"{media_root}/{url}"
        else:
            video_dict["subtitles"] = []

        video_dict.update(
            {
                "channel": channel,
                "media_url": f"{media_root}/{media_url}",
                "vid_last_refresh": vid_last_refresh,
                "published": published,
                "vid_thumb_url": f"{cache_root}/{vid_thumb_url}",
                "description": video_dict.get("description"),
            }
        )

        if self.position_index:
            player_position = self.position_index.get(video_id)
            total = video_dict["player"].get("duration")
            if player_position and total:
                progress = 100 * (player_position / total)
                video_dict["player"].update(
                    {
                        "progress": progress,
                        "position": player_position,
                    }
                )

        if "playlist" not in video_dict:
            video_dict["playlist"] = []

        return dict(sorted(video_dict.items()))

    @staticmethod
    def _process_playlist(playlist_dict):
        """run on single playlist dict"""
        playlist_id = playlist_dict["playlist_id"]
        playlist_last_refresh = date_parser(
            playlist_dict["playlist_last_refresh"]
        )
        cache_root = EnvironmentSettings().get_cache_root()
        playlist_thumbnail = f"{cache_root}/playlists/{playlist_id}.jpg"
        description = playlist_dict.get("playlist_description")
        playlist_dict.update(
            {
                "playlist_thumbnail": playlist_thumbnail,
                "playlist_last_refresh": playlist_last_refresh,
                "playlist_description": description,
            }
        )

        return dict(sorted(playlist_dict.items()))

    def _process_download(self, download_dict):
        """run on single download item"""
        vid_thumb_url = None
        if download_dict.get("vid_thumb_url"):
            video_id = download_dict["youtube_id"]
            cache_root = EnvironmentSettings().get_cache_root()
            relative_path = ThumbManager(video_id).vid_thumb_path()
            vid_thumb_url = f"{cache_root}/{relative_path}"

        download_dict.update(
            {
                "vid_thumb_url": vid_thumb_url,
                "published": date_parser(download_dict["published"]),
            }
        )
        return dict(sorted(download_dict.items()))

    def _process_comment(self, comment_dict):
        """run on all comments, create reply thread"""
        comment_tree = []
        lookup = {}
        for comment in comment_dict["comment_comments"]:
            comment = comment.copy()
            comment["comment_replies"] = []
            lookup[comment["comment_id"]] = comment

        for comment in lookup.values():
            parent = comment.get("comment_parent")
            if parent == "root":
                comment_tree.append(comment)
            else:
                parent_node = lookup.get(parent)
                if parent_node:
                    parent_node["comment_replies"].append(comment)

        return comment_tree

    def _process_subtitle(self, result):
        """take complete result dict to extract highlight"""
        subtitle_dict = result["_source"]
        highlight = result.get("highlight")
        if highlight:
            # replace lines with the highlighted markdown
            subtitle_line = highlight.get("subtitle_line")[0]
            subtitle_dict.update({"subtitle_line": subtitle_line})

        thumb_path = ThumbManager(subtitle_dict["youtube_id"]).vid_thumb_path()
        subtitle_dict.update({"vid_thumb_url": f"/cache/{thumb_path}"})

        return subtitle_dict
