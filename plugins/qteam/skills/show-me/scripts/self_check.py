#!/usr/bin/env python3
"""Dependency-free contract checks for Show Me HTML lessons."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path


MAX_BYTES = 2 * 1024 * 1024


class ContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.policies: list[str] = []
        self.policy_errors: list[str] = []
        self.print_invariant_depth = 0
        self.print_invariant_text: list[str] = []
        self.print_invariant_count = 0
        self.print_invariant_hidden = False
        self._hidden_stack: list[tuple[str, bool]] = []
        self._in_head = False
        self._active_head_content_seen = False
        self._in_style = False
        self.styles: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.casefold()
        normalized = [(name.casefold(), value or "") for name, value in attrs]
        data = {name: value for name, value in normalized}
        if tag == "head":
            self._in_head = True
        http_values = [value for name, value in normalized if name == "http-equiv"]
        if tag == "meta" and any(
                value.casefold() == "content-security-policy"
                for value in http_values):
            if not self._in_head:
                self.policy_errors.append(
                    "offline content security policy must be inside head"
                )
            if self._active_head_content_seen:
                self.policy_errors.append(
                    "offline content security policy must precede active head content"
                )
            names = [name for name, _value in normalized]
            if len(names) != len(set(names)):
                self.policy_errors.append(
                    "offline content security policy has duplicate attributes"
                )
            contents = [value for name, value in normalized if name == "content"]
            if len(contents) != 1:
                self.policy_errors.append(
                    "offline content security policy needs exactly one content attribute"
                )
            self.policies.append(contents[0] if len(contents) == 1 else "")
        elif self._in_head and (
                tag in {"script", "style", "link", "img", "iframe", "object"}
                or any(name in {"src", "href", "srcset", "data"}
                       for name, _value in normalized)):
            self._active_head_content_seen = True
        if tag == "style":
            self._in_style = True
        hidden = (self._hidden_stack[-1][1] if self._hidden_stack else False)
        styles = [value for name, value in normalized if name == "style"]
        hidden = hidden or "hidden" in data or data.get("aria-hidden", "").casefold() == "true"
        hidden = hidden or any(re.search(
            r"(?:display\s*:\s*none|visibility\s*:\s*hidden)",
            style, re.IGNORECASE
        ) for style in styles)
        self._hidden_stack.append((tag, hidden))
        classes = data.get("class", "").split()
        if self.print_invariant_depth:
            self.print_invariant_depth += 1
        elif "print-invariant" in classes:
            self.print_invariant_depth = 1
            self.print_invariant_count += 1
            self.print_invariant_hidden = self.print_invariant_hidden or hidden

    def handle_endtag(self, tag) -> None:
        if self.print_invariant_depth:
            self.print_invariant_depth -= 1
        tag = tag.casefold()
        if tag == "head":
            self._in_head = False
        if tag == "style":
            self._in_style = False
        for index in range(len(self._hidden_stack) - 1, -1, -1):
            if self._hidden_stack[index][0] == tag:
                del self._hidden_stack[index:]
                break

    def handle_data(self, data) -> None:
        if self._in_style:
            self.styles.append(data)
        if self.print_invariant_depth:
            self.print_invariant_text.append(data)


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: self_check.py <lesson-show-me.html>")
    path = Path(sys.argv[1])
    if not path.is_file() or path.is_symlink():
        fail("lesson must be a regular HTML file")
    if path.stat().st_size > MAX_BYTES:
        fail("lesson exceeds the 2 MiB self-contained artifact limit")
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        fail(f"lesson is not valid UTF-8: {exc}")

    lowered = source.lower()
    required = {
        "HTML language": '<html lang=',
        "lesson heading": "<h1",
        "native controls": "<button",
        "live narration": 'aria-live="polite"',
        "progress semantics": 'role="progressbar"',
        "reduced motion": "prefers-reduced-motion",
        "keyboard support": "keydown",
        "no-JavaScript fallback": "<noscript",
        "print fallback": "@media print",
    }
    for label, token in required.items():
        if token not in lowered:
            fail(f"missing {label}")

    parser = ContractParser()
    parser.feed(source)
    parser.close()
    if parser.policy_errors:
        fail(parser.policy_errors[0])
    if len(parser.policies) != 1:
        fail("lesson needs exactly one offline content security policy")
    directives = {}
    for raw in parser.policies[0].split(";"):
        parts = raw.strip().split()
        if not parts:
            continue
        name = parts[0].casefold()
        if name in directives:
            fail(f"offline content security policy repeats {name}")
        directives[name] = [value.casefold() for value in parts[1:]]
    expected_policy = {
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
    if directives != expected_policy:
        fail("offline content security policy does not match the closed policy")
    if parser.print_invariant_count != 1 or parser.print_invariant_hidden:
        fail("print fallback needs exactly one visible governing invariant")
    for match in re.finditer(
            r"([^{}]+)\{([^{}]*)\}", "".join(parser.styles), re.DOTALL):
        selector, declarations = match.groups()
        if not any(token in selector for token in
                   (".print-invariant", ".static-summary")):
            continue
        if re.search(
                r"(?:display\s*:\s*none|visibility\s*:\s*hidden)",
                declarations, re.IGNORECASE):
            fail("print fallback CSS hides the governing invariant")
    invariant = " ".join("".join(parser.print_invariant_text).split())
    if not invariant.startswith("Invariant:") or len(invariant) < 20:
        fail("print fallback is missing the governing invariant")

    external = re.compile(
        r"<(?:script|link|img|iframe|audio|video|source|object)\b[^>]*"
        r"(?:src|href|data)\s*=\s*['\"](?:https?:)?//",
        re.IGNORECASE,
    )
    if external.search(source) or re.search(
        r"url\(\s*['\"]?(?:https?:)?//", source, re.IGNORECASE
    ):
        fail("external runtime resources are not allowed")

    network_apis = {
        "fetch API": r"\bfetch\s*\(",
        "XMLHttpRequest API": r"\bXMLHttpRequest\b",
        "WebSocket API": r"\bWebSocket\s*\(",
        "EventSource API": r"\bEventSource\s*\(",
        "sendBeacon API": r"\bsendBeacon\s*\(",
        "Image constructor": r"\bnew\s+Image\s*\(",
        "Worker constructor": r"\bnew\s+(?:Shared)?Worker\s*\(",
        "dynamic import": r"\bimport\s*\(",
        "window.open navigation": r"\bwindow\.open\s*\(",
        "location navigation": (
            r"\b(?:(?:window|document)\.)?location"
            r"(?:(?:\.href)?\s*=|\.(?:assign|replace)\s*\()"
        ),
        "dynamic resource assignment": r"\.(?:src|href|action)\s*=",
        "dynamic resource attribute": (
            r"\bsetAttribute\s*\(\s*['\"](?:src|href|action)['\"]"
        ),
        "CSS import": r"@import\b",
        "external form action": (
            r"<form\b[^>]*\baction\s*=\s*['\"](?:https?:)?//"
        ),
        "external meta refresh": (
            r"<meta\b[^>]*http-equiv\s*=\s*['\"]?refresh['\"]?[^>]*"
            r"content\s*=\s*['\"][^'\"]*url\s*=\s*(?:https?:)?//"
        ),
    }
    for label, pattern in network_apis.items():
        if re.search(pattern, source, re.IGNORECASE):
            fail(f"external runtime resources are not allowed ({label})")

    forbidden = {
        "inline event handler": r"\son[a-z]+\s*=",
        "innerHTML sink": r"\binnerHTML\b",
        "insertAdjacentHTML sink": r"\binsertAdjacentHTML\b",
        "document.write sink": r"\bdocument\.write\b",
        "eval sink": r"\beval\s*\(",
        "Function constructor": r"\bnew\s+Function\b",
    }
    for label, pattern in forbidden.items():
        if re.search(pattern, source, re.IGNORECASE):
            fail(f"forbidden {label}")

    if "textcontent" not in lowered:
        fail("renderer must use textContent for dynamic narration")
    if re.search(r"<[^>]+\bautoplay\b", source, re.IGNORECASE):
        fail("autoplay media is not allowed")
    print(f"ok: {path}")


if __name__ == "__main__":
    main()
