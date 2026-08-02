"""FR-16 — determinism and hermeticity of the engine.

The cross-process ``PYTHONHASHSEED`` comparison is eval gate M5; these tests
cover the same property inside one process plus the static guarantees the
engine layer must keep (no clock, no filesystem, no network, no randomness).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from tickerpress.adapters.clock import FixedClock
from tickerpress.adapters.feeds_fixture import FixtureFeedSource
from tickerpress.engine.models import Channel
from tickerpress.services import NotifierRegistry, TickerPressService
from tickerpress.store import InMemoryRepository
from tickerpress_testkit import NOW, rss_feed, rss_item

ENGINE_DIR = Path(__file__).resolve().parents[1] / "src" / "tickerpress" / "engine"

FEED_A = rss_feed(
    [
        rss_item(
            guid="a1",
            link="https://a.example/apple-q2?utm_source=rss",
            title="Apple beats March-quarter estimates on services strength",
            description="Apple Inc. (NASDAQ: AAPL) reported quarterly revenue ahead of estimates.",
            pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
        ),
        rss_item(
            guid="a2",
            link="https://a.example/apple-growers",
            title="Apple growers brace for frost",
            description="Orchard owners expect a difficult harvest. Cider makers warn of shortages.",
            pub_date="Sun, 01 Mar 2026 09:10:00 GMT",
        ),
    ]
)
FEED_B = rss_feed(
    [
        rss_item(
            guid="b1",
            link="https://b.example/apple-q2",
            title="Apple tops March-quarter estimates on services",
            description="Apple Inc. (NASDAQ: AAPL) reported quarterly revenue ahead of estimates.",
            pub_date="Sun, 01 Mar 2026 22:00:00 GMT",
        ),
        rss_item(
            guid="b2",
            link="https://b.example/tesla-recall",
            title="Tesla, Inc. recalls 12,000 vehicles",
            description="Tesla, Inc. said the recall covers vehicles built between January and March.",
            pub_date="Mon, 02 Mar 2026 06:05:00 GMT",
        ),
    ]
)


def run_pipeline(tmp_path: Path, tag: str) -> str:
    repository = InMemoryRepository()
    source = FixtureFeedSource(documents={"file:///a.xml": FEED_A, "file:///b.xml": FEED_B})
    service = TickerPressService(
        repository,
        clock=FixedClock(NOW),
        feed_source=source,
        notifiers=NotifierRegistry(outbox_dir=tmp_path / tag),
    )
    service.add_company(
        "AAPL", "Apple Inc.", context_terms=["iphone"], anti_terms=["orchard", "cider"]
    )
    service.add_company("TSLA", "Tesla, Inc.")
    service.add_feed("A Wire", "file:///a.xml")
    service.add_feed("B Daily", "file:///b.xml")
    service.ingest(deliver_alerts=False)
    digest = service.run_digest(Channel.FILE)

    dump = {
        "articles": [
            article.model_dump(mode="json") for article in service.repository.iter_articles()
        ],
        "stories": [story.model_dump(mode="json") for story in service.repository.list_stories()],
        "mentions": [
            mention.model_dump(mode="json")
            for article in service.repository.iter_articles()
            for mention in service.repository.list_mentions(article.id or 0)
        ],
        "appearances": [
            appearance.model_dump(mode="json")
            for appearance in service.repository.list_appearances()
        ],
        "digest": digest.body,
        "explain": [
            {
                "candidates": [
                    item.mention.model_dump(mode="json")
                    for item in service.explain(article_id).candidates
                ],
                "similarity": service.explain(article_id).dedup_similarity,
            }
            for article_id in (1, 2, 3)
        ],
    }
    repository.close()
    return json.dumps(dump, sort_keys=True, indent=2)


def test_fr16_two_runs_from_empty_stores_are_byte_identical(tmp_path) -> None:
    assert run_pipeline(tmp_path, "a") == run_pipeline(tmp_path, "b")


def test_fr16_digest_body_is_stable_across_runs(tmp_path) -> None:
    first = json.loads(run_pipeline(tmp_path, "a"))
    second = json.loads(run_pipeline(tmp_path, "b"))
    assert first["digest"] == second["digest"]
    assert first["digest"] is not None


def test_fr16_outbox_filenames_are_deterministic_under_a_fixed_clock(tmp_path) -> None:
    run_pipeline(tmp_path, "a")
    run_pipeline(tmp_path, "b")
    names_a = sorted(path.name for path in (tmp_path / "a").iterdir())
    names_b = sorted(path.name for path in (tmp_path / "b").iterdir())
    assert names_a == names_b == ["20260302T130000Z-digest-1.md"]


@pytest.mark.parametrize("module", sorted(path.name for path in ENGINE_DIR.glob("*.py")))
def test_fr16_engine_modules_are_pure(module: str) -> None:
    """No clock reads, no filesystem, no network, no randomness under engine/."""

    source = (ENGINE_DIR / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned_modules = {"random", "os", "socket", "urllib.request", "httpx", "requests", "time"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned_modules, f"{module} imports {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in banned_modules, f"{module} imports from {node.module}"
    for forbidden in ("datetime.now(", "datetime.utcnow(", "time.time(", "open(", "Path("):
        assert forbidden not in source, f"{module} uses {forbidden}"


def test_fr16_engine_never_reads_the_lexicons_itself() -> None:
    for path in ENGINE_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "load_lexicons" not in source, f"{path.name} loads lexicons instead of taking them"
        assert "importlib.resources" not in source


def test_fr16_scoring_has_no_hidden_randomness(tmp_path) -> None:
    """Same inputs, different insertion order of unrelated companies."""

    def scores(order: list[tuple[str, str]]) -> list[tuple[str, float]]:
        repository = InMemoryRepository()
        service = TickerPressService(
            repository,
            clock=FixedClock(NOW),
            feed_source=FixtureFeedSource(documents={"file:///a.xml": FEED_A}),
            notifiers=NotifierRegistry(outbox_dir=tmp_path),
        )
        for ticker, name in order:
            service.add_company(ticker, name)
        service.add_feed("A Wire", "file:///a.xml")
        service.ingest(deliver_alerts=False)
        out = [
            (mention.company_ticker, mention.score)
            for article in service.repository.iter_articles()
            for mention in service.repository.list_mentions(article.id or 0)
            if mention.company_ticker == "AAPL"
        ]
        repository.close()
        return out

    forward = scores([("AAPL", "Apple Inc."), ("TSLA", "Tesla, Inc."), ("MSFT", "Microsoft Corp")])
    backward = scores([("MSFT", "Microsoft Corp"), ("TSLA", "Tesla, Inc."), ("AAPL", "Apple Inc.")])
    assert forward == backward
