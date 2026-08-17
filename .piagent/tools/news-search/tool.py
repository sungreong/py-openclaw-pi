from __future__ import annotations

import json
import hashlib
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from xml.etree import ElementTree

from langchain.tools import tool


_LOCALES = {
    "en": ("en-US", "US", "US:en"),
    "ko": ("ko", "KR", "KR:ko"),
    "vi": ("vi", "VN", "VN:vi"),
}
_PROVIDERS = {"auto", "google", "bing"}
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_URL_LENGTH = 2_048
_MAX_EVIDENCE_ROWS = 500
_SEARCH_EVIDENCE: dict[str, dict[str, Any]] = {}


def _clean_text(value: str, limit: int = 600) -> str:
    text = re.sub(r"(?is)<[^>]+>", " ", unescape(str(value or "")))
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(1, int(limit))]


def _published_iso(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return raw


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_http_url(value: str) -> str:
    url = str(value or "").strip()
    if not url or len(url) > _MAX_URL_LENGTH:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def _canonical_result_url(value: str) -> tuple[str, str]:
    provider_url = _safe_http_url(value)
    if not provider_url:
        return "", ""
    parsed = urllib.parse.urlsplit(provider_url)
    host = (parsed.hostname or "").lower()
    if host in {"bing.com", "www.bing.com"} and parsed.path.lower().endswith("/news/apiclick.aspx"):
        target = urllib.parse.parse_qs(parsed.query).get("url", [""])[0]
        canonical = _safe_http_url(target)
        if canonical:
            return canonical, provider_url
    return provider_url, ""


def _parse_published(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_recent(value: str, *, now: datetime, days: int) -> bool:
    published = _parse_published(value)
    if published is None:
        return False
    return now - timedelta(days=days) <= published <= now + timedelta(minutes=5)


def _query_terms(query: str) -> set[str]:
    stopwords = {
        "and", "the", "for", "from", "with", "month", "news", "recent",
        "và", "của", "tháng", "năm", "tin", "mới",
        "년", "월", "최근", "뉴스", "관련",
    }
    terms = {
        token.lower()
        for token in re.findall(r"[^\W_]+", query, flags=re.UNICODE)
        if len(token) >= 2 and not token.isdigit()
    }
    return {term for term in terms if term not in stopwords and not re.fullmatch(r"\d{4}(?:년)?", term)}


def _add_evidence_metadata(row: dict[str, Any], query: str) -> dict[str, Any] | None:
    canonical_url, provider_url = _canonical_result_url(str(row.get("url", "")))
    if not canonical_url:
        return None
    normalized = dict(row)
    normalized["url"] = canonical_url
    if provider_url:
        normalized["provider_url"] = provider_url
    searchable = f"{normalized.get('title', '')} {normalized.get('snippet', '')}".lower()
    matches = sorted(term for term in _query_terms(query) if term in searchable)
    normalized["query_match_terms"] = matches
    normalized["query_match_score"] = len(matches)
    fingerprint = "\n".join(
        str(normalized.get(key, ""))
        for key in ("title", "publisher", "published_at", "url")
    )
    normalized["evidence_id"] = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return normalized


def _remember_evidence(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        evidence_id = str(row.get("evidence_id", ""))
        if evidence_id:
            _SEARCH_EVIDENCE[evidence_id] = dict(row)
    while len(_SEARCH_EVIDENCE) > _MAX_EVIDENCE_ROWS:
        _SEARCH_EVIDENCE.pop(next(iter(_SEARCH_EVIDENCE)))


def _child_text(item: ElementTree.Element, name: str) -> str:
    wanted = name.lower()
    for child in item:
        tag = str(child.tag).rsplit("}", 1)[-1].lower()
        if tag == wanted:
            return str(child.text or "").strip()
    return ""


def _parse_rss(payload: bytes, provider: str) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(payload)
    rows: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = _clean_text(_child_text(item, "title"), 300)
        url = _child_text(item, "link")
        if not title or not url:
            continue
        rows.append(
            {
                "title": title,
                "publisher": _clean_text(_child_text(item, "source"), 120),
                "published_at": _published_iso(_child_text(item, "pubDate")),
                "url": url,
                "snippet": _clean_text(_child_text(item, "description")),
                "provider": provider,
                "verification": "snippet-only",
            }
        )
    return rows


def _fetch_rss(url: str, timeout_s: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "PiAgent/1.0 (+workspace-tool:news-search)"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=max(1, int(timeout_s))) as response:
        payload = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ValueError("news RSS response exceeded the 2 MB safety limit")
    return payload


def _google_url(query: str, days: int, language: str) -> str:
    locale, country, ceid = _LOCALES[language]
    params = {
        "q": f"{query} when:{days}d",
        "hl": locale,
        "gl": country,
        "ceid": ceid,
    }
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


def _bing_url(query: str, language: str) -> str:
    locale, country, _ceid = _LOCALES[language]
    params = {
        "q": query,
        "format": "rss",
        "setlang": locale,
        "cc": country,
    }
    return "https://www.bing.com/news/search?" + urllib.parse.urlencode(params)


def _deduplicate(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = re.sub(r"\W+", "", str(row.get("title", "")).lower())
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


@tool("news_search")
def news_search(
    query: str,
    days: int = 30,
    limit: int = 10,
    language: str = "en",
    provider: str = "auto",
) -> str:
    """Search recent Google News/Bing News RSS results with dates, publishers, URLs, and snippets."""
    clean_query = re.sub(r"[\x00-\x1f\x7f]", " ", str(query or ""))
    clean_query = re.sub(r"\s+", " ", clean_query).strip()[:400]
    language_key = str(language or "en").strip().lower()
    provider_key = str(provider or "auto").strip().lower()
    if not clean_query:
        return json.dumps({"status": "error", "error": "query is required"})
    if language_key not in _LOCALES:
        return json.dumps({"status": "error", "error": "language must be en, ko, or vi"})
    if provider_key not in _PROVIDERS:
        return json.dumps({"status": "error", "error": "provider must be auto, google, or bing"})

    bounded_days = max(1, min(365, int(days)))
    bounded_limit = max(1, min(20, int(limit)))
    provider_urls: list[tuple[str, str]] = []
    if provider_key in {"auto", "google"}:
        provider_urls.append(("google", _google_url(clean_query, bounded_days, language_key)))
    if provider_key in {"auto", "bing"}:
        provider_urls.append(("bing", _bing_url(clean_query, language_key)))

    rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for name, url in provider_urls:
        try:
            rows.extend(_parse_rss(_fetch_rss(url), name))
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {str(exc)[:300]}"

    retrieved_at = _utcnow()
    normalized_rows = [
        normalized
        for row in rows
        if _is_recent(str(row.get("published_at", "")), now=retrieved_at, days=bounded_days)
        if (normalized := _add_evidence_metadata(row, clean_query)) is not None
    ]
    normalized_rows.sort(key=lambda row: int(row.get("query_match_score", 0)), reverse=True)
    selected = _deduplicate(normalized_rows, bounded_limit)
    _remember_evidence(selected)
    status = "ok" if selected else ("error" if errors and len(errors) == len(provider_urls) else "no_results")
    return json.dumps(
        {
            "status": status,
            "query": clean_query,
            "days": bounded_days,
            "language": language_key,
            "providers_requested": [name for name, _url in provider_urls],
            "provider_errors": errors,
            "result_count": len(selected),
            "retrieved_at_utc": retrieved_at.isoformat(),
            "results": selected,
        },
        ensure_ascii=False,
    )


@tool("news_evidence_validate")
def news_evidence_validate(evidence_json: str) -> str:
    """Validate report evidence IDs and URLs against results returned by news_search in this process."""
    try:
        requested = json.loads(str(evidence_json or ""))
    except json.JSONDecodeError as exc:
        return json.dumps({"status": "invalid", "error": f"invalid JSON: {exc.msg}"})
    if not isinstance(requested, list) or not requested:
        return json.dumps({"status": "invalid", "error": "evidence_json must be a non-empty JSON array"})

    validated: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, item in enumerate(requested):
        if not isinstance(item, dict):
            errors.append({"index": index, "error": "row must be an object"})
            continue
        evidence_id = str(item.get("evidence_id", "")).strip()
        source = _SEARCH_EVIDENCE.get(evidence_id)
        if source is None:
            errors.append({"index": index, "evidence_id": evidence_id, "error": "unknown evidence_id"})
            continue
        provided_url = str(item.get("url", "")).strip()
        if provided_url != str(source.get("url", "")):
            errors.append({"index": index, "evidence_id": evidence_id, "error": "URL does not exactly match search evidence"})
            continue
        validated.append(dict(source))

    return json.dumps(
        {
            "status": "valid" if not errors and len(validated) == len(requested) else "invalid",
            "requested_count": len(requested),
            "validated_count": len(validated),
            "errors": errors,
            "validated_results": validated,
        },
        ensure_ascii=False,
    )


@tool("news_research_bundle")
def news_research_bundle(
    query_en: str,
    query_vi: str,
    query_ko: str,
    days: int = 30,
    limit_per_query: int = 6,
    candidate_limit: int = 12,
) -> str:
    """Run English, Vietnamese, and Korean dated news searches and return one compact evidence candidate set."""
    queries = [("en", query_en), ("vi", query_vi), ("ko", query_ko)]
    searches: list[dict[str, Any]] = []
    combined: list[dict[str, Any]] = []
    for language, query in queries:
        payload = json.loads(
            news_search.invoke(
                {
                    "query": query,
                    "days": days,
                    "limit": max(1, min(10, int(limit_per_query))),
                    "language": language,
                    "provider": "auto",
                }
            )
        )
        searches.append(
            {
                "language": language,
                "query": payload.get("query", ""),
                "status": payload.get("status", "error"),
                "result_count": payload.get("result_count", 0),
                "provider_errors": payload.get("provider_errors", {}),
            }
        )
        combined.extend(payload.get("results", []))

    unique = _deduplicate(combined, len(combined) or 1)
    unique.sort(
        key=lambda row: (
            0 if urllib.parse.urlsplit(str(row.get("url", ""))).hostname == "news.google.com" else 1,
            int(row.get("query_match_score", 0)),
            str(row.get("published_at", "")),
        ),
        reverse=True,
    )
    selected = unique[: max(1, min(30, int(candidate_limit)))]
    publishers = sorted({str(row.get("publisher", "")).strip() for row in selected if str(row.get("publisher", "")).strip()})
    status = "ok" if selected else "no_results"
    if all(search.get("status") == "error" for search in searches):
        status = "error"
    return json.dumps(
        {
            "status": status,
            "searches_completed": len(searches),
            "searches": searches,
            "raw_result_count": sum(int(search.get("result_count", 0)) for search in searches),
            "candidate_count": len(selected),
            "independent_publisher_count": len(publishers),
            "publishers": publishers,
            "candidates": selected,
        },
        ensure_ascii=False,
    )


TOOLS = [news_search, news_research_bundle, news_evidence_validate]
