"""Milestone 4: S3 (and GitHub) access. The only module in this pipeline
that touches the network. No pricing here either -- fetches raw traj JSON,
nothing else.

Listing the bucket root (no prefix) 403s; a request scoped to a known
prefix (bash-only/<entry>/trajs/) succeeds -- the bucket policy is
prefix-scoped, not fully public. Every call here must pass a prefix.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import requests

from .config import BUCKET_URL

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def list_keys(prefix: str, max_retries: int = 5) -> list[str]:
    """All object keys under `prefix`, paginated via list-type=2 +
    continuation-token. Raises after max_retries failed attempts on any
    single page -- a partial listing is worse than a loud failure, since a
    truncated key list would silently understate n_trajs_found."""
    keys: list[str] = []
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        resp = _get_with_backoff(BUCKET_URL + "/", params=params, max_retries=max_retries)
        root = ET.fromstring(resp.content)
        for contents in root.findall(f"{_S3_NS}Contents"):
            key_el = contents.find(f"{_S3_NS}Key")
            if key_el is not None and key_el.text:
                keys.append(key_el.text)
        truncated = root.findtext(f"{_S3_NS}IsTruncated")
        if truncated != "true":
            break
        token = root.findtext(f"{_S3_NS}NextContinuationToken")
        if not token:
            break
    return keys


def fetch_json(key: str, max_retries: int = 5) -> dict:
    resp = _get_with_backoff(f"{BUCKET_URL}/{key}", max_retries=max_retries)
    return resp.json()


def _get_with_backoff(url: str, params: dict | None = None, max_retries: int = 5) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = RuntimeError(f"S3 {resp.status_code} for {url}")
            else:
                resp.raise_for_status()
        except requests.RequestException as exc:
            last_exc = exc
        time.sleep(min(2**attempt, 30))
    raise RuntimeError(f"S3 request failed after {max_retries} attempts: {url}") from last_exc


# ---------------------------------------------------------------------------
# GitHub (one entry: 20260901_mini-v2.4.2_gemini-3-5-flash, assets.repo)
# ---------------------------------------------------------------------------


class GithubRepoSource:
    """Fetches trajs from a submitter's own GitHub repo via the git trees
    API (to list) and raw.githubusercontent.com (to fetch), for the one
    entry whose assets point at assets.repo instead of S3."""

    def __init__(self, repo: str, ref: str = "main", subdir: str = ""):
        # repo like "org/name", parsed from the metadata.yaml assets.repo URL.
        self.repo = repo
        self.ref = ref
        self.subdir = subdir.strip("/")

    def list_keys(self, max_retries: int = 5) -> list[str]:
        url = f"https://api.github.com/repos/{self.repo}/git/trees/{self.ref}?recursive=1"
        resp = _get_with_backoff(url, max_retries=max_retries)
        data = resp.json()
        paths = [item["path"] for item in data.get("tree", []) if item.get("type") == "blob"]
        if self.subdir:
            paths = [p for p in paths if p.startswith(self.subdir + "/")]
        return paths

    def fetch_json(self, path: str, max_retries: int = 5) -> dict:
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.ref}/{path}"
        resp = _get_with_backoff(url, max_retries=max_retries)
        return resp.json()
