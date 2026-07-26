from uuid import UUID

from app.core.config import settings
from app.db.connection import fetch_all
from app.models.schemas import SearchResult
from app.pipeline.providers import get_embedding


async def semantic_search(query: str, limit: int = 20) -> list[SearchResult]:
    embedding = await get_embedding().embed(query)
    vector_str = "[" + ",".join(str(x) for x in embedding) + "]"
    include_past = any(
        w in query.lower()
        for w in ("used to", "previously", "before", "former", "past", "was at", "old")
    )

    if include_past:
        fact_filter = "(f.id is null or f.status in ('current', 'past'))"
    else:
        fact_filter = "(f.id is null or f.status = 'current')"

    rows = await fetch_all(
        f"""
        select e.source_type, e.source_id, e.text_content,
               p.id as person_id, p.name as person_name,
               1 - (e.vector <=> $1::vector) as similarity
        from embedding e
        left join fact f on e.source_type = 'fact' and e.source_id = f.id
        left join event ev on e.source_type = 'event' and e.source_id = ev.id
        left join event_participant ep on ev.id = ep.event_id
        left join person p on p.id = coalesce(
            f.person_id,
            ep.person_id,
            case when e.source_type = 'person_summary' then e.source_id end
        )
        where p.id is not null
          and {fact_filter}
        order by e.vector <=> $1::vector
        limit $2
        """,
        vector_str,
        limit,
    )

    results: list[SearchResult] = []
    seen: set[UUID] = set()
    for row in rows:
        pid = row["person_id"]
        if pid in seen:
            continue
        seen.add(pid)
        results.append(
            SearchResult(
                person_id=pid,
                person_name=row["person_name"],
                match_reason=row["text_content"][:200],
                source_type=row["source_type"],
                source_id=row["source_id"],
            )
        )
    return results


async def upsert_fact_embedding(fact_id: UUID, text: str) -> None:
    from app.db.connection import execute

    embedding = await get_embedding().embed(text)
    vector_str = "[" + ",".join(str(x) for x in embedding) + "]"
    # Include status hint for history-aware search
    content = text if text.startswith("[") else f"[current] {text}"
    await execute(
        """
        insert into embedding (source_type, source_id, vector, text_content, model_version)
        values ('fact', $1, $2::vector, $3, $4)
        """,
        fact_id,
        vector_str,
        content,
        settings.embedding_model,
    )


async def upsert_event_embedding(event_id: UUID, text: str) -> None:
    from app.db.connection import execute

    embedding = await get_embedding().embed(text)
    vector_str = "[" + ",".join(str(x) for x in embedding) + "]"
    await execute(
        """
        insert into embedding (source_type, source_id, vector, text_content, model_version)
        values ('event', $1, $2::vector, $3, $4)
        """,
        event_id,
        vector_str,
        text,
        settings.embedding_model,
    )


async def upsert_person_summary_embedding(person_id: UUID, text: str) -> None:
    from app.db.connection import execute

    embedding = await get_embedding().embed(text)
    vector_str = "[" + ",".join(str(x) for x in embedding) + "]"
    await execute(
        "delete from embedding where source_type = 'person_summary' and source_id = $1",
        person_id,
    )
    await execute(
        """
        insert into embedding (source_type, source_id, vector, text_content, model_version)
        values ('person_summary', $1, $2::vector, $3, $4)
        """,
        person_id,
        vector_str,
        text,
        settings.embedding_model,
    )
