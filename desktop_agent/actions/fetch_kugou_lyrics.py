"""Fetch public KuGou LRC lyrics from fixed, read-only endpoints."""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import urlparse

from desktop_agent.actions import ActionResult, register_action


SONG_SEARCH_URL = "http://mobilecdn.kugou.com/api/v3/search/song"
LYRICS_SEARCH_URL = "http://krcs.kugou.com/search"
LYRICS_DOWNLOAD_URL = "http://lyrics.kugou.com/download"
ALLOWED_DOMAINS = frozenset(
    {
        "mobilecdn.kugou.com",
        "krcs.kugou.com",
        "lyrics.kugou.com",
    }
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


async def handle_fetch_kugou_lyrics(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    del context
    query = str(params.get("query") or "").strip()
    if not query:
        raise ValueError("fetch_kugou_lyrics requires query")
    timeout = max(1.0, float(params.get("timeout_seconds", 15)))
    min_chars = max(1, int(params.get("min_chars", 2)))
    text = await asyncio.wait_for(
        asyncio.to_thread(_fetch_kugou_lyrics, query, timeout),
        timeout=timeout + 2,
    )
    if len(text.strip()) < min_chars:
        raise ValueError("KuGou lyrics response was empty")
    return ActionResult(
        success=True,
        message="KuGou lyrics fetched",
        text=text,
        data={
            "query": query,
            "characters": len(text),
            "allowed_domains": sorted(ALLOWED_DOMAINS),
            "source": "public_http",
        },
    )


def _fetch_kugou_lyrics(query: str, timeout: float) -> str:
    encoded_query = urllib.parse.quote(query, safe="")
    song_payload = _http_json(
        (
            f"{SONG_SEARCH_URL}?format=json&keyword={encoded_query}"
            "&page=1&pagesize=10&showtype=1"
        ),
        timeout,
    )
    song = _select_song(song_payload, query)
    if song is None:
        raise ValueError(f"KuGou song not found: {query}")
    song_hash = str(song.get("hash") or "").strip()
    duration = int(song.get("duration") or 0)
    if not song_hash:
        raise ValueError("KuGou song result has no hash")

    lyric_payload = _http_json(
        (
            f"{LYRICS_SEARCH_URL}?ver=1&man=yes&client=mobi"
            f"&keyword={encoded_query}&duration={duration}"
            f"&hash={urllib.parse.quote(song_hash, safe='')}"
        ),
        timeout,
    )
    lyric_id, access_key = _select_lyric_candidate(lyric_payload)
    download_payload = _http_text(
        (
            f"{LYRICS_DOWNLOAD_URL}?ver=1&client=pc"
            f"&id={urllib.parse.quote(lyric_id, safe='')}"
            f"&accesskey={urllib.parse.quote(access_key, safe='')}"
            "&fmt=lrc&charset=utf8"
        ),
        timeout,
    )
    try:
        envelope = json.loads(download_payload)
    except json.JSONDecodeError as exc:
        raise ValueError("KuGou lyrics response was not JSON") from exc
    encoded_content = str(envelope.get("content") or "").strip()
    if not encoded_content:
        raise ValueError("KuGou lyrics content is empty")
    try:
        raw = base64.b64decode(encoded_content)
    except Exception as exc:
        raise ValueError("KuGou lyrics content is not valid base64") from exc
    charset = str(envelope.get("charset") or "utf-8")
    try:
        return raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def _select_song(payload: dict[str, Any], query: str) -> dict[str, Any] | None:
    data = payload.get("data")
    items = data.get("info") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    normalized = query.casefold()
    for item in items:
        if (
            isinstance(item, dict)
            and str(item.get("songname") or "").strip().casefold()
            == normalized
        ):
            return item
    for item in items:
        if (
            isinstance(item, dict)
            and str(item.get("songname_original") or "").strip().casefold()
            == normalized
        ):
            return item
    return next(
        (item for item in items if isinstance(item, dict)),
        None,
    )


def _select_lyric_candidate(
    payload: dict[str, Any],
) -> tuple[str, str]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("KuGou lyrics candidate not found")
    candidate = next(
        (item for item in candidates if isinstance(item, dict)),
        None,
    )
    if candidate is None:
        raise ValueError("KuGou lyrics candidate not found")
    lyric_id = str(
        candidate.get("id") or candidate.get("download_id") or ""
    ).strip()
    access_key = str(candidate.get("accesskey") or "").strip()
    if not lyric_id or not access_key:
        raise ValueError("KuGou lyrics candidate is incomplete")
    return lyric_id, access_key


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    text = _http_text(url, timeout)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("KuGou endpoint returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("KuGou endpoint returned a non-object response")
    return payload


def _http_text(url: str, timeout: float) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ALLOWED_DOMAINS:
        raise ValueError("KuGou lyrics URL is outside the fixed allowlist")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "NL-RPA/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = urlparse(response.geturl())
        if final_url.hostname not in ALLOWED_DOMAINS:
            raise ValueError("KuGou response redirected outside allowlist")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("KuGou response exceeded size limit")
    return payload.decode("utf-8", errors="replace")


register_action("fetch_kugou_lyrics", handle_fetch_kugou_lyrics)

__all__ = ["handle_fetch_kugou_lyrics"]
