#!/usr/bin/env python3
"""
GHL Pipeline Content Gate for jredmonson.github.io — validates a finished
Jekyll post pushed by the external GHL Workflow AI / Make.com publishing
pipeline (see the jrbp-blog-ranking-protocol skill and JR's GHL pipeline
blueprint, Sep 2026) before it's allowed to merge.

This is the automated, always-on version of this repo's own "#1 RULE"
(always run validate_batch.py before pushing) adapted for content that
arrives fully-written from an external system with no supervising
Claude/JR session watching it. It runs in CI via
.github/workflows/validate-ghl-content.yml on every PR touching _posts/**,
and never needs to be re-run manually or updated just because a new batch
is going out.

It checks the actual file bytes rather than a batch.json manifest, because
the GHL pipeline pushes one finished .md file per keyphrase with no
separate JSON alongside it.

Usage: python3 scripts/validate_ghl_post.py <base-ref> <head-ref>
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from validate_batch import (  # noqa: E402
    CONTROL_BYTE_RE,
    DATE_LINE_RE,
    KNOWN_ROUTES,
    UNSUBSTITUTED_PLACEHOLDER_RE,
)

WORD_COUNT_MIN = {"pillar": 2000, "cluster": 800}
GHL_PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}|\{workflow\.[^}]*\}", re.IGNORECASE)


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True).stdout


def changed_post_files(base_ref, head_ref):
    out = sh(f"git diff --name-status {base_ref}...{head_ref} -- _posts")
    added, bad = [], []
    for line in out.splitlines():
        if not line.strip():
            continue
        status, path = line.split("\t", 1)
        if status != "A":
            bad.append((status, path))
        else:
            added.append(path)
    return added, bad


def existing_pillars_by_cluster(base_ref):
    """Which `cluster` values already have a live pillar, per the base ref."""
    pillars = set()
    try:
        files = sh(f"git ls-tree -r --name-only {base_ref} -- _posts").splitlines()
    except subprocess.CalledProcessError:
        return pillars
    for f in files:
        if not f.endswith(".md"):
            continue
        try:
            text = sh(f"git show {base_ref}:{f}")
        except subprocess.CalledProcessError:
            continue
        if re.search(r"^type:\s*pillar\s*$", text, re.MULTILINE):
            m = re.search(r"^cluster:\s*(\S+)\s*$", text, re.MULTILINE)
            if m:
                pillars.add(m.group(1))
    return pillars


def main():
    if len(sys.argv) != 3:
        print("Usage: validate_ghl_post.py <base-ref> <head-ref>")
        sys.exit(1)
    base_ref, head_ref = sys.argv[1], sys.argv[2]

    added, bad = changed_post_files(base_ref, head_ref)
    errors = []
    warnings = []

    for status, path in bad:
        errors.append(
            f'"{path}" has git status "{status}", not "A" (added) — this pipeline may never modify or delete an existing published post.'
        )

    if not added and not bad:
        print("No _posts changes to check.")
        sys.exit(0)

    live_pillars = existing_pillars_by_cluster(base_ref)
    batch_pillars = set()
    for path in added:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        if re.search(r"^type:\s*pillar\s*$", text, re.MULTILINE):
            m = re.search(r"^cluster:\s*(\S+)\s*$", text, re.MULTILINE)
            if m:
                batch_pillars.add(m.group(1))

    for path in added:
        label = f'"{path}"'
        try:
            raw = open(path, "rb").read()
        except OSError as e:
            errors.append(f"{label}: could not read file: {e}")
            continue
        if CONTROL_BYTE_RE.search(raw):
            errors.append(f"{label}: contains NULL/control bytes — corrupted, do not merge.")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            errors.append(f"{label}: not valid UTF-8: {e}")
            continue

        parts = text.split("---", 2)
        if len(parts) < 3:
            errors.append(f"{label}: frontmatter delimiters ('---') missing or malformed.")
            continue
        fm, body = parts[1], parts[2]

        for pat in (UNSUBSTITUTED_PLACEHOLDER_RE, GHL_PLACEHOLDER_RE):
            hits = pat.findall(fm) + pat.findall(body)
            if hits:
                errors.append(
                    f"{label}: unsubstituted template placeholder found: {hits[:3]} — a webhook variable almost certainly failed to fill in."
                )

        date_match = DATE_LINE_RE.search(fm)
        if not date_match:
            errors.append(f"{label}: no 'date:' line in frontmatter.")
        elif not re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+[+-]\d{4}$", date_match.group(1).strip()):
            errors.append(f"{label}: date field does not match 'YYYY-MM-DD HH:MM:SS +HHMM': got {date_match.group(1).strip()!r}")

        for field, pattern in [
            ("title", r"^title:\s*\S"),
            ("layout", r"^layout:\s*\S"),
            ("categories", r"^categories:\s*\S"),
            ("type", r"^type:\s*\S"),
            ("quick_answer", r"^quick_answer:\s*\S"),
        ]:
            if not re.search(pattern, fm, re.MULTILINE):
                errors.append(f"{label}: no non-empty '{field}:' line in frontmatter.")

        type_match = re.search(r"^type:\s*(\S+)\s*$", fm, re.MULTILINE)
        ptype = type_match.group(1) if type_match else None
        if ptype and ptype not in ("pillar", "cluster"):
            errors.append(f"{label}: type '{ptype}' is not 'pillar' or 'cluster'.")

        min_words = WORD_COUNT_MIN.get(ptype, 800)
        word_count = len(body.split())
        if word_count < min_words:
            errors.append(f"{label}: type '{ptype}' body is {word_count} words, below the {min_words}-word minimum.")

        cluster_match = re.search(r"^cluster:\s*(\S+)\s*$", fm, re.MULTILINE)
        cluster_val = cluster_match.group(1) if cluster_match else None
        if ptype == "cluster":
            if not cluster_val:
                errors.append(f"{label}: type 'cluster' but no 'cluster:' key set.")
            elif cluster_val not in live_pillars and cluster_val not in batch_pillars:
                errors.append(
                    f"{label}: cluster '{cluster_val}' has no live pillar yet (and none in this same batch) — JRBP requires the pillar to publish first."
                )

        found_url = False
        for m in re.finditer(r"url:\s*(\S+)", fm):
            found_url = True
            url = m.group(1).strip("\"'")
            if url not in KNOWN_ROUTES:
                errors.append(
                    f"{label}: link target '{url}' is not in KNOWN_ROUTES (scripts/validate_batch.py) — add it there first if it's a real destination, or fix the typo."
                )
        if not found_url:
            errors.append(f"{label}: no money_page_links or next_step_link set — every article must route authority somewhere.")

        if not re.search(r"^faq:\s*\n\s*-\s*question:", fm, re.MULTILINE):
            errors.append(f"{label}: no 'faq:' list with at least one question/answer pair — required for FAQPage schema.")

    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s).\n")
    for w in warnings:
        print(f"  WARN: {w}")
    if errors:
        print("ERRORS (blocking):")
        for e in errors:
            print(f"  - {e}")
        print("\n❌ GHL content gate FAILED.")
        sys.exit(1)
    print("✅ GHL content gate passed.")


if __name__ == "__main__":
    main()
