from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).resolve().parent / "data.db"

CLAN_TAG_PATTERN = r"^[A-Z]{3}$"


@asynccontextmanager
async def connect_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


async def init_db() -> None:
    async with connect_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS clans (
                tag TEXT PRIMARY KEY CHECK (length(tag) = 3 AND tag GLOB '[A-Z][A-Z][A-Z]'),
                creator_id INTEGER NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clan_tag TEXT NOT NULL REFERENCES clans(tag) ON DELETE CASCADE,
                name TEXT NOT NULL,
                march_power INTEGER NOT NULL CHECK (march_power >= 0),
                UNIQUE (clan_tag, name)
            )
            """
        )
        await db.commit()


async def create_clan(tag: str, creator_id: int) -> tuple[bool, str]:
    """Returns (success, message_key or error)."""
    try:
        async with connect_db() as db:
            await db.execute(
                "INSERT INTO clans (tag, creator_id) VALUES (?, ?)",
                (tag, creator_id),
            )
            await db.commit()
        return True, "ok"
    except aiosqlite.IntegrityError:
        return False, "clan_exists"


async def get_clan(tag: str) -> tuple[str, int] | None:
    async with connect_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT tag, creator_id FROM clans WHERE tag = ?", (tag,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return row["tag"], row["creator_id"]


async def add_player(
    clan_tag: str, name: str, march_power: int, user_id: int
) -> tuple[bool, str]:
    clan = await get_clan(clan_tag)
    if clan is None:
        return False, "no_clan"
    _, creator_id = clan
    if user_id != creator_id:
        return False, "not_owner"
    try:
        async with connect_db() as db:
            await db.execute(
                """
                INSERT INTO players (clan_tag, name, march_power)
                VALUES (?, ?, ?)
                """,
                (clan_tag, name.strip(), march_power),
            )
            await db.commit()
        return True, "ok"
    except aiosqlite.IntegrityError as e:
        if "UNIQUE" in str(e):
            return False, "player_exists"
        raise


async def update_player(
    clan_tag: str, name: str, march_power: int, user_id: int
) -> tuple[bool, str]:
    clan = await get_clan(clan_tag)
    if clan is None:
        return False, "no_clan"
    _, creator_id = clan
    if user_id != creator_id:
        return False, "not_owner"
    async with connect_db() as db:
        cur = await db.execute(
            """
            UPDATE players SET march_power = ?
            WHERE clan_tag = ? AND name = ?
            """,
            (march_power, clan_tag, name.strip()),
        )
        await db.commit()
        if cur.rowcount == 0:
            return False, "no_player"
    return True, "ok"


async def remove_player(
    clan_tag: str, name: str, user_id: int
) -> tuple[bool, str]:
    clan = await get_clan(clan_tag)
    if clan is None:
        return False, "no_clan"
    _, creator_id = clan
    if user_id != creator_id:
        return False, "not_owner"
    async with connect_db() as db:
        cur = await db.execute(
            "DELETE FROM players WHERE clan_tag = ? AND name = ?",
            (clan_tag, name.strip()),
        )
        await db.commit()
        if cur.rowcount == 0:
            return False, "no_player"
    return True, "ok"


async def list_players(
    clan_tag: str, limit: int | None = None
) -> list[tuple[str, int]] | None:
    """Список игроков по убыванию силы марша; при limit — только первые N записей."""
    clan = await get_clan(clan_tag)
    if clan is None:
        return None
    async with connect_db() as db:
        if limit is not None:
            cur = await db.execute(
                """
                SELECT name, march_power FROM players
                WHERE clan_tag = ?
                ORDER BY march_power DESC, name COLLATE NOCASE
                LIMIT ?
                """,
                (clan_tag, limit),
            )
        else:
            cur = await db.execute(
                """
                SELECT name, march_power FROM players
                WHERE clan_tag = ?
                ORDER BY march_power DESC, name COLLATE NOCASE
                """,
                (clan_tag,),
            )
        rows = await cur.fetchall()
    return [(r[0], r[1]) for r in rows]
