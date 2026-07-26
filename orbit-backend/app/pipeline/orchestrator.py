import logging
from uuid import UUID

from tenacity import retry, stop_after_attempt, wait_exponential

from app.db import repository as repo
from app.pipeline import (
    context_extraction,
    entity_resolution,
    fact_extraction,
    group_reconciliation,
    task_extraction,
    transcription,
)

logger = logging.getLogger(__name__)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def _run_step(name: str, coro):
    return await coro


async def run_pipeline(event_id: UUID, audio_bytes: bytes | None = None) -> None:
    event = await repo.get_event(event_id)
    if not event:
        raise ValueError(f"Event {event_id} not found")

    try:
        if audio_bytes and not event.get("audio_storage_path"):
            from pathlib import Path

            audio_dir = Path(__file__).resolve().parents[2] / "data" / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            path = audio_dir / f"{event_id}.m4a"
            path.write_bytes(audio_bytes)
            from app.db.connection import execute

            await execute(
                "update event set audio_storage_path = $2 where id = $1",
                event_id,
                str(path),
            )

        if event.get("raw_transcript"):
            transcript = event["raw_transcript"]
        else:
            transcript = await _run_step(
                "transcription",
                transcription.transcribe_event(event_id, audio_bytes),
            )
            await repo.save_transcript(event_id, transcript)

        existing_people = await repo.get_people_index()
        resolutions = await _run_step(
            "entity_resolution",
            entity_resolution.resolve_entities(event_id, transcript, existing_people),
        )

        ambiguous_names = {m.name.lower() for m in resolutions.ambiguous}
        resolved_map: dict[UUID, str] = {}
        people_by_name: dict[str, UUID] = {}

        for match in resolutions.matches:
            if match.person_id is None:
                continue
            resolved_map[match.person_id] = match.transcript_segment
            people_by_name[match.name.lower()] = match.person_id

        for person_id, segment in resolved_map.items():
            person = await repo.get_person(person_id)
            if person and person["name"].lower() in ambiguous_names:
                continue
            existing_facts = await repo.get_current_facts(person_id)
            await _run_step(
                f"fact_extraction_{person_id}",
                fact_extraction.extract_facts(event_id, person_id, segment, existing_facts),
            )

        await _run_step(
            "task_extraction",
            task_extraction.extract_tasks(event_id, transcript, people_by_name),
        )

        await _run_step(
            "context_extraction",
            context_extraction.extract_context(
                event_id, transcript, list(resolved_map.keys())
            ),
        )

        await _run_step(
            "group_reconciliation",
            group_reconciliation.reconcile_group_attribution(event_id),
        )

        # Embed event summary when available
        refreshed = await repo.get_event(event_id)
        if refreshed and refreshed.get("summary"):
            from app.services.search import upsert_event_embedding

            await upsert_event_embedding(event_id, refreshed["summary"])

        await repo.mark_event_status(event_id, "captured")
    except Exception:
        logger.exception("Pipeline failed for event %s", event_id)
        raise
