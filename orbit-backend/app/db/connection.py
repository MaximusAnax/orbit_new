import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import FastAPI

from app.core.config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get_pool() -> asyncpg.Pool:
    return await init_pool()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


async def fetch_one(query: str, *args: Any) -> asyncpg.Record | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetch_all(query: str, *args: Any) -> list[asyncpg.Record]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def execute(query: str, *args: Any) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


async def fetch_val(query: str, *args: Any) -> Any:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


def record_to_dict(record: asyncpg.Record | None) -> dict[str, Any] | None:
    if record is None:
        return None
    result: dict[str, Any] = {}
    for key in record.keys():
        val = record[key]
        if isinstance(val, UUID):
            result[key] = str(val)
        elif hasattr(val, "isoformat"):
            result[key] = val.isoformat()
        else:
            result[key] = val
    return result


def records_to_dicts(records: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [record_to_dict(r) for r in records if record_to_dict(r) is not None]


def json_dumps(data: Any) -> str:
    return json.dumps(data, default=str)
