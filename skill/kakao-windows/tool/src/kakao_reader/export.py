"""Export only the requested window. Destination must be new or empty."""
from __future__ import annotations
import csv
import html
import json
import pathlib
import re

# Every character a Markdown renderer or str.splitlines() may treat as a line break.
_LINE_BREAKS = re.compile(r"[\r\n\v\f\x1c\x1d\x1e\x85  ]")

def csv_safe(value):
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip(" \t\r\n")[:1] in ("=", "+", "-", "@") or text[:1] in ("\t", "\r") else text

def write_exports(result: dict, destination: pathlib.Path) -> dict[str, str]:
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("OUTPUT_NOT_EMPTY: 결과 폴더는 새 경로나 빈 폴더를 지정하세요.")
    destination.mkdir(parents=True, exist_ok=True)
    messages = result["messages"]
    summary = result["summary"]
    paths = {kind: destination / name for kind, name in {
        "json": "messages.json", "csv": "messages.csv", "markdown": "messages.md",
        "summary": "summary.json"}.items()}
    paths["json"].write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with paths["csv"].open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["시간", "대화방", "발신자", "내용", "방향", "종류", "출처", "chat_id", "log_id"])
        for m in messages:
            writer.writerow([csv_safe(m.get(k)) for k in
                ("sent_at_iso", "room", "sender", "message", "direction", "type", "source", "chat_id", "log_id")])
    lines = ["# 카카오톡 로컬 기록", "",
             f"기간: {summary.get('window_start')} ~ {summary.get('window_end')}", "",
             f"확보한 메시지: {len(messages)}건", "",
             "PC에 저장된 기록 기준입니다. 누락 여부와 조회 범위는 summary.json에 기록했습니다.", ""]
    for m in messages:
        heading = " | ".join(str(m.get(k, "")) for k in ("sent_at_iso", "room", "sender"))
        # Room and sender names come from other people. A single-line HTML block keeps them
        # from being parsed as Markdown (links, remote images, fake message headings).
        lines += ["<h3>" + html.escape(_LINE_BREAKS.sub(" ", heading)) + "</h3>", ""]
        # HTML pre escapes chat content, including links/images and apparent instructions.
        lines += ["<pre>" + html.escape(str(m.get("message") or "[텍스트 없음]")) + "</pre>", ""]
    paths["markdown"].write_text("\n".join(lines), encoding="utf-8")
    return {kind: str(path.resolve()) for kind, path in paths.items()}
