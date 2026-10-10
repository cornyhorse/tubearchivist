"""tests for the read only ES exporter, against a fake ES"""

import io
import json
import urllib.error

import pytest
from storage import es_export


class FakeEs:
    """answers the handful of calls es_export makes"""

    def __init__(self, indexes: dict[str, list[dict]], miscount=None):
        self.indexes = indexes
        self.miscount = miscount or {}
        self.calls = []
        self.open_pits = set()

    def urlopen(self, request, timeout):
        """stand in for urllib.request.urlopen"""
        assert timeout == 60
        assert request.headers["Authorization"].startswith("Basic ")
        method = request.get_method()
        path = request.full_url.removeprefix("http://es:9200/")
        body = json.loads(request.data) if request.data else None
        self.calls.append((method, path))
        return io.BytesIO(json.dumps(self.answer(method, path, body)).encode())

    def answer(self, method, path, body):
        """dispatch on method and path"""
        index = path.split("/")[0]
        if path.endswith("/_count"):
            if index == "ta_boom":
                raise urllib.error.HTTPError(path, 500, "boom", {}, None)
            if index not in self.indexes:
                raise urllib.error.HTTPError(path, 404, "missing", {}, None)
            count = len(self.indexes[index]) + self.miscount.get(index, 0)
            return {"count": count}
        if "/_pit" in path:
            self.open_pits.add(index)
            return {"id": index}
        if path == "_search":
            pit = body["pit"]["id"]
            docs = self.indexes[pit]
            start = body.get("search_after", [0])[0]
            page = docs[start : start + es_export.PAGE_SIZE]
            hits = [
                {"_id": d["id"], "_source": d, "sort": [start + n + 1]}
                for n, d in enumerate(page)
            ]
            return {"pit_id": pit, "hits": {"hits": hits}}
        if method == "DELETE" and path == "_pit":
            self.open_pits.discard(body["id"])
            return {"succeeded": True}
        raise AssertionError(f"unexpected call {method} {path}")


@pytest.fixture(name="fake")
def fixture_fake(monkeypatch):
    """fake ES with a paged index, a small one, the rest missing"""
    fake = FakeEs(
        {
            "ta_video": [{"id": f"v{i}"} for i in range(5)],
            "ta_config": [{"id": "appsettings"}],
        }
    )
    monkeypatch.setattr(es_export, "PAGE_SIZE", 2)
    monkeypatch.setattr(es_export.urllib.request, "urlopen", fake.urlopen)
    return fake


def reader():
    """reader pointed at the fake"""
    return es_export.EsReader("http://es:9200/", "elastic", "pw")


def test_export_pages_and_manifest(fake, tmp_path):
    counts = es_export.export(reader(), tmp_path)

    assert counts["ta_video"] == 5
    assert counts["ta_subtitle"] == 0
    lines = (tmp_path / "ta_video.ndjson").read_text().splitlines()
    ids = [json.loads(line)["_id"] for line in lines]
    assert ids == [f"v{i}" for i in range(5)]
    assert json.loads((tmp_path / "manifest.json").read_text()) == counts
    assert not fake.open_pits
    assert all(m in ("GET", "POST", "DELETE") for m, _ in fake.calls)


def test_count_mismatch_fails(fake, tmp_path):
    fake.miscount = {"ta_video": 1}

    with pytest.raises(RuntimeError, match="exported 5, count 6"):
        es_export.export(reader(), tmp_path)
    assert not (tmp_path / "ta_video.ndjson").exists()
    assert not (tmp_path / "ta_video.ndjson.tmp").exists()
    assert not fake.open_pits


def test_other_http_errors_raise(fake):
    with pytest.raises(urllib.error.HTTPError):
        reader().exists("ta_boom")
    assert fake.calls == [("GET", "ta_boom/_count")]


def test_main(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ES_URL", "http://es:9200")
    monkeypatch.setenv("ELASTIC_PASSWORD", "pw")
    monkeypatch.setattr(es_export.sys, "argv", ["es_export", str(tmp_path)])

    assert es_export.main() == 0
    assert "ta_video: 5" in capsys.readouterr().out


def test_main_usage(monkeypatch, capsys):
    monkeypatch.setattr(es_export.sys, "argv", ["es_export"])

    assert es_export.main() == 2
    assert "usage" in capsys.readouterr().out
