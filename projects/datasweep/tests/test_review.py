"""The review workflow and revisions (SCOPE.md FR-11, DATA_MODEL §2.6/§2.7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from datasweep.engine.models import ReviewStatus
from datasweep.engine.transforms import revert
from datasweep.errors import ItemAlreadyDecidedError, UnknownItemError
from datasweep.services import DatasweepService, _read_audit_artifact, _read_table_artifact
from support_datasweep import service_for, write_sample

#: `U.S.A.` / ` usa ` merge at auto tier; `Slovenia` is a rare near-label of
#: `Slovakia`, which is what puts a `fix.label_merge_nn` item in the queue.
REVIEW_CSV = "country,amount\n" + "".join(
    f"{'Slovakia' if index else 'Slovenia'},{index + 1}.50\n" for index in range(40)
)


@pytest.fixture
def service(tmp_path: Path) -> DatasweepService:
    return service_for(tmp_path)


def _run(service: DatasweepService, tmp_path: Path):
    source = write_sample(tmp_path, "countries.csv", REVIEW_CSV)
    return service.clean_file(source, out=str(tmp_path / "out"))


def test_fr11_queue_holds_the_ambiguous_merge(service: DatasweepService, tmp_path: Path) -> None:
    run = _run(service, tmp_path)
    items = service.review_items(run.id)
    assert [item.rule for item in items] == ["fix.label_merge_nn"]
    assert items[0].status is ReviewStatus.PENDING
    assert items[0].affected_cells == len(items[0].proposal["cells"])


def test_fr11_every_review_cell_is_also_a_finding(
    service: DatasweepService, tmp_path: Path
) -> None:
    """The review queue never contains an un-reported finding (SCOPE.md FR-11)."""
    run = _run(service, tmp_path)
    findings = {
        (line["row"], line["col"])
        for line in map(
            json.loads,
            Path(run.artifact_dir, "findings.jsonl").read_text(encoding="utf-8").splitlines(),
        )
    }
    for item in service.review_items(run.id):
        for cell in item.proposal["cells"]:
            assert (cell["row"], cell["col"]) in findings


def test_fr11_item_ids_are_content_derived_and_stable(
    service: DatasweepService, tmp_path: Path
) -> None:
    """Same content + policy + engine version ⇒ same ids, across runs (§2.6)."""
    first = _run(service, tmp_path)
    ids = [item.id for item in service.review_items(first.id)]
    second = service.clean_file(
        write_sample(tmp_path, "countries.csv", REVIEW_CSV),
        out=str(tmp_path / "out2"),
        force=True,
    )
    assert [item.id for item in service.review_items(second.id)] == ids
    assert all(len(item_id) == 8 for item_id in ids)


def test_fr11_accept_writes_a_complete_revision(service: DatasweepService, tmp_path: Path) -> None:
    run = _run(service, tmp_path)
    item = service.review_items(run.id)[0]
    revision = service.decide(run.id, accept=[item.id])

    assert revision.revision_no == 2
    assert revision.accepted_item_ids == [item.id]
    assert Path(revision.cleaned_path).name == "cleaned.r2.csv"
    assert Path(revision.audit_path).name == "audit.r2.jsonl"

    stored = service.repo.get_review_item(run.id, item.id)
    assert stored.status is ReviewStatus.ACCEPTED and stored.decided_at is not None

    # findings.jsonl and report.md describe what was *detected*: accepting a
    # proposal does not change that, so they are written once (§2.7).
    assert sorted(p.name for p in Path(run.artifact_dir).iterdir()) == [
        "audit.jsonl",
        "audit.r2.jsonl",
        "cleaned.csv",
        "cleaned.r2.csv",
        "findings.jsonl",
        "report.md",
    ]

    r1 = _read_table_artifact(str(Path(run.artifact_dir, "cleaned.csv")), run.format)
    r2 = _read_table_artifact(revision.cleaned_path, run.format)
    assert r1.rows != r2.rows, "accepting the merge must actually change the table"
    assert all(row[0] == "Slovakia" for row in r2.rows)


def test_fr9_revision_audit_is_complete_not_a_delta(
    service: DatasweepService, tmp_path: Path
) -> None:
    """`revert(cleaned.rN, audit.rN) == parsed original` for every N (D4)."""
    run = _run(service, tmp_path)
    item = service.review_items(run.id)[0]
    revision = service.decide(run.id, accept=[item.id])

    check = service.revert_check(run.id, revision.revision_no)
    assert check.match, check.mismatches

    cleaned = _read_table_artifact(revision.cleaned_path, run.format)
    audit = _read_audit_artifact(revision.audit_path)
    rebuilt = revert(cleaned, audit)
    original_rows = [row.split(",") for row in REVIEW_CSV.strip().splitlines()[1:]]
    assert rebuilt.rows == original_rows


def test_fr11_single_transition(service: DatasweepService, tmp_path: Path) -> None:
    run = _run(service, tmp_path)
    item = service.review_items(run.id)[0]
    service.decide(run.id, accept=[item.id])
    with pytest.raises(ItemAlreadyDecidedError):
        service.decide(run.id, reject=[item.id])


def test_fr11_unknown_item_is_rejected(service: DatasweepService, tmp_path: Path) -> None:
    run = _run(service, tmp_path)
    with pytest.raises(UnknownItemError):
        service.decide(run.id, accept=["deadbeef"])


def test_fr11_rejected_items_are_never_re_proposed(
    service: DatasweepService, tmp_path: Path
) -> None:
    run = _run(service, tmp_path)
    item = service.review_items(run.id)[0]
    service.decide(run.id, reject=[item.id])

    again = service.clean_file(
        write_sample(tmp_path, "countries.csv", REVIEW_CSV),
        out=str(tmp_path / "out3"),
        force=True,
    )
    assert [entry.id for entry in service.review_items(again.id)] == []
    # The detection itself is unchanged — artifacts stay a pure function of
    # (bytes, policy, engine version), so the finding is still reported.
    findings = Path(again.artifact_dir, "findings.jsonl").read_text(encoding="utf-8")
    assert "fix.label_merge_nn" in findings
