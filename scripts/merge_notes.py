"""Check section notes against their frames and transcript, then merge them into notes.md.

Usage:
    python3 scripts/merge_notes.py <library_dir> --check   # report only
    python3 scripts/merge_notes.py <library_dir>           # check, then write notes.md

Errors block the merge: a missing section, missing headings, a timestamp outside its
section, a link to a frame that isn't there. Warnings get printed and don't block:
a long stretch with no notes, or a "Said" line that doesn't match the transcript.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_ANCHOR_RX = re.compile(r"^###\s+\[t=(\d+(?::\d{2}){1,2})\]")
_IMAGE_RX = re.compile(r"!\[[^\]]*\]\((frames/[^)\s]+)\)")
_SAID_RX = re.compile(r"\*\*Said:\*\*\s*(.+)")
_CONCEPT_RX = re.compile(r"^-\s+\*\*(.+?)\*\*\s*(?:—|:|-)\s*(.*?)\s*(?:·\s*)?((?:`\[t=[^\]]+\]`[\s,]*)*)$")
_TS_RX = re.compile(r"`\[t=[^\]]+\]`")
_WORD_RX = re.compile(r"[a-z0-9']{4,}")

# Timestamps may reach a little past the edge: a section title can cite the sentence
# that led into it.
_EDGE_SLACK_S = 60.0
# A quote is checked against the transcript this far around its anchor.
_QUOTE_WINDOW_S = 120.0
_QUOTE_MIN_MATCH = 0.5
_GAP_WARN_S = 6 * 60.0
_REQUIRED = ("Section Summary", "Notes")
_SECTION_ORDER = ("Code & Commands", "Diagrams Referenced", "Open Questions")


@dataclass
class Findings:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_t(text: str) -> float:
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + int(part)
    return total


def fmt_t(t: float) -> str:
    total = int(t)
    return f"{total // 60:02d}:{total % 60:02d}"


def split_h2(text: str) -> dict[str, str]:
    """Split markdown on its H2 headings, ignoring anything inside a code fence."""
    blocks: dict[str, list[str]] = {}
    current = None
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            current = line[3:].strip()
            blocks.setdefault(current, [])
        elif current is not None:
            blocks[current].append(line)
    return {k: "\n".join(v).strip() for k, v in blocks.items()}


def _anchors(notes_block: str) -> list[tuple[float, str]]:
    """[(t, the text under that anchor)] for each ### [t=..] heading."""
    found: list[tuple[float, list[str]]] = []
    in_fence = False
    for line in notes_block.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _ANCHOR_RX.match(line)
        if m:
            found.append((parse_t(m.group(1)), []))
        elif found:
            found[-1][1].append(line)
    return [(t, "\n".join(body)) for t, body in found]


def _check_quotes(section: dict, anchors: list[tuple[float, str]], cues: list[dict], f: Findings) -> None:
    if not cues:
        return
    for i, (t, body) in enumerate(anchors):
        said = _SAID_RX.search(body)
        if not said:
            continue
        words = _WORD_RX.findall(said.group(1).lower())
        if len(words) < 4:
            continue
        next_t = anchors[i + 1][0] if i + 1 < len(anchors) else section["end_s"]
        nearby = " ".join(
            c["text"] for c in cues
            if t - _QUOTE_WINDOW_S <= c["t_start"] <= next_t + _QUOTE_WINDOW_S
        ).lower()
        spoken = set(_WORD_RX.findall(nearby))
        matched = sum(1 for w in words if w in spoken) / len(words)
        if matched < _QUOTE_MIN_MATCH:
            f.warnings.append(
                f"section {section['index']:02d} [t={fmt_t(t)}]: only {matched:.0%} of the "
                f"\"Said\" words are in the transcript around that time"
            )


def check_section(section: dict, library_dir: Path, f: Findings) -> dict | None:
    """Check one section's notes. Returns its parsed blocks, or None if unusable."""
    name = f"section {section['index']:02d}"
    path = library_dir / section["notes_path"]
    if not path.exists():
        f.errors.append(f"{name}: no notes yet at {section['notes_path']}")
        return None
    blocks = split_h2(path.read_text())
    missing = [h for h in _REQUIRED if not blocks.get(h)]
    if missing:
        f.errors.append(f"{name}: missing or empty heading(s): {', '.join(missing)}")
        return None

    anchors = _anchors(blocks["Notes"])
    if not anchors:
        f.errors.append(f"{name}: Notes has no ### [t=MM:SS] sections")
        return None

    start, end = section["start_s"], section["end_s"]
    for t, _ in anchors:
        if t < start - _EDGE_SLACK_S or t > end + _EDGE_SLACK_S:
            f.errors.append(
                f"{name}: [t={fmt_t(t)}] is outside this section ({fmt_t(start)}-{fmt_t(end)})"
            )

    for rel in _IMAGE_RX.findall(blocks["Notes"]):
        if not (library_dir / rel).exists():
            f.errors.append(f"{name}: links a frame that doesn't exist: {rel}")

    # Coverage inside the section, including the stretch up to its last edge.
    times = sorted(t for t, _ in anchors)
    edges = [start] + times + [end]
    for a, b in zip(edges, edges[1:]):
        if b - a > _GAP_WARN_S:
            f.warnings.append(f"{name}: no notes between {fmt_t(a)} and {fmt_t(b)}")

    linked = set(_IMAGE_RX.findall(blocks["Notes"]))
    frame_paths = {fr["path"] for fr in section["frames"]}
    if frame_paths and len(linked & frame_paths) < len(frame_paths) / 4:
        f.warnings.append(
            f"{name}: only {len(linked & frame_paths)} of {len(frame_paths)} frames are linked"
        )

    cues_path = library_dir / section["transcript_path"]
    cues = json.loads(cues_path.read_text()) if cues_path.exists() else []
    _check_quotes(section, anchors, cues, f)
    return blocks


def _merge_concepts(blocks: list[str]) -> list[str]:
    """One line per concept, first definition kept, later timestamps added to it."""
    order: list[str] = []
    seen: dict[str, dict] = {}
    for block in blocks:
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _CONCEPT_RX.match(line)
            key = (m.group(1) if m else line).strip().lower()
            stamps = _TS_RX.findall(line)
            if key not in seen:
                order.append(key)
                seen[key] = {"line": line, "stamps": list(stamps), "matched": bool(m)}
            else:
                seen[key]["stamps"] += [s for s in stamps if s not in seen[key]["stamps"]]
    merged = []
    for key in order:
        entry = seen[key]
        if not entry["matched"]:
            merged.append(entry["line"])
            continue
        m = _CONCEPT_RX.match(entry["line"])
        stamps = " ".join(entry["stamps"])
        tail = f" · {stamps}" if stamps else ""
        merged.append(f"- **{m.group(1)}**: {m.group(2)}{tail}")
    return merged


def _dedupe_lines(blocks: list[str]) -> list[str]:
    seen = set()
    out = []
    for block in blocks:
        for line in block.splitlines():
            key = re.sub(r"\W+", " ", line).strip().lower()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append(line)
    return out


def merge(meta: dict, sections: list[dict], parsed: list[dict], tldr: str) -> str:
    duration = meta.get("duration_s") or 0
    source = meta.get("source", "")
    out = [
        f"# {meta.get('title', 'Untitled')}",
        "",
        f"**Source:** {source}  ·  **Duration:** {fmt_t(duration)}  ·  "
        f"**Watched:** {meta.get('watched_at', '')}",
        "",
        "## TLDR",
        tldr.strip(),
        "",
        "## Parts",
    ]
    for s, blocks in zip(sections, parsed):
        out.append(
            f"- **{fmt_t(s['start_s'])} to {fmt_t(s['end_s'])}.** "
            + " ".join(blocks["Section Summary"].split())
        )
    out += ["", "## Key Concepts"]
    out += _merge_concepts([b.get("Key Concepts", "") for b in parsed])
    out += ["", "## Notes", ""]
    for blocks in parsed:
        out += [blocks["Notes"], ""]
    for heading in _SECTION_ORDER:
        bodies = [b[heading] for b in parsed if b.get(heading)]
        out.append(f"## {heading}")
        if heading == "Code & Commands":
            # Code is kept whole: two identical lines can both matter.
            out += ["\n\n".join(bodies)] if bodies else []
        else:
            out += _dedupe_lines(bodies)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def run(library_dir: Path, *, check_only: bool, out=sys.stdout) -> int:
    plan_path = library_dir / "sections.json"
    if not plan_path.exists():
        print(f"no sections.json in {library_dir}: this video doesn't use sections", file=out)
        return 2
    sections = json.loads(plan_path.read_text())["sections"]
    meta_path = library_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    f = Findings()
    parsed = [check_section(s, library_dir, f) for s in sections]

    tldr_path = library_dir / "tldr.md"
    tldr = tldr_path.read_text() if tldr_path.exists() else ""
    if not check_only and not tldr.strip():
        f.errors.append("no tldr.md yet: write it from the Section Summary blocks, then merge again")

    for w in f.warnings:
        print(f"WARN   {w}", file=out)
    for e in f.errors:
        print(f"ERROR  {e}", file=out)
    if f.errors:
        print(f"{len(f.errors)} error(s), notes.md not written", file=out)
        return 1

    n = len(sections)
    if check_only:
        print(f"{n} of {n} sections ready, {len(f.warnings)} warning(s)", file=out)
        return 0
    (library_dir / "notes.md").write_text(merge(meta, sections, parsed, tldr))
    print(f"merged {n} sections into {library_dir / 'notes.md'}, {len(f.warnings)} warning(s)", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="merge_notes")
    p.add_argument("library_dir")
    p.add_argument("--check", action="store_true", help="report only, write nothing")
    args = p.parse_args(argv)
    return run(Path(args.library_dir).expanduser(), check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
