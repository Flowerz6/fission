"""Parse a DiscordChatExporter HTML export → messages JSONL.

Reads the static HTML produced by https://github.com/Tyrrrz/DiscordChatExporter
(the standalone .html "single file" export format) and writes one
JSONL line per textual message.

Output schema (one JSON object per line)::

    {
      "id":         str,    # Discord message id (when present in HTML)
      "author":     str,    # display name as rendered
      "timestamp":  str,    # ISO-8601 from the `data-timestamp` / title attr
      "content":    str,    # raw text content with newlines preserved
      "content_sha256": str # for dedupe — stable across re-exports
    }

Filters applied at parse time:

  * Skip system messages (joins, pins, server bot notices) — their
    container class differs and they carry no usable prompt text.
  * Skip messages whose stripped content is below ``--min-chars``
    (default 16). Discord chat is full of one-word reactions and
    those aren't useful seeds.
  * Dedupe by content_sha256 — copy-paste of the same prompt is
    very common in jailbreak channels and we want one seed per
    unique payload.

This script does NOT call any LLM and does NOT send the content
anywhere. It's a pure structural transform — safe to run locally
without an API key.

Usage::

    poetry run python scripts/discord_to_seeds/parse_discord_html.py \
        --html /path/to/export.html \
        --out  out/discord_seeds/raw_messages.jsonl \
        --min-chars 32

The output JSONL feeds ``classify_prompts.py`` next.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--html", type=Path, required=True, help="Input HTML export.")
    p.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output JSONL path. Parent dirs are created.",
    )
    p.add_argument(
        "--min-chars",
        type=int,
        default=16,
        help="Skip messages whose stripped content is shorter than this (default 16).",
    )
    p.add_argument(
        "--max-chars",
        type=int,
        default=8_000,
        help=(
            "Skip messages whose content is longer than this (default 8000). "
            "Very long pastes are usually multi-prompt dumps that the "
            "classifier handles poorly; split them by hand if you want "
            "to keep them."
        ),
    )
    return p.parse_args(argv)


def _iter_messages(html_path: Path) -> Iterator[dict[str, Any]]:
    """Yield one dict per chatlog message in the export.

    Uses BeautifulSoup with the ``lxml`` parser when available
    (much faster on 90MB files) and falls back to the stdlib
    parser otherwise.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover — env check
        raise SystemExit("BeautifulSoup not installed. Run: poetry add --group dev beautifulsoup4 lxml") from exc

    parser = "lxml"
    try:
        import lxml  # noqa: F401
    except ImportError:
        parser = "html.parser"

    with html_path.open("rb") as fp:
        soup = BeautifulSoup(fp, parser)

    # DiscordChatExporter wraps every message in a div with class
    # ``chatlog__message``. The container's ``data-message-id`` carries
    # the snowflake id when the exporter is in default mode.
    for node in soup.select("div.chatlog__message"):
        # System messages (joins, pins) live in
        # ``chatlog__message--system`` — skip them.
        classes = node.get("class") or []
        if any("system" in c for c in classes):
            continue

        content_el = node.select_one(".chatlog__content")
        if content_el is None:
            continue
        # ``get_text`` preserves newlines if we pass separator='\n'.
        # The exporter renders code blocks as <pre>; the default
        # text extraction keeps their contents intact.
        text = content_el.get_text(separator="\n").strip()
        if not text:
            continue

        author_el = node.select_one(".chatlog__author-name") or node.select_one(".chatlog__author")
        author = author_el.get_text(strip=True) if author_el else ""

        ts_el = node.select_one(".chatlog__timestamp")
        ts_value = ""
        if ts_el is not None:
            # The exporter writes both a human-readable label and an
            # ISO timestamp in the title attr. Title is more reliable.
            ts_value = ts_el.get("title") or ts_el.get_text(strip=True)

        msg_id = node.get("data-message-id") or ""

        yield {
            "id": str(msg_id),
            "author": author,
            "timestamp": ts_value,
            "content": text,
        }


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _filtered(
    messages: Iterator[dict[str, Any]],
    *,
    min_chars: int,
    max_chars: int,
) -> Iterator[dict[str, Any]]:
    """Apply length + dedupe filters. ``seen`` lives across the whole
    iteration so we keep only the first occurrence of any duplicate
    content."""
    seen: set[str] = set()
    for msg in messages:
        content = msg["content"]
        if len(content) < min_chars or len(content) > max_chars:
            continue
        h = _content_hash(content)
        if h in seen:
            continue
        seen.add(h)
        msg["content_sha256"] = h
        yield msg


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if not args.html.exists():
        print(f"error: --html path not found: {args.html}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Track counts ourselves rather than parsing the HTML twice —
    # the 90MB exports take ~30s on lxml, so the second pass is
    # not free. Wrap the source iterator so each yield bumps the
    # totals before filters get a look.
    total = 0

    def _counting_iter() -> Iterator[dict[str, Any]]:
        nonlocal total
        for msg in _iter_messages(args.html):
            total += 1
            yield msg

    kept = 0
    with args.out.open("w", encoding="utf-8") as out_fp:
        for msg in _filtered(
            _counting_iter(),
            min_chars=args.min_chars,
            max_chars=args.max_chars,
        ):
            out_fp.write(json.dumps(msg, ensure_ascii=False) + "\n")
            kept += 1

    print(
        f"parsed {total} messages from {args.html.name}; "
        f"kept {kept} after length+dedupe filters "
        f"(min={args.min_chars}, max={args.max_chars}) → {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
