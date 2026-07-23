"""Update version comparison and check behavior."""
import updater
from core import VERSION


def test_parse_handles_prefixes():
    assert updater._parse("v11.0.0") == (11, 0, 0)
    assert updater._parse("11.2.3") == (11, 2, 3)
    assert updater._parse("LegalMind 12.0.1") == (12, 0, 1)
    assert updater._parse("nope") is None


def test_is_newer():
    assert updater.is_newer("11.0.1", "11.0.0")
    assert updater.is_newer("v12.0.0", "11.9.9")
    assert not updater.is_newer("11.0.0", "11.0.0")
    assert not updater.is_newer("10.9.9", "11.0.0")
    assert not updater.is_newer("garbage", "11.0.0")


def test_current_version_is_valid_semver():
    assert updater._parse(VERSION) is not None, f"VERSION {VERSION!r} not semver"


def test_check_is_none_on_network_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    assert updater.check_for_update(timeout=1) is None


def test_check_reports_newer_release(monkeypatch):
    import io, json

    class FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    payload = {"tag_name": "v99.0.0", "body": "Big new release",
               "html_url": "https://example/releases/99",
               "assets": [{"name": "LegalMind-Setup.zip",
                           "browser_download_url": "https://example/dl.zip"}]}
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: FakeResp(json.dumps(payload).encode()))
    upd = updater.check_for_update(timeout=1)
    assert upd and upd["version"] == "99.0.0"
    assert upd["asset_name"] == "LegalMind-Setup.zip"


def test_check_none_when_up_to_date(monkeypatch):
    import io, json

    class FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    payload = {"tag_name": f"v{VERSION}", "assets": []}
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: FakeResp(json.dumps(payload).encode()))
    assert updater.check_for_update(timeout=1) is None
