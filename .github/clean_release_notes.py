"""一次性脚本：把仓库所有 release 正文替换为脱敏版本（不含任何协议细节）。

用法（CI 中）：
    GITHUB_TOKEN=... GITHUB_REPOSITORY=luxiangPS/hdl_link python .github/clean_release_notes.py

读取 .github/release-notes-clean.json，按 tag 逐个 PATCH 正文；标题保持不变。
"""
from __future__ import annotations

import json
import os
import pathlib
import urllib.request

API = "https://api.github.com"
TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
HERE = pathlib.Path(__file__).resolve().parent


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "hdl-link-release-clean",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> None:
    notes = json.loads((HERE / "release-notes-clean.json").read_text(encoding="utf-8"))
    releases = []
    for page in (1, 2, 3):
        items = call("GET", f"/repos/{REPO}/releases?per_page=100&page={page}")
        if not items:
            break
        releases.extend(items)
    by_tag = {r["tag_name"]: r for r in releases}
    print(f"仓库共 {len(releases)} 个 release，待改写 {len(notes)} 个")

    done, missing = [], []
    for tag, body in notes.items():
        rel = by_tag.get(tag)
        if not rel:
            missing.append(tag)
            continue
        call("PATCH", f"/repos/{REPO}/releases/{rel['id']}", {"body": body})
        done.append(tag)
        print(f"已改写 {tag}")

    print("完成:", done)
    if missing:
        print("未找到:", missing)


if __name__ == "__main__":
    main()
