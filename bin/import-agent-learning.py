#!/usr/bin/env python3
"""qnote-side importer for an agent-team run's learning outbox.

Run from the qnote repository root:

  tools/codex-agent-team-template/bin/import-agent-learning.py <target-repo> <run-id> [--dry-run] [--update]

Reads <target-repo>/.agents/runs/<run-id>/learning-outbox/manifest.json,
verifies the manifest and its source commits, and imports APPROVED items only:

  knowledge → misc/ai/session-knowledge/knowledge/   (or item's destination)
  lessons   → misc/ai/session-knowledge/lessons/     (or item's destination)
  skills    → skills/proposals/<skill-name>/         (never a canonical skill)

Existing files are never overwritten without --update; duplicate titles at the
destination are reported and skipped. This is the only step that writes to
qnote; nothing inside a run can.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

KNOWLEDGE_DIR = "misc/ai/session-knowledge/knowledge"
LESSONS_DIR = "misc/ai/session-knowledge/lessons"
SKILL_PROPOSALS_DIR = "skills/proposals"
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def fail(msg):
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(2)


def safe_under(base, rel):
    """Resolve base/rel and require the result to stay under base.

    The manifest is produced inside an untrusted run: absolute paths, '..'
    components, and 'a/./b' tricks must not escape. Returns None if unsafe.
    """
    rel = str(rel)
    if not rel or rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
        return None
    base = base.resolve()
    p = (base / rel).resolve()
    if p != base and base not in p.parents:
        return None
    return p


def commit_exists(repo, sha):
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0


def extract_section(md_file, section):
    """Return the '## <section>' block from a markdown file (fence-aware)."""
    if not md_file.is_file():
        return None
    lines = md_file.read_text(encoding="utf-8").splitlines(keepends=True)
    out, taking, in_fence = [], False, False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif line.startswith("## ") and not in_fence:
            if taking:
                break
            taking = line[3:].strip() == section
        if taking:
            out.append(line)
    return "".join(out) if out else None


def slugify(title):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target_repo")
    ap.add_argument("run_id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--update", action="store_true",
                    help="allow updating an existing destination file")
    args = ap.parse_args()

    qnote = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True).stdout.strip() or ".").resolve()
    if not (qnote / "misc/ai/session-knowledge").is_dir():
        fail("run this from the qnote repository root")

    target = Path(args.target_repo).expanduser().resolve()
    outbox = target / ".agents" / "runs" / args.run_id / "learning-outbox"
    manifest_file = outbox / "manifest.json"
    if not manifest_file.is_file():
        fail(f"missing {manifest_file}")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"manifest.json is not valid JSON: {e}")

    for key in ("schema_version", "run_id", "project", "items"):
        if key not in manifest:
            fail(f"manifest missing required key '{key}'")
    if manifest["run_id"] != args.run_id:
        fail(f"manifest run_id {manifest['run_id']!r} != requested {args.run_id!r}")
    for sha in manifest.get("source_commits", []):
        if not commit_exists(target, sha):
            fail(f"source commit {sha} not found in {target} — refusing unverifiable outbox")

    imported, skipped = [], []
    for item in manifest["items"]:
        title = item.get("title", "(untitled)")
        if item.get("status") != "approved":
            skipped.append((title, f"status={item.get('status')}"))
            continue
        category = item.get("category")
        project = manifest["project"]

        if category == "skill":
            name = item.get("skill_name") or slugify(title)
            if not name or not SKILL_NAME_RE.match(name):
                skipped.append((title, f"invalid skill name {name!r}"))
                continue
            src = safe_under(outbox, f"skill-proposals/{name}.md")
            if src is None or not src.is_file():
                skipped.append((title, f"missing {name}.md in skill-proposals/"))
                continue
            dst = safe_under(qnote, f"{SKILL_PROPOSALS_DIR}/{name}/{args.run_id}.md")
            content = src.read_text(encoding="utf-8")
        elif category in ("knowledge", "lesson"):
            src = safe_under(outbox, item.get("file", f"{category}.md"))
            if src is None:
                skipped.append((title, "unsafe source file path in manifest"))
                continue
            section = item.get("section", title)
            content = extract_section(src, section)
            if content is None:
                skipped.append((title, f"section '{section}' not found in {src.name}"))
                continue
            dest = item.get("intended_destination")
            if dest:
                dst = safe_under(qnote, dest)
            else:
                slug = slugify(title)
                if not slug:
                    skipped.append((title, "title slugifies to empty — set intended_destination"))
                    continue
                base = KNOWLEDGE_DIR if category == "knowledge" else LESSONS_DIR
                dst = safe_under(qnote, f"{base}/{project}-{slug}.md")
        else:
            skipped.append((title, f"unknown category {category!r}"))
            continue

        if dst is None:
            skipped.append((title, "unsafe destination path escapes qnote"))
            continue
        # canonical-skill guard on the RESOLVED path: anything under skills/
        # must be under skills/proposals/ (kills ../, ./, // bypasses)
        skills_root = (qnote / "skills").resolve()
        proposals_root = (qnote / SKILL_PROPOSALS_DIR).resolve()
        if (skills_root == dst or skills_root in dst.parents) and \
           not (proposals_root == dst or proposals_root in dst.parents):
            skipped.append((title, f"destination {dst.relative_to(qnote)} is inside a "
                                   f"canonical skill area — proposals only"))
            continue

        if dst.exists() and not args.update:
            skipped.append((title, f"exists: {dst.relative_to(qnote)} (use --update)"))
            continue
        # dedupe: same title already present anywhere in the destination dir
        if not dst.exists():
            dup = [p for p in dst.parent.glob("*.md")
                   if p.is_file() and f"## {title}" in p.read_text(encoding="utf-8", errors="ignore")]
            if dup:
                skipped.append((title, f"duplicate title in {dup[0].relative_to(qnote)}"))
                continue

        header = (f"<!-- imported from {manifest['project']} run {args.run_id}, "
                  f"source: {item.get('source', '?')}, confidence: {item.get('confidence', '?')} -->\n")
        if args.dry_run:
            imported.append((title, dst, True))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(header + content, encoding="utf-8")
            imported.append((title, dst, False))

    for title, dst, dry in imported:
        print(f"{'would import' if dry else 'imported'}: {title} -> {dst.relative_to(qnote)}")
    for title, why in skipped:
        print(f"skipped: {title} ({why})")
    print(f"\n{len(imported)} imported, {len(skipped)} skipped"
          f"{' (dry run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
