#!/usr/bin/env python3
"""Validate and inspect QTeam Diagram Contract v1 artifacts.

The contract is the semantic source embedded in a self-contained HTML file.
The inline SVG repeats only semantic IDs plus rendered bounds/routes.  This
tool binds the two and emits deterministic composition evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import self_check
import verify_geometry


MAX_FILE_BYTES = 1024 * 1024
MAX_CONTRACT_BYTES = 256 * 1024
MAX_COORDINATE = 1_000_000.0
SAFE_ID = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SAFE_KIND = re.compile(r"^[a-z][a-z0-9-]{0,47}$")
PATH_TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?")
NON_RENDERED_SVG_CONTEXTS = {
    "clippath", "defs", "filter", "foreignobject", "lineargradient",
    "marker", "mask", "meshgradient", "metadata", "pattern",
    "radialgradient", "solidcolor", "symbol", "template", "view",
}
RENDERED_SVG_CONTAINERS = {"a", "g", "svg"}
SVG_MOTION_TAGS = {"animate", "animatemotion", "animatetransform", "discard", "set"}
NON_CONTAINER_TAGS = {
    # HTML void elements plus SVG leaf primitives. All other tags are tracked
    # as possible renderable ancestors, including custom elements.
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
    "circle", "ellipse", "image", "line", "path", "polygon", "polyline",
    "rect", "text", "tspan", "use",
}
ROOT_FIELDS = {
    "schema_version", "diagram_type", "semantic_profile",
    "composition_profile", "title", "elements", "relationships",
}
ELEMENT_FIELDS = {"id", "label", "kind", "parent", "stereotype"}
RELATIONSHIP_FIELDS = {"id", "source", "target", "label", "kind"}
DIAGRAM_TYPES = {
    "architecture", "data-flow", "er", "tree", "org-chart", "process",
    "swimlane", "nested",
    "layers", "high-level", "it-state", "medallion", "dp-integration",
    "uml-class", "uml-component", "uml-deployment",
}
GENERIC_TYPES = {"tree", "org-chart", "nested", "layers"}
PROFILE_KINDS = {
    "generic": (
        {"element", "group"},
        {"relationship"},
    ),
    "architecture": (
        {"actor", "service", "component", "store", "external", "queue", "boundary"},
        {"request", "response", "render", "read", "write", "query", "event",
         "dependency", "deployment", "blocked"},
    ),
    "data-flow": (
        {"source", "process", "store", "sink", "queue", "actor"},
        {"data", "event", "control", "feedback"},
    ),
    "er": (
        {"entity", "associative-entity"},
        {"association", "identifying"},
    ),
    "uml-class": (
        {"class", "interface", "abstract-class", "enumeration"},
        {"association", "dependency", "generalization", "realization",
         "aggregation", "composition"},
    ),
    "uml-component": (
        {"component", "interface", "provided-interface", "required-interface",
         "port", "artifact", "subsystem"},
        {"dependency", "realization", "assembly", "delegation"},
    ),
    "uml-deployment": (
        {"device", "execution-environment", "artifact", "component"},
        {"communication", "deployment", "dependency"},
    ),
}
PROFILE_TYPES = {
    "generic": GENERIC_TYPES,
    "architecture": {"architecture", "high-level", "it-state", "medallion", "dp-integration"},
    "data-flow": {"data-flow", "process", "swimlane"},
    "er": {"er"},
    "uml-class": {"uml-class"},
    "uml-component": {"uml-component"},
    "uml-deployment": {"uml-deployment"},
}
COMPOSITION_PROFILES = {
    "standard": {
        "max_bends_per_relationship": 4,
        "max_total_bends": 24,
        "max_route_stretch": 3.0,
        "max_crossings": 0,
        "min_element_gap": 16.0,
        "min_segment_length": 8.0,
    },
    "showcase": {
        "max_bends_per_relationship": 2,
        "max_total_bends": 12,
        "max_route_stretch": 1.6,
        "max_crossings": 0,
        "min_element_gap": 32.0,
        "min_segment_length": 16.0,
    },
}


class ContractError(ValueError):
    pass


class CompositionError(ContractError):
    def __init__(self, report: Mapping[str, Any]) -> None:
        self.report = report
        codes = "; ".join(item["code"] for item in report["composition"]["violations"])
        super().__init__("composition contract failed: %s" % codes)


def _object_no_duplicates(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON field: %s" % key)
        result[key] = value
    return result


def _regular_text(path: Path, limit: int) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags)
    except OSError as error:
        raise ContractError("cannot open %s: %s" % (path, error)) from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ContractError("input must be a singly-linked regular file: %s" % path)
        if info.st_size > limit:
            raise ContractError("input exceeds %d bytes: %s" % (limit, path))
        chunks: List[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limit:
            raise ContractError("input exceeds %d bytes: %s" % (limit, path))
        try:
            return payload.decode("utf-8")
        except UnicodeError as error:
            raise ContractError("input is not valid UTF-8: %s" % path) from error
    finally:
        os.close(fd)


def _loads_object(source: str, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(source, object_pairs_hook=_object_no_duplicates)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ContractError("invalid %s JSON: %s" % (label, error)) from error
    if not isinstance(value, dict):
        raise ContractError("%s must be a JSON object" % label)
    return value


def _text(value: Any, path: str, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractError("%s must be a string" % path)
    normalized = " ".join(value.split())
    if value != normalized:
        raise ContractError("%s must use canonical single-space text" % path)
    if not allow_empty and not normalized:
        raise ContractError("%s must be non-empty" % path)
    if len(normalized) > maximum:
        raise ContractError("%s exceeds %d characters" % (path, maximum))
    return value


def _identifier(value: Any, path: str) -> str:
    text = _text(value, path, 64)
    if not SAFE_ID.fullmatch(text):
        raise ContractError("%s must be a safe identifier" % path)
    return text


def _kind(value: Any, path: str) -> str:
    text = _text(value, path, 48)
    if not SAFE_KIND.fullmatch(text):
        raise ContractError("%s must be lower-case hyphenated text" % path)
    return text


def validate_contract(raw: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ContractError("diagram contract must be an object")
    unknown = set(raw) - ROOT_FIELDS
    missing = ROOT_FIELDS - set(raw)
    if unknown or missing:
        raise ContractError("diagram contract fields differ: missing=%s unknown=%s" %
                            (sorted(missing), sorted(unknown)))
    if isinstance(raw["schema_version"], bool) or raw["schema_version"] != 1:
        raise ContractError("schema_version must be 1")
    diagram_type = _kind(raw["diagram_type"], "diagram_type")
    if diagram_type not in DIAGRAM_TYPES:
        raise ContractError("unsupported diagram_type: %s" % diagram_type)
    profile = _kind(raw["semantic_profile"], "semantic_profile")
    if profile not in PROFILE_KINDS:
        raise ContractError("unsupported semantic_profile: %s" % profile)
    if diagram_type not in PROFILE_TYPES[profile]:
        raise ContractError("semantic_profile %s does not apply to %s" %
                            (profile, diagram_type))
    composition = _kind(raw["composition_profile"], "composition_profile")
    if composition not in COMPOSITION_PROFILES:
        raise ContractError("unsupported composition_profile: %s" % composition)
    title = _text(raw["title"], "title", 160)
    if not isinstance(raw["elements"], list) or not 1 <= len(raw["elements"]) <= 64:
        raise ContractError("elements must contain 1..64 objects")
    if not isinstance(raw["relationships"], list) or len(raw["relationships"]) > 128:
        raise ContractError("relationships must contain at most 128 objects")

    element_kinds, relationship_kinds = PROFILE_KINDS[profile]
    elements: List[Dict[str, Any]] = []
    element_ids = set()
    for index, item in enumerate(raw["elements"]):
        path = "elements[%d]" % index
        if not isinstance(item, dict) or set(item) - ELEMENT_FIELDS or not {"id", "label", "kind"} <= set(item):
            raise ContractError("%s has invalid fields" % path)
        item_id = _identifier(item["id"], path + ".id")
        if item_id in element_ids:
            raise ContractError("duplicate element id: %s" % item_id)
        kind = _kind(item["kind"], path + ".kind")
        if kind not in element_kinds:
            raise ContractError("%s.kind %s is invalid for %s" % (path, kind, profile))
        normalized = {
            "id": item_id,
            "label": _text(item["label"], path + ".label", 128),
            "kind": kind,
        }
        if "parent" in item:
            normalized["parent"] = _identifier(item["parent"], path + ".parent")
        if "stereotype" in item:
            normalized["stereotype"] = _kind(item["stereotype"], path + ".stereotype")
        element_ids.add(item_id)
        elements.append(normalized)

    by_id = {item["id"]: item for item in elements}
    for item in elements:
        parent = item.get("parent")
        if parent is not None and (parent not in by_id or parent == item["id"]):
            raise ContractError("element %s has an invalid parent" % item["id"])
        seen = {item["id"]}
        while parent is not None:
            if parent in seen:
                raise ContractError("element parent cycle at %s" % item["id"])
            seen.add(parent)
            parent = by_id[parent].get("parent")

    relationships: List[Dict[str, Any]] = []
    all_ids = set(element_ids)
    for index, item in enumerate(raw["relationships"]):
        path = "relationships[%d]" % index
        if not isinstance(item, dict) or set(item) != RELATIONSHIP_FIELDS:
            raise ContractError("%s has invalid fields" % path)
        item_id = _identifier(item["id"], path + ".id")
        if item_id in all_ids:
            raise ContractError("duplicate diagram id: %s" % item_id)
        source = _identifier(item["source"], path + ".source")
        target = _identifier(item["target"], path + ".target")
        if source not in element_ids or target not in element_ids:
            raise ContractError("relationship %s references an unknown element" % item_id)
        kind = _kind(item["kind"], path + ".kind")
        if kind not in relationship_kinds:
            raise ContractError("%s.kind %s is invalid for %s" % (path, kind, profile))
        if source == target and kind not in {"self", "transition", "association"}:
            raise ContractError("relationship %s cannot be a self-loop" % item_id)
        relationships.append({
            "id": item_id,
            "source": source,
            "target": target,
            "label": _text(item["label"], path + ".label", 128, allow_empty=True),
            "kind": kind,
        })
        all_ids.add(item_id)

    return {
        "schema_version": 1,
        "diagram_type": diagram_type,
        "semantic_profile": profile,
        "composition_profile": composition,
        "title": title,
        "elements": elements,
        "relationships": relationships,
    }


class ProjectionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contracts: List[Dict[str, Any]] = []
        self.elements: Dict[str, List[Dict[str, Any]]] = {}
        self.element_labels: Dict[str, List[str]] = {}
        self.element_stereotypes: Dict[str, List[str]] = {}
        self.relationships: Dict[str, List[Dict[str, Any]]] = {}
        self.labels: Dict[str, List[str]] = {}
        self.label_records: Dict[str, List[Dict[str, Any]]] = {}
        self.html_titles: List[str] = []
        self.svg_records: List[Dict[str, Any]] = []
        self.styles: List[List[str]] = []
        self.semantic_css_tokens = {"*"}
        self.semantic_paths: List[List[Dict[str, Any]]] = []
        self.errors: List[str] = []
        self._script: Optional[Dict[str, Any]] = None
        self._style: Optional[List[str]] = None
        self._label_stack: List[Tuple[str, str, List[str]]] = []
        self._svg_scope: List[Tuple[str, bool]] = []
        self._rendered_svg_stack: List[bool] = []
        self._svg_record_stack: List[Dict[str, Any]] = []
        self._non_rendered_depth = 0
        self._dom_scope: List[Dict[str, Any]] = []

    @staticmethod
    def _unsafe_projection(attrs: Mapping[str, str]) -> bool:
        style = re.sub(r"\s+", "", attrs.get("style", "").casefold())
        opacity = attrs.get("opacity", "")
        zero_opacity = False
        try:
            zero_opacity = bool(opacity) and float(opacity) <= 0.0
        except ValueError:
            pass
        return ("transform" in attrs or "hidden" in attrs
                or attrs.get("aria-hidden", "").casefold() == "true"
                or attrs.get("display", "").casefold() == "none"
                or attrs.get("visibility", "").casefold() in {"hidden", "collapse"}
                or zero_opacity or "display:none" in style
                or "visibility:hidden" in style or "visibility:collapse" in style
                or "transform:" in style or "fill:none" in style
                or "fill:transparent" in style or "stroke:none" in style
                or "stroke:transparent" in style
                or re.search(r"(?:^|;)opacity:0+(?:\.0+)?(?:;|$)", style) is not None)

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.casefold()
        if (self._label_stack
                and self._label_stack[-1][1] not in {"html-title"}
                and not self._label_stack[-1][1].startswith("svg-title:")):
            self.errors.append("contract labels must contain plain SVG text")
        names = [name.casefold() for name, _ in attrs]
        if len(names) != len(set(names)):
            self.errors.append("duplicate attributes on <%s>" % tag)
        data = {name.casefold(): value or "" for name, value in attrs}
        local_css_tokens = {tag}
        local_css_tokens.update("." + item for item in data.get("class", "").split() if item)
        if data.get("id"):
            local_css_tokens.add("#" + data["id"])
        ancestor_css_tokens = set().union(
            *(record["tokens"] for record in self._dom_scope)
        ) if self._dom_scope else set()
        inherited_unsafe = self._svg_scope[-1][1] if self._svg_scope else False
        local_unsafe = inherited_unsafe or self._unsafe_projection(data)
        if tag == "svg":
            rendered = data.get("aria-hidden", "").casefold() != "true" and not local_unsafe
            record = {
                "rendered": rendered, "markers": 0, "titles": [],
                "attrs": dict(data),
            }
            self.svg_records.append(record)
            self._svg_record_stack.append(record)
            self._rendered_svg_stack.append(rendered)
        inside_rendered_svg = bool(self._rendered_svg_stack and self._rendered_svg_stack[-1])
        if self._rendered_svg_stack and tag in SVG_MOTION_TAGS:
            self.errors.append("Diagram Contract v1 does not allow SVG motion elements")
        blocked_projection = self._non_rendered_depth > 0 or tag in NON_RENDERED_SVG_CONTEXTS
        semantic_marker = any(name in data for name in (
            "data-diagram-element", "data-diagram-element-label",
            "data-diagram-element-stereotype",
            "data-diagram-relationship", "data-diagram-relationship-label",
        ))
        if semantic_marker:
            self.semantic_css_tokens.update(ancestor_css_tokens | local_css_tokens)
            self.semantic_css_tokens.update("[" + name + "]" for name in data if name.startswith("data-diagram-"))
            self.semantic_paths.append([
                {"tag": record["tag"], "attrs": dict(record["attrs"])}
                for record in self._dom_scope
            ] + [{"tag": tag, "attrs": dict(data)}])
            if data.get("style", "").strip():
                self.errors.append("contract projection cannot use inline CSS")
            if any(record["attrs"].get("style", "").strip()
                   for record in self._dom_scope):
                self.errors.append("contract projection ancestors cannot use inline CSS")
            if any(self._unsafe_projection(record["attrs"])
                   for record in self._dom_scope):
                self.errors.append("contract projection ancestor is hidden or transformed")
            inherited_presentation = {
                "clip-path", "color", "fill", "fill-opacity", "filter",
                "font-family", "font-size", "mask", "opacity", "stroke",
                "stroke-opacity", "stroke-width", "visibility",
            }
            if any(inherited_presentation & set(record["attrs"])
                   for record in self._dom_scope):
                self.errors.append(
                    "contract projection ancestors cannot supply SVG paint or text presentation"
                )
            if any(
                (record["tag"] in {"details", "dialog"}
                 and "open" not in record["attrs"])
                or "popover" in record["attrs"]
                for record in self._dom_scope
            ):
                self.errors.append("contract projection is inside a browser-hidden ancestor")
            svg_ancestors: List[str] = []
            seen_svg = False
            for record in self._dom_scope:
                if record["tag"] == "svg":
                    seen_svg = True
                if seen_svg:
                    svg_ancestors.append(record["tag"])
            if svg_ancestors.count("svg") != 1:
                self.errors.append("contract projections require one non-nested SVG coordinate space")
            if any(name not in RENDERED_SVG_CONTAINERS for name in svg_ancestors):
                self.errors.append("contract projection is inside a non-rendered SVG container")
            if any(name in data for name in (
                    "data-diagram-element-label",
                    "data-diagram-element-stereotype",
                    "data-diagram-relationship-label",
            )) and tag != "text":
                self.errors.append("contract labels must annotate an SVG text element")
        if semantic_marker and local_unsafe:
            self.errors.append("contract projection cannot be transformed or hidden")
        no_paint = {"none", "transparent", "currentcolor"}
        if "data-diagram-element" in data:
            fill = data.get("fill", "black").casefold()
            stroke = data.get("stroke", "none").casefold()
            try:
                fill_opacity = float(data.get("fill-opacity", "1"))
                stroke_opacity = float(data.get("stroke-opacity", "1"))
                stroke_width = float(data.get("stroke-width", "1"))
            except ValueError:
                fill_opacity = stroke_opacity = stroke_width = 0.0
            fill_visible = (fill not in no_paint and "var(" not in fill
                            and "calc(" not in fill and fill_opacity > 0)
            stroke_visible = (stroke not in no_paint and "var(" not in stroke
                              and "calc(" not in stroke and stroke_opacity > 0
                              and stroke_width > 0)
            if not fill_visible and not stroke_visible:
                self.errors.append("contract element must have visible fill or stroke")
        if "data-diagram-relationship" in data:
            stroke = data.get("stroke", "none").casefold()
            try:
                stroke_width = float(data.get("stroke-width", "1"))
                stroke_opacity = float(data.get("stroke-opacity", "1"))
            except ValueError:
                stroke_width = stroke_opacity = 0.0
            if (stroke in no_paint or "var(" in stroke or "calc(" in stroke
                    or stroke_width <= 0 or stroke_opacity <= 0):
                self.errors.append("contract relationship must have a visible stroke")
        if ("data-diagram-element-label" in data
                or "data-diagram-element-stereotype" in data
                or "data-diagram-relationship-label" in data):
            try:
                fill_opacity = float(data.get("fill-opacity", "1"))
            except ValueError:
                fill_opacity = 0.0
            label_fill = data.get("fill", "black").casefold()
            if (label_fill in no_paint or "var(" in label_fill
                    or "calc(" in label_fill or fill_opacity <= 0):
                self.errors.append("contract label cannot disable its fill")
        if semantic_marker and (not inside_rendered_svg or blocked_projection):
            self.errors.append("contract projection must be inside a rendered SVG, not a definition container")
        if semantic_marker and self._svg_record_stack:
            self._svg_record_stack[-1]["markers"] += 1
        if tag in NON_RENDERED_SVG_CONTEXTS:
            self._non_rendered_depth += 1
        if tag == "svg" or self._rendered_svg_stack:
            self._svg_scope.append((tag, local_unsafe))
        if tag not in NON_CONTAINER_TAGS:
            self._dom_scope.append({
                "tag": tag, "attrs": dict(data), "tokens": local_css_tokens,
            })
        if tag == "script" and "data-diagram-contract" in data:
            self._script = {"tag": tag, "attrs": data, "body": []}
            self.contracts.append(self._script)
        if tag == "style":
            self._style = []
            self.styles.append(self._style)
        element_id = data.get("data-diagram-element")
        if element_id is not None:
            record = {"tag": tag, "attrs": data,
                      "bounds": data.get("data-diagram-bounds", "")}
            self.elements.setdefault(element_id, []).append(record)
        relationship_id = data.get("data-diagram-relationship")
        if relationship_id is not None:
            self.relationships.setdefault(relationship_id, []).append({
                "route": data.get("data-diagram-route", ""),
                "tag": tag,
                "attrs": data,
            })
        element_label_id = data.get("data-diagram-element-label")
        if element_label_id is not None:
            body = []
            self._label_stack.append((tag, "element:" + element_label_id, body))
            self.label_records.setdefault("element:" + element_label_id, []).append({
                "tag": tag, "attrs": dict(data),
            })
        element_stereotype_id = data.get("data-diagram-element-stereotype")
        if element_stereotype_id is not None:
            body = []
            self._label_stack.append((tag, "stereotype:" + element_stereotype_id, body))
            self.label_records.setdefault("stereotype:" + element_stereotype_id, []).append({
                "tag": tag, "attrs": dict(data),
            })
        label_id = data.get("data-diagram-relationship-label")
        if label_id is not None:
            body: List[str] = []
            self._label_stack.append((tag, label_id, body))
            self.label_records.setdefault("relationship:" + label_id, []).append({
                "tag": tag, "attrs": dict(data),
            })
        if tag == "title":
            body = []
            if self._svg_record_stack:
                marker = "svg-title:%d" % (len(self.svg_records) - 1)
            else:
                marker = "html-title"
            self._label_stack.append((tag, marker, body))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "script" and self._script is not None:
            self._script = None
        if tag == "style":
            self._style = None
        if self._label_stack and self._label_stack[-1][0] == tag:
            _, label_id, body = self._label_stack.pop()
            text = " ".join("".join(body).split())
            if label_id.startswith("element:"):
                self.element_labels.setdefault(label_id[8:], []).append(text)
            elif label_id.startswith("stereotype:"):
                self.element_stereotypes.setdefault(label_id[11:], []).append(text)
            elif label_id.startswith("svg-title:"):
                self.svg_records[int(label_id[10:])]["titles"].append(text)
            elif label_id == "html-title":
                self.html_titles.append(text)
            else:
                self.labels.setdefault(label_id, []).append(text)
        if self._svg_scope and self._svg_scope[-1][0] == tag:
            self._svg_scope.pop()
        if tag == "svg" and self._rendered_svg_stack:
            self._rendered_svg_stack.pop()
            self._svg_record_stack.pop()
        if tag in NON_RENDERED_SVG_CONTEXTS and self._non_rendered_depth:
            self._non_rendered_depth -= 1
        if self._dom_scope and self._dom_scope[-1]["tag"] == tag:
            self._dom_scope.pop()

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script["body"].append(data)
        if self._style is not None:
            self._style.append(data)
        for _, _, body in self._label_stack:
            body.append(data)


def _parse_numbers(value: str, count: int, path: str) -> Tuple[float, ...]:
    pieces = [piece.strip() for piece in value.split(",")]
    if len(pieces) != count:
        raise ContractError("%s must contain %d comma-separated numbers" % (path, count))
    result = []
    for piece in pieces:
        try:
            number = float(piece)
        except ValueError as error:
            raise ContractError("%s contains a non-number" % path) from error
        if not math.isfinite(number):
            raise ContractError("%s contains a non-finite number" % path)
        if abs(number) > MAX_COORDINATE:
            raise ContractError("%s exceeds the coordinate bound" % path)
        result.append(number)
    return tuple(result)


def _parse_route(value: str, path: str) -> List[Tuple[float, float]]:
    pieces = value.split()
    if not 2 <= len(pieces) <= 16:
        raise ContractError("%s must contain 2..16 points" % path)
    points = [_parse_numbers(piece, 2, path) for piece in pieces]
    for first, second in zip(points, points[1:]):
        if first == second:
            raise ContractError("%s contains a zero-length segment" % path)
        if abs(first[0] - second[0]) > 1e-6 and abs(first[1] - second[1]) > 1e-6:
            raise ContractError("%s contains a diagonal segment" % path)
    return [(point[0], point[1]) for point in points]


def _same_point(first: Tuple[float, float], second: Tuple[float, float]) -> bool:
    return abs(first[0] - second[0]) <= 1e-6 and abs(first[1] - second[1]) <= 1e-6


def _simplify_route(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    result: List[Tuple[float, float]] = []
    for point in points:
        if result and _same_point(result[-1], point):
            continue
        result.append(point)
        while len(result) >= 3:
            first, middle, last = result[-3:]
            same_x = abs(first[0] - middle[0]) <= 1e-6 and abs(middle[0] - last[0]) <= 1e-6
            same_y = abs(first[1] - middle[1]) <= 1e-6 and abs(middle[1] - last[1]) <= 1e-6
            coordinate = 1 if same_x else 0
            monotonic = (min(first[coordinate], last[coordinate]) - 1e-6
                         <= middle[coordinate]
                         <= max(first[coordinate], last[coordinate]) + 1e-6)
            if not (same_x or same_y) or not monotonic:
                break
            result.pop(-2)
    return result


def _path_route(value: str, path: str) -> List[Tuple[float, float]]:
    tokens = PATH_TOKEN.findall(value)
    remainder = PATH_TOKEN.sub("", value).replace(",", "")
    if remainder.strip() or not tokens:
        raise ContractError("%s has unsupported SVG path syntax" % path)
    index = 0
    command: Optional[str] = None
    current: Optional[Tuple[float, float]] = None
    points: List[Tuple[float, float]] = []

    def number() -> float:
        nonlocal index
        if index >= len(tokens) or tokens[index].isalpha():
            raise ContractError("%s has a missing path coordinate" % path)
        value_number = float(tokens[index])
        index += 1
        if not math.isfinite(value_number):
            raise ContractError("%s has a non-finite path coordinate" % path)
        if abs(value_number) > MAX_COORDINATE:
            raise ContractError("%s exceeds the coordinate bound" % path)
        return value_number

    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
        if command not in {"M", "H", "V", "L", "Q"}:
            raise ContractError("%s supports only absolute M/H/V/L/Q path commands" % path)
        if command == "M":
            current = (number(), number())
            if points:
                raise ContractError("%s must contain one continuous subpath" % path)
            points.append(current)
            command = None
            continue
        if current is None:
            raise ContractError("%s must begin with M" % path)
        if command == "H":
            current = (number(), current[1])
            points.append(current)
        elif command == "V":
            current = (current[0], number())
            points.append(current)
        elif command == "L":
            endpoint = (number(), number())
            if abs(endpoint[0] - current[0]) > 1e-6 and abs(endpoint[1] - current[1]) > 1e-6:
                raise ContractError("%s contains a diagonal L segment" % path)
            current = endpoint
            points.append(current)
        else:
            control = (number(), number())
            endpoint = (number(), number())
            first_axis = (abs(control[0] - current[0]) <= 1e-6,
                          abs(control[1] - current[1]) <= 1e-6)
            second_axis = (abs(endpoint[0] - control[0]) <= 1e-6,
                           abs(endpoint[1] - control[1]) <= 1e-6)
            first_length = abs(control[0] - current[0]) + abs(control[1] - current[1])
            second_length = abs(endpoint[0] - control[0]) + abs(endpoint[1] - control[1])
            if (sum(first_axis) != 1 or sum(second_axis) != 1
                    or first_axis == second_axis or not 0 < first_length <= 16
                    or not 0 < second_length <= 16):
                raise ContractError("%s Q command must be a 16px-or-smaller orthogonal corner" % path)
            points.extend((control, endpoint))
            current = endpoint
        command = None
    return _simplify_route(points)


def _actual_route(record: Mapping[str, Any], path: str) -> List[Tuple[float, float]]:
    tag = record["tag"]
    attrs = record["attrs"]
    try:
        if tag == "line":
            result = [(float(attrs["x1"]), float(attrs["y1"])),
                      (float(attrs["x2"]), float(attrs["y2"]))]
        elif tag == "polyline":
            tokens = attrs.get("points", "").replace(",", " ").split()
            if len(tokens) < 4 or len(tokens) % 2:
                raise ContractError("%s polyline points are malformed" % path)
            raw = [(float(tokens[index]), float(tokens[index + 1]))
                   for index in range(0, len(tokens), 2)]
            if any(abs(value) > MAX_COORDINATE for point in raw for value in point):
                raise ContractError("%s exceeds the coordinate bound" % path)
            result = _simplify_route(raw)
        elif tag == "path":
            result = _path_route(attrs.get("d", ""), path)
        else:
            raise ContractError("%s must annotate a line, polyline, or path" % path)
    except (KeyError, ValueError) as error:
        raise ContractError("%s has malformed visible geometry" % path) from error
    if any(not math.isfinite(value) for point in result for value in point):
        raise ContractError("%s has non-finite visible geometry" % path)
    if any(abs(value) > MAX_COORDINATE for point in result for value in point):
        raise ContractError("%s exceeds the coordinate bound" % path)
    return result


def _actual_bounds(record: Mapping[str, Any], path: str) -> Tuple[float, float, float, float]:
    tag = record["tag"]
    attrs = record["attrs"]
    try:
        if tag == "rect":
            result = (float(attrs.get("x", "0")), float(attrs.get("y", "0")),
                      float(attrs["width"]), float(attrs["height"]))
        elif tag == "circle":
            cx, cy, radius = float(attrs["cx"]), float(attrs["cy"]), float(attrs["r"])
            result = (cx - radius, cy - radius, 2 * radius, 2 * radius)
        elif tag == "ellipse":
            cx, cy = float(attrs["cx"]), float(attrs["cy"])
            rx, ry = float(attrs["rx"]), float(attrs["ry"])
            result = (cx - rx, cy - ry, 2 * rx, 2 * ry)
        else:
            raise ContractError("%s must annotate a rect, circle, or ellipse" % path)
    except (KeyError, ValueError) as error:
        raise ContractError("%s has malformed visible geometry" % path) from error
    if any(not math.isfinite(value) for value in result):
        raise ContractError("%s has non-finite visible geometry" % path)
    return result


def _contains(outer: Tuple[float, float, float, float], inner: Tuple[float, float, float, float]) -> bool:
    return (inner[0] >= outer[0] and inner[1] >= outer[1]
            and inner[0] + inner[2] <= outer[0] + outer[2]
            and inner[1] + inner[3] <= outer[1] + outer[3])


def _ancestor(first: str, second: str, elements: Mapping[str, Mapping[str, Any]]) -> bool:
    parent = elements[second].get("parent")
    while parent is not None:
        if parent == first:
            return True
        parent = elements[parent].get("parent")
    return False


def _gap(first: Tuple[float, float, float, float], second: Tuple[float, float, float, float]) -> float:
    horizontal = max(first[0] - (second[0] + second[2]), second[0] - (first[0] + first[2]), 0.0)
    vertical = max(first[1] - (second[1] + second[3]), second[1] - (first[1] + first[3]), 0.0)
    return math.hypot(horizontal, vertical)


def _overlap(first: Tuple[float, float, float, float], second: Tuple[float, float, float, float]) -> bool:
    return (min(first[0] + first[2], second[0] + second[2]) - max(first[0], second[0]) > 1e-6
            and min(first[1] + first[3], second[1] + second[3]) - max(first[1], second[1]) > 1e-6)


def _on_boundary(point: Tuple[float, float], bounds: Tuple[float, float, float, float]) -> bool:
    x, y, width, height = bounds
    within_x = x - 1e-6 <= point[0] <= x + width + 1e-6
    within_y = y - 1e-6 <= point[1] <= y + height + 1e-6
    on_x = abs(point[0] - x) <= 1e-6 or abs(point[0] - (x + width)) <= 1e-6
    on_y = abs(point[1] - y) <= 1e-6 or abs(point[1] - (y + height)) <= 1e-6
    return (within_y and on_x) or (within_x and on_y)


def _segment_crossing(a: Tuple[float, float], b: Tuple[float, float],
                      c: Tuple[float, float], d: Tuple[float, float]) -> bool:
    a_vertical = abs(a[0] - b[0]) <= 1e-6
    c_vertical = abs(c[0] - d[0]) <= 1e-6
    if a_vertical == c_vertical:
        return False
    vertical = (a, b) if a_vertical else (c, d)
    horizontal = (c, d) if a_vertical else (a, b)
    vx = vertical[0][0]
    hy = horizontal[0][1]
    return (min(vertical[0][1], vertical[1][1]) < hy < max(vertical[0][1], vertical[1][1])
            and min(horizontal[0][0], horizontal[1][0]) < vx < max(horizontal[0][0], horizontal[1][0]))


def _segments_overlap(a: Tuple[float, float], b: Tuple[float, float],
                      c: Tuple[float, float], d: Tuple[float, float]) -> bool:
    first_vertical = abs(a[0] - b[0]) <= 1e-6
    second_vertical = abs(c[0] - d[0]) <= 1e-6
    if first_vertical != second_vertical:
        return False
    if first_vertical:
        if abs(a[0] - c[0]) > 1e-6:
            return False
        overlap = min(max(a[1], b[1]), max(c[1], d[1])) - max(min(a[1], b[1]), min(c[1], d[1]))
    else:
        if abs(a[1] - c[1]) > 1e-6:
            return False
        overlap = min(max(a[0], b[0]), max(c[0], d[0])) - max(min(a[0], b[0]), min(c[0], d[0]))
    return overlap > 1e-6


def _point_on_segment(point: Tuple[float, float], first: Tuple[float, float],
                      second: Tuple[float, float]) -> bool:
    if abs(first[0] - second[0]) <= 1e-6:
        return (abs(point[0] - first[0]) <= 1e-6
                and min(first[1], second[1]) - 1e-6 <= point[1]
                <= max(first[1], second[1]) + 1e-6)
    return (abs(point[1] - first[1]) <= 1e-6
            and min(first[0], second[0]) - 1e-6 <= point[0]
            <= max(first[0], second[0]) + 1e-6)


def _segment_touch_points(a: Tuple[float, float], b: Tuple[float, float],
                          c: Tuple[float, float], d: Tuple[float, float]
                          ) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    for point in (a, b, c, d):
        if (_point_on_segment(point, a, b) and _point_on_segment(point, c, d)
                and not any(_same_point(point, known) for known in points)):
            points.append(point)
    return points


def _segment_enters_bounds(a: Tuple[float, float], b: Tuple[float, float],
                           bounds: Tuple[float, float, float, float]) -> bool:
    x, y, width, height = bounds
    if abs(a[0] - b[0]) <= 1e-6:
        if not x + 1e-6 < a[0] < x + width - 1e-6:
            return False
        overlap = min(max(a[1], b[1]), y + height) - max(min(a[1], b[1]), y)
    else:
        if not y + 1e-6 < a[1] < y + height - 1e-6:
            return False
        overlap = min(max(a[0], b[0]), x + width) - max(min(a[0], b[0]), x)
    return overlap > 1e-6


def _svg_view_box(attrs: Mapping[str, str]) -> Tuple[float, float, float, float]:
    pieces = [piece for piece in re.split(r"[\s,]+", attrs.get("viewbox", "").strip())
              if piece]
    if len(pieces) != 4:
        raise ContractError("contract SVG must have a four-number viewBox")
    try:
        result = tuple(float(piece) for piece in pieces)
    except ValueError as error:
        raise ContractError("contract SVG viewBox is malformed") from error
    if (any(not math.isfinite(value) or abs(value) > MAX_COORDINATE for value in result)
            or result[2] <= 0 or result[3] <= 0):
        raise ContractError("contract SVG viewBox must be finite with positive dimensions")
    for name in ("width", "height"):
        if name not in attrs:
            continue
        value = attrs[name].strip().casefold()
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(?:px|em|rem|%|vh|vw)?", value)
        if match is None or float(match.group(1)) < 1.0:
            raise ContractError("contract SVG %s must be a positive visible dimension" % name)
    return result  # type: ignore[return-value]


def _inside_view_box(point: Tuple[float, float],
                     view_box: Tuple[float, float, float, float]) -> bool:
    return (view_box[0] - 1e-6 <= point[0] <= view_box[0] + view_box[2] + 1e-6
            and view_box[1] - 1e-6 <= point[1] <= view_box[1] + view_box[3] + 1e-6)


def _validate_text_projection(record: Mapping[str, Any], text: str, path: str,
                              view_box: Tuple[float, float, float, float]) -> None:
    attrs = record["attrs"]
    if any(name in attrs for name in {"dx", "dy", "rotate", "textlength", "lengthadjust"}):
        raise ContractError("%s uses unsupported text-positioning attributes" % path)
    if not {"x", "y", "font-size"} <= set(attrs):
        raise ContractError("%s must declare x, y, and font-size" % path)
    try:
        x = float(attrs["x"])
        y = float(attrs["y"])
        font_value = attrs["font-size"]
        font_size = float(font_value[:-2] if font_value.endswith("px") else font_value)
    except ValueError as error:
        raise ContractError("%s has malformed text geometry" % path) from error
    if (not math.isfinite(x) or not math.isfinite(y) or not math.isfinite(font_size)
            or font_size <= 0 or font_size > 256):
        raise ContractError("%s has invalid text geometry" % path)
    estimated_width = max(1, len(text)) * font_size * 0.6
    anchor = attrs.get("text-anchor", "start").casefold()
    if anchor == "start":
        left, right = x, x + estimated_width
    elif anchor == "middle":
        left, right = x - estimated_width / 2, x + estimated_width / 2
    elif anchor == "end":
        left, right = x - estimated_width, x
    else:
        raise ContractError("%s has an invalid text-anchor" % path)
    top, bottom = y - font_size, y + font_size * 0.25
    if (not _inside_view_box((left, top), view_box)
            or not _inside_view_box((right, bottom), view_box)):
        raise ContractError("%s estimated glyph box lies outside the contract SVG viewBox" % path)


def bind_projection(contract: Mapping[str, Any], parser: ProjectionParser) -> Dict[str, Any]:
    if parser.errors:
        raise ContractError("; ".join(parser.errors))
    css = re.sub(
        r"/\*.*?\*/", "",
        "\n".join("".join(body) for body in parser.styles),
        flags=re.DOTALL,
    )

    def parsed_declarations(source: str) -> List[Tuple[str, str]]:
        result: List[Tuple[str, str]] = []
        for declaration in source.split(";"):
            if not declaration.strip():
                continue
            if ":" not in declaration:
                raise ContractError("contract CSS contains a malformed declaration")
            name, value = declaration.split(":", 1)
            name = name.strip().casefold()
            value = value.strip().casefold().replace("!important", "").strip()
            if not name or not value:
                raise ContractError("contract CSS contains a malformed declaration")
            result.append((name, value))
        return result

    def split_selector(branch: str) -> Optional[List[str]]:
        if "\\" in branch or re.search(r"(^|[^|])(?:\+|~)", branch):
            return None
        pieces: List[str] = []
        current: List[str] = []
        quote = ""
        square = round_depth = 0
        for char in branch.strip():
            if quote:
                current.append(char)
                if char == quote:
                    quote = ""
                continue
            if char in {'\"', "'"}:
                quote = char
                current.append(char)
            elif char == "[":
                square += 1
                current.append(char)
            elif char == "]":
                square -= 1
                current.append(char)
            elif char == "(":
                round_depth += 1
                current.append(char)
            elif char == ")":
                round_depth -= 1
                current.append(char)
            elif not square and not round_depth and (char.isspace() or char == ">"):
                if current:
                    pieces.append("".join(current))
                    current = []
            else:
                current.append(char)
            if square < 0 or round_depth < 0:
                return None
        if quote or square or round_depth:
            return None
        if current:
            pieces.append("".join(current))
        return pieces or None

    def compound_matches(compound: str, node: Mapping[str, Any]) -> Optional[bool]:
        if ":not(" in compound.casefold():
            return None
        # Pseudo-classes/elements only narrow a selector. Ignoring them is a
        # conservative over-approximation once their balanced syntax was checked.
        base = re.sub(r":{1,2}[A-Za-z_-][A-Za-z0-9_-]*(?:\([^)]*\))?", "", compound)
        attrs = node["attrs"]
        attribute_pattern = re.compile(
            r"\[\s*([A-Za-z_:][A-Za-z0-9_:.-]*)\s*"
            r"(?:(~=|\|=|\^=|\$=|\*=|=)\s*(?:\"([^\"]*)\"|'([^']*)'|([^\]\s]+)))?\s*\]"
        )
        for match in attribute_pattern.finditer(base):
            name, operator, double, single, bare = match.groups()
            actual = attrs.get(name.casefold())
            if actual is None:
                return False
            if operator:
                expected = double if double is not None else single if single is not None else bare
                if operator == "=" and actual != expected:
                    return False
                if operator == "~=" and expected not in actual.split():
                    return False
                if operator == "|=" and actual != expected and not actual.startswith(expected + "-"):
                    return False
                if operator == "^=" and not actual.startswith(expected):
                    return False
                if operator == "$=" and not actual.endswith(expected):
                    return False
                if operator == "*=" and expected not in actual:
                    return False
        base = attribute_pattern.sub("", base)
        if "[" in base or "]" in base:
            return None
        ids = re.findall(r"#([A-Za-z_][A-Za-z0-9_-]*)", base)
        classes = re.findall(r"\.([A-Za-z_][A-Za-z0-9_-]*)", base)
        if any(item != attrs.get("id") for item in ids):
            return False
        class_names = set(attrs.get("class", "").split())
        if any(item not in class_names for item in classes):
            return False
        base = re.sub(r"#[A-Za-z_][A-Za-z0-9_-]*", "", base)
        base = re.sub(r"\.[A-Za-z_][A-Za-z0-9_-]*", "", base).strip()
        if base and base != "*":
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", base):
                return None
            if base.casefold() != node["tag"]:
                return False
        return True

    def selector_matches(branch: str, path: List[Dict[str, Any]]) -> bool:
        compounds = split_selector(branch)
        if compounds is None:
            return True
        node_index = len(path) - 1
        result = compound_matches(compounds[-1], path[node_index])
        if result is None:
            return True
        if not result:
            return False
        for compound in reversed(compounds[:-1]):
            matched = False
            node_index -= 1
            while node_index >= 0:
                result = compound_matches(compound, path[node_index])
                if result is None:
                    return True
                if result:
                    matched = True
                    break
                node_index -= 1
            if not matched:
                return False
        return True

    direct_safe = {"box-sizing", "margin", "padding"}
    ancestor_safe = direct_safe | {
        "align-items", "background", "background-color", "color", "display",
        "font-family", "justify-content", "max-width", "min-height",
        "min-width", "width",
    }

    def unsafe_direct(declarations: List[Tuple[str, str]]) -> bool:
        return any(not name.startswith("--")
                   and name not in direct_safe
                   and not name.startswith("margin-")
                   and not name.startswith("padding-")
                   for name, _ in declarations)

    def unsafe_ancestor(declarations: List[Tuple[str, str]]) -> bool:
        for name, value in declarations:
            if name.startswith("--"):
                continue
            if (name not in ancestor_safe and not name.startswith("margin-")
                    and not name.startswith("padding-")):
                return True
            if name == "display" and (value == "none" or "var(" in value or "calc(" in value):
                return True
            if name in {"width", "min-width", "max-width", "min-height"}:
                compact = re.sub(r"\s+", "", value)
                if "var(" in compact or "calc(" in compact:
                    return True
                dimension = re.fullmatch(
                    r"(?:([0-9]+(?:\.[0-9]+)?)(px|em|rem|%|vh|vw)|auto)",
                    compact,
                )
                if dimension is None or (dimension.group(1) is not None
                                         and float(dimension.group(1)) < 1.0):
                    return True
        return False

    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector, source = match.groups()
        declarations = parsed_declarations(source)
        if not declarations or all(name.startswith("--") for name, _ in declarations):
            continue
        branches = selector.split(",")
        for path in parser.semantic_paths:
            if unsafe_direct(declarations) and any(
                    selector_matches(branch, path) for branch in branches):
                raise ContractError(
                    "CSS cannot restyle a contract projection; use SVG attributes"
                )
            if unsafe_ancestor(declarations):
                for index in range(len(path) - 1):
                    if any(selector_matches(branch, path[:index + 1])
                           for branch in branches):
                        raise ContractError(
                            "CSS cannot hide or transform a contract projection ancestor"
                        )
    if len(parser.contracts) != 1:
        raise ContractError("diagram HTML must contain exactly one Diagram Contract")
    marked_svgs = [record for record in parser.svg_records if record["markers"]]
    if len(marked_svgs) != 1 or not marked_svgs[0]["rendered"]:
        raise ContractError("contract projections must share one rendered SVG")
    view_box = _svg_view_box(marked_svgs[0]["attrs"])
    if marked_svgs[0]["titles"] != [contract["title"]]:
        raise ContractError("accessible SVG title differs from the Diagram Contract title")
    if parser.html_titles != [contract["title"]]:
        raise ContractError("HTML title differs from the Diagram Contract title")
    script = parser.contracts[0]
    if set(script["attrs"]) != {"type", "data-diagram-contract"} or script["attrs"].get("type") != "application/json":
        raise ContractError("Diagram Contract script must carry only type=application/json and data-diagram-contract")
    element_ids = {item["id"] for item in contract["elements"]}
    relationship_ids = {item["id"] for item in contract["relationships"]}
    if set(parser.elements) != element_ids or any(len(items) != 1 for items in parser.elements.values()):
        raise ContractError("rendered element IDs do not exactly match the Diagram Contract")
    if set(parser.element_labels) != element_ids or any(len(items) != 1 for items in parser.element_labels.values()):
        raise ContractError("rendered element-label IDs do not exactly match the Diagram Contract")
    expected_stereotypes = {item["id"] for item in contract["elements"] if item.get("stereotype")}
    if (set(parser.element_stereotypes) != expected_stereotypes
            or any(len(items) != 1 for items in parser.element_stereotypes.values())):
        raise ContractError("rendered element-stereotype IDs do not exactly match the Diagram Contract")
    if set(parser.relationships) != relationship_ids or any(len(items) != 1 for items in parser.relationships.values()):
        raise ContractError("rendered relationship IDs do not exactly match the Diagram Contract")
    expected_labels = {item["id"] for item in contract["relationships"] if item["label"]}
    if set(parser.labels) != expected_labels or any(len(items) != 1 for items in parser.labels.values()):
        raise ContractError("rendered relationship-label IDs do not exactly match the Diagram Contract")

    bounds: Dict[str, Tuple[float, float, float, float]] = {}
    for item in contract["elements"]:
        item_id = item["id"]
        record = parser.elements[item_id][0]
        item_bounds = _parse_numbers(record["bounds"], 4, "element %s bounds" % item_id)
        visible_bounds = _actual_bounds(record, "element %s" % item_id)
        if any(abs(actual - declared) > 1e-6
               for actual, declared in zip(visible_bounds, item_bounds)):
            raise ContractError("element %s bounds differ from its visible geometry" % item_id)
        if item_bounds[2] <= 0 or item_bounds[3] <= 0:
            raise ContractError("element %s bounds must have positive width and height" % item_id)
        if (not _inside_view_box((item_bounds[0], item_bounds[1]), view_box)
                or not _inside_view_box((item_bounds[0] + item_bounds[2],
                                         item_bounds[1] + item_bounds[3]), view_box)):
            raise ContractError("element %s lies outside the contract SVG viewBox" % item_id)
        if parser.element_labels[item_id][0] != item["label"]:
            raise ContractError("element %s label differs from its contract" % item_id)
        _validate_text_projection(
            parser.label_records["element:" + item_id][0],
            item["label"],
            "element %s label" % item_id, view_box,
        )
        if item.get("stereotype"):
            expected = "«%s»" % item["stereotype"]
            if parser.element_stereotypes[item_id][0] != expected:
                raise ContractError("element %s stereotype differs from its contract" % item_id)
            _validate_text_projection(
                parser.label_records["stereotype:" + item_id][0],
                expected,
                "element %s stereotype" % item_id, view_box,
            )
        bounds[item_id] = item_bounds
    for item in contract["elements"]:
        if item.get("parent") and not _contains(bounds[item["parent"]], bounds[item["id"]]):
            raise ContractError("element %s is not contained by parent %s" %
                                (item["id"], item["parent"]))

    routes: Dict[str, List[Tuple[float, float]]] = {}
    for item in contract["relationships"]:
        item_id = item["id"]
        route = _parse_route(parser.relationships[item_id][0]["route"],
                             "relationship %s route" % item_id)
        actual_route = _actual_route(parser.relationships[item_id][0],
                                     "relationship %s" % item_id)
        if (len(actual_route) != len(route)
                or any(not _same_point(actual, declared)
                       for actual, declared in zip(actual_route, route))):
            raise ContractError("relationship %s route differs from its visible geometry" % item_id)
        if not _on_boundary(route[0], bounds[item["source"]]):
            raise ContractError("relationship %s does not start on source %s" %
                                (item_id, item["source"]))
        if not _on_boundary(route[-1], bounds[item["target"]]):
            raise ContractError("relationship %s does not end on target %s" %
                                (item_id, item["target"]))
        if (_segment_enters_bounds(route[0], route[1], bounds[item["source"]])
                or _segment_enters_bounds(route[-2], route[-1], bounds[item["target"]])):
            raise ContractError("relationship %s traverses an endpoint interior" % item_id)
        if any(not _inside_view_box(point, view_box) for point in route):
            raise ContractError("relationship %s lies outside the contract SVG viewBox" % item_id)
        if item["label"] and parser.labels[item_id][0] != item["label"]:
            raise ContractError("relationship %s label differs from its contract" % item_id)
        if item["label"]:
            _validate_text_projection(
                parser.label_records["relationship:" + item_id][0],
                item["label"],
                "relationship %s label" % item_id, view_box,
            )
        routes[item_id] = route
    return {"bounds": bounds, "routes": routes, "view_box": view_box,
            "elements": len(bounds), "relationships": len(routes)}


def composition_report(contract: Mapping[str, Any], binding: Mapping[str, Any]) -> Dict[str, Any]:
    profile = contract["composition_profile"]
    limits = COMPOSITION_PROFILES[profile]
    bounds = binding["bounds"]
    routes = binding["routes"]
    elements = {item["id"]: item for item in contract["elements"]}
    relationships = {item["id"]: item for item in contract["relationships"]}
    violations: List[Dict[str, Any]] = []
    minimum_gap: Optional[float] = None
    ids = sorted(bounds)
    for index, first_id in enumerate(ids):
        for second_id in ids[index + 1:]:
            if _ancestor(first_id, second_id, elements) or _ancestor(second_id, first_id, elements):
                continue
            if _overlap(bounds[first_id], bounds[second_id]):
                violations.append({"code": "ELEMENT_OVERLAP", "element": "%s,%s" % (first_id, second_id)})
                continue
            gap = _gap(bounds[first_id], bounds[second_id])
            minimum_gap = gap if minimum_gap is None else min(minimum_gap, gap)
            if gap + 1e-6 < limits["min_element_gap"]:
                violations.append({"code": "ELEMENT_GAP", "element": "%s,%s" % (first_id, second_id),
                                   "actual": round(gap, 3), "limit": limits["min_element_gap"]})

    total_bends = 0
    max_stretch = 1.0
    shortest_segment: Optional[float] = None
    for item_id, route in routes.items():
        bends = max(0, len(route) - 2)
        total_bends += bends
        lengths = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(route, route[1:])]
        local_shortest = min(lengths)
        shortest_segment = local_shortest if shortest_segment is None else min(shortest_segment, local_shortest)
        direct = abs(route[-1][0] - route[0][0]) + abs(route[-1][1] - route[0][1])
        stretch = 1.0 if direct <= 1e-6 else sum(lengths) / direct
        max_stretch = max(max_stretch, stretch)
        if bends > limits["max_bends_per_relationship"]:
            violations.append({"code": "RELATIONSHIP_BENDS", "element": item_id,
                               "actual": bends, "limit": limits["max_bends_per_relationship"]})
        if stretch > limits["max_route_stretch"] + 1e-6:
            violations.append({"code": "ROUTE_STRETCH", "element": item_id,
                               "actual": round(stretch, 3), "limit": limits["max_route_stretch"]})
        if local_shortest + 1e-6 < limits["min_segment_length"]:
            violations.append({"code": "MICRO_SEGMENT", "element": item_id,
                               "actual": round(local_shortest, 3), "limit": limits["min_segment_length"]})
        relationship = relationships[item_id]
        for element_id in ids:
            if element_id in {relationship["source"], relationship["target"]}:
                continue
            if (_ancestor(element_id, relationship["source"], elements)
                    or _ancestor(element_id, relationship["target"], elements)):
                continue
            if any(_segment_enters_bounds(first, second, bounds[element_id])
                   for first, second in zip(route, route[1:])):
                violations.append({"code": "ROUTE_THROUGH_ELEMENT", "element": "%s@%s" % (item_id, element_id)})
    if total_bends > limits["max_total_bends"]:
        violations.append({"code": "TOTAL_BENDS", "element": "diagram",
                           "actual": total_bends, "limit": limits["max_total_bends"]})

    crossings = 0
    overlaps = 0
    touches = 0
    relationship_ids = sorted(routes)
    for index, first_id in enumerate(relationship_ids):
        first = relationships[first_id]
        for second_id in relationship_ids[index + 1:]:
            second = relationships[second_id]
            pair_overlaps = any(
                _segments_overlap(a, b, c, d)
                for a, b in zip(routes[first_id], routes[first_id][1:])
                for c, d in zip(routes[second_id], routes[second_id][1:])
            )
            if pair_overlaps:
                overlaps += 1
            allowed_touches: List[Tuple[float, float]] = []
            for shared in {first["source"], first["target"]} & {second["source"], second["target"]}:
                first_points = []
                second_points = []
                if first["source"] == shared:
                    first_points.append(routes[first_id][0])
                if first["target"] == shared:
                    first_points.append(routes[first_id][-1])
                if second["source"] == shared:
                    second_points.append(routes[second_id][0])
                if second["target"] == shared:
                    second_points.append(routes[second_id][-1])
                for first_point in first_points:
                    for second_point in second_points:
                        if _same_point(first_point, second_point):
                            allowed_touches.append(first_point)
            pair_touches: List[Tuple[float, float]] = []
            for a, b in zip(routes[first_id], routes[first_id][1:]):
                for c, d in zip(routes[second_id], routes[second_id][1:]):
                    crossings += int(_segment_crossing(a, b, c, d))
                    if not pair_overlaps:
                        for point in _segment_touch_points(a, b, c, d):
                            if (not any(_same_point(point, allowed) for allowed in allowed_touches)
                                    and not any(_same_point(point, known) for known in pair_touches)):
                                pair_touches.append(point)
            touches += int(bool(pair_touches))
    if overlaps:
        violations.append({"code": "RELATIONSHIP_OVERLAP", "element": "diagram",
                           "actual": overlaps, "limit": 0})
    if crossings > limits["max_crossings"]:
        violations.append({"code": "RELATIONSHIP_CROSSINGS", "element": "diagram",
                           "actual": crossings, "limit": limits["max_crossings"]})
    if touches:
        violations.append({"code": "RELATIONSHIP_TOUCH", "element": "diagram",
                           "actual": touches, "limit": 0})

    attachments: Dict[str, List[Tuple[str, Tuple[float, float]]]] = {item_id: [] for item_id in ids}
    for relationship_id, relationship in relationships.items():
        attachments[relationship["source"]].append((relationship_id, routes[relationship_id][0]))
        attachments[relationship["target"]].append((relationship_id, routes[relationship_id][-1]))
    for element_id, points in attachments.items():
        for index, (first_id, first_point) in enumerate(points):
            for second_id, second_point in points[index + 1:]:
                separation = math.hypot(first_point[0] - second_point[0],
                                        first_point[1] - second_point[1])
                if separation + 1e-6 < 12.0:
                    violations.append({"code": "ATTACHMENT_FANOUT", "element": "%s:%s,%s" %
                                       (element_id, first_id, second_id),
                                       "actual": round(separation, 3), "limit": 12.0})
    score = max(0, 100 - 12 * len(violations))
    return {
        "profile": profile,
        "ok": not violations,
        "score": score,
        "metrics": {
            "elements": len(bounds),
            "relationships": len(routes),
            "total_bends": total_bends,
            "crossings": crossings,
            "overlapping_route_pairs": overlaps,
            "touching_route_pairs": touches,
            "max_route_stretch": round(max_stretch, 3),
            "minimum_element_gap": round(minimum_gap, 3) if minimum_gap is not None else None,
            "shortest_segment": round(shortest_segment, 3) if shortest_segment is not None else None,
        },
        "limits": dict(limits),
        "violations": violations,
    }


def parse_artifact(path: Path) -> Tuple[str, ProjectionParser, Dict[str, Any]]:
    source = _regular_text(path, MAX_FILE_BYTES)
    parser = ProjectionParser()
    try:
        parser.feed(source)
        parser.close()
    except (ValueError, RecursionError) as error:
        raise ContractError("invalid diagram HTML: %s" % error) from error
    if len(parser.contracts) != 1:
        raise ContractError("diagram HTML must contain exactly one Diagram Contract")
    body = "".join(parser.contracts[0]["body"])
    if len(body.encode("utf-8")) > MAX_CONTRACT_BYTES:
        raise ContractError("embedded Diagram Contract exceeds %d bytes" % MAX_CONTRACT_BYTES)
    contract = validate_contract(_loads_object(body, "Diagram Contract"))
    return source, parser, contract


def check_artifact(path: Path) -> Dict[str, Any]:
    _, parser, contract = parse_artifact(path)
    safety = self_check.verify(path)
    geometry = verify_geometry.check(path)
    if safety or geometry:
        raise ContractError("artifact checks failed: %s" % "; ".join(safety + geometry))
    binding = bind_projection(contract, parser)
    composition = composition_report(contract, binding)
    return {
        "ok": composition["ok"],
        "contract": {
            "schema_version": contract["schema_version"],
            "diagram_type": contract["diagram_type"],
            "semantic_profile": contract["semantic_profile"],
            "composition_profile": contract["composition_profile"],
            "title": contract["title"],
        },
        "binding": {"elements": binding["elements"], "relationships": binding["relationships"]},
        "composition": composition,
    }


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def command_validate(args: argparse.Namespace) -> Dict[str, Any]:
    raw = _loads_object(_regular_text(args.contract, MAX_CONTRACT_BYTES), "Diagram Contract")
    contract = validate_contract(raw)
    return {
        "ok": True,
        "schema_version": 1,
        "diagram_type": contract["diagram_type"],
        "semantic_profile": contract["semantic_profile"],
        "composition_profile": contract["composition_profile"],
        "elements": len(contract["elements"]),
        "relationships": len(contract["relationships"]),
    }


def command_check(args: argparse.Namespace) -> Dict[str, Any]:
    if args.report:
        try:
            input_path = args.html.resolve(strict=True)
            report_path = args.report.resolve(strict=False)
        except OSError as error:
            raise ContractError("cannot resolve report path: %s" % error) from error
        if input_path == report_path:
            raise ContractError("composition report must not replace the HTML artifact")
    report = check_artifact(args.html)
    if args.report:
        _write_report(args.report, report)
    if not report["ok"]:
        raise CompositionError(report)
    return report


def command_inspect(args: argparse.Namespace) -> Dict[str, Any]:
    _, parser, contract = parse_artifact(args.html)
    binding = bind_projection(contract, parser)
    return {
        "ok": True,
        "schema_version": contract["schema_version"],
        "diagram_type": contract["diagram_type"],
        "semantic_profile": contract["semantic_profile"],
        "composition_profile": contract["composition_profile"],
        "title": contract["title"],
        "elements": binding["elements"],
        "relationships": binding["relationships"],
        "model": {
            "elements": contract["elements"],
            "relationships": contract["relationships"],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate a Diagram Contract JSON file")
    validate.add_argument("contract", type=Path)
    validate.set_defaults(function=command_validate)
    check = commands.add_parser("check", help="bind and check a contract-backed HTML diagram")
    check.add_argument("html", type=Path)
    check.add_argument("--report", type=Path)
    check.set_defaults(function=command_check)
    inspect = commands.add_parser("inspect", help="print the bound semantic summary")
    inspect.add_argument("html", type=Path)
    inspect.set_defaults(function=command_inspect)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = args.function(args)
    except CompositionError as error:
        print(json.dumps({
            "ok": False,
            "error": str(error),
            "composition": error.report["composition"],
        }, ensure_ascii=False, allow_nan=False), file=sys.stderr)
        return 1
    except (ContractError, OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)},
                         ensure_ascii=False, allow_nan=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True,
                     allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
