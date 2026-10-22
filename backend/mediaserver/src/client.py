"""
Functionality:
- talk to Emby or Jellyfin: test, libraries, media updated, refresh
- the API key goes in a header only, never in a url, log or error
"""

import requests
from mediaserver.src.settings import Connection

TIMEOUT = 15
# Emby and Jellyfin accept at most this many updates per call comfortably
CHUNK = 500
# items per page when listing a user's items
PAGE = 2000
# ids per playlist call, keeps the url short
IDS_PER_CALL = 100


class MediaServerError(Exception):
    """the server could not be reached or refused, message is safe to show"""


class MediaServerNotFound(MediaServerError):
    """the server answered 404: the item is gone"""


class MediaServerClient:
    """Emby and Jellyfin share these endpoints"""

    def __init__(self, conn: Connection):
        self.conn = conn

    def _headers(self) -> dict:
        if self.conn.type == "jellyfin":
            token = f'MediaBrowser Token="{self.conn.api_key}"'
            return {"Authorization": token}
        return {"X-Emby-Token": self.conn.api_key}

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.conn.url}{path}"
        try:
            response = requests.request(
                method, url, headers=self._headers(), timeout=TIMEOUT, **kwargs
            )
        except requests.RequestException as err:
            # the message of a connection error never contains the headers
            reason = err.__class__.__name__
            raise MediaServerError(
                f"can't reach {self.conn.url}: {reason}"
            ) from err
        if response.status_code in (401, 403):
            raise MediaServerError("the server rejected the API key")
        if response.status_code == 404:
            raise MediaServerNotFound(f"{method} {path}: not found")
        if response.status_code >= 400:
            raise MediaServerError(
                f"{method} {path} failed: HTTP {response.status_code}"
            )
        return response

    def info(self) -> dict:
        """server name, product and version; checks the key"""
        data = self._request("GET", "/System/Info").json()
        product = data.get("ProductName") or (
            "Jellyfin" if self.conn.type == "jellyfin" else "Emby Server"
        )
        return {
            "server_name": data.get("ServerName"),
            "product": product,
            "version": data.get("Version"),
        }

    def libraries(self) -> list[dict]:
        """the virtual folders"""
        folders = self._request("GET", "/Library/VirtualFolders").json()
        return [
            {
                "id": str(folder.get("ItemId") or folder.get("Id") or ""),
                "name": folder.get("Name") or "",
                "collection_type": folder.get("CollectionType"),
                "paths": list(folder.get("Locations") or []),
            }
            for folder in folders
        ]

    def media_updated(self, updates: list[dict]) -> int:
        """
        report changed files: [{Path, UpdateType}], UpdateType Created,
        Modified or Deleted; returns the last HTTP status code
        """
        status = 0
        for start in range(0, len(updates), CHUNK):
            chunk = updates[start : start + CHUNK]
            response = self._request(
                "POST", "/Library/Media/Updated", json={"Updates": chunk}
            )
            status = response.status_code
        return status

    def users(self) -> list[dict]:
        """the server's users: id and name"""
        data = self._request("GET", "/Users").json()
        return [
            {
                "id": str(u.get("Id")),
                "name": u.get("Name") or "",
                "admin": bool((u.get("Policy") or {}).get("IsAdministrator")),
            }
            for u in data
        ]

    def watched_paths(self, user_id: str) -> set[str]:
        """
        server paths of the episodes a user has played or started (a read
        only query of the user's data); pages of PAGE items
        """
        paths: set[str] = set()
        for state in ("IsPlayed", "IsResumable"):
            start = 0
            while True:
                data = self._request(
                    "GET",
                    f"/Users/{user_id}/Items",
                    params={
                        "Recursive": "true",
                        "IncludeItemTypes": "Episode",
                        "Filters": state,
                        "Fields": "Path",
                        "StartIndex": start,
                        "Limit": PAGE,
                    },
                ).json()
                items = data.get("Items") or []
                paths.update(i["Path"] for i in items if i.get("Path"))
                start += PAGE
                if start >= data.get("TotalRecordCount", 0):
                    break
        return paths

    def series_with_tag(self, tag: str) -> list[dict]:
        """
        shows that carry a tag (a read only query): name, server path,
        provider ids and all their tags
        """
        data = self._request(
            "GET",
            "/Items",
            params={
                "Tags": tag,
                "Recursive": "true",
                "IncludeItemTypes": "Series",
                "Fields": "Path,ProviderIds,Tags,TagItems",
            },
        ).json()
        return [
            {
                "id": str(item.get("Id") or ""),
                "name": item.get("Name") or "",
                "path": item.get("Path") or "",
                "provider_ids": item.get("ProviderIds") or {},
                "tags": sorted(
                    (
                        {t.get("Name") for t in item.get("TagItems") or []}
                        | set(item.get("Tags") or [])
                    )
                    - {None}
                ),
            }
            for item in data.get("Items") or []
        ]

    def library_metadata(self) -> list[dict]:
        """
        shows and episodes with what is set on them that a refresh that
        replaces metadata would clear: path, rating, genres, tags (a read
        only query of the whole library, in pages)
        """
        items: list[dict] = []
        start = 0
        while True:
            data = self._request(
                "GET",
                "/Items",
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": "Series,Episode",
                    "Fields": "Path,OfficialRating,CustomRating,Genres,"
                    "Tags,TagItems",
                    "StartIndex": start,
                    "Limit": PAGE,
                },
            ).json()
            for item in data.get("Items") or []:
                items.append(
                    {
                        "path": item.get("Path") or "",
                        "rating": item.get("OfficialRating")
                        or item.get("CustomRating")
                        or "",
                        "genres": list(item.get("Genres") or []),
                        "tags": sorted(
                            {t.get("Name") for t in item.get("TagItems") or []}
                            - {None}
                        ),
                    }
                )
            start += PAGE
            if start >= data.get("TotalRecordCount", 0):
                return items

    def series_episode_paths(self, series_id: str) -> list[str]:
        """server paths of every episode of a show (a read only query)"""
        paths: list[str] = []
        start = 0
        while True:
            data = self._request(
                "GET",
                "/Items",
                params={
                    "ParentId": series_id,
                    "Recursive": "true",
                    "IncludeItemTypes": "Episode",
                    "Fields": "Path",
                    "StartIndex": start,
                    "Limit": PAGE,
                },
            ).json()
            paths += [
                i["Path"] for i in data.get("Items") or [] if i.get("Path")
            ]
            start += PAGE
            if start >= data.get("TotalRecordCount", 0):
                return paths

    def find_item(self, server_path: str, item_type: str) -> str | None:
        """
        id of the item of this type at exactly this server path, if any;
        the filter is not trusted, the path of each result is compared
        """
        data = self._request(
            "GET",
            "/Items",
            params={
                "Path": server_path,
                "Recursive": "true",
                "IncludeItemTypes": item_type,
                "Fields": "Path",
                "Limit": 5,
            },
        ).json()
        for item in data.get("Items") or []:
            if item.get("Path") == server_path:
                return str(item["Id"])
        return None

    def refresh_item(self, item_id: str) -> int:
        """
        read the item's NFO again and replace what the server holds with
        it. The default refresh mode only fills in what is empty: a tag
        taken out of the NFO, or another one put in its place, stayed as
        it was (tested on Emby 4.9.5.0), which would leave a user with
        access that was withdrawn.
        TA owns these NFOs; images are sidecar files (ReplaceAllImages
        stays off) and watch state is not metadata.
        """
        return self._request(
            "POST",
            f"/Items/{item_id}/Refresh",
            params={
                "Recursive": "false",
                "MetadataRefreshMode": "FullRefresh",
                "ReplaceAllMetadata": "true",
                "ReplaceAllImages": "false",
            },
        ).status_code

    def refresh(self, library_id: str | None = None) -> int:
        """full refresh of one library, or of all of them"""
        if library_id:
            path = f"/Items/{library_id}/Refresh"
            params = {"Recursive": "true"}
        else:
            path, params = "/Library/Refresh", None
        return self._request("POST", path, params=params).status_code

    # playlists (Emby; Jellyfin's API differs)

    def episode_ids(self) -> dict[str, str]:
        """{server path: item id} of every episode the server knows"""
        ids: dict[str, str] = {}
        start = 0
        while True:
            data = self._request(
                "GET",
                "/Items",
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": "Episode",
                    "Fields": "Path",
                    "StartIndex": start,
                    "Limit": PAGE,
                },
            ).json()
            for item in data.get("Items") or []:
                if item.get("Path"):
                    ids[item["Path"]] = str(item["Id"])
            start += PAGE
            if start >= data.get("TotalRecordCount", 0):
                return ids

    def playlist_items(
        self, playlist_id: str, user_id: str
    ) -> list[tuple[str, str]]:
        """
        [(entry id, item id)] in order, as user_id is allowed to see;
        MediaServerNotFound if the playlist is gone
        """
        items: list[tuple[str, str]] = []
        start = 0
        while True:
            try:
                data = self._request(
                    "GET",
                    f"/Playlists/{playlist_id}/Items",
                    params={
                        "UserId": user_id,
                        "StartIndex": start,
                        "Limit": PAGE,
                    },
                ).json()
            except MediaServerNotFound:
                raise
            except MediaServerError:
                # Emby answers 500, not 404, for a playlist that is gone
                if self.item_exists(playlist_id):
                    raise
                raise MediaServerNotFound(f"playlist {playlist_id} is gone")
            items += [
                (str(i["PlaylistItemId"]), str(i["Id"]))
                for i in data.get("Items") or []
            ]
            start += PAGE
            if start >= data.get("TotalRecordCount", 0):
                return items

    def item_exists(self, item_id: str) -> bool:
        """the server holds an item with this id"""
        data = self._request(
            "GET", "/Items", params={"Ids": item_id, "Recursive": "true"}
        ).json()
        return bool(data.get("Items"))

    def owned_item(self, user_id: str, item_id: str) -> dict | None:
        """
        {type, name} of an item as user_id sees it, None if they can't:
        a playlist is visible to its owner only, so this tells whose it is
        """
        data = self._request(
            "GET", f"/Users/{user_id}/Items", params={"Ids": item_id}
        ).json()
        items = data.get("Items") or []
        if not items:
            return None
        return {"type": items[0].get("Type"), "name": items[0].get("Name")}

    def create_playlist(
        self, name: str, user_id: str, item_ids: list[str]
    ) -> str:
        """a playlist owned by user_id holding item_ids; its id"""
        first, rest = item_ids[:IDS_PER_CALL], item_ids[IDS_PER_CALL:]
        data = self._request(
            "POST",
            "/Playlists",
            params={
                "Name": name,
                "Ids": ",".join(first),
                "MediaType": "Video",
                "UserId": user_id,
            },
        ).json()
        playlist_id = str(data["Id"])
        if rest:
            self.add_to_playlist(playlist_id, user_id, rest)
        return playlist_id

    def add_to_playlist(
        self, playlist_id: str, user_id: str, item_ids: list[str]
    ) -> None:
        """append items, in order"""
        for start in range(0, len(item_ids), IDS_PER_CALL):
            chunk = item_ids[start : start + IDS_PER_CALL]
            self._request(
                "POST",
                f"/Playlists/{playlist_id}/Items",
                params={"Ids": ",".join(chunk), "UserId": user_id},
            )

    def remove_from_playlist(
        self, playlist_id: str, entry_ids: list[str]
    ) -> None:
        """remove entries (the PlaylistItemId, not the item id)"""
        for start in range(0, len(entry_ids), IDS_PER_CALL):
            chunk = entry_ids[start : start + IDS_PER_CALL]
            self._request(
                "DELETE",
                f"/Playlists/{playlist_id}/Items",
                params={"EntryIds": ",".join(chunk)},
            )

    def delete_item(self, item_id: str) -> None:
        """delete an item (a playlist, never a media file here)"""
        self._request("DELETE", f"/Items/{item_id}")
