"""Digest and alert composition (SCOPE FR-9, FR-10, D14).

Pure: selection, ordering and Markdown rendering only. The ledger filter — "not
yet delivered on this channel" — is applied upstream by the repository query
(FR-11), so this module never needs to know what has been sent.

The finance safeguard is structural rather than textual: every rendered field is
a fact carried by an archived row (title, link, outlet, timestamp, relevance,
matched surfaces, copy count), the wording comes from the committed template,
and there is no code path that can emit an opinion, a sentiment value or a price
target. The informational-only footer is a template section, so it cannot be
dropped without changing a hashed file.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from .models import Company, DeliveryMode

__all__ = [
    "ComposedBody",
    "DigestCandidate",
    "compose_alert",
    "compose_digest",
    "parse_template",
    "select_alert_candidates",
    "select_digest_candidates",
]

_SECTION_PREFIX = "=== "
_SECTION_SUFFIX = " ==="

_REQUIRED_SECTIONS = (
    "digest_subject",
    "digest_heading",
    "alert_subject",
    "alert_heading",
    "company_heading",
    "item",
    "extra_one",
    "extra_many",
    "footer",
)


def parse_template(text: str) -> dict[str, str]:
    """Split ``data/digest_template.md`` into its named sections.

    Sections are introduced by a ``=== name ===`` line; leading and trailing
    blank lines are dropped and everything else is preserved byte for byte.
    """

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_SECTION_PREFIX) and stripped.endswith(_SECTION_SUFFIX):
            current = stripped[len(_SECTION_PREFIX) : -len(_SECTION_SUFFIX)].strip()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)

    rendered: dict[str, str] = {}
    for name, lines in sections.items():
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        rendered[name] = "\n".join(lines)

    missing = [name for name in _REQUIRED_SECTIONS if name not in rendered]
    if missing:
        raise ValueError(f"digest template is missing section(s): {', '.join(missing)}")
    return rendered


@dataclass(frozen=True, slots=True)
class DigestCandidate:
    """One (company, story) pair eligible for delivery on a channel."""

    company: Company
    story_id: int
    relevance: int
    article_id: int
    title: str
    url: str
    outlet: str
    published_at: datetime
    first_published_at: datetime
    copy_count: int
    matched_surfaces: tuple[str, ...]

    @property
    def order_key(self) -> tuple[str, int, float, int]:
        """FR-9 ordering: ticker asc, relevance desc, published desc, story asc."""

        return (
            self.company.ticker,
            -self.relevance,
            -self.first_published_at.timestamp(),
            self.story_id,
        )


@dataclass(frozen=True, slots=True)
class ComposedBody:
    """A rendered message plus the items it cites (the ledger rows to write)."""

    subject: str
    body_text: str
    items: tuple[DigestCandidate, ...]


def select_digest_candidates(
    candidates: Iterable[DigestCandidate],
) -> tuple[DigestCandidate, ...]:
    """Apply the FR-9 filters and ordering law."""

    selected = [
        candidate
        for candidate in candidates
        if candidate.company.mode in (DeliveryMode.DIGEST, DeliveryMode.BOTH)
        and candidate.relevance >= candidate.company.min_relevance
    ]
    return tuple(sorted(selected, key=lambda item: item.order_key))


def select_alert_candidates(
    candidates: Iterable[DigestCandidate],
) -> tuple[DigestCandidate, ...]:
    """Apply the FR-10 filters: alert-ish mode and the alert floor."""

    selected = [
        candidate
        for candidate in candidates
        if candidate.company.mode in (DeliveryMode.ALERT, DeliveryMode.BOTH)
        and candidate.relevance >= candidate.company.alert_min_relevance
    ]
    return tuple(sorted(selected, key=lambda item: item.order_key))


def _render_extra(candidate: DigestCandidate, template: dict[str, str]) -> str:
    others = max(0, candidate.copy_count - 1)
    if others == 0:
        return ""
    if others == 1:
        return template["extra_one"]
    return template["extra_many"].format(count=others)


def _render_item(candidate: DigestCandidate, template: dict[str, str]) -> str:
    return template["item"].format(
        title=candidate.title,
        url=candidate.url,
        outlet=candidate.outlet,
        published=candidate.published_at.strftime("%Y-%m-%d %H:%M"),
        relevance=candidate.relevance,
        matched=", ".join(candidate.matched_surfaces),
        extra=_render_extra(candidate, template),
    )


def _render_company_block(
    company: Company,
    items: Sequence[DigestCandidate],
    template: dict[str, str],
) -> str:
    heading = template["company_heading"].format(ticker=company.ticker, name=company.name)
    lines = [heading]
    lines.extend(_render_item(item, template) for item in items)
    return "\n".join(lines)


def compose_digest(
    candidates: Iterable[DigestCandidate],
    now: datetime,
    template: dict[str, str],
) -> ComposedBody | None:
    """Compose one digest body, or ``None`` when nothing is eligible.

    An empty selection composes nothing: no body, no Delivery row, no notifier
    call (FR-9). ``dry_run`` is the caller's concern — a dry run renders this
    body and persists nothing at all.
    """

    selected = select_digest_candidates(candidates)
    if not selected:
        return None

    date = now.strftime("%Y-%m-%d")
    blocks: list[str] = []
    current: list[DigestCandidate] = []
    for candidate in selected:
        if current and current[-1].company.ticker != candidate.company.ticker:
            blocks.append(_render_company_block(current[0].company, current, template))
            current = []
        current.append(candidate)
    if current:
        blocks.append(_render_company_block(current[0].company, current, template))

    body = "\n\n".join([template["digest_heading"].format(date=date), *blocks, template["footer"]])
    return ComposedBody(
        subject=template["digest_subject"].format(date=date),
        body_text=body + "\n",
        items=selected,
    )


def compose_alert(
    candidate: DigestCandidate,
    now: datetime,
    template: dict[str, str],
) -> ComposedBody:
    """Compose a single-story alert (same template family, same footer)."""

    heading = template["alert_heading"].format(
        ticker=candidate.company.ticker, name=candidate.company.name, date=now.strftime("%Y-%m-%d")
    )
    block = _render_company_block(candidate.company, [candidate], template)
    body = "\n\n".join([heading, block, template["footer"]])
    return ComposedBody(
        subject=template["alert_subject"].format(
            ticker=candidate.company.ticker, title=candidate.title
        ),
        body_text=body + "\n",
        items=(candidate,),
    )
