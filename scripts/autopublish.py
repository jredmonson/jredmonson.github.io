#!/usr/bin/env python3
"""
Autopublish script for jredmonson.github.io
Runs inside GitHub Actions: reads topic-queue.json, generates one or more
articles via the Anthropic API, writes Jekyll posts, and updates the queue.
No GitHub PAT is used anywhere - git push relies on the Actions-provided
GITHUB_TOKEN via actions/checkout's built-in credential helper.

JRBP cluster support (Aug 2026): topics may carry `cluster` (a slug grouping
key), `type` (`pillar` or `cluster`), `anchor` (short anchor text a pillar
should use when linking down to this cluster), and `next_step_link` (a
pillar-to-pillar "Service Funnel Stack" cross-link with its own anchor).
Posts sharing a `cluster` value auto-interlink at build time via Liquid in
_layouts/post.html - no per-article manual linking needed. This script
enforces the JRBP rule that a cluster article never publishes before its
pillar is live.

Set PUBLISH_COUNT (env var, default 1) to publish more than one topic in a
single run - useful for pushing out a whole batch (e.g. all 10 pillars) in
one workflow_dispatch instead of triggering the Action once per article.
Each article is checkpointed (queue saved) immediately after it's written,
so a failure partway through a multi-article run doesn't lose earlier
progress or risk double-publishing on retry.
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
    "https://agency.jredmonson.com/services",
    "https://agency.jredmonson.com/",
    "https://www.gedmonson.com/",
    "https://proaffiliatevault.etsy.com",
    "https://leadsleap.com/?r=jredmonson",
    "https://llpgpro.com/bk8hwzhf/",
    "https://llpgpro.com/6jjpsb3w/",
    "https://llpgpro.com/f6jhrm7v/",
    "https://pictory.ai?fpr=george62",
    # Agency service pillars (Service Funnel Stack batch, Aug 2026)
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
]

WORD_COUNT_MIN = {"pillar": 2000, "cluster": 800}

SYSTEM_PROMPT = """You are an automated content writer for JR Edmonson's affiliate \
marketing blog, ProAffiliateVault (https://jredmonson.github.io/). Voice: direct \
practitioner, no fluff, no hype. Audience: affiliate marketers, digital marketers, \
online entrepreneurs, and local business owners evaluating done-for-you services.

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
5. If cluster_links were provided (this is a pillar article), naturally mention \
each one in-prose using its exact given anchor text as the link text, ideally \
one per relevant H2 section rather than clumped together.
6. If next_step_link was provided, one natural transition sentence near the end \
of the article (just before the FAQ or CTA), using its exact given anchor text, \
framing it as the logical next step in the client's buying journey - not a hard \
sell, a "once this is handled, here's what most clients tackle next" framing.
7. "## FAQ" - 4 Q&A pairs formatted as "**Q:** ... **A:** ".
8. Immediately after the FAQ, with no heading above it, a raw <script \
type="application/ld+json"> tag (not in a code block) containing a valid \
FAQPage JSON-LD schema built from the same 4 FAQ pairs.
9. A CTA div styled exactly like this, filled in for the offer:
<div style="background:#f0f7ff;border-left:4px solid #0066cc;padding:1.2em 1.5em;margin:2em 0;border-radius:4px;">
<strong>Ready to get started?</strong><br>
[Compelling 1-sentence hook about the offer]<br><br>
<a href="[AFFILIATE_LINK]" style="background:#0066cc;color:#fff;padding:0.6em 1.2em;border-radius:4px;text-decoration:none;font-weight:bold;">&rarr; [CTA Text]</a>
</div>
10. Always include this secondary CTA line: "**[Grab the Free LeadsLeap Blueprint \
->](https://llpgpro.com/6jjpsb3w/)** - Build your leads system from scratch, free."
11. Final line, disclosure: "*This post contains affiliate links. I may earn a \
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


def cluster_siblings(queue, cluster_key, exclude_id=None):
    """All cluster-type topics (published or not) sharing this cluster key,
    used to build the pillar's cluster_links for the generation prompt."""
    return [
        t for t in queue["topics"]
        if t.get("cluster") == cluster_key and t.get("type") == "cluster"
        and t.get("id") != exclude_id
    ]


def pick_topic(queue):
    offer_streak = queue.get("offer_streak", 0)
    pending = [t for t in queue["topics"] if t.get("status") == "pending"]
    if not pending:
        return None

    def eligible(t):
        # JRBP rule: never publish a cluster article before its pillar is live.
        if t.get("type") == "cluster" and t.get("cluster"):
            return cluster_pillar_is_live(queue, t["cluster"])
        return True

    candidates = [t for t in pending if eligible(t)]
    if not candidates:
        return None

    if offer_streak >= 3:
        for t in candidates:
            if t.get("category") == "educational":
                return t
    for t in candidates:
        if t.get("category") == "offer":
            return t
    return candidates[0]


def generate_article(queue, topic):
    client = anthropic.Anthropic()

    cluster_links = []
    if topic.get("type") == "pillar" and topic.get("cluster"):
        for sib in cluster_siblings(queue, topic["cluster"], exclude_id=topic.get("id")):
            if sib.get("anchor"):
                cluster_links.append({
                    "url": f"https://jredmonson.github.io/{sib['category_tag']}/{sib['id']}/",
                    "anchor": sib["anchor"],
                })

    user_prompt = json.dumps({
        "title": topic["title"],
        "keyphrase": topic["keyphrase"],
        "affiliate_link": topic["affiliate_link"],
        "offer": topic.get("offer"),
        "pov": topic["pov"],
        "target_question": topic["target_question"],
        "type": topic.get("type", "cluster"),
        "money_page_links": topic.get("money_page_links", []),
        "cluster_links": cluster_links,
        "next_step_link": topic.get("next_step_link"),
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


def slot_time(offset_minutes=0):
    hour = datetime.now(timezone.utc).hour
    base = "07:00:00" if hour < 13 else "12:00:00" if hour < 19 else "17:00:00"
    h, m, s = (int(x) for x in base.split(":"))
    total = h * 60 + m + offset_minutes
    h, m = divmod(total % (24 * 60), 60)
    return f"{h:02d}:{m:02d}:{s:02d} -0500"


def yaml_str(s):
    return json.dumps(s, ensure_ascii=False)  # valid YAML double-quoted scalar


def build_front_matter(topic, article, today, offset_minutes):
    lines = [
        "---",
        "layout: post",
        f"title: {yaml_str(topic['title'])}",
        f"date: {today} {slot_time(offset_minutes)}",
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
    nsl = topic.get("next_step_link")
    if nsl:
        lines.append("next_step_link:")
        lines.append(f"  url: {yaml_str(nsl['url'])}")
        lines.append(f"  label: {yaml_str(nsl['label'])}")
    lines.append("---\n")
    return "\n".join(lines)


def build_post(topic, article, today, offset_minutes):
    return build_front_matter(topic, article, today, offset_minutes) + "\n" + article["body_markdown"].strip() + "\n"


def validate_generated(topic, article):
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

    nsl = topic.get("next_step_link")
    if nsl and nsl["url"] not in KNOWN_ROUTES:
        errors.append(f"next_step_link target '{nsl['url']}' is not in KNOWN_ROUTES")

    if topic.get("type") == "cluster" and not topic.get("cluster"):
        errors.append("type=cluster but no `cluster` grouping key set - this article won't interlink to its pillar")

    if errors:
        print("VALIDATION FAILED - not committing this post:")
        for e in errors:
            print(f"  - {e}")
        return False
    return True


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


def publish_one(queue, today, offset_minutes):
    topic = pick_topic(queue)
    if topic is None:
        return None, False

    article = generate_article(queue, topic)
    if not validate_generated(topic, article):
        # Don't publish this one, but don't crash the whole batch either -
        # mark it skipped so a bad generation doesn't block the queue forever.
        topic["status"] = "skipped_validation_failed"
        save_queue(queue)
        return topic, False

    post_content = build_post(topic, article, today, offset_minutes)
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
    return topic, True


def main():
    count = int(os.environ.get("PUBLISH_COUNT", "1"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    queue = load_queue()

    published = 0
    for i in range(count):
        topic, ok = publish_one(queue, today, offset_minutes=i)
        if topic is None:
            print("No more eligible pending topics - stopping early." if published == 0
                  else f"No more eligible pending topics after {published} published - stopping early.")
            break
        if ok:
            published += 1

    if published == 0:
        print("Nothing was published this run.")
        sys.exit(1)

    print(f"Run complete: {published}/{count} requested article(s) published.")


if __name__ == "__main__":
    main()
