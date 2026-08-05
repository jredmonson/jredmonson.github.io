#!/usr/bin/env python3
"""
Autopublish script for jredmonson.github.io
Runs inside GitHub Actions: reads topic-queue.json, generates one article
via the Anthropic API, writes the Jekyll post, and updates the queue.
No GitHub PAT is used anywhere - git push relies on the Actions-provided
GITHUB_TOKEN via actions/checkout's built-in credential helper.

JRBP cluster support (Aug 2026): topics may carry `cluster` (a slug grouping
key) and `type` (`pillar` or `cluster`). Posts sharing a `cluster` value
auto-interlink at build time via Liquid in _layouts/post.html - no per-article
manual linking needed. This script enforces the JRBP rule that a cluster
article never publishes before its pillar is live.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
import urllib.request

import anthropic

QUEUE_PATH = "queue/topic-queue.json"
POSTS_DIR = "_posts"
MODEL = "claude-sonnet-5"
INDEXNOW_KEY = "99fd7e5092a742d4bbcdcd761699f345"
INDEXNOW_HOST = "jredmonson.github.io"

KNOWN_ROUTES = [
    "https://agency.jredmonson.com/local-business-services",
    "https://agency.jredmonson.com/",
    "https://proaffiliatevault.etsy.com",
    "https://leadsleap.com/?r=jredmonson",
    "https://llpgpro.com/bk8hwzhf/",
    "https://llpgpro.com/6jjpsb3w/",
    "https://llpgpro.com/f6jhrm7v/",
    "https://pictory.ai?fpr=george62",
]

WORD_COUNT_MIN = {"pillar": 2000, "cluster": 800}

SYSTEM_PROMPT = """You are an automated content writer for JR Edmonson's affiliate \
marketing blog, ProAffiliateVault (https://jredmonson.github.io/). Voice: direct \
practitioner, no fluff, no hype. Audience: affiliate marketers, digital marketers, \
online entrepreneurs.

Honesty rules: No fabricated statistics. No hype words (revolutionize, \
game-changing, unleash). Write as a practitioner with a specific point of view. \
Use "[insert verified stat + source]" as a placeholder if a number would help \
but you don't have a verified source.

Return ONLY a single JSON object (no markdown fences, no commentary) with this \
exact shape:
{
  "excerpt": "2-sentence excerpt using the keyphrase",
  "quick_answer": "a self-contained 40-80 word direct answer to the target_question, written to stand alone with no pronouns needing prior context",
  "body_markdown": "the full article body in Markdown, NOT including YAML front matter and NOT including a Quick Answer block (that is handled separately)"
}

The body_markdown must follow this exact structure:
1. "## Key Takeaways" - 4-5 bullet points of the most extractable facts.
2. Introduction - 2-3 paragraphs establishing the problem/opportunity using the POV, \
opening with the keyphrase in the first sentence.
3. 6-8 H2 sections, each opening with a direct self-contained 40-60 word answer \
before elaborating, plus at least one data table or bullet list per section.
4. If money_page_links were provided, one contextual in-prose link to the most \
relevant money page within the first 200-300 words, using descriptive commercial \
anchor text (never "click here").
5. "## FAQ" - 4 Q&A pairs formatted as "**Q:** ... **A:** ".
6. Immediately after the FAQ, with no heading above it, a raw <script \
type="application/ld+json"> tag (not in a code block) containing a valid \
FAQPage JSON-LD schema built from the same 4 FAQ pairs.
7. A CTA div styled exactly like this, filled in for the offer:
<div style="background:#f0f7ff;border-left:4px solid #0066cc;padding:1.2em 1.5em;margin:2em 0;border-radius:4px;">
<strong>Ready to get started?</strong><br>
[Compelling 1-sentence hook about the offer]<br><br>
<a href="[AFFILIATE_LINK]" style="background:#0066cc;color:#fff;padding:0.6em 1.2em;border-radius:4px;text-decoration:none;font-weight:bold;">&rarr; [CTA Text]</a>
</div>
8. Always include this secondary CTA line: "**[Grab the Free LeadsLeap Blueprint \
->](https://llpgpro.com/6jjpsb3w/)** - Build your leads system from scratch, free."
9. Final line, disclosure: "*This post contains affiliate links. I may earn a \
commission at no extra cost to you.*"

Target length: pillar-type topics 2,500-3,500 words; cluster-type topics \
1,000-1,500 words for body_markdown (quick_answer and excerpt are separate \
and don't count toward this)."""


def load_queue():
    with open(QUEUE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_queue(queue):
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)
        f.write("\n")


def cluster_pillar_is_live(queue, cluster_key):
    """True if a pillar-type topic in this cluster has already been published."""
    for t in queue["topics"]:
        if t.get("cluster") == cluster_key and t.get("type") == "pillar" \
                and t.get("status") == "published":
            return True
    return False


def pick_topic(queue):
    offer_streak = queue.get("offer_streak", 0)
    pending = [t for t in queue["topics"] if t.get("status") == "pending"]
    if not pending:
        print("No pending topics left in the queue.")
        sys.exit(1)

    def eligible(t):
        # JRBP rule: never publish a cluster article before its pillar is live.
        if t.get("type") == "cluster" and t.get("cluster"):
            return cluster_pillar_is_live(queue, t["cluster"])
        return True

    candidates = [t for t in pending if eligible(t)]
    if not candidates:
        print("Pending topics exist but all clusters are waiting on an unpublished pillar.")
        sys.exit(1)

    if offer_streak >= 3:
        for t in candidates:
            if t.get("category") == "educational":
                return t
    for t in candidates:
        if t.get("category") == "offer":
            return t
    return candidates[0]


def generate_article(topic):
    client = anthropic.Anthropic()
    user_prompt = json.dumps({
        "title": topic["title"],
        "keyphrase": topic["keyphrase"],
        "affiliate_link": topic["affiliate_link"],
        "offer": topic.get("offer"),
        "pov": topic["pov"],
        "target_question": topic["target_question"],
        "type": topic.get("type", "cluster"),
        "money_page_links": topic.get("money_page_links", []),
    }, ensure_ascii=False)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    # Some models return a ThinkingBlock before the TextBlock - only text
    # blocks have a .text attribute, so filter for those specifically.
    text_parts = [block.text for block in resp.content if block.type == "text"]
    text = "".join(text_parts).strip()
    text = re.sub(r"^```(json)?", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    return json.loads(text, strict=False)


def slot_time():
    hour = datetime.now(timezone.utc).hour
    if hour < 13:
        return "07:00:00 -0500"
    elif hour < 19:
        return "12:00:00 -0500"
    return "17:00:00 -0500"


def yaml_str(s):
    return json.dumps(s, ensure_ascii=False)  # valid YAML double-quoted scalar


def build_front_matter(topic, article, today):
    lines = [
        "---",
        "layout: post",
        f"title: {yaml_str(topic['title'])}",
        f"date: {today} {slot_time()}",
        f"categories: {topic['category_tag']}",
        "author: JR Edmonson",
        f"excerpt: {yaml_str(article['excerpt'])}",
        f"quick_answer: {yaml_str(article['quick_answer'])}",
        f"type: {topic.get('type', 'cluster')}",
    ]
    if topic.get("cluster"):
        lines.append(f"cluster: {topic['cluster']}")
    money_links = topic.get("money_page_links", [])
    if money_links:
        lines.append("money_page_links:")
        for m in money_links:
            lines.append(f"  - url: {yaml_str(m['url'])}")
            lines.append(f"    label: {yaml_str(m['label'])}")
    lines.append("---\n")
    return "\n".join(lines)


def build_post(topic, article, today):
    return build_front_matter(topic, article, today) + "\n" + article["body_markdown"].strip() + "\n"


def validate_generated(topic, article, post_content):
    """Gate before commit - fail loud instead of publishing something broken."""
    errors = []
    ptype = topic.get("type", "cluster")
    word_count = len(article["body_markdown"].split())
    min_words = WORD_COUNT_MIN.get(ptype, 800)
    if word_count < min_words:
        errors.append(f"body_markdown is {word_count} words, below the {min_words}-word minimum for type={ptype}")

    qa_words = len(article["quick_answer"].split())
    if not (30 <= qa_words <= 100):
        errors.append(f"quick_answer is {qa_words} words, expected roughly 40-80")

    for m in topic.get("money_page_links", []):
        if m["url"] not in KNOWN_ROUTES:
            errors.append(f"money_page_links target '{m['url']}' is not in KNOWN_ROUTES - add it to scripts/autopublish.py if it's a real destination")

    if topic.get("type") == "cluster" and not topic.get("cluster"):
        errors.append("type=cluster but no `cluster` grouping key set - this article won't interlink to its pillar")

    if errors:
        print("VALIDATION FAILED - not committing this post:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)


def ping_indexnow(urls):
    """Notify IndexNow-participating search engines (Bing, Yandex, etc.)
    that these URLs are new or updated, so they crawl without delay."""
    payload = json.dumps({
        "host": INDEXNOW_HOST,
        "key": INDEXNOW_KEY,
        "keyLocation": f"https://{INDEXNOW_HOST}/{INDEXNOW_KEY}.txt",
        "urlList": urls,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.indexnow.org/indexnow", data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"IndexNow ping status: {resp.status}")
    except Exception as e:
        print(f"IndexNow ping failed (continuing anyway): {e}")


def main():
    queue = load_queue()
    topic = pick_topic(queue)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    article = generate_article(topic)
    validate_generated(topic, article, article["body_markdown"])
    post_content = build_post(topic, article, today)

    os.makedirs(POSTS_DIR, exist_ok=True)
    filename = f"{POSTS_DIR}/{today}-{topic['id']}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(post_content)

    topic["status"] = "published"
    topic["date"] = today
    if topic.get("category") == "offer":
        queue["offer_streak"] = queue.get("offer_streak", 0) + 1
    else:
        queue["offer_streak"] = 0
    queue["last_published_date"] = today
    save_queue(queue)

    url = f"https://jredmonson.github.io/{topic['category_tag']}/{topic['id']}/"
    ping_indexnow([url, f"https://{INDEXNOW_HOST}/sitemap.xml"])

    print(f"Published: {topic['id']} - {topic['title']} ({today}) [{topic.get('type', 'cluster')}, cluster={topic.get('cluster')}]")


if __name__ == "__main__":
    main()
