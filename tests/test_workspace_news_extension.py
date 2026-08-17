from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / ".piagent" / "tools" / "news-search" / "tool.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("piagent_news_search_test", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Vietnam and Korea expand cooperation</title>
    <link>https://example.test/story</link>
    <pubDate>Fri, 14 Aug 2026 09:30:00 GMT</pubDate>
    <description><![CDATA[<b>Verified snippet</b> about cooperation.]]></description>
    <source url="https://example.test">Example News</source>
  </item>
</channel></rss>"""


def test_news_search_builds_dated_multilingual_google_url():
    module = _load_module()

    url = module._google_url("Han Quoc Viet Nam", 30, "vi")

    assert "news.google.com/rss/search" in url
    assert "when%3A30d" in url
    assert "hl=vi" in url
    assert "gl=VN" in url
    assert "ceid=VN%3Avi" in url


def test_news_search_merges_providers_and_deduplicates(monkeypatch):
    module = _load_module()
    calls: list[str] = []

    def fake_fetch(url: str, timeout_s: int = 20) -> bytes:
        calls.append(url)
        return RSS

    monkeypatch.setattr(module, "_fetch_rss", fake_fetch)
    monkeypatch.setattr(module, "_utcnow", lambda: datetime(2026, 8, 15, tzinfo=timezone.utc))
    payload = json.loads(
        module.news_search.invoke(
            {"query": "Vietnam Korea", "days": 31, "limit": 10, "language": "en", "provider": "auto"}
        )
    )

    assert payload["status"] == "ok"
    assert payload["result_count"] == 1
    assert payload["providers_requested"] == ["google", "bing"]
    assert payload["results"][0]["publisher"] == "Example News"
    assert payload["results"][0]["published_at"] == "2026-08-14T09:30:00+00:00"
    assert payload["results"][0]["verification"] == "snippet-only"
    assert payload["results"][0]["evidence_id"]
    assert len(calls) == 2


def test_news_search_fails_closed_when_all_providers_fail(monkeypatch):
    module = _load_module()

    def fail_fetch(url: str, timeout_s: int = 20) -> bytes:
        raise TimeoutError("offline")

    monkeypatch.setattr(module, "_fetch_rss", fail_fetch)
    payload = json.loads(module.news_search.invoke({"query": "Vietnam Korea"}))

    assert payload["status"] == "error"
    assert payload["result_count"] == 0
    assert set(payload["provider_errors"]) == {"google", "bing"}


def test_news_search_validates_language_and_provider():
    module = _load_module()

    bad_language = json.loads(module.news_search.invoke({"query": "x", "language": "fr"}))
    bad_provider = json.loads(module.news_search.invoke({"query": "x", "provider": "other"}))

    assert bad_language == {"status": "error", "error": "language must be en, ko, or vi"}
    assert bad_provider == {"status": "error", "error": "provider must be auto, google, or bing"}


def test_news_search_extracts_bing_target_and_filters_out_of_range(monkeypatch):
    module = _load_module()
    rss = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Vietnam Korea current cooperation</title>
        <link>https://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fexample.test%2Fcurrent</link>
        <pubDate>Fri, 14 Aug 2026 09:30:00 GMT</pubDate><source>Current News</source></item>
      <item><title>Vietnam Korea old cooperation</title><link>https://example.test/old</link>
        <pubDate>Mon, 01 Jun 2026 09:30:00 GMT</pubDate><source>Old News</source></item>
    </channel></rss>"""
    monkeypatch.setattr(module, "_fetch_rss", lambda *_args, **_kwargs: rss)
    monkeypatch.setattr(module, "_utcnow", lambda: datetime(2026, 8, 15, tzinfo=timezone.utc))

    payload = json.loads(module.news_search.invoke({"query": "Vietnam Korea", "days": 30, "provider": "bing"}))

    assert payload["result_count"] == 1
    assert payload["results"][0]["url"] == "https://example.test/current"
    assert payload["results"][0]["provider_url"].startswith("https://www.bing.com/")


def test_news_evidence_validate_rejects_rewritten_url(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "_fetch_rss", lambda *_args, **_kwargs: RSS)
    monkeypatch.setattr(module, "_utcnow", lambda: datetime(2026, 8, 15, tzinfo=timezone.utc))
    search = json.loads(module.news_search.invoke({"query": "Vietnam Korea", "provider": "google"}))
    row = search["results"][0]

    valid = json.loads(module.news_evidence_validate.invoke({"evidence_json": json.dumps([
        {"evidence_id": row["evidence_id"], "url": row["url"]}
    ])}))
    invalid = json.loads(module.news_evidence_validate.invoke({"evidence_json": json.dumps([
        {"evidence_id": row["evidence_id"], "url": "https://example.test/invented"}
    ])}))

    assert valid["status"] == "valid"
    assert valid["validated_count"] == 1
    assert invalid["status"] == "invalid"
    assert invalid["errors"][0]["error"] == "URL does not exactly match search evidence"


def test_news_search_rejects_unsafe_or_oversized_urls():
    module = _load_module()

    assert module._canonical_result_url("javascript:alert(1)") == ("", "")
    assert module._canonical_result_url("https://example.test/" + "x" * 2100) == ("", "")


def test_news_research_bundle_runs_three_language_searches(monkeypatch):
    module = _load_module()
    calls: list[str] = []

    def fake_fetch(url: str, timeout_s: int = 20) -> bytes:
        calls.append(url)
        return RSS

    monkeypatch.setattr(module, "_fetch_rss", fake_fetch)
    monkeypatch.setattr(module, "_utcnow", lambda: datetime(2026, 8, 15, tzinfo=timezone.utc))
    payload = json.loads(
        module.news_research_bundle.invoke(
            {
                "query_en": "Vietnam Korea cooperation",
                "query_vi": "Việt Nam Hàn Quốc hợp tác",
                "query_ko": "베트남 한국 협력",
            }
        )
    )

    assert payload["status"] == "ok"
    assert payload["searches_completed"] == 3
    assert [row["language"] for row in payload["searches"]] == ["en", "vi", "ko"]
    assert payload["candidate_count"] == 1
    assert len(calls) == 6
