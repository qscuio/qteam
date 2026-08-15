#!/usr/bin/env python3
"""Verify no diagram label mask is clipped by a node painted after it.

SKILL.md §6 keeps an arrow label 6-10px clear of its connector, and §5 fixes the
paint order as background -> zones -> arrows -> labels -> nodes. Nothing keeps a
label mask off a *node*, so a label whose mask lands partly inside a node
rectangle that is painted later gets covered by the node fill: the text renders
as a fragment sitting on the node border.

Paint order is what makes this a defect rather than a stylistic choice:

* A mask overlapping a zone container is fine - zones are painted before labels,
  so the label stays on top. Zone eyebrows rely on this.
* A mask overlapping a node declared *later* in the document is clipped by that
  node. That is the failure this check reports.

Shape heuristics follow the shipped templates:

* A node is a `<rect>` at least 60x40 - large enough for a title and sublabel.
* A label mask is a `<rect>` 20-120 wide and 8-14 tall - the masking plate that
  SKILL.md prescribes for arrow labels and zone eyebrows.
* A mask fully contained in a node is a badge chip (`EXT`, `EDGE`, `ORIG`) and
  is legal.

Usage:
    python3 <skill-dir>/scripts/verify_geometry.py --all
    python3 <skill-dir>/scripts/verify_geometry.py path/to/diagram.html
"""

from __future__ import annotations

import argparse
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = ROOT / "assets"

NODE_MIN_W = 60.0
NODE_MIN_H = 40.0
MASK_MIN_W = 20.0
MASK_MAX_W = 120.0
MASK_MIN_H = 8.0
MASK_MAX_H = 14.0
EPSILON = 0.5


class Rect:
    __slots__ = ("x", "y", "w", "h", "line", "offset")

    def __init__(self, x, y, w, h, line, offset) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h
        self.line, self.offset = line, offset

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def __repr__(self) -> str:
        return f"({self.x:g},{self.y:g} {self.w:g}x{self.h:g})"


def parse_rects(source: str) -> tuple[list[Rect], list[str]]:
    class RectParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.rects: list[Rect] = []
            self.errors: list[str] = []
            self._transformed: list[tuple[str, bool]] = []

        def handle_starttag(self, tag, attrs) -> None:
            tag = tag.casefold()
            data = {name.casefold(): value for name, value in attrs}
            transformed = (
                (self._transformed[-1][1] if self._transformed else False)
                or bool((data.get("transform") or "").strip())
            )
            if tag != "rect":
                self._transformed.append((tag, transformed))
                return
            if transformed:
                self.errors.append(
                    f"line {self.getpos()[0]}: transformed rect geometry is unsupported"
                )
                return
            if any(data.get(name) is None for name in ("width", "height")):
                return
            try:
                values = [
                    float(data.get("x") or 0),
                    float(data.get("y") or 0),
                    float(data["width"]),
                    float(data["height"]),
                ]
            except (TypeError, ValueError):
                return
            self.rects.append(
                Rect(*values, self.getpos()[0], len(self.rects))
            )

        def handle_endtag(self, tag) -> None:
            tag = tag.casefold()
            for index in range(len(self._transformed) - 1, -1, -1):
                if self._transformed[index][0] == tag:
                    del self._transformed[index:]
                    break

    parser = RectParser()
    parser.feed(source)
    parser.close()
    return parser.rects, parser.errors


def overlap(a: Rect, b: Rect) -> tuple[float, float]:
    return (
        min(a.right, b.right) - max(a.x, b.x),
        min(a.bottom, b.bottom) - max(a.y, b.y),
    )


def contained(inner: Rect, outer: Rect) -> bool:
    return (
        inner.x >= outer.x - EPSILON
        and inner.y >= outer.y - EPSILON
        and inner.right <= outer.right + EPSILON
        and inner.bottom <= outer.bottom + EPSILON
    )


def check(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    rects, parse_errors = parse_rects(source)
    nodes = [r for r in rects if r.w >= NODE_MIN_W and r.h >= NODE_MIN_H]
    masks = [
        r
        for r in rects
        if MASK_MIN_W <= r.w <= MASK_MAX_W and MASK_MIN_H <= r.h <= MASK_MAX_H
    ]

    findings: list[str] = [f"{path.name}:{error}" for error in parse_errors]
    for mask in masks:
        for node in nodes:
            if node.offset <= mask.offset:
                continue  # painted before the label; the label stays on top
            dx, dy = overlap(mask, node)
            if dx <= 1.0 or dy <= 1.0 or contained(mask, node):
                continue
            findings.append(
                f"{path.name}:{mask.line}: label mask {mask} is clipped by node "
                f"{node} declared later at line {node.line} (overlap {dx:g}x{dy:g}px)"
                f" - move the label onto a free segment of its connector"
            )
            break
    return findings


def targets(args: argparse.Namespace) -> list[Path]:
    if args.all:
        return sorted(ASSET_DIR.glob("*.html"))
    return [Path(p) for p in args.files]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="HTML diagrams to check")
    parser.add_argument("--all", action="store_true", help="check every shipped asset")
    args = parser.parse_args()

    paths = targets(args)
    if not paths:
        parser.error("pass one or more files, or --all")

    findings: list[str] = []
    for path in paths:
        if not path.exists():
            findings.append(f"{path}: file not found")
            continue
        findings.extend(check(path))

    for finding in findings:
        print(finding)
    print(f"Summary: {len(paths)} file(s) checked, {len(findings)} finding(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
