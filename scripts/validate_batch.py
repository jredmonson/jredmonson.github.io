#!/usr/bin/env python3
"""
JRBP publish gate for jredmonson.github.io - run this against a batch of new
topic entries BEFORE merging them into queue/topic-queue.json.

Usage:
    python3 scripts/validate_batch.py path/to/new_batch.json

new_batch.json shape: {"topics": [ {...topic entries in the same shape as
queue/topic-queue.json topics...} ]}

Checks (per JRBP Step 3, adapted for a pre-generation queue rather than
post-generation content, since articles here are written by Claude at
publish time, not authored up front):
  - required fields present on every topic
  - no duplicate `id` within the batch or against the existing queue
  - every `type: cluster` topic has a `cluster` key, and either a `pillar`
    topic with the same `cluster` key exists in this batch or is already
    published in the existing queue (never publish a cluster before its
    pillar is live)
  - every `type: cluster` topic has an `anchor` (short link text the pillar
    uses when linking down to it - required for the Service Funnel Stack /
    dense interlinking pattern to render correctly)
  - every money_page_links[].url and next_step_link.url resolves to a
    KNOWN_ROUTES entry
  - batch size is capped (default 500, matching JRBP's own guidance to
    launch no more than 100-500 URLs at a time)

Exits non-zero and prints exactly what failed if anything's wrong.
"""
import json
import sys

QUEUE_PATH = "queue/topic-queue.json"
BATCH_SIZE_CAP = 500

REQUIRED_FIELDS = [
    "id", "status", "category", "title", "keyphrase", "affiliate_link",
    "pov", "target_question", "category_tag", "type",
]

KNOWN_ROUTES = [
    "https://agency.jredmonson.com/local-business-services",
    "https://agency.jredmonson.com/services",
    "https://agency.jredmonson.com/",
    "https://www.gedmonson.com/",
    "https://proaffiliatevault.etsy.com",
    "https://leadsleap.com/?r=jredmonson",
    "https://llpgpro.com/bk8hwzhf/",
    "https://llpgpro.com/6jjpsb3w/",
    "https://llpgpro.com/f6jhrm7v/",
    "https://pictory.ai?fpr=george62",
    "https://jredmonson.github.io/local-business/social-media-management/",
    "https://jredmonson.github.io/local-business/custom-logo-design/",
    "https://jredmonson.github.io/local-business/seo-geo-optimization/",
    "https://jredmonson.github.io/local-business/wordpress-elementor-websites/",
    "https://jredmonson.github.io/local-business/video-editing-youtube-tiktok/",
    "https://jredmonson.github.io/local-business/google-business-profile-optimization/",
    "https://jredmonson.github.io/local-business/seo-content-copywriting/",
    "https://jredmonson.github.io/local-business/ugc-video-production/",
    "https://jredmonson.github.io/local-business/local-seo-citations/",
    "https://jredmonson.github.io/local-business/wordpress-development-agency/",
    "https://jredmonson.github.io/local-business/affordable-marketing-agency-central-texas/",
    "https://jredmonson.github.io/local-business/marketing-agency-near-me-central-texas/",
    "https://jredmonson.github.io/local-business/lead-generation-experts-central-texas/",
    "https://jredmonson.github.io/local-business/industry-specific-marketing-central-texas/",
    "https://jredmonson.github.io/local-business/local-pack-ranking-expert-central-texas/",
]


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/validate_batch.py path/to/new_batch.json")
        sys.exit(2)

    batch = load_json(sys.argv[1])
    existing = load_json(QUEUE_PATH)

    errors = []
    new_topics = batch.get("topics", [])

    if len(new_topics) > BATCH_SIZE_CAP:
        errors.append(f"Batch has {len(new_topics)} topics, over the {BATCH_SIZE_CAP} cap - split it up.")

    existing_ids = {t["id"] for t in existing["topics"]}
    seen_ids = set()

    published_pillars = {
        t["cluster"] for t in existing["topics"]
        if t.get("type") == "pillar" and t.get("status") == "published" and t.get("cluster")
    }
    batch_pillars = {
        t["cluster"] for t in new_topics
        if t.get("type") == "pillar" and t.get("cluster")
    }

    for i, t in enumerate(new_topics):
        label = t.get("id", f"[index {i}]")

        missing = [f for f in REQUIRED_FIELDS if not t.get(f)]
        if missing:
            errors.append(f"{label}: missing required fields: {', '.join(missing)}")

        tid = t.get("id")
        if tid:
            if tid in existing_ids:
                errors.append(f"{label}: id already exists in queue/topic-queue.json")
            if tid in seen_ids:
                errors.append(f"{label}: duplicate id within this batch")
            seen_ids.add(tid)

        if t.get("type") == "cluster":
            cluster = t.get("cluster")
            if not cluster:
                errors.append(f"{label}: type=cluster but no `cluster` grouping key set")
            elif cluster not in published_pillars and cluster not in batch_pillars:
                errors.append(f"{label}: cluster '{cluster}' has no pillar published yet and no pillar in this batch - publish/queue the pillar first")
            if not t.get("anchor"):
                errors.append(f"{label}: type=cluster but no `anchor` set - the pillar's Go Deeper block needs this to render correct link text")

        for m in t.get("money_page_links", []):
            if m.get("url") not in KNOWN_ROUTES:
                errors.append(f"{label}: money_page_links target '{m.get('url')}' not in KNOWN_ROUTES (edit this script's KNOWN_ROUTES if it's a real destination)")

        nsl = t.get("next_step_link")
        if nsl and nsl.get("url") not in KNOWN_ROUTES:
            errors.append(f"{label}: next_step_link target '{nsl.get('url')}' not in KNOWN_ROUTES")

    if errors:
        print(f"VALIDATION FAILED - {len(errors)} problem(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print(f"OK - {len(new_topics)} topics pass validation and are safe to merge into {QUEUE_PATH}.")


if __name__ == "__main__":
    main()
