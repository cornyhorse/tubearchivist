"""
Export every TubeArchivist ES index to newline-delimited JSON, read only.

Each output line is {"_id": ..., "_source": {...}}. Uses point-in-time plus
search_after, so it never modifies the source cluster and copes with indices
larger than 10k docs (ta_subtitle). Standard library only, so it can run on
any host that can reach ES.

usage:
  ES_URL=http://127.0.0.1:9200 ELASTIC_PASSWORD=... \
      python -m storage.es_export /path/to/export_dir
"""

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

INDEXES = [
    "ta_config",
    "ta_channel",
    "ta_video",
    "ta_playlist",
    "ta_download",
    "ta_comment",
    "ta_subtitle",
]
PAGE_SIZE = 1000


class EsReader:
    """minimal read-only ES client"""

    def __init__(self, url: str, user: str, password: str):
        self.url = url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        """make request, return parsed json"""
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.url}/{path.lstrip('/')}",
            data=data,
            headers=self.headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())

    def exists(self, index: str) -> bool:
        """check index alias exists"""
        try:
            self.call("GET", f"{index}/_count")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return False
            raise
        return True

    def iter_docs(self, index: str):
        """yield all hits of index"""
        pit = self.call("POST", f"{index}/_pit?keep_alive=5m")["id"]
        search_after = None
        try:
            while True:
                body = {
                    "size": PAGE_SIZE,
                    "query": {"match_all": {}},
                    "pit": {"id": pit, "keep_alive": "5m"},
                    "sort": [{"_shard_doc": "asc"}],
                }
                if search_after:
                    body["search_after"] = search_after
                response = self.call("POST", "_search", body)
                pit = response.get("pit_id", pit)
                hits = response["hits"]["hits"]
                if not hits:
                    break
                yield from hits
                search_after = hits[-1]["sort"]
        finally:
            self.call("DELETE", "_pit", {"id": pit})


def export(reader: EsReader, out_dir: Path) -> dict[str, int]:
    """export all indexes, return counts"""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for index in INDEXES:
        if not reader.exists(index):
            counts[index] = 0
            continue
        expected = reader.call("GET", f"{index}/_count")["count"]
        tmp = out_dir / f"{index}.ndjson.tmp"
        written = 0
        with open(tmp, "w", encoding="utf-8") as f:
            for hit in reader.iter_docs(index):
                line = {"_id": hit["_id"], "_source": hit["_source"]}
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
                written += 1
        if written != expected:
            raise RuntimeError(f"{index}: exported {written}, count {expected}")
        tmp.replace(out_dir / f"{index}.ndjson")
        counts[index] = written

    (out_dir / "manifest.json").write_text(json.dumps(counts, indent=2))
    return counts


def main() -> int:
    """entry point"""
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    reader = EsReader(
        os.environ.get("ES_URL", "http://127.0.0.1:9200"),
        os.environ.get("ELASTIC_USER", "elastic"),
        os.environ["ELASTIC_PASSWORD"],
    )
    counts = export(reader, Path(sys.argv[1]))
    for index, count in counts.items():
        print(f"{index}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
