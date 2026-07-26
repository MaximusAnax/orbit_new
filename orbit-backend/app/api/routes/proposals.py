import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_current_user_id
from app.db import repository as repo
from app.models.schemas import (
    ProposalAcceptBody,
    ProposalDecision,
    ProposalOut,
    ProposedFact,
)
from app.services.search import (
    upsert_event_embedding,
    upsert_fact_embedding,
    upsert_person_summary_embedding,
)

router = APIRouter(prefix="/proposals", tags=["proposals"])


def _proposal_out(row: dict) -> ProposalOut:
    fact_data = row["proposed_fact_json"]
    if isinstance(fact_data, str):
        fact_data = json.loads(fact_data)
    return ProposalOut(
        id=row["id"],
        event_id=row["event_id"],
        person_id=row["person_id"],
        proposed_fact=ProposedFact(**fact_data),
        decision=row["decision"],
        resolved_at=row.get("resolved_at"),
        created_at=row["created_at"],
    )


@router.get("", response_model=list[ProposalOut])
async def list_proposals(
    event_id: UUID | None = None,
    _user: str = Depends(get_current_user_id),
):
    rows = await repo.list_proposals(event_id)
    return [_proposal_out(r) for r in rows]


@router.post("/{proposal_id}/accept", response_model=ProposalOut)
async def accept_proposal(
    proposal_id: UUID,
    body: ProposalAcceptBody | None = None,
    _user: str = Depends(get_current_user_id),
):
    row = await repo.get_proposal(proposal_id)
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")

    fact_data = row["proposed_fact_json"]
    if isinstance(fact_data, str):
        fact_data = json.loads(fact_data)
    proposed = ProposedFact(**fact_data)

    decision = ProposalDecision.EDITED if body and body.value else ProposalDecision.ACCEPTED
    materialized_value = body.value if body and body.value else proposed.formatted_value()
    await repo.materialize_fact_from_proposal(
        row["person_id"],
        proposed,
        row["event_id"],
        body.value if body else None,
    )
    updated = await repo.resolve_proposal(proposal_id, decision)

    fact_row = await repo.get_current_facts(row["person_id"])
    for f in fact_row:
        if f["category"] == proposed.category.value and f["value"] == materialized_value:
            await upsert_fact_embedding(f["id"], f["value"])
            break

    # Refresh person summary embedding from current facts
    summary_bits = [f"{f['category']}: {f['value']}" for f in fact_row]
    if summary_bits:
        await upsert_person_summary_embedding(row["person_id"], "; ".join(summary_bits))

    event = await repo.get_event(row["event_id"])
    if event and event.get("summary"):
        await upsert_event_embedding(row["event_id"], event["summary"])

    pending = await repo.proposal_counts_for_event(row["event_id"])
    if pending.get("pending", 0) == 0:
        await repo.mark_event_status(row["event_id"], "synced")

    return _proposal_out(updated)


@router.post("/{proposal_id}/reject", response_model=ProposalOut)
async def reject_proposal(
    proposal_id: UUID,
    _user: str = Depends(get_current_user_id),
):
    row = await repo.get_proposal(proposal_id)
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")
    updated = await repo.resolve_proposal(proposal_id, ProposalDecision.REJECTED)
    return _proposal_out(updated)


@router.post("/{proposal_id}/defer", response_model=ProposalOut)
async def defer_proposal(
    proposal_id: UUID,
    _user: str = Depends(get_current_user_id),
):
    row = await repo.get_proposal(proposal_id)
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")
    updated = await repo.resolve_proposal(proposal_id, ProposalDecision.DEFERRED)
    return _proposal_out(updated)
