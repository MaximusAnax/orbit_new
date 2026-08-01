"""M1-M5 and the five naive baselines (EVALS § Metrics, § Naive baselines).

Everything here runs against committed fixtures with offline components only
(`LexicalRouter`, `NullPolisher`, the eval-only `FaultyPolisher`, the in-memory
store). No network, no clock, no randomness. Scoring convention: an abstained
question counts as a **miss**, never as "not applicable".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ethos.corpus import Corpus
from ethos.engine.normalize import normalize
from ethos.engine.router import TopicIndex, build_index, coverage, route, score_topic

TIMESTAMP = "2026-08-01T00:00:00+00:00"


@dataclass(frozen=True)
class Routed:
    """One routing outcome, with the raw signals the gates need."""

    top1: str | None
    top3: tuple[str, ...]
    abstained: bool
    s1: float
    coverage: float


class RouterHarness:
    """FR-3/FR-4 routing over the committed index (the system under test)."""

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.index: TopicIndex = build_index(
            corpus.topics, corpus.router_config, corpus.stopwords
        )

    def route(self, text: str) -> Routed:
        result = route(text, self.index)
        ranked = tuple(entry.topic_id for entry in result.ranked)
        s1 = result.ranked[0].score if result.ranked else 0.0
        return Routed(
            top1=ranked[0] if ranked and not result.abstained else None,
            top3=ranked[:3],
            abstained=result.abstained,
            s1=s1,
            coverage=result.coverage,
        )

    def raw_scores(self, text: str) -> list[tuple[float, str]]:
        tokens = normalize(text, self.corpus.stopwords)
        scored = [(score_topic(self.index, tid, tokens), tid) for tid in self.index.topic_ids]
        return sorted(((s, t) for s, t in scored if s > 0.0), key=lambda st: (-st[0], st[1]))


class BaselineTitleOverlap:
    """`baseline_title_overlap` — rank topics by shared stemmed tokens with the
    topic's title + description; ties lexicographic; never abstains."""

    id = "baseline_title_overlap"

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.docs = {
            topic.id: set(normalize(topic.title, corpus.stopwords))
            | set(normalize(topic.description, corpus.stopwords))
            for topic in corpus.topics
        }

    def route(self, text: str) -> Routed:
        tokens = set(normalize(text, self.corpus.stopwords))
        scored = sorted(
            ((len(tokens & doc), tid) for tid, doc in self.docs.items()),
            key=lambda st: (-st[0], st[1]),
        )
        ranked = tuple(tid for score, tid in scored if score > 0)
        return Routed(
            top1=ranked[0] if ranked else None,
            top3=ranked[:3],
            abstained=False,
            s1=float(scored[0][0]) if scored else 0.0,
            coverage=0.0,
        )


class BaselineS1ZeroAbstain:
    """`baseline_s1_zero_abstain` — full BM25 ranking, abstain iff s1 = 0."""

    id = "baseline_s1_zero_abstain"

    def __init__(self, harness: RouterHarness) -> None:
        self.harness = harness

    def route(self, text: str) -> Routed:
        scored = self.harness.raw_scores(text)
        ranked = tuple(tid for _s, tid in scored)
        s1 = scored[0][0] if scored else 0.0
        abstained = s1 <= 0.0
        return Routed(
            top1=ranked[0] if ranked and not abstained else None,
            top3=ranked[:3],
            abstained=abstained,
            s1=s1,
            coverage=(
                coverage(
                    self.harness.index,
                    ranked[0],
                    normalize(text, self.harness.corpus.stopwords),
                )
                if ranked
                else 0.0
            ),
        )


# --- M1: routing accuracy ---------------------------------------------------


def _tier(questions: list[dict[str, Any]], tier: str) -> list[dict[str, Any]]:
    return [q for q in questions if q.get("tier") == tier]


def m1_tier(router: Any, questions: list[dict[str, Any]]) -> float:
    """#(top1 = truth) / |tier|; an abstention is a miss."""
    if not questions:
        return 0.0
    hits = sum(1 for q in questions if router.route(q["text"]).top1 == q["truth_topic"])
    return hits / len(questions)


def m1_recall_at_3(router: Any, questions: list[dict[str, Any]]) -> float:
    """M1c — truth in top-3; an abstention is a miss (no ranking is shown)."""
    if not questions:
        return 0.0
    hits = 0
    for question in questions:
        routed = router.route(question["text"])
        if not routed.abstained and question["truth_topic"] in routed.top3:
            hits += 1
    return hits / len(questions)


def m1_dual_home(router: Any, questions: list[dict[str, Any]]) -> float:
    """M1d — both truth topics in top-3; an abstention is a miss."""
    if not questions:
        return 0.0
    hits = 0
    for question in questions:
        routed = router.route(question["text"])
        if not routed.abstained and set(question["truth_topics"]) <= set(routed.top3):
            hits += 1
    return hits / len(questions)


def m1_suite(router: Any, fixtures: dict[str, Any]) -> dict[str, float]:
    """Every M1 number in one pass over the committed question sets."""
    routing = fixtures["routing_questions"]
    holdout = fixtures["oblique_holdout"]
    dual = fixtures["ambiguous_questions"]
    direct = m1_tier(router, _tier(routing, "direct"))
    coll = m1_tier(router, _tier(routing, "colloquial"))
    oblique = m1_tier(router, _tier(routing, "oblique"))
    holdout_score = m1_tier(router, holdout)
    return {
        "M1-direct": direct,
        "M1-coll": coll,
        "M1b": oblique,
        "M1b'": holdout_score,
        "M1gap": oblique - holdout_score,
        "M1a": m1_tier(router, routing),
        "M1c": m1_recall_at_3(router, routing),
        "M1d": m1_dual_home(router, dual),
    }


# --- M2: abstention quality -------------------------------------------------


def m2_suite(router: Any, fixtures: dict[str, Any]) -> dict[str, float]:
    """M2a (refusals), M2a_near (hard subset), M2b (false refusals over 260)."""
    oos = fixtures["oos_questions"]
    near = [q for q in oos if q["kind"] == "moral_out_of_taxonomy"]
    in_scope = fixtures["routing_questions"] + fixtures["ambiguous_questions"]
    refused = sum(1 for q in oos if router.route(q["text"]).abstained)
    refused_near = sum(1 for q in near if router.route(q["text"]).abstained)
    false_refusals = sum(1 for q in in_scope if router.route(q["text"]).abstained)
    return {
        "M2a": refused / len(oos) if oos else 0.0,
        "M2a_near": refused_near / len(near) if near else 0.0,
        "M2b": false_refusals / len(in_scope) if in_scope else 0.0,
    }


def c20_scores(harness: RouterHarness, fixtures: dict[str, Any]) -> tuple[list[float], list[float]]:
    """Raw s1 for the out-of-scope set and for the in-scope direct tier (C20)."""
    oos = [harness.raw_scores(q["text"]) for q in fixtures["oos_questions"]]
    direct = [
        harness.raw_scores(q["text"])
        for q in _tier(fixtures["routing_questions"], "direct")
    ]
    top = lambda scored: scored[0][0] if scored else 0.0  # noqa: E731
    return [top(s) for s in oos], [top(s) for s in direct]


def sweep(harness: RouterHarness, fixtures: dict[str, Any], taus, kappas) -> list[dict[str, float]]:
    """(tau, kappa) tradeoff curve printed by `run.py --sweep`."""
    routing = fixtures["routing_questions"]
    holdout = fixtures["oblique_holdout"]
    dual = fixtures["ambiguous_questions"]
    oos = fixtures["oos_questions"]

    def signals(question: str) -> tuple[float, float, str | None]:
        scored = harness.raw_scores(question)
        if not scored:
            return 0.0, 0.0, None
        tokens = normalize(question, harness.corpus.stopwords)
        return scored[0][0], coverage(harness.index, scored[0][1], tokens), scored[0][1]

    cached = {
        "tune": [
            (q["truth_topic"], *signals(q["text"]))
            for q in routing
            if q["tier"] in ("direct", "colloquial")
        ],
        "oblique": [(q["truth_topic"], *signals(q["text"])) for q in _tier(routing, "oblique")],
        "holdout": [(q["truth_topic"], *signals(q["text"])) for q in holdout],
        "dual": [(None, *signals(q["text"])) for q in dual],
        "oos": [(None, *signals(q["text"])) for q in oos],
    }
    rows: list[dict[str, float]] = []
    for tau in taus:
        for kappa in kappas:
            def hit(rows_: list, tau=tau, kappa=kappa) -> float:
                ok = sum(
                    1
                    for truth, s1, cov, top in rows_
                    if not (s1 < tau or cov < kappa) and top == truth
                )
                return ok / len(rows_) if rows_ else 0.0

            def refused(rows_: list, tau=tau, kappa=kappa) -> float:
                ab = sum(1 for _t, s1, cov, _top in rows_ if s1 < tau or cov < kappa)
                return ab / len(rows_) if rows_ else 0.0

            in_scope = cached["tune"] + cached["oblique"] + cached["dual"]
            rows.append(
                {
                    "tau": tau,
                    "kappa": kappa,
                    "tune_acc": hit(cached["tune"]),
                    "M1b": hit(cached["oblique"]),
                    "M1b'": hit(cached["holdout"]),
                    "M2a": refused(cached["oos"]),
                    "M2b": refused(in_scope),
                }
            )
    return rows


# --- composition helpers (shared by M3 and M5) ------------------------------


def compose_unverified(
    corpus: Corpus, topic_id: str, traditions: list[str] | None, forced: bool = True
) -> tuple[Any, str]:
    """Compose + render with FR-8 acceptance bypassed (EVALS M3).

    M3 scores *composition* and M4 scores *the verifier*; running the verifier
    here would make one instrument downstream of the other.
    """
    from ethos.engine.compose import compose_answer, render_text
    from ethos.engine.retrieve import retrieve
    from ethos.models import RoutingEcho

    echo = RoutingEcho(
        topic_id=topic_id,
        confidence=None if forced else 1.0,
        alternates=[],
        forced=forced,
    )
    retrieval = retrieve(corpus, topic_id, traditions)
    body = compose_answer(corpus, topic_id, retrieval, echo)
    titles = {t.id: t.title for t in corpus.topics}
    names = {t.id: t.name for t in corpus.traditions}
    return body, render_text(body, titles, names)


def render_variants(corpus: Corpus, topic_id: str) -> list[tuple[list[str] | None, Any, str]]:
    """The three render variants used by M3 and M5: all / 3-tradition / 1-tradition."""
    covered = [
        tradition.id
        for tradition in corpus.traditions
        if (topic_id, tradition.id) in corpus.position_by_cell
    ]
    filters: list[list[str] | None] = [None, covered[:3], covered[:1]]
    out = []
    for requested in filters:
        body, text = compose_unverified(corpus, topic_id, requested)
        out.append((requested, body, text))
    return out


class BaselineFirstTraditionOnly:
    """`baseline_first_tradition_only` — renders only the first requested
    tradition while computing the agreement map as if unfiltered (M5)."""

    id = "baseline_first_tradition_only"

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus

    def render(self, topic_id: str, requested: list[str] | None) -> tuple[Any, str]:
        from ethos.engine.compose import compose_answer, render_text
        from ethos.engine.retrieve import retrieve
        from ethos.models import RoutingEcho

        echo = RoutingEcho(topic_id=topic_id, confidence=None, alternates=[], forced=True)
        full = retrieve(self.corpus, topic_id, requested)
        clipped = type(full)(
            topic_id=full.topic_id,
            rendered=full.rendered[:1],
            not_covered=full.not_covered,
            filtered_out=full.filtered_out,
        )
        body = compose_answer(self.corpus, topic_id, clipped, echo)
        unfiltered = compose_answer(
            self.corpus, topic_id, retrieve(self.corpus, topic_id, None), echo
        )
        body = body.model_copy(update={"agreement_map": unfiltered.agreement_map})
        titles = {t.id: t.title for t in self.corpus.topics}
        names = {t.id: t.name for t in self.corpus.traditions}
        return body, render_text(body, titles, names)


# --- M3: citation integrity, measured by the independent checker ------------


def m3_answer_set(
    corpus: Corpus, harness: RouterHarness, fixtures: dict[str, Any]
) -> list[tuple[str, str]]:
    """(topic_id, rendered_text) for every answer M3 scores."""
    answers: list[tuple[str, str]] = []
    in_scope = fixtures["routing_questions"] + fixtures["ambiguous_questions"]
    for question in in_scope:
        routed = harness.route(question["text"])
        if routed.top1 is None:
            continue
        _body, text = compose_unverified(corpus, routed.top1, None, forced=False)
        answers.append((routed.top1, text))
    for topic in corpus.topics:
        for _requested, _body, text in render_variants(corpus, topic.id):
            answers.append((topic.id, text))
    return answers


def m3_citation_integrity(
    corpus: Corpus, harness: RouterHarness, fixtures: dict[str, Any], data_dir: Any
) -> tuple[float, list[str]]:
    """M3 = passing citations / max(rendered, expected), over the answer set."""
    from evals import independent_check

    raw = independent_check.load_raw_corpus(data_dir)
    passing = total = 0
    failures: list[str] = []
    for topic_id, text in m3_answer_set(corpus, harness, fixtures):
        ok, denominator = independent_check.score_render(text, raw, topic_id)
        passing += ok
        total += denominator
        if ok != denominator and len(failures) < 10:
            parsed = independent_check.parse_render(text)
            scoped = dict(raw, table=parsed["table"], topic_id=topic_id)
            for citation in parsed["citations"]:
                failures.extend(
                    f"{topic_id}: {reason}"
                    for reason in independent_check.check_citation(citation, scoped)
                )
    return (passing / total if total else 0.0), failures[:10]


def m3_over_corpus_dir(corpus: Corpus, data_dir: Any) -> float:
    """M3 restricted to the forced-topic renders — used by the negative controls,
    which swap in a deliberately broken `data/` directory."""
    from evals import independent_check

    raw = independent_check.load_raw_corpus(data_dir)
    passing = total = 0
    for topic in corpus.topics:
        for _requested, _body, text in render_variants(corpus, topic.id):
            ok, denominator = independent_check.score_render(text, raw, topic.id)
            passing += ok
            total += denominator
    return passing / total if total else 0.0


# --- M4: tamper detection ---------------------------------------------------


def m4_tamper(
    corpus: Corpus, cases: list[dict[str, Any]], verifier: Any = None
) -> dict[str, Any]:
    """M4a recall over mutated cases, M4b false-positive rate over clean ones.

    `verifier` swaps the FR-8 gate the service calls, which is how the two M4
    reference baselines are *measured* through the real pipeline rather than
    asserted (`baseline_no_verifier`, `baseline_reject_all`). It is `None` —
    the real verifier — for the metric itself.
    """
    from ethos.service import EthosService, IntegrityError
    from ethos.store.memory_repo import MemoryRepository

    from evals.faulty_polisher import FaultyPolisher, MutationError

    mutated_rejected = clean_rejected = mutated = clean = 0
    problems: list[str] = []
    for case in cases:
        polisher = FaultyPolisher(case)
        repo = MemoryRepository()
        service = EthosService(corpus, repo, polisher=polisher, verifier=verifier)
        service.init_store(TIMESTAMP)
        topic_id = case["topic_id"]
        traditions = case.get("traditions")
        try:
            result = service.ask(
                case.get("question", f"[{case['id']}]"),
                TIMESTAMP,
                traditions=traditions,
                topic_id=topic_id,
                polish=True,
            )
        except MutationError as exc:
            problems.append(f"{case['id']}: stale fixture — {exc}")
            if polisher.clean:
                clean += 1
            else:
                mutated += 1
            continue
        except IntegrityError as exc:
            # The deterministic fallback itself failed FR-8: nothing was served,
            # so the polish did not survive, but this is a composer/corpus bug
            # (or a reject-all verifier) and must be visible rather than fatal.
            problems.append(f"{case['id']}: deterministic render failed FR-8 — {exc}")
            if polisher.clean:
                clean += 1
                clean_rejected += 1
            else:
                mutated += 1
                mutated_rejected += 1
            continue
        answer = result.answer
        assert answer is not None
        rejected = answer.polish_fell_back
        if polisher.clean:
            clean += 1
            clean_rejected += int(rejected)
            if rejected:
                problems.append(f"{case['id']}: clean case was rejected (false positive)")
            elif not answer.polish_used:
                # a no-op "polish" would score as a pass without exercising check (e)
                problems.append(f"{case['id']}: clean case changed nothing (stale fixture)")
        else:
            mutated += 1
            mutated_rejected += int(rejected)
            if not rejected:
                problems.append(f"{case['id']}: mutation survived verification")
            deterministic, _ = compose_unverified(corpus, topic_id, traditions)
            if rejected and answer.body.model_dump() != deterministic.model_dump():
                problems.append(f"{case['id']}: fallback body differs from the deterministic one")
        if not answer.verified:
            problems.append(f"{case['id']}: persisted an unverified answer")
    return {
        "M4a": mutated_rejected / mutated if mutated else 0.0,
        "M4b": clean_rejected / clean if clean else 0.0,
        "counts": {"mutated": mutated, "clean": clean},
        "problems": problems[:10],
    }


#: Every FR-8 check plus the FR-9 envelope parse-back, each of which EVALS § M4
#: requires at least one mutation class to exercise.
M4_CHECKS = frozenset("abcdefghi") | {"envelope"}
M4_CLEAN_CASES = 20
M4_MUTATED_CASES = 30
M4_MUTATION_CLASSES = 25


def m4_case_composition(corpus: Corpus, cases: list[dict[str, Any]]) -> list[str]:
    """EVALS § M4: the properties of `polish_cases.json` that make M4a/M4b mean
    anything — and which the M4a/M4b *values* cannot detect.

    In particular "M4b is not vacuous" rests on >= 5 clean cases whose mutable
    prose legitimately names a work and a number and is preserved verbatim: a
    check (e) that scanned whole regions instead of changed spans would reject
    them. Delete those five and M4b stays 0.0 with the gate hollowed out.
    """
    import re

    from ethos.engine import envelope as env_mod
    from ethos.engine.verify import PROSE_LOCATOR_SCAN

    from evals.faulty_polisher import FaultyPolisher, MutationError

    problems: list[str] = []
    clean = [case for case in cases if case["mode"] == "clean"]
    mutated = [case for case in cases if case["mode"] != "clean"]
    if len(clean) != M4_CLEAN_CASES:
        problems.append(f"polish_cases: {len(clean)} clean cases, EVALS requires 20")
    if len(mutated) != M4_MUTATED_CASES:
        problems.append(f"polish_cases: {len(mutated)} mutated cases, EVALS requires 30")
    classes = {case["mode"] for case in mutated}
    if len(classes) != M4_MUTATION_CLASSES:
        problems.append(
            f"polish_cases: {len(classes)} distinct mutation classes, EVALS requires 25"
        )
    exercised = {
        part.strip()
        for case in mutated
        for part in str(case.get("check", "")).split("+")
        if part.strip()
    }
    for missing in sorted(M4_CHECKS - exercised):
        problems.append(f"polish_cases: no mutation class exercises FR-8 check ({missing})")
    for unknown in sorted(exercised - M4_CHECKS):
        problems.append(f"polish_cases: case names unknown FR-8 check ({unknown})")

    scan = [re.compile(pattern) for pattern in PROSE_LOCATOR_SCAN]
    work_number = length_priced = 0
    for case in clean:
        body, _text = compose_unverified(corpus, case["topic_id"], case.get("traditions"))
        pre = env_mod.serialize(body, case.get("traditions"))
        try:
            post = FaultyPolisher(case).polish(pre)
        except MutationError as exc:
            problems.append(f"polish_cases: clean case {case['id']} is stale — {exc}")
            continue
        if post == pre:
            problems.append(f"polish_cases: clean case {case['id']} changes nothing")
            continue
        pre_regions = {tag: payload for tag, payload, mutable in env_mod.regions_of(pre) if mutable}
        post_regions = {
            tag: payload for tag, payload, mutable in env_mod.regions_of(post) if mutable
        }
        if any(
            any(rx.search(payload) for rx in scan) and any(rx.search(pre_regions.get(tag, "")) for rx in scan)
            for tag, payload in post_regions.items()
        ):
            work_number += 1
        for tag, payload in post_regions.items():
            base = pre_regions.get(tag, "")
            if base and 0.30 <= abs(len(payload) / len(base) - 1.0) <= 0.40:
                length_priced += 1
                break
    if work_number < 5:
        problems.append(
            f"polish_cases: only {work_number} clean cases preserve work+number prose"
            " (EVALS requires >= 5; without them check (e) is untested)"
        )
    if length_priced < 3:
        problems.append(
            f"polish_cases: only {length_priced} clean cases change a mutable region by"
            " 30-40% (EVALS requires >= 3; without them the length bound is untested)"
        )
    return problems


def _accept_everything(*_args: Any, **_kwargs: Any) -> list[Any]:
    """`baseline_no_verifier` — FR-8 disabled: nothing is ever rejected."""
    return []


def _reject_everything(*_args: Any, **_kwargs: Any) -> list[Any]:
    """`baseline_reject_all` — a verifier that refuses every answer."""
    from ethos.engine.verify import Failure

    return [Failure("baseline", "reject-all verifier")]


def m4_baselines(corpus: Corpus, cases: list[dict[str, Any]]) -> dict[str, float]:
    """`baseline_no_verifier` (M4a) and `baseline_reject_all` (M4b), **measured**
    by running the same 50 cases through the same pipeline with the FR-8 gate
    swapped out. Recording these as constants would have made the two most
    important gates in the suite self-certifying.
    """
    mutated = sum(1 for case in cases if case["mode"] != "clean")
    clean = sum(1 for case in cases if case["mode"] == "clean")
    if not mutated or not clean:  # pragma: no cover - a malformed case list
        raise ValueError("polish_cases.json must contain clean and mutated cases")
    no_verifier = m4_tamper(corpus, cases, verifier=_accept_everything)
    reject_all = m4_tamper(corpus, cases, verifier=_reject_everything)
    return {
        "baseline_no_verifier:M4a": no_verifier["M4a"],
        "baseline_reject_all:M4b": reject_all["M4b"],
    }


# --- M5: answer completeness & well-formedness ------------------------------


def _section_wellformed(perspective: Any, text: str) -> bool:
    from ethos.models import Stance

    if perspective.stance not in set(Stance):
        return False
    if not perspective.summary.strip():
        return False
    if not perspective.quotes:
        return False
    for quote in perspective.quotes:
        if not quote.locator or not quote.source_line:
            return False
        if quote.is_paraphrase and quote.label is None:
            return False
        if f"[{quote.marker}]" not in text:
            return False
    return bool(perspective.further_reading)


def m5_completeness(corpus: Corpus, renderer: Any = None) -> tuple[float, list[str]]:
    """M5 = well-formed sections in answers whose answer-level checks pass /
    expected sections, over 24 topics x 3 render variants."""
    problems: list[str] = []
    good = expected = 0
    for topic in corpus.topics:
        covered = [
            tradition.id
            for tradition in corpus.traditions
            if (topic.id, tradition.id) in corpus.position_by_cell
        ]
        variants: list[list[str] | None] = [None, covered[:3], covered[:1]]
        corpus_stances = {
            corpus.position_by_cell[(topic.id, tid)].stance.value for tid in covered
        }
        for requested in variants:
            requested_ids = requested if requested is not None else [
                tradition.id for tradition in corpus.traditions
            ]
            expected_here = sum(
                1 for tid in requested_ids if (topic.id, tid) in corpus.position_by_cell
            )
            expected += expected_here
            if renderer is None:
                body, text = compose_unverified(corpus, topic.id, requested)
            else:
                body, text = renderer.render(topic.id, requested)
            answer_ok = True
            rendered_ids = [p.tradition_id for p in body.perspectives]
            mapped = [tid for members in body.agreement_map.values() for tid in members]
            if sorted(mapped) != sorted(rendered_ids):
                answer_ok = False
                problems.append(f"{topic.id}/{requested}: agreement map is not a partition")
            want_not_covered = [
                tid for tid in requested_ids if (topic.id, tid) not in corpus.position_by_cell
            ]
            if sorted(body.not_covered) != sorted(want_not_covered):
                answer_ok = False
                problems.append(f"{topic.id}/{requested}: not_covered is wrong")
            want_filtered = [tid for tid in covered if tid not in requested_ids]
            if sorted(body.filtered_out) != sorted(want_filtered):
                answer_ok = False
                problems.append(f"{topic.id}/{requested}: filtered_out is wrong")
            if requested is None and set(body.agreement_map) != corpus_stances:
                answer_ok = False
                problems.append(f"{topic.id}: unfiltered stance set != corpus stance set")
            for perspective in body.perspectives:
                position = corpus.position_by_cell[(topic.id, perspective.tradition_id)]
                declared = {
                    ref.passage_id
                    for ref in position.passages
                    if ref.role.value == "complicating"
                }
                shown = {quote.passage_id for quote in perspective.quotes}
                if not declared <= shown:
                    answer_ok = False
                    problems.append(
                        f"{topic.id}/{perspective.tradition_id}: complicating passage missing"
                    )
                if position.intra_tradition_note and (
                    position.intra_tradition_note not in text
                ):
                    answer_ok = False
                    problems.append(
                        f"{topic.id}/{perspective.tradition_id}: intra-tradition note missing"
                    )
            if not answer_ok:
                continue
            good += sum(1 for p in body.perspectives if _section_wellformed(p, text))
    return (good / expected if expected else 0.0), problems[:10]
