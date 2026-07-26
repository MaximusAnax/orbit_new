from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_current_user_id
from app.db import repository as repo
from app.models.schemas import TaskOut, TaskPatch, TaskStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    status: TaskStatus | None = TaskStatus.OPEN,
    _user: str = Depends(get_current_user_id),
):
    if status == TaskStatus.OPEN:
        rows = await repo.get_open_tasks()
    else:
        from app.db.connection import fetch_all

        rows_raw = await fetch_all("select * from task order by created_at desc")
        rows = [dict(r) for r in rows_raw]
    return [
        TaskOut(
            id=r["id"],
            person_id=r["person_id"],
            event_id=r.get("event_id"),
            description=r["description"],
            status=TaskStatus(r["status"]),
            due_date=r.get("due_date"),
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.patch("/{task_id}", response_model=TaskOut)
async def patch_task(
    task_id: UUID,
    body: TaskPatch,
    _user: str = Depends(get_current_user_id),
):
    row = await repo.update_task_status(task_id, body.status)
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskOut(
        id=row["id"],
        person_id=row["person_id"],
        event_id=row.get("event_id"),
        description=row["description"],
        status=TaskStatus(row["status"]),
        due_date=row.get("due_date"),
        created_at=row["created_at"],
    )
