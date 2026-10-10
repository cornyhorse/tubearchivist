"""tests for the yt-dlp wrapper and the cookie handler"""

from http import cookiejar

import pytest
import yt_dlp
from appsettings.src.config import AppConfig
from common.src.ta_redis import RedisArchivist
from download.src.yt_dlp_base import CookieHandler, YtWrap

COOKIE = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tA\tB\n"


class FakeYDL:
    """yt_dlp.YoutubeDL stand in, behaviour set on the class"""

    error: Exception | None = None
    response: dict | None = None
    write_cookie: str | None = None
    instances: list = []

    def __init__(self, obs):
        self.obs = obs
        self.calls = []
        FakeYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def _run(self, call):
        self.calls.append(call)
        if self.write_cookie is not None:
            cookie_io = self.obs["cookiefile"]
            cookie_io.seek(0)
            cookie_io.truncate()
            cookie_io.write(self.write_cookie)
        if self.error:
            raise self.error

    def download(self, urls):
        """fake download"""
        self._run(("download", urls))

    def extract_info(self, url):
        """fake extract"""
        self._run(("extract", url))
        return self.response


@pytest.fixture(name="ydl")
def fixture_ydl(monkeypatch):
    """replace YoutubeDL, reset behaviour"""
    FakeYDL.error = None
    FakeYDL.response = None
    FakeYDL.write_cookie = None
    FakeYDL.instances = []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


def config(cookie_import=False, pot_provider_url=None):
    """config with the downloads keys YtWrap reads"""
    return {
        "downloads": {
            "cookie_import": cookie_import,
            "pot_provider_url": pot_provider_url,
            "sleep_interval": 0,
        }
    }


def test_build_obs_without_config():
    """request merged into base, no cookie"""
    obs = YtWrap({"skip_download": True, "quiet": False}).obs
    assert obs["skip_download"] is True
    assert obs["quiet"] is False
    assert "cookiefile" not in obs
    assert "extractor_args" not in obs


def test_build_obs_debug(settings):
    """debug drops quiet"""
    settings.DEBUG = True
    assert "quiet" not in YtWrap({}).obs


def test_build_obs_cookie_and_potoken(redis_ns):
    """imported cookie and pot provider url"""
    RedisArchivist().set_message("cookie", COOKIE)
    obs = YtWrap({}, config(True, "http://pot:4416")).obs

    assert obs["cookiefile"].getvalue() == COOKIE
    assert obs["extractor_args"]["youtubepot-bgutilhttp"] == {
        "base_url": ["http://pot:4416"]
    }


def test_build_obs_temp_cookie_no_potoken(redis_ns):
    """temp cookie, empty when not stored; pot provider disabled"""
    obs = YtWrap({}, config()).obs

    assert obs["cookiefile"].getvalue() == CookieHandler.COOKIE_EMPTY
    assert obs["extractor_args"]["youtubepot-bgutilhttp"] == {
        "disable": ["True"]
    }


def test_download_ok(ydl):
    """checks selected formats, no cookie to validate"""
    assert YtWrap({}).download("abc") == (True, True)
    assert ydl.instances[0].obs["check_formats"] == "selected"
    assert ydl.instances[0].calls == [("download", ["abc"])]


def test_download_error(ydl):
    """error message returned"""
    ydl.error = yt_dlp.utils.DownloadError("Video unavailable")
    assert YtWrap({}).download("abc") == (False, "Video unavailable")


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Temporary failure in name resolution", "lost the internet"),
        ("Sign in to confirm you're not a bot", "bot detection"),
    ],
)
@pytest.mark.parametrize("with_config", [True, False])
def test_download_abort(ydl, redis_ns, message, expected, with_config):
    """no internet and bot detection abort the run"""
    ydl.error = yt_dlp.utils.DownloadError(message)
    wrap = YtWrap({}, config() if with_config else False)
    with pytest.raises(ConnectionError, match=expected):
        wrap.download("abc")


@pytest.mark.parametrize(
    "error, expected",
    [
        (cookiejar.LoadError("bad cookie"), (None, "bad cookie")),
        (yt_dlp.utils.ExtractorError("gone"), (None, "gone")),
        (
            yt_dlp.utils.DownloadError("This channel does not have a shorts"),
            (None, None),
        ),
        (yt_dlp.utils.DownloadError("private"), (None, "private")),
    ],
)
def test_extract_errors(ydl, error, expected):
    """errors return None and the message"""
    ydl.error = error
    response, message = YtWrap({}).extract("abc")
    assert response is None
    if expected[1] is None:
        assert message is None
    else:
        assert expected[1] in message


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Temporary failure in name resolution", "lost the internet"),
        ("Sign in to confirm you're not a bot", "bot detection"),
    ],
)
@pytest.mark.parametrize("with_config", [True, False])
def test_extract_abort(ydl, redis_ns, message, expected, with_config):
    """no internet and bot detection abort the run"""
    ydl.error = yt_dlp.utils.DownloadError(message)
    wrap = YtWrap({}, config() if with_config else False)
    with pytest.raises(ConnectionError, match=expected):
        wrap.extract("abc")


def test_extract_ok_refreshes_imported_cookie(ydl, redis_ns):
    """changed cookie is written back without expiry"""
    RedisArchivist().set_message("cookie", COOKIE)
    ydl.response = {"id": "abc"}
    ydl.write_cookie = COOKIE + "refreshed\n"

    assert YtWrap({}, config(True)).extract("abc") == ({"id": "abc"}, None)
    assert RedisArchivist().get_message_str("cookie") == (
        COOKIE + "refreshed\n"
    )
    assert redis_ns.ttl(f"{RedisArchivist.NAME_SPACE}cookie") == -1


def test_download_ok_refreshes_temp_cookie(ydl, redis_ns):
    """temp cookie is stored with expiry"""
    ydl.response = {"id": "abc"}
    ydl.write_cookie = COOKIE

    YtWrap({}, config()).download("abc")
    assert RedisArchivist().get_message_str("cookie_temp") == COOKIE
    assert redis_ns.ttl(f"{RedisArchivist.NAME_SPACE}cookie_temp") > 0


def test_extract_ok_unchanged_cookie(ydl, redis_ns, monkeypatch):
    """same or empty cookie is not written"""
    RedisArchivist().set_message("cookie", COOKIE)
    ydl.response = {"id": "abc"}
    writes = []
    monkeypatch.setattr(
        RedisArchivist, "set_message", lambda self, *a, **k: writes.append(a)
    )

    YtWrap({}, config(True)).extract("abc")
    ydl.write_cookie = "\x00"
    YtWrap({}, config(True)).extract("abc")
    assert not writes


def test_cookie_set_and_revoke(app_config, redis_ns):
    """set activates import in config, revoke clears it"""
    handler = CookieHandler(config())
    handler.set_cookie(COOKIE + "\x00")

    assert RedisArchivist().get_message_str("cookie") == COOKIE
    assert handler.config["downloads"]["cookie_import"] is True
    assert AppConfig().config["downloads"]["cookie_import"] is True

    RedisArchivist().set_message("cookie:valid", {"status": True})
    CookieHandler.revoke()
    assert RedisArchivist().get_message_str("cookie") is None
    assert RedisArchivist().get_message_str("cookie:valid") is None
    assert AppConfig().config["downloads"]["cookie_import"] is False


def test_cookie_validate_cached(redis_ns, ydl):
    """cached validation, no request"""
    CookieHandler.store_validation(True)
    assert CookieHandler(config(True)).validate() is True
    assert not ydl.instances


def test_cookie_validate_success(redis_ns, ydl):
    """liked videos extracted, cookie stored back"""
    RedisArchivist().set_message("cookie", COOKIE)
    ydl.response = {"id": "LL"}

    assert CookieHandler(config(True)).validate() is True
    assert ydl.instances[0].calls == [("extract", "LL")]
    assert RedisArchivist().get_message_dict("cookie:valid")["status"]
    assert RedisArchivist().get_message_str("cookie") == COOKIE


def test_cookie_validate_failure(redis_ns, ydl):
    """failed validation notifies, empty cookie not stored back"""
    ydl.error = yt_dlp.utils.DownloadError("login required")
    ydl.write_cookie = "\x00"

    assert CookieHandler(config(True)).validate() is False
    assert RedisArchivist().get_message_dict("cookie:valid")["status"] is False
    message = RedisArchivist().get_message_dict("message:download")
    assert message["title"] == "Cookie validation failed, exiting..."
    assert RedisArchivist().get_message_str("cookie") is None

