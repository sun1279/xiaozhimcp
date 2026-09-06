"""Minimal YouTube Data API v3 video-search demo.

Usage:
    set YOUTUBE_API_KEY=your_api_key
    python youtube_search_demo.py "python tutorial"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SEARCH_API_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEO_DETAILS_API_URL = "https://www.googleapis.com/youtube/v3/videos"
DEFAULT_TIMEOUT_SECONDS = 15
ROOT_DIR = Path(__file__).resolve().parent
LOCAL_ENV_FILE = ROOT_DIR / ".env.local"
DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


class YouTubeApiError(RuntimeError):
    """Raised when the YouTube API returns an error or invalid data."""


def load_api_key() -> str | None:
    """Load the API key from the environment or a local .env file."""
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if api_key:
        return api_key
    return _load_key_from_env_file(LOCAL_ENV_FILE)


def search_videos(
    query: str,
    api_key: str,
    max_results: int = 5,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Search public YouTube videos and return the API's item objects."""
    if not query.strip():
        raise ValueError("搜索关键词不能为空。")
    if not 1 <= max_results <= 50:
        raise ValueError("max_results 必须介于 1 和 50 之间。")

    payload = _get_json(
        SEARCH_API_URL,
        {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": max_results,
            "order": "relevance",
            "key": api_key,
        },
        timeout,
    )

    items = payload.get("items", [])
    if not isinstance(items, list):
        raise YouTubeApiError("YouTube API 返回的 items 字段格式不正确。")

    video_ids = [
        item.get("id", {}).get("videoId")
        for item in items
        if isinstance(item, dict) and item.get("id", {}).get("videoId")
    ]
    durations = fetch_video_durations(video_ids, api_key, timeout)
    for item in items:
        if not isinstance(item, dict):
            continue
        video_id = item.get("id", {}).get("videoId")
        item["duration"] = durations.get(video_id, "未知")
    return items


def fetch_video_durations(
    video_ids: list[str],
    api_key: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Return formatted durations keyed by video id."""
    if not video_ids:
        return {}

    payload = _get_json(
        VIDEO_DETAILS_API_URL,
        {
            "part": "contentDetails",
            "id": ",".join(video_ids),
            "key": api_key,
        },
        timeout,
    )
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise YouTubeApiError("YouTube API 返回的视频详情 items 字段格式不正确。")

    durations: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        video_id = item.get("id")
        iso_duration = item.get("contentDetails", {}).get("duration")
        if isinstance(video_id, str) and isinstance(iso_duration, str):
            durations[video_id] = _format_duration(iso_duration)
    return durations


def _get_json(url: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    query = urlencode(params)
    request = Request(
        f"{url}?{query}",
        headers={"Accept": "application/json", "User-Agent": "youtube-search-demo/1.0"},
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as exc:
        detail = _read_api_error(exc)
        raise YouTubeApiError(f"YouTube API 请求失败（HTTP {exc.code}）：{detail}") from exc
    except URLError as exc:
        raise YouTubeApiError(f"网络请求失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise YouTubeApiError(f"请求超时（超过 {timeout} 秒）。") from exc
    except json.JSONDecodeError as exc:
        raise YouTubeApiError("YouTube API 返回的不是有效 JSON。") from exc

    if not isinstance(payload, dict):
        raise YouTubeApiError("YouTube API 返回了无法识别的数据格式。")
    if "error" in payload:
        raise YouTubeApiError(_format_api_error(payload["error"]))
    return payload


def _format_duration(iso_duration: str) -> str:
    match = DURATION_RE.match(iso_duration)
    if not match:
        return iso_duration

    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    total_seconds = days * 24 * 60 * 60 + hours * 60 * 60 + minutes * 60 + seconds

    display_hours, remainder = divmod(total_seconds, 60 * 60)
    display_minutes, display_seconds = divmod(remainder, 60)
    if display_hours:
        return f"{display_hours}:{display_minutes:02d}:{display_seconds:02d}"
    return f"{display_minutes}:{display_seconds:02d}"


def _read_api_error(error: HTTPError) -> str:
    """Extract a useful message from an HTTP error response when possible."""
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return str(error.reason or "未知错误")
    return _format_api_error(payload.get("error", payload))


def _format_api_error(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
        errors = error.get("errors")
        if isinstance(errors, list) and errors:
            first_error = errors[0]
            if isinstance(first_error, dict) and first_error.get("reason"):
                return str(first_error["reason"])
    return "未知 API 错误"


def _print_results(items: list[dict[str, Any]]) -> None:
    if not items:
        print("没有找到匹配的视频。")
        return

    for index, item in enumerate(items, start=1):
        snippet = item.get("snippet", {})
        video_id = item.get("id", {}).get("videoId")
        title = snippet.get("title", "无标题")
        channel = snippet.get("channelTitle", "未知频道")
        published_at = snippet.get("publishedAt", "未知时间")
        description = " ".join(str(snippet.get("description", "")).split())
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else "无视频链接"

        print(f"{index}. {title}")
        print(f"   频道：{channel}")
        print(f"   发布时间：{published_at}")
        print(f"   时长：{item.get('duration', '未知')}")
        print(f"   简介：{description or '无'}")
        print(f"   链接：{url}")
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="搜索 YouTube 视频（YouTube Data API v3）")
    parser.add_argument("query", help="搜索关键词，例如：Python 教程")
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="返回数量，范围 1-50，默认 5",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    api_key = load_api_key()
    if not api_key:
        print(
            "错误：未设置 YOUTUBE_API_KEY 环境变量。\n"
            "Windows PowerShell 示例：$env:YOUTUBE_API_KEY = '你的 API key'",
            file=sys.stderr,
        )
        return 2

    try:
        items = search_videos(args.query, api_key, args.max_results)
    except (ValueError, YouTubeApiError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    _print_results(items)
    return 0


def _load_key_from_env_file(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == "YOUTUBE_API_KEY":
                return value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
