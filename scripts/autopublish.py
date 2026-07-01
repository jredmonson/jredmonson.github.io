#!/usr/bin/env python3
"""
Autopublish script for jredmonson.github.io
Runs inside GitHub Actions: reads topic-queue.json, generates one article
via the Anthropic API, writes the Jekyll post, updates the queue, and
notifies the Pinterest Pin Queue webhook. No GitHub PAT is used anywhere -
git push relies on the Actions-provided GITHUB_TOKEN via actions/checkout's
built-in credential helper.
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
PIN_QUEUE_WEBHOOK = "https://hook.us2.make.com/7t8ye1hj1etbnjgihmr9w5a0yu5akfvy"
MODEL = "claude-sonnet-5"

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
  "body_markdown": "the full article body in Markdown, NOT including YAML front matter"
}

The body_markdown must follow this exact structure:
1. A blockquote Quick Answer: "> **Quick Answer:** " followed by a 40-60 word \
direct answer to the target_question.
2. "## Key Takeaways" - 4-5 bullet points of the most extractable facts.
3. Introduction - 2-3 paragraphs establishing the problem/opportunity using the POV.
4. 6-8 H2 sections, each with 2-3 paragraphs plus at least one data table or \
bullet list.
5. "## FAQ" - 4 Q&A pairs formatted as "**Q:** ... **A:** ...".
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

Target length: 2,000-2,500 words for body_markdown."""


def load_queue():
    with open(QUEUE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_queue(queue):
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)
        f.write("\n")


def pick_topic(queue):
    offer_streak = queue.get("offer_streak", 0)
    pending = [t for t in queue["topics"] if t.get("status") == "pending"]
    if not pending:
        print("No pending topics left in the queue.")
        sys.exit(1)

    if offer_streak >= 3:
        for t in pending:
            if t.get("category") == "educational":
                return t
    for t in pending:
        if t.get("category") == "offer":
            return t
    return pending[0]


def generate_article(topic):
    client = anthropic.Anthropic()
    user_prompt = json.dumps({
        "title": topic["title"],
        "keyphrase": topic["keyphrase"],
        "affiliate_link": topic["affiliate_link"],
        "offer": topic.get("offer"),
        "pov": topic["pov"],
        "target_question": topic["target_question"],
    }, ensure_ascii=False)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = resp.content[0].text.strip()
    text = re.sub(r"^```(json)?", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    return json.loads(text)


def slot_time():
    hour = datetime.now(timezone.utc).hour
    if hour < 13:
        return "07:00:00 -0500"
    elif hour < 19:
        return "12:00:00 -0500"
    return "17:00:00 -0500"


def build_post(topic, article, today):
    front_matter = (
        "---\n"
        f"layout: post\n"
        f"title: \"{topic['title']}\"\n"
        f"date: {today} {slot_time()}\n"
        f"categories: {topic['category_tag']}\n"
        f"author: JR Edmonson\n"
        f"excerpt: \"{article['excerpt']}\"\n"
        "---\n\n"
    )
    return front_matter + article["body_markdown"].strip() + "\n"


def notify_pin_queue(today, title, url):
    payload = json.dumps({
        "source": "GitHub",
        "publish_date": today,
        "post_title": title,
        "destination_url": url,
    }).encode("utf-8")
    req = urllib.request.Request(
        PIN_QUEUE_WEBHOOK, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"Pin queue webhook status: {resp.status}")
    except Exception as e:
        print(f"Pin queue webhook failed (continuing anyway): {e}")


def main():
    queue = load_queue()
    topic = pick_topic(queue)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    article = generate_article(topic)
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
    notify_pin_queue(today, topic["title"], url)

    print(f"Published: {topic['id']} - {topic['title']} ({today})")


if __name__ == "__main__":
    main()
