#!/usr/bin/env python3
"""Validate a QTeam isometric map and its repository-bound evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath


SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "assets/template.html"
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_TEXT = 2048
MAX_STRUCTURES = 40
MAX_EVIDENCE = 512
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
EXPECTED_CSP = {
    "default-src": ["'none'"],
    "script-src": ["'unsafe-inline'"],
    "style-src": ["'unsafe-inline'"],
    "img-src": ["data:"],
    "font-src": ["'none'"],
    "connect-src": ["'none'"],
    "media-src": ["'none'"],
    "object-src": ["'none'"],
    "frame-src": ["'none'"],
    "form-action": ["'none'"],
    "base-uri": ["'none'"],
}
REFERENCE_ATTRS = {"src", "href", "xlink:href", "poster", "srcset", "action", "formaction"}
SUMMARY_TAGS = {"h2", "p", "strong", "ul", "li"}


class MapParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.active_head_seen = False
        self.csp: list[tuple[str, bool, bool]] = []
        self.scripts: list[dict] = []
        self.current_script: dict | None = None
        self.styles: list[str] = []
        self.in_style = False
        self.references: list[tuple[str, str, str]] = []
        self.inline_styles: list[str] = []
        self.static_depth = 0
        self.static_text: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.casefold()
        normalized = [(name.casefold(), value or "") for name, value in attrs]
        names = [name for name, _value in normalized]
        data = {name: value for name, value in normalized}
        if tag == "head":
            self.in_head = True
        http_values = [value for name, value in normalized if name == "http-equiv"]
        if tag == "meta" and any(value.casefold() == "content-security-policy" for value in http_values):
            contents = [value for name, value in normalized if name == "content"]
            self.csp.append((contents[0] if len(contents) == 1 else "", self.in_head, len(names) != len(set(names))))
            if self.active_head_seen:
                self.csp[-1] = (self.csp[-1][0], False, self.csp[-1][2])
        elif self.in_head and (
            tag in {"script", "style", "link", "img", "iframe", "object"}
            or any(name in REFERENCE_ATTRS for name, _value in normalized)
        ):
            self.active_head_seen = True
        for name, value in normalized:
            if name in REFERENCE_ATTRS:
                self.references.append((tag, name, value))
            if name == "style":
                self.inline_styles.append(value)
        if tag == "script":
            self.current_script = {"attrs": normalized, "body": [], "closed": False}
            self.scripts.append(self.current_script)
        if tag == "style":
            self.in_style = True
        if self.static_depth:
            self.static_depth += 1
        elif data.get("id") == "isometric-static-summary":
            self.static_depth = 1
        self.stack.append(tag)

    def handle_endtag(self, tag) -> None:
        tag = tag.casefold()
        if tag == "head":
            self.in_head = False
        if tag == "style":
            self.in_style = False
        if tag == "script" and self.current_script is not None:
            self.current_script["closed"] = True
            self.current_script = None
        if self.static_depth:
            self.static_depth -= 1
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index] == tag:
                del self.stack[index:]
                break

    def handle_data(self, value) -> None:
        if self.current_script is not None:
            self.current_script["body"].append(value)
        if self.in_style:
            self.styles.append(value)
        if self.static_depth:
            self.static_text.append(value)


class SummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.stack: list[str] = []
        self.frames: list[list] = []
        self.counts = {tag: 0 for tag in SUMMARY_TAGS}
        self.texts = {tag: [] for tag in SUMMARY_TAGS}
        self.shape: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.casefold()
        if tag not in SUMMARY_TAGS:
            self.errors.append(f"static summary contains unauthorized <{tag}> markup")
            return
        if attrs:
            self.errors.append(f"static summary <{tag}> must not have attributes")
        self.counts[tag] += 1
        self.shape.append((tag, self.stack[-1] if self.stack else None))
        self.stack.append(tag)
        self.frames.append([tag, []])

    def handle_startendtag(self, tag, attrs) -> None:
        self.errors.append(f"static summary contains unauthorized self-closing <{tag}> markup")

    def handle_endtag(self, tag) -> None:
        tag = tag.casefold()
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"static summary has unbalanced </{tag}> markup")
            return
        self.stack.pop()
        _tag, values = self.frames.pop()
        self.texts[tag].append(" ".join("".join(values).split()))

    def handle_data(self, value) -> None:
        for _tag, values in self.frames:
            values.append(value)

    def handle_comment(self, value) -> None:
        self.errors.append("static summary must not contain comments")

    def handle_decl(self, declaration) -> None:
        self.errors.append("static summary must not contain declarations")

    def close(self) -> None:
        super().close()
        if self.stack:
            self.errors.append("static summary has unclosed markup")


class Contract:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def require(self, condition, message) -> bool:
        if not condition:
            self.errors.append(message)
            return False
        return True

    def exact(self, value, keys, label) -> bool:
        return self.require(isinstance(value, dict) and set(value) == set(keys), f"{label} has unknown or missing fields")

    def text(self, value, label, *, maximum=MAX_TEXT, allow_empty=False) -> bool:
        return self.require(
            isinstance(value, str) and len(value) <= maximum and (allow_empty or bool(value.strip())),
            f"{label} must be a bounded{' non-empty' if not allow_empty else ''} string",
        )

    def identifier(self, value, label) -> bool:
        return self.require(isinstance(value, str) and SAFE_ID.fullmatch(value) is not None, f"{label} is not a safe identifier")

    def integer(self, value, minimum, maximum, label) -> bool:
        return self.require(type(value) is int and minimum <= value <= maximum, f"{label} must be an integer in {minimum}..{maximum}")

    def evidence_refs(self, value, known, used, label) -> None:
        if not self.require(isinstance(value, list) and 1 <= len(value) <= 32, f"{label} must reference 1..32 evidence IDs"):
            return
        if not self.require(all(isinstance(item, str) for item in value), f"{label} contains a non-string evidence ID"):
            return
        self.require(len(value) == len(set(value)), f"{label} contains duplicate evidence IDs")
        for item in value:
            if item not in known:
                self.errors.append(f"{label} does not resolve evidence ID {item!r}")
            else:
                used.add(item)


def parse_document(source: str) -> MapParser:
    parser = MapParser()
    parser.feed(source)
    parser.close()
    return parser


def attrs(script) -> dict[str, str] | None:
    pairs = script["attrs"]
    names = [name for name, _value in pairs]
    if len(names) != len(set(names)):
        return None
    return {name: value for name, value in pairs}


def normalized_script(script) -> str:
    return "".join(script["body"]).replace("\r\n", "\n").replace("\r", "\n").strip()


def skeleton(source: str) -> str:
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    source, data_count = re.subn(
        r'(<script\s+id="isometric-data"\s+type="application/json"\s*>).*?(</script>)',
        r"\1__QTEAM_ISOMETRIC_DATA__\2", source, count=1, flags=re.DOTALL,
    )
    source, summary_count = re.subn(
        r'(<section\s+class="static-summary"\s+id="isometric-static-summary"\s*>).*?(</section>)',
        r"\1__QTEAM_ISOMETRIC_SUMMARY__\2", source, count=1, flags=re.DOTALL,
    )
    if data_count != 1 or summary_count != 1:
        return ""
    return source


def validate_static_summary(source: str, data: dict, contract: Contract) -> None:
    match = re.search(
        r'<section\s+class="static-summary"\s+id="isometric-static-summary"\s*>(.*?)</section>',
        source,
        flags=re.DOTALL,
    )
    if not contract.require(match is not None, "map needs the marked static summary"):
        return
    summary = SummaryParser()
    try:
        summary.feed(match.group(1))
        summary.close()
    except (UnicodeError, RecursionError, ValueError) as exc:
        contract.errors.append(f"static summary markup is invalid: {exc}")
        return
    contract.errors.extend(summary.errors)
    structures = data.get("structures")
    structure_names = [
        item.get("name") for item in structures
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ] if isinstance(structures, list) else []
    contract.require(summary.counts["h2"] == 1, "static summary needs exactly one h2")
    contract.require(summary.counts["p"] == 1, "static summary needs exactly one invariant paragraph")
    contract.require(summary.counts["strong"] == 1, "static summary needs exactly one invariant label")
    contract.require(summary.counts["ul"] == 1, "static summary needs exactly one structure list")
    contract.require(summary.counts["li"] == len(structure_names), "static summary structure count does not match data")
    contract.require(summary.texts["h2"] == [data.get("title")], "static summary title must exactly match data")
    invariant = data.get("overview", {}).get("invariant") if isinstance(data.get("overview"), dict) else None
    contract.require(summary.texts["strong"] == ["Invariant:"], "static summary invariant label must be exact")
    contract.require(summary.texts["p"] == [f"Invariant: {invariant}"], "static summary invariant must exactly match data")
    contract.require(summary.texts["li"] == structure_names, "static summary structure list must exactly match data order")
    expected_shape = [("h2", None), ("p", None), ("strong", "p"), ("ul", None)]
    expected_shape.extend(("li", "ul") for _name in structure_names)
    contract.require(summary.shape == expected_shape, "static summary markup does not match the exact h2/p/strong/ul/li grammar")


def parse_csp(raw: str, contract: Contract) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in raw.split(";"):
        parts = item.strip().split()
        if not parts:
            continue
        name = parts[0].casefold()
        if name in result:
            contract.errors.append(f"content security policy repeats {name}")
        result[name] = [value.casefold() for value in parts[1:]]
    return result


def reference_allowed(attribute: str, value: str) -> bool:
    stripped = value.strip().casefold()
    if attribute == "srcset":
        return False
    return bool(stripped) and (stripped.startswith("#") or stripped.startswith("data:image/"))


def canonical_path(value) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and path.as_posix() == value and all(part not in {"", ".", ".."} for part in path.parts)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repo_file(repo: Path, relative: str) -> Path:
    current = repo
    for part in PurePosixPath(relative).parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"evidence path contains a symlink: {relative}")
    info = current.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"evidence path is not a regular file: {relative}")
    return current


def validate_data(data, contract: Contract, *, template_mode=False, repo: Path | None = None, artifact: Path | None = None) -> None:
    top = {
        "schema_version", "sample_data", "repository", "title", "subtitle", "stats", "groups",
        "structures", "edges", "externals", "trace", "overview", "evidence",
    }
    if not contract.exact(data, top, "data packet"):
        return
    contract.require(data["schema_version"] == 1, "schema_version must be 1")
    contract.require(type(data["sample_data"]) is bool, "sample_data must be boolean")
    contract.require(data["sample_data"] is template_mode, "sample_data must be true only for --template validation")
    repository = data["repository"]
    if contract.exact(repository, {"source_head_sha", "source_dirty", "scope"}, "repository"):
        contract.require(isinstance(repository["source_head_sha"], str) and GIT_OID.fullmatch(repository["source_head_sha"]) is not None, "repository.source_head_sha must be a lowercase Git object ID")
        contract.require(type(repository["source_dirty"]) is bool, "repository.source_dirty must be boolean")
        contract.text(repository["scope"], "repository.scope", maximum=512)
    contract.text(data["title"], "title", maximum=160)
    contract.text(data["subtitle"], "subtitle", maximum=240)

    evidence = data["evidence"]
    if not contract.require(isinstance(evidence, list) and 1 <= len(evidence) <= MAX_EVIDENCE, f"evidence must contain 1..{MAX_EVIDENCE} records"):
        return
    evidence_ids: set[str] = set()
    sources: list[tuple[str, str]] = []
    for index, item in enumerate(evidence):
        label = f"evidence[{index}]"
        if not contract.exact(item, {"id", "claim", "sources", "measurement"}, label):
            continue
        if contract.identifier(item["id"], f"{label}.id"):
            contract.require(item["id"] not in evidence_ids, f"duplicate evidence ID {item['id']!r}")
            evidence_ids.add(item["id"])
        contract.text(item["claim"], f"{label}.claim", maximum=512)
        source_items = item["sources"]
        if contract.require(isinstance(source_items, list) and 1 <= len(source_items) <= 32, f"{label}.sources must contain 1..32 files"):
            seen_paths = set()
            for source_index, source in enumerate(source_items):
                source_label = f"{label}.sources[{source_index}]"
                if not contract.exact(source, {"path", "sha256"}, source_label):
                    continue
                path = source["path"]
                contract.require(canonical_path(path), f"{source_label}.path must be canonical and repository-relative")
                contract.require(isinstance(source["sha256"], str) and SHA256.fullmatch(source["sha256"]) is not None, f"{source_label}.sha256 must be lowercase SHA-256")
                if isinstance(path, str):
                    contract.require(path not in seen_paths, f"{label}.sources contains duplicate path {path!r}")
                    seen_paths.add(path)
                    if canonical_path(path) and isinstance(source["sha256"], str):
                        sources.append((path, source["sha256"]))
        measurement = item["measurement"]
        if measurement is not None and contract.exact(measurement, {"command", "result", "exclusions"}, f"{label}.measurement"):
            contract.text(measurement["command"], f"{label}.measurement.command", maximum=2048)
            contract.text(measurement["result"], f"{label}.measurement.result", maximum=512)
            contract.text(measurement["exclusions"], f"{label}.measurement.exclusions", maximum=1024)

    used_evidence: set[str] = set()
    stats = data["stats"]
    if contract.require(isinstance(stats, list) and len(stats) <= 16, "stats must be an array of at most 16 records"):
        for index, item in enumerate(stats):
            label = f"stats[{index}]"
            if contract.exact(item, {"label", "value", "evidence_ids"}, label):
                contract.text(item["label"], f"{label}.label", maximum=64)
                contract.text(item["value"], f"{label}.value", maximum=64)
                contract.evidence_refs(item["evidence_ids"], evidence_ids, used_evidence, f"{label}.evidence_ids")

    groups = data["groups"]
    group_ids: set[str] = set()
    if contract.require(isinstance(groups, list) and 1 <= len(groups) <= 16, "groups must contain 1..16 records"):
        for index, item in enumerate(groups):
            label = f"groups[{index}]"
            if contract.exact(item, {"id", "name", "color"}, label):
                if contract.identifier(item["id"], f"{label}.id"):
                    contract.require(item["id"] not in group_ids, f"duplicate group ID {item['id']!r}")
                    group_ids.add(item["id"])
                contract.text(item["name"], f"{label}.name", maximum=80)
                contract.require(isinstance(item["color"], str) and COLOR.fullmatch(item["color"]) is not None, f"{label}.color must be #RRGGBB")

    structures = data["structures"]
    structure_ids: set[str] = set()
    structure_codes: set[str] = set()
    rectangles: list[tuple[str, int, int, int, int]] = []
    if contract.require(isinstance(structures, list) and 1 <= len(structures) <= MAX_STRUCTURES, f"structures must contain 1..{MAX_STRUCTURES} records"):
        fields = {"id", "code", "name", "group", "size", "position", "footprint", "height", "kind", "what", "how", "talks", "evidence_ids", "children"}
        for index, item in enumerate(structures):
            label = f"structures[{index}]"
            if not contract.exact(item, fields, label):
                continue
            if contract.identifier(item["id"], f"{label}.id"):
                contract.require(item["id"] not in structure_ids, f"duplicate structure ID {item['id']!r}")
                structure_ids.add(item["id"])
            if contract.require(isinstance(item["code"], str) and re.fullmatch(r"[A-Z0-9]{2}", item["code"]) is not None, f"{label}.code must be two uppercase letters/digits"):
                contract.require(item["code"] not in structure_codes, f"duplicate structure code {item['code']!r}")
                structure_codes.add(item["code"])
            contract.text(item["name"], f"{label}.name", maximum=96)
            contract.require(item["group"] in group_ids if isinstance(item["group"], str) else False, f"{label}.group does not resolve")
            if contract.exact(item["size"], {"value", "unit"}, f"{label}.size"):
                contract.integer(item["size"]["value"], 0, 10**9, f"{label}.size.value")
                contract.require(isinstance(item["size"]["unit"], str) and item["size"]["unit"] in {"loc", "files", "role"}, f"{label}.size.unit is invalid")
            position = item["position"]
            footprint = item["footprint"]
            if contract.exact(position, {"gx", "gy"}, f"{label}.position") and contract.exact(footprint, {"w", "d"}, f"{label}.footprint"):
                valid = all((contract.integer(position[key], -20, 40, f"{label}.position.{key}") for key in ("gx", "gy")))
                valid = all((contract.integer(footprint[key], 1, 8, f"{label}.footprint.{key}") for key in ("w", "d"))) and valid
                if valid and isinstance(item["id"], str):
                    rectangles.append((item["id"], position["gx"], position["gy"], footprint["w"], footprint["d"]))
            contract.integer(item["height"], 1, 16, f"{label}.height")
            contract.require(isinstance(item["kind"], str) and item["kind"] in {"service", "module", "worker", "library", "store", "role"}, f"{label}.kind is invalid")
            contract.text(item["what"], f"{label}.what", maximum=640)
            contract.text(item["how"], f"{label}.how", maximum=1024)
            contract.evidence_refs(item["evidence_ids"], evidence_ids, used_evidence, f"{label}.evidence_ids")
            children = item["children"]
            if contract.require(isinstance(children, list) and len(children) <= 12, f"{label}.children must be an array of at most 12 records"):
                child_ids = set()
                child_codes = set()
                for child_index, child in enumerate(children):
                    child_label = f"{label}.children[{child_index}]"
                    if contract.exact(child, {"id", "code", "name", "what", "how", "evidence_ids"}, child_label):
                        if contract.identifier(child["id"], f"{child_label}.id"):
                            contract.require(child["id"] not in child_ids, f"{label} has duplicate child ID {child['id']!r}")
                            child_ids.add(child["id"])
                        if contract.require(isinstance(child["code"], str) and re.fullmatch(r"[A-Z0-9]{2}", child["code"]) is not None, f"{child_label}.code must be two uppercase letters/digits"):
                            contract.require(child["code"] not in child_codes, f"{label} has duplicate child code {child['code']!r}")
                            child_codes.add(child["code"])
                        contract.text(child["name"], f"{child_label}.name", maximum=96)
                        contract.text(child["what"], f"{child_label}.what", maximum=640)
                        contract.text(child["how"], f"{child_label}.how", maximum=1024)
                        contract.evidence_refs(child["evidence_ids"], evidence_ids, used_evidence, f"{child_label}.evidence_ids")
        for left_index, left in enumerate(rectangles):
            for right in rectangles[left_index + 1:]:
                separated = left[1] + left[3] <= right[1] or right[1] + right[3] <= left[1] or left[2] + left[4] <= right[2] or right[2] + right[4] <= left[2]
                contract.require(separated, f"structure footprints overlap: {left[0]} and {right[0]}")
        for index, item in enumerate(structures):
            if not isinstance(item, dict):
                continue
            talks = item.get("talks")
            label = f"structures[{index}].talks"
            if contract.require(isinstance(talks, list) and len(talks) <= 32 and all(isinstance(value, str) for value in talks), f"{label} must be a bounded string array"):
                contract.require(len(talks) == len(set(talks)), f"{label} contains duplicates")
                for target in talks:
                    contract.require(target in structure_ids, f"{label} does not resolve {target!r}")

    edges = data["edges"]
    outgoing = {identifier: set() for identifier in structure_ids}
    if contract.require(isinstance(edges, list) and len(edges) <= 128, "edges must be an array of at most 128 records"):
        seen_edges = set()
        for index, item in enumerate(edges):
            label = f"edges[{index}]"
            if not contract.exact(item, {"from", "to", "label", "kind", "evidence_ids", "via"}, label):
                continue
            contract.require(item["from"] in structure_ids if isinstance(item["from"], str) else False, f"{label}.from does not resolve")
            contract.require(item["to"] in structure_ids if isinstance(item["to"], str) else False, f"{label}.to does not resolve")
            contract.require(isinstance(item["from"], str) and isinstance(item["to"], str) and item["from"] != item["to"], f"{label} self-loop edges are not supported")
            if isinstance(item["from"], str) and isinstance(item["to"], str) and item["from"] in outgoing and item["to"] in structure_ids:
                outgoing[item["from"]].add(item["to"])
            identity_values = (item.get("from"), item.get("to"), item.get("kind"), item.get("label"))
            identity = identity_values if all(isinstance(value, str) for value in identity_values) else None
            contract.require(identity is not None and identity not in seen_edges, f"duplicate or malformed edge identity {identity_values!r}")
            if identity is not None:
                seen_edges.add(identity)
            contract.text(item["label"], f"{label}.label", maximum=120)
            contract.require(isinstance(item["kind"], str) and item["kind"] in {"flow", "advisory", "build"}, f"{label}.kind is invalid")
            contract.evidence_refs(item["evidence_ids"], evidence_ids, used_evidence, f"{label}.evidence_ids")
            via = item["via"]
            if contract.require(isinstance(via, list) and len(via) <= 8, f"{label}.via must contain at most 8 grid points"):
                for point_index, point in enumerate(via):
                    if contract.require(isinstance(point, list) and len(point) == 2, f"{label}.via[{point_index}] must be [gx,gy]"):
                        contract.integer(point[0], -20, 40, f"{label}.via[{point_index}][0]")
                        contract.integer(point[1], -20, 40, f"{label}.via[{point_index}][1]")
    if isinstance(structures, list):
        for index, item in enumerate(structures):
            if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("talks"), list) and all(isinstance(value, str) for value in item["talks"]):
                contract.require(set(item["talks"]) == outgoing.get(item["id"], set()), f"structures[{index}].talks must exactly match outgoing edge targets")

    externals = data["externals"]
    if contract.require(isinstance(externals, list) and len(externals) <= 24, "externals must be an array of at most 24 records"):
        external_ids = set()
        for index, item in enumerate(externals):
            label = f"externals[{index}]"
            if contract.exact(item, {"id", "name", "target", "label", "position", "evidence_ids"}, label):
                if contract.identifier(item["id"], f"{label}.id"):
                    contract.require(item["id"] not in external_ids and item["id"] not in structure_ids, f"duplicate/colliding external ID {item['id']!r}")
                    external_ids.add(item["id"])
                contract.text(item["name"], f"{label}.name", maximum=96)
                contract.text(item["label"], f"{label}.label", maximum=160)
                contract.require(item["target"] in structure_ids if isinstance(item["target"], str) else False, f"{label}.target does not resolve")
                if contract.exact(item["position"], {"gx", "gy"}, f"{label}.position"):
                    contract.integer(item["position"]["gx"], -20, 40, f"{label}.position.gx")
                    contract.integer(item["position"]["gy"], -20, 40, f"{label}.position.gy")
                contract.evidence_refs(item["evidence_ids"], evidence_ids, used_evidence, f"{label}.evidence_ids")

    trace = data["trace"]
    if contract.require(isinstance(trace, list) and (len(trace) == 0 or 2 <= len(trace) <= 20), "trace must be empty or contain 2..20 steps"):
        for index, item in enumerate(trace):
            label = f"trace[{index}]"
            if contract.exact(item, {"structure_id", "text", "evidence_ids"}, label):
                contract.require(item["structure_id"] in structure_ids if isinstance(item["structure_id"], str) else False, f"{label}.structure_id does not resolve")
                contract.text(item["text"], f"{label}.text", maximum=320)
                contract.evidence_refs(item["evidence_ids"], evidence_ids, used_evidence, f"{label}.evidence_ids")

    overview = data["overview"]
    if contract.exact(overview, {"what", "how", "invariant", "evidence_ids"}, "overview"):
        contract.text(overview["what"], "overview.what", maximum=1024)
        contract.text(overview["how"], "overview.how", maximum=1024)
        contract.text(overview["invariant"], "overview.invariant", maximum=512)
        contract.evidence_refs(overview["evidence_ids"], evidence_ids, used_evidence, "overview.evidence_ids")
    contract.require(used_evidence == evidence_ids, f"evidence ledger has unreferenced IDs: {sorted(evidence_ids - used_evidence)}")

    if repo is not None and isinstance(repository, dict):
        try:
            actual_root = Path(subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL).strip()).resolve()
            if actual_root != repo.resolve():
                contract.errors.append("--repo must name the exact Git worktree root")
            actual_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            source_head = repository.get("source_head_sha")
            artifact_relative = None
            if artifact is not None:
                try:
                    artifact_relative = artifact.resolve().relative_to(repo.resolve()).as_posix()
                except ValueError:
                    pass
            pathspec = ["."]
            if artifact_relative is not None:
                contract.require(not artifact_relative.startswith(".git/"), "map artifact cannot live under .git")
                pathspec.append(f":(exclude,literal){artifact_relative}")
            if actual_head != source_head:
                if artifact_relative is None:
                    contract.errors.append("repository source HEAD does not match the external map")
                else:
                    ancestor = subprocess.run(
                        ["git", "-C", str(repo), "merge-base", "--is-ancestor", str(source_head), actual_head],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    contract.require(ancestor.returncode == 0, "repository source HEAD is not an ancestor of the current map commit")
                    changed = subprocess.check_output(
                        ["git", "-C", str(repo), "diff", "--name-only", "-z", f"{source_head}..{actual_head}", "--", *pathspec]
                    )
                    contract.require(not changed, "commits after repository source HEAD change files other than the map artifact")
            actual_dirty = bool(subprocess.check_output(
                ["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *pathspec]
            ))
            contract.require(actual_dirty is repository.get("source_dirty"), "repository source dirty state does not match the map")
            for relative, expected in sources:
                candidate = repo_file(repo, relative)
                contract.require(file_sha256(candidate) == expected, f"evidence digest changed: {relative}")
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            contract.errors.append(f"repository evidence validation failed: {exc}")


def validate(path: Path, *, template_mode=False, repo=None, forbidden=()) -> tuple[list[str], dict | None]:
    contract = Contract()
    if not path.is_file() or path.is_symlink():
        return ["map must be a regular HTML file"], None
    if path.stat().st_size > MAX_HTML_BYTES:
        return ["map exceeds the 2 MiB artifact limit"], None
    try:
        source = path.read_text(encoding="utf-8")
        template_source = TEMPLATE.read_text(encoding="utf-8")
        parser = parse_document(source)
        canonical = parse_document(template_source)
    except (OSError, UnicodeError, RecursionError) as exc:
        return [f"cannot read map: {exc}"], None
    contract.require(skeleton(source) == skeleton(template_source), "map markup/engine differs from the packaged template; edit only marked data sections")
    contract.require(len(parser.csp) == 1, "map needs exactly one content security policy")
    if len(parser.csp) == 1:
        raw, valid_place, duplicates = parser.csp[0]
        contract.require(valid_place, "content security policy must be inside head before active content")
        contract.require(not duplicates, "content security policy has duplicate attributes")
        contract.require(parse_csp(raw, contract) == EXPECTED_CSP, "content security policy does not match the closed offline policy")
    for tag, attribute, value in parser.references:
        if not reference_allowed(attribute, value):
            contract.errors.append(f"non-self-contained {attribute} on <{tag}>: {value[:80]!r}")
    css_sources = parser.styles + parser.inline_styles
    for css in css_sources:
        if re.search(r"@import\b|(?:-webkit-)?image-set\s*\(", css, re.IGNORECASE):
            contract.errors.append("CSS imports/image-set are not allowed")
        for match in re.finditer(r"url\(\s*(['\"]?)(.*?)\1\s*\)", css, re.IGNORECASE | re.DOTALL):
            value = match.group(2).strip().casefold()
            if not value.startswith(("#", "data:image/")):
                contract.errors.append(f"CSS sidecar/remote resource is not allowed: {value[:80]!r}")
    contract.require(len(parser.scripts) == 2, "map must contain exactly the data script and packaged engine")
    data_scripts = []
    engines = []
    for script in parser.scripts:
        values = attrs(script)
        contract.require(script["closed"] and values is not None, "script has duplicate attributes or is not closed")
        if values == {"id": "isometric-data", "type": "application/json"}:
            data_scripts.append(script)
        elif values == {"data-isometric-engine": ""}:
            engines.append(script)
        else:
            contract.errors.append("map contains an unauthorized script")
    contract.require(len(data_scripts) == 1, "map needs exactly one application/json data script")
    contract.require(len(engines) == 1, "map needs exactly one packaged engine script")
    canonical_engines = [script for script in canonical.scripts if attrs(script) == {"data-isometric-engine": ""}]
    if engines and canonical_engines:
        contract.require(normalized_script(engines[0]) == normalized_script(canonical_engines[0]), "isometric engine differs from the packaged template")
    data = None
    if data_scripts:
        try:
            data = json.loads("".join(data_scripts[0]["body"]))
        except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
            contract.errors.append(f"isometric data is not valid bounded JSON: {exc}")
    if data is not None:
        validate_data(data, contract, template_mode=template_mode, repo=repo, artifact=path)
        if isinstance(data, dict):
            structure_records = data.get("structures")
            validate_static_summary(source, data, contract)
            static = " ".join("".join(parser.static_text).split())
            for label, value in [("title", data.get("title")), ("invariant", data.get("overview", {}).get("invariant") if isinstance(data.get("overview"), dict) else None)]:
                contract.require(isinstance(value, str) and value in static, f"static summary does not mirror {label}")
            if isinstance(structure_records, list):
                for item in structure_records:
                    if isinstance(item, dict) and isinstance(item.get("name"), str):
                        contract.require(item["name"] in static, f"static summary omits structure {item['name']!r}")
            visible = json.dumps(data, ensure_ascii=False, sort_keys=True)
            safety = {
                "email/handle": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+",
                "absolute URL": r"https?://",
                "common API key": r"\b(?:sk-|xox[baprs]-|gh[pousr]_)[A-Za-z0-9_-]+",
                "cloud resource ID": r"\b(?:arn:aws|projects/[a-z0-9-]+|subscriptions/[0-9a-f-]+)\b",
                "mount/credential path": r"(?:/mnt/|/mount/|/Volumes/|/var/run/secrets/)",
            }
            for label, pattern in safety.items():
                if re.search(pattern, visible, re.IGNORECASE):
                    contract.errors.append(f"share-safety scan found {label}")
            for prefix in forbidden:
                if prefix and prefix.casefold() in visible.casefold():
                    contract.errors.append(f"forbidden organization/infrastructure prefix remains: {prefix!r}")
    return contract.errors, data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    parser.add_argument("--repo", type=Path, help="recompute source hashes in this exact Git worktree")
    parser.add_argument("--forbid-prefix", action="append", default=[])
    parser.add_argument("--template", action="store_true", help="validate the packaged sample template")
    args = parser.parse_args()
    if args.template and args.repo:
        parser.error("--template cannot be combined with --repo")
    errors, data = validate(args.html, template_mode=args.template, repo=args.repo, forbidden=args.forbid_prefix)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"FAILED: {len(errors)} error(s)")
        return 1
    print(f"OK: {args.html} has {len(data['structures'])} structures, {len(data['edges'])} edges, {len(data['evidence'])} evidence records")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, RecursionError, ValueError) as exc:
        raise SystemExit(f"error: {exc}")
