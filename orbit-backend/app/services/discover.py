from pydantic import BaseModel

from app.db.connection import fetch_all
from app.models.schemas import DiscoverResult
from app.pipeline.providers import get_llm
from app.services.search import semantic_search


class DiscoverIntent(BaseModel):
    query_type: str
    target_org: str | None = None
    target_skill: str | None = None
    target_interest: str | None = None


DISCOVER_SYSTEM = """Parse a network discovery query into structured intent.
query_type: direct_connection | introduction_path | interest_match
Extract target org, skill, or interest as applicable."""


async def discover(query: str, limit: int = 20) -> list[DiscoverResult]:
    llm = get_llm()
    parsed, _, _ = await llm.structured_completion(
        DISCOVER_SYSTEM, query, DiscoverIntent
    )

    results: list[DiscoverResult] = []

    if parsed.target_org:
        rows = await fetch_all(
            """
            select distinct p.id as person_id, p.name as person_name,
                   f.value as match_reason
            from person p
            join fact f on f.person_id = p.id
            left join organization o on o.id = f.organization_id
            where f.category = 'job' and f.status = 'current'
              and (lower(f.value) like $1 or lower(o.name) like $1)
            limit $2
            """,
            f"%{parsed.target_org.lower()}%",
            limit,
        )
        for row in rows:
            results.append(
                DiscoverResult(
                    person_id=row["person_id"],
                    person_name=row["person_name"],
                    match_reason=f"Works at {row['match_reason']}",
                )
            )

    if parsed.target_interest or parsed.target_skill:
        target = parsed.target_interest or parsed.target_skill or ""
        rows = await fetch_all(
            """
            select distinct p.id as person_id, p.name as person_name, f.value as match_reason
            from person p
            join fact f on f.person_id = p.id
            where f.category in ('interest', 'other') and f.status = 'current'
              and lower(f.value) like $1
            limit $2
            """,
            f"%{target.lower()}%",
            limit,
        )
        for row in rows:
            results.append(
                DiscoverResult(
                    person_id=row["person_id"],
                    person_name=row["person_name"],
                    match_reason=f"Interested in {row['match_reason']}",
                )
            )

    if parsed.query_type == "introduction_path":
        if parsed.target_org:
            rows = await fetch_all(
                """
                select distinct p2.id as person_id, p2.name as person_name,
                       p1.name as introducer_name, coalesce(o.name, f.value) as org_name
                from event_participant ep1
                join event_participant ep2 on ep1.event_id = ep2.event_id
                join person p1 on p1.id = ep1.person_id
                join person p2 on p2.id = ep2.person_id
                join fact f on f.person_id = p2.id and f.category = 'job' and f.status = 'current'
                left join organization o on o.id = f.organization_id
                where ep1.role = 'introducer' and ep2.role = 'introduced'
                  and (lower(coalesce(o.name, f.value)) like $1)
                limit $2
                """,
                f"%{parsed.target_org.lower()}%",
                limit,
            )
        else:
            rows = await fetch_all(
                """
                select distinct p2.id as person_id, p2.name as person_name,
                       p1.name as introducer_name, null as org_name
                from event_participant ep1
                join event_participant ep2 on ep1.event_id = ep2.event_id
                join person p1 on p1.id = ep1.person_id
                join person p2 on p2.id = ep2.person_id
                where ep1.role = 'introducer' and ep2.role = 'introduced'
                limit $1
                """,
                limit,
            )
        for row in rows:
            org_bit = f" ({row['org_name']})" if row.get("org_name") else ""
            results.append(
                DiscoverResult(
                    person_id=row["person_id"],
                    person_name=row["person_name"],
                    match_reason=f"Introduced via {row['introducer_name']}{org_bit}",
                    path=[row["introducer_name"], row["person_name"]],
                )
            )

    if not results:
        search_hits = await semantic_search(query, limit=limit)
        for hit in search_hits:
            results.append(
                DiscoverResult(
                    person_id=hit.person_id,
                    person_name=hit.person_name,
                    match_reason=hit.match_reason,
                )
            )

    return results[:limit]
