from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).resolve().parent / "data.db"

CLAN_TAG_PATTERN = r"^[A-Z]{3}$"
# Штат — число 1–999 (обычно три цифры, напр. 284)
STATE_MIN = 1
STATE_MAX = 999


@asynccontextmanager
async def connect_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


async def _migrate_v1_to_v2(db: aiosqlite.Connection) -> None:
    """Старый формат: clans(tag), players(clan_tag). Перенос с штатом по умолчанию 100."""
    await db.execute(
        """
        CREATE TABLE clans_new (
            tag TEXT NOT NULL CHECK (length(tag) = 3 AND tag GLOB '[A-Z][A-Z][A-Z]'),
            state INTEGER NOT NULL CHECK (state >= ? AND state <= ?),
            creator_id INTEGER NOT NULL,
            PRIMARY KEY (tag, state)
        )
        """,
        (STATE_MIN, STATE_MAX),
    )
    await db.execute(
        """
        INSERT INTO clans_new (tag, state, creator_id)
        SELECT tag, 100, creator_id FROM clans
        """
    )
    await db.execute(
        """
        CREATE TABLE players_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clan_tag TEXT NOT NULL,
            state INTEGER NOT NULL,
            name TEXT NOT NULL,
            march_power INTEGER NOT NULL CHECK (march_power >= 0),
            telegram_user_id INTEGER,
            UNIQUE (clan_tag, state, name),
            FOREIGN KEY (clan_tag, state) REFERENCES clans_new(tag, state) ON DELETE CASCADE
        )
        """
    )
    await db.execute(
        """
        INSERT INTO players_new (clan_tag, state, name, march_power, telegram_user_id)
        SELECT clan_tag, 100, name, march_power, NULL FROM players
        """
    )
    await db.execute("DROP TABLE players")
    await db.execute("DROP TABLE clans")
    await db.execute("ALTER TABLE clans_new RENAME TO clans")
    await db.execute("ALTER TABLE players_new RENAME TO players")
    await db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_players_telegram
        ON players(telegram_user_id) WHERE telegram_user_id IS NOT NULL
        """
    )


async def _create_schema_v2(db: aiosqlite.Connection) -> None:
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS clans (
            tag TEXT NOT NULL CHECK (length(tag) = 3 AND tag GLOB '[A-Z][A-Z][A-Z]'),
            state INTEGER NOT NULL CHECK (state >= {STATE_MIN} AND state <= {STATE_MAX}),
            creator_id INTEGER NOT NULL,
            PRIMARY KEY (tag, state)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clan_tag TEXT NOT NULL,
            state INTEGER NOT NULL,
            name TEXT NOT NULL,
            march_power INTEGER NOT NULL CHECK (march_power >= 0),
            telegram_user_id INTEGER,
            UNIQUE (clan_tag, state, name),
            FOREIGN KEY (clan_tag, state) REFERENCES clans(tag, state) ON DELETE CASCADE
        )
        """
    )
    await db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_players_telegram
        ON players(telegram_user_id) WHERE telegram_user_id IS NOT NULL
        """
    )


async def init_db() -> None:
    async with connect_db() as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='clans'"
        )
        has_clans = await cur.fetchone()
        if has_clans:
            cur = await db.execute("PRAGMA table_info(clans)")
            cols = {r[1] for r in await cur.fetchall()}
            if "state" not in cols:
                await _migrate_v1_to_v2(db)
        else:
            await _create_schema_v2(db)

        cur = await db.execute("PRAGMA table_info(players)")
        pcols = {r[1] for r in await cur.fetchall()}
        if pcols and "telegram_user_id" not in pcols:
            await db.execute(
                "ALTER TABLE players ADD COLUMN telegram_user_id INTEGER"
            )
            await db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_players_telegram
                ON players(telegram_user_id) WHERE telegram_user_id IS NOT NULL
                """
            )

        await _ensure_unique_creator_index(db)
        await db.commit()


async def _ensure_unique_creator_index(db: aiosqlite.Connection) -> None:
    """Один клан на создателя; при дубликатах в старых данных индекс не создаётся."""
    try:
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_clans_creator_unique ON clans(creator_id)"
        )
    except aiosqlite.OperationalError:
        pass


async def get_clan_by_creator(
    creator_id: int,
) -> tuple[str, int, int] | None:
    """Клан создателя: tag, state, creator_id."""
    async with connect_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT tag, state, creator_id FROM clans WHERE creator_id = ?",
            (creator_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return row["tag"], row["state"], row["creator_id"]


async def create_clan(tag: str, state: int, creator_id: int) -> tuple[bool, str]:
    if await get_clan_by_creator(creator_id):
        return False, "creator_has_clan"
    try:
        async with connect_db() as db:
            await db.execute(
                "INSERT INTO clans (tag, state, creator_id) VALUES (?, ?, ?)",
                (tag, state, creator_id),
            )
            await _ensure_unique_creator_index(db)
            await db.commit()
        return True, "ok"
    except aiosqlite.IntegrityError:
        return False, "clan_exists"


async def delete_clan_by_creator(user_id: int) -> tuple[bool, str]:
    c = await get_clan_by_creator(user_id)
    if c is None:
        return False, "no_clan"
    tag, state, _ = c
    async with connect_db() as db:
        await db.execute(
            "DELETE FROM clans WHERE tag = ? AND state = ?",
            (tag, state),
        )
        await db.commit()
    return True, "ok"


async def get_clan(tag: str, state: int) -> tuple[str, int, int] | None:
    async with connect_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT tag, state, creator_id FROM clans WHERE tag = ? AND state = ?",
            (tag, state),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return row["tag"], row["state"], row["creator_id"]


async def add_player(
    clan_tag: str,
    state: int,
    name: str,
    march_power: int,
    user_id: int,
) -> tuple[bool, str]:
    clan = await get_clan(clan_tag, state)
    if clan is None:
        return False, "no_clan"
    _, _, creator_id = clan
    if user_id != creator_id:
        return False, "not_owner"
    try:
        async with connect_db() as db:
            await db.execute(
                """
                INSERT INTO players (clan_tag, state, name, march_power, telegram_user_id)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (clan_tag, state, name.strip(), march_power),
            )
            await db.commit()
        return True, "ok"
    except aiosqlite.IntegrityError as e:
        if "UNIQUE" in str(e):
            return False, "player_exists"
        raise


async def update_player(
    clan_tag: str,
    state: int,
    name: str,
    march_power: int,
    user_id: int,
) -> tuple[bool, str]:
    clan = await get_clan(clan_tag, state)
    if clan is None:
        return False, "no_clan"
    _, _, creator_id = clan
    if user_id != creator_id:
        return False, "not_owner"
    async with connect_db() as db:
        cur = await db.execute(
            """
            UPDATE players SET march_power = ?
            WHERE clan_tag = ? AND state = ? AND name = ?
            """,
            (march_power, clan_tag, state, name.strip()),
        )
        await db.commit()
        if cur.rowcount == 0:
            return False, "no_player"
    return True, "ok"


async def remove_player(
    clan_tag: str,
    state: int,
    name: str,
    user_id: int,
) -> tuple[bool, str]:
    clan = await get_clan(clan_tag, state)
    if clan is None:
        return False, "no_clan"
    _, _, creator_id = clan
    if user_id != creator_id:
        return False, "not_owner"
    async with connect_db() as db:
        cur = await db.execute(
            """
            DELETE FROM players WHERE clan_tag = ? AND state = ? AND name = ?
            """,
            (clan_tag, state, name.strip()),
        )
        await db.commit()
        if cur.rowcount == 0:
            return False, "no_player"
    return True, "ok"


async def add_player_by_creator(
    creator_id: int, name: str, march_power: int
) -> tuple[bool, str]:
    c = await get_clan_by_creator(creator_id)
    if c is None:
        return False, "no_clan"
    tag, state, _ = c
    return await add_player(tag, state, name, march_power, creator_id)


async def update_player_by_creator(
    creator_id: int, name: str, march_power: int
) -> tuple[bool, str]:
    c = await get_clan_by_creator(creator_id)
    if c is None:
        return False, "no_clan"
    tag, state, _ = c
    return await update_player(tag, state, name, march_power, creator_id)


async def remove_player_by_creator(
    creator_id: int, name: str
) -> tuple[bool, str]:
    c = await get_clan_by_creator(creator_id)
    if c is None:
        return False, "no_clan"
    tag, state, _ = c
    return await remove_player(tag, state, name, creator_id)


async def list_players(
    clan_tag: str,
    state: int,
    limit: int | None = None,
) -> list[tuple[str, int]] | None:
    clan = await get_clan(clan_tag, state)
    if clan is None:
        return None
    async with connect_db() as db:
        if limit is not None:
            cur = await db.execute(
                """
                SELECT name, march_power FROM players
                WHERE clan_tag = ? AND state = ?
                ORDER BY march_power DESC, name COLLATE NOCASE
                LIMIT ?
                """,
                (clan_tag, state, limit),
            )
        else:
            cur = await db.execute(
                """
                SELECT name, march_power FROM players
                WHERE clan_tag = ? AND state = ?
                ORDER BY march_power DESC, name COLLATE NOCASE
                """,
                (clan_tag, state),
            )
        rows = await cur.fetchall()
    return [(r[0], r[1]) for r in rows]


async def join_self(
    clan_tag: str,
    state: int,
    name: str,
    march_power: int,
    telegram_user_id: int,
) -> tuple[bool, str]:
    """
    Вступление в клан: новая строка или привязка Telegram к строке,
    которую создатель добавил без telegram_user_id (то же имя в том же клане).
    """
    clan = await get_clan(clan_tag, state)
    if clan is None:
        return False, "no_clan"
    name_clean = name.strip()

    async with connect_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT clan_tag, state, name FROM players
            WHERE telegram_user_id = ?
            """,
            (telegram_user_id,),
        )
        existing_link = await cur.fetchone()
        if existing_link is not None:
            if (
                existing_link["clan_tag"] == clan_tag
                and int(existing_link["state"]) == state
                and existing_link["name"] == name_clean
            ):
                await db.execute(
                    """
                    UPDATE players SET march_power = ?
                    WHERE telegram_user_id = ?
                    """,
                    (march_power, telegram_user_id),
                )
                await db.commit()
                return True, "ok"
            return False, "already_linked"

        cur = await db.execute(
            """
            SELECT telegram_user_id FROM players
            WHERE clan_tag = ? AND state = ? AND name = ?
            """,
            (clan_tag, state, name_clean),
        )
        row = await cur.fetchone()

        if row is not None:
            tid = row["telegram_user_id"]
            if tid is not None:
                if tid == telegram_user_id:
                    await db.execute(
                        """
                        UPDATE players SET march_power = ?
                        WHERE telegram_user_id = ?
                        """,
                        (march_power, telegram_user_id),
                    )
                    await db.commit()
                    return True, "ok"
                return False, "name_claimed_by_other"
            await db.execute(
                """
                UPDATE players
                SET telegram_user_id = ?, march_power = ?
                WHERE clan_tag = ? AND state = ? AND name = ?
                """,
                (telegram_user_id, march_power, clan_tag, state, name_clean),
            )
            await db.commit()
            return True, "linked_existing"

        try:
            await db.execute(
                """
                INSERT INTO players (clan_tag, state, name, march_power, telegram_user_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (clan_tag, state, name_clean, march_power, telegram_user_id),
            )
            await db.commit()
            return True, "ok"
        except aiosqlite.IntegrityError as e:
            err = str(e).upper()
            if "UNIQUE" in err and "TELEGRAM" in err:
                return False, "already_linked"
            if "UNIQUE" in err:
                return False, "player_exists"
            raise


async def update_self_power(telegram_user_id: int, march_power: int) -> tuple[bool, str]:
    async with connect_db() as db:
        cur = await db.execute(
            """
            UPDATE players SET march_power = ?
            WHERE telegram_user_id = ?
            """,
            (march_power, telegram_user_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            return False, "no_self"
    return True, "ok"


async def get_self_player(telegram_user_id: int) -> tuple[str, int, str, int] | None:
    """tag, state, name, march_power для привязанного профиля."""
    async with connect_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT clan_tag, state, name, march_power FROM players
            WHERE telegram_user_id = ?
            """,
            (telegram_user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return (
            row["clan_tag"],
            row["state"],
            row["name"],
            row["march_power"],
        )
