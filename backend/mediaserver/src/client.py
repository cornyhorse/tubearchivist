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


class MediaServerError(Exception):
    """the server could not be reached or refused, message is safe to show"""


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
