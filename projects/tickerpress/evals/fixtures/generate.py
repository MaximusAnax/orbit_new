"""Build the committed fixture feeds and the story-group truth file.

Run with::

    python evals/fixtures/generate.py --seed 4242

It reads ``corpus/articles.json`` (68 hand-authored base articles) and
``watchlist.json``, expands the 20 syndication groups into 40 variants with a
seeded RNG, writes the six feed documents under ``feeds/`` and emits
``labels/story_groups.json`` — the first-class M2 truth file (EVALS §2).

This script shares **no code with the engine**. Its only "linguistic" step is a
plain, case-sensitive whole-token regex scan over the watchlist's alias
surfaces, which it uses to *enforce* the three inheritance constraints of
EVALS §2 rather than to decide anything:

1. an alternate headline preserves, for every company, the number of title
   surfaces (so ``title_hit`` and the title mention count are inherited);
2. a dropped sentence contains no watchlist surface;
3. appended boilerplate and decorated URLs contain no watchlist surface.

A violation raises, so a corpus edit cannot silently invalidate the labels.
``evals/test_gates.py::test_fixtures_regenerate_identically`` re-runs this file
into a temp directory and asserts byte-identity with what is committed.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

HERE = Path(__file__).resolve().parent
DEFAULT_SEED = 4242

#: Outlet boilerplate a syndicating outlet appends. Contains no watchlist
#: surface (asserted below).
BOILERPLATE = {
    "wire_one": "Reporting by the markets desk.",
    "wire_two": "Editing by the standards desk.",
    "biz_daily": "Corrections appear at the foot of the page.",
    "tech_ledger": "Sign up for the daily briefing.",
    "market_minute": "Figures are indicative only.",
    "global_desk": "Additional reporting from the bureaux.",
}

TRACKING_SUFFIXES = (
    "utm_source=rss&utm_medium=feed",
    "utm_source=partner&utm_campaign=syndication",
    "utm_source=newsletter&utm_medium=email",
    "utm_source=rss&utm_medium=feed&utm_content=variant",
)


# ---------------------------------------------------------------------------
# the plain surface scan (constraint enforcement only)
# ---------------------------------------------------------------------------


def alias_surfaces(watchlist: dict) -> dict[str, list[str]]:
    """Every matchable surface per ticker, straight from the watchlist file."""

    surfaces: dict[str, list[str]] = {}
    for company in watchlist["companies"]:
        ticker = company["ticker"]
        name = company["name"]
        found = [ticker, f"${ticker}", name]
        # the generated short name: strip a trailing legal form
        parts = name.replace(",", " ").split()
        while parts and parts[-1].strip(".").lower() in {
            "inc",
            "corp",
            "corporation",
            "co",
            "ltd",
            "plc",
            "company",
            "holdings",
            "group",
            "sa",
            "nv",
            "ag",
            "se",
        }:
            parts.pop()
        short = " ".join(parts)
        if short and short != name:
            found.append(short)
        found.extend(alias["text"] for alias in company.get("aliases", []))
        surfaces[ticker] = sorted(set(found), key=lambda text: (-len(text), text))
    return surfaces


def surface_counts(text: str, surfaces: dict[str, list[str]]) -> dict[str, int]:
    """Case-sensitive whole-token occurrences per ticker (longest match wins)."""

    counts: dict[str, int] = {}
    for ticker, forms in surfaces.items():
        taken: list[tuple[int, int]] = []
        for form in forms:
            pattern = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(form)}(?![A-Za-z0-9_])")
            for match in pattern.finditer(text):
                span = (match.start(), match.end())
                if any(span[0] < end and start < span[1] for start, end in taken):
                    continue
                taken.append(span)
        if taken:
            counts[ticker] = len(taken)
    return counts


def has_surface(text: str, surfaces: dict[str, list[str]]) -> bool:
    return bool(surface_counts(text, surfaces))


# ---------------------------------------------------------------------------
# item construction
# ---------------------------------------------------------------------------


@dataclass
class Item:
    """One feed item, base or variant, ready to render."""

    key: str
    base_id: int
    group: int
    feed: str
    title: str
    summary: list[str]
    content: list[str]
    url: str
    guid: str | None
    published_at: datetime
    variant_of: int | None = None
    ops: list[str] = field(default_factory=list)

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.published_at.isoformat(), self.key)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _base_url(host: str, slug: str) -> str:
    return f"https://{host}/{slug}"


def build_items(corpus: dict, watchlist: dict, seed: int) -> list[Item]:
    """Bases plus their seeded variants, with every constraint enforced."""

    surfaces = alias_surfaces(watchlist)
    feeds = {feed["slug"]: feed for feed in corpus["feeds"]}
    articles = {article["id"]: article for article in corpus["articles"]}
    group_of: dict[int, int] = {}
    for group in corpus["groups"]:
        group_of[group["base"]] = group["id"]

    rng = random.Random(seed)
    items: list[Item] = []

    for article in corpus["articles"]:
        base_id = article["id"]
        group = group_of.get(base_id, 100 + base_id)
        feed = feeds[article["feed"]]
        items.append(
            Item(
                key=f"b{base_id:03d}",
                base_id=base_id,
                group=group,
                feed=article["feed"],
                title=article["title"],
                summary=list(article["summary"]),
                content=list(article["content"]),
                url=_base_url(feed["host"], article["slug"]),
                guid=f"{article['feed']}-{base_id:03d}",
                published_at=_parse_iso(article["published_at"]),
            )
        )

    for group in corpus["groups"]:
        base = articles[group["base"]]
        base_surface_counts = surface_counts(base["title"], surfaces)
        for index, variant in enumerate(group["variants"], start=1):
            feed = feeds[variant["feed"]]
            title = rng.choice(base["alt_titles"])
            if surface_counts(title, surfaces) != base_surface_counts:
                raise SystemExit(
                    f"alternate headline for base {base['id']} changes the title surface "
                    f"count: {title!r}"
                )
            ops = ["alt_title"]
            if rng.random() < 0.7:
                title = f"{feed['prefix']} | {title}"
                ops.append("outlet_prefix")

            content = list(base["content"])
            droppable = list(base.get("drop_ok", []))
            # At most one paragraph is dropped, and only from an article long
            # enough that the copy still reads as the same story: a syndicating
            # outlet trims, it does not rewrite (and M2 recall depends on it).
            drop_budget = 1 if len(base["content"]) >= 3 else 0
            drop_count = min(len(droppable), drop_budget, rng.randint(0, 1))
            dropped = sorted(rng.sample(droppable, drop_count), reverse=True)
            for position in dropped:
                if has_surface(content[position], surfaces):
                    raise SystemExit(
                        f"base {base['id']} marks sentence {position} droppable but it "
                        f"contains a watchlist surface"
                    )
                content.pop(position)
            if dropped:
                ops.append(f"drop:{','.join(str(p) for p in sorted(dropped))}")

            boilerplate = BOILERPLATE[variant["feed"]]
            if has_surface(boilerplate, surfaces):
                raise SystemExit(f"boilerplate for {variant['feed']} contains a surface")
            content.append(boilerplate)
            ops.append("boilerplate")

            jitter = timedelta(hours=rng.randint(0, 36), minutes=rng.choice((0, 5, 20, 35, 50)))
            noise = rng.choice(TRACKING_SUFFIXES)
            if variant.get("same_url"):
                url = f"{_base_url(feeds[base['feed']]['host'], base['slug'])}?{noise}"
                ops.append("same_canonical_url")
            else:
                url = f"{_base_url(feed['host'], base['slug'])}?{noise}"
            if has_surface(url, surfaces):
                raise SystemExit(f"decorated url contains a surface: {url}")

            items.append(
                Item(
                    key=f"v{group['id']:03d}-{index}",
                    base_id=base["id"],
                    group=group["id"],
                    feed=variant["feed"],
                    title=title,
                    summary=list(base["summary"]),
                    content=content,
                    url=url,
                    guid=f"{variant['feed']}-g{group['id']:03d}-{index}",
                    published_at=_parse_iso(base["published_at"]) + jitter,
                    variant_of=base["id"],
                    ops=ops,
                )
            )

    return items


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _paragraphs(sentences: list[str]) -> str:
    return "".join(f"<p>{escape(sentence)}</p>" for sentence in sentences)


def render_rss(feed: dict, items: list[Item]) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        "  <channel>",
        f"    <title>{escape(feed['name'])}</title>",
        f"    <link>https://{feed['host']}/</link>",
        "    <description>TickerPress evaluation fixture feed.</description>",
    ]
    for item in items:
        lines.append("    <item>")
        lines.append(f"      <title>{escape(item.title)}</title>")
        lines.append(f"      <link>{escape(item.url)}</link>")
        if feed["guid_style"] == "guid":
            lines.append(f'      <guid isPermaLink="false">{escape(item.guid or "")}</guid>')
        elif feed["guid_style"] == "permalink":
            lines.append(f'      <guid isPermaLink="true">{escape(item.url)}</guid>')
        lines.append(f"      <description>{escape(' '.join(item.summary))}</description>")
        if item.content:
            lines.append(
                f"      <content:encoded><![CDATA[{_paragraphs(item.content)}]]></content:encoded>"
            )
        lines.append(f"      <pubDate>{format_datetime(item.published_at)}</pubDate>")
        lines.append("    </item>")
    lines.extend(["  </channel>", "</rss>", ""])
    return "\n".join(lines)


def render_atom(feed: dict, items: list[Item]) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>{escape(feed['name'])}</title>",
        f"  <id>https://{feed['host']}/</id>",
        f"  <link href={quoteattr('https://' + feed['host'] + '/')}/>",
    ]
    for item in items:
        stamp = item.published_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append("  <entry>")
        lines.append(f"    <id>tag:{feed['host']},2026:{escape(item.guid or item.key)}</id>")
        lines.append(f"    <title>{escape(item.title)}</title>")
        lines.append(f"    <link href={quoteattr(item.url)}/>")
        lines.append(f"    <summary>{escape(' '.join(item.summary))}</summary>")
        if item.content:
            lines.append(f'    <content type="html">{escape(_paragraphs(item.content))}</content>')
        lines.append(f"    <published>{stamp}</published>")
        lines.append(f"    <updated>{stamp}</updated>")
        lines.append("  </entry>")
    lines.extend(["</feed>", ""])
    return "\n".join(lines)


def order_items(items: list[Item]) -> list[Item]:
    """Newest first inside a feed — the usual RSS convention, and deterministic."""

    return sorted(items, key=lambda item: (item.published_at, item.key), reverse=True)


def archived_ids(corpus: dict, items: list[Item]) -> dict[str, int]:
    """Predict the archive's rowids.

    Ingest walks feeds in ascending feed id (FR-2) and items in document order
    (FR-3), so an article's id is a pure function of the committed bytes: feeds
    are registered in the order they appear in ``corpus.feeds``.
    """

    next_id = 1
    ids: dict[str, int] = {}
    for feed in corpus["feeds"]:
        for item in order_items([i for i in items if i.feed == feed["slug"]]):
            ids[item.key] = next_id
            next_id += 1
    return ids


def write_fixtures(out_dir: Path, seed: int = DEFAULT_SEED) -> dict:
    corpus = json.loads((HERE / "corpus" / "articles.json").read_text(encoding="utf-8"))
    watchlist = json.loads((HERE / "watchlist.json").read_text(encoding="utf-8"))
    items = build_items(corpus, watchlist, seed)

    (out_dir / "feeds").mkdir(parents=True, exist_ok=True)
    (out_dir / "labels").mkdir(parents=True, exist_ok=True)

    for feed in corpus["feeds"]:
        feed_items = order_items([item for item in items if item.feed == feed["slug"]])
        render = render_atom if feed["format"] == "atom" else render_rss
        (out_dir / "feeds" / feed["file"]).write_text(render(feed, feed_items), encoding="utf-8")

    ids = archived_ids(corpus, items)
    ordered = sorted(items, key=lambda i: ids[i.key])
    groups = {str(ids[item.key]): item.group for item in ordered}
    base_of = {str(ids[item.key]): item.base_id for item in ordered}
    story_groups = {
        "_comment": [
            "article id -> syndication group id. Emitted by generate.py --seed 4242,",
            "hand-reviewed, committed: this is M2's same_true truth (EVALS §2).",
            "Ids 1-20 are the syndication groups; 100+base_id marks a singleton.",
            "base_of carries the label-inheritance map (archived article -> the base",
            "article whose hand-written labels it inherits, EVALS §2), and",
            "base_article_ids names the archived row of each base article, which is",
            "what M1_amb_base restricts to.",
        ],
        "groups": groups,
        "base_of": base_of,
        "base_article_ids": {
            str(item.base_id): ids[item.key] for item in ordered if item.variant_of is None
        },
    }
    (out_dir / "labels" / "story_groups.json").write_text(
        json.dumps(story_groups, indent=2) + "\n", encoding="utf-8"
    )

    expected = {
        "_comment": [
            "Generator bookkeeping. INFORMATIONAL ONLY - every gate asserts",
            "live-computed values (EVALS §4).",
        ],
        "seed": seed,
        "base_articles": len(corpus["articles"]),
        "variants": len(items) - len(corpus["articles"]),
        "archived_items": len(items),
        "true_same_story_pairs": sum(
            len(group["variants"]) * (len(group["variants"]) + 1) // 2 for group in corpus["groups"]
        ),
        "same_canonical_url_pairs": sum(
            1 for group in corpus["groups"] for v in group["variants"] if v.get("same_url")
        ),
        "items_per_feed": {
            feed["slug"]: sum(1 for item in items if item.feed == feed["slug"])
            for feed in corpus["feeds"]
        },
        "variant_ops": {
            item.key: item.ops for item in sorted(items, key=lambda i: i.key) if item.variant_of
        },
    }
    (out_dir / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the TickerPress eval fixtures.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=HERE)
    args = parser.parse_args()
    summary = write_fixtures(args.out, args.seed)
    print(json.dumps({k: v for k, v in summary.items() if k != "variant_ops"}, indent=2))


if __name__ == "__main__":
    main()
