"""
Telegram-бот: кланы (тег + штат), игроки и сила марша.
Создатель управляет списком; игроки могут сами вступить и обновлять свою силу (/my_power).
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from config import BOT_TOKEN
from db import (
    CLAN_TAG_PATTERN,
    STATE_MAX,
    STATE_MIN,
    add_player_by_creator,
    create_clan,
    delete_clan_by_creator,
    get_clan,
    get_clan_by_creator,
    get_self_player,
    init_db,
    join_self,
    list_players,
    remove_player_by_creator,
    update_player_by_creator,
    update_self_power,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

TAG_RE = re.compile(CLAN_TAG_PATTERN)


def parse_march_power(raw: str) -> int | None:
    """
    Целое неотрицательное число. Разрешены разделители: _ , . и пробел
    (например 12_345_678, 12,345,678, 12.345.678).
    """
    s = raw.strip().replace("_", "").replace(" ", "")
    if not s:
        return None
    for ch in s:
        if ch not in "0123456789.,":
            return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    value = int(digits)
    if value < 0:
        return None
    return value


def parse_tag(raw: str) -> str | None:
    t = raw.strip().upper()
    if TAG_RE.match(t):
        return t
    return None


def parse_state(raw: str) -> int | None:
    s = raw.strip()
    if not s.isdigit():
        return None
    v = int(s)
    if v < STATE_MIN or v > STATE_MAX:
        return None
    return v


def parse_creator_name_power(text: str) -> tuple[str, int] | tuple[None, None]:
    """/cmd НикИгрока сила_марша — клан берётся по создателю."""
    parts = text.split()
    if len(parts) < 3:
        return None, None
    rest = parts[1:]
    max_k = len(rest) - 1
    for k in range(max_k, 0, -1):
        suffix = " ".join(rest[-k:])
        power = parse_march_power(suffix)
        if power is None:
            continue
        name = " ".join(rest[:-k]).strip()
        if not name:
            continue
        return name, power
    return None, None


def parse_remove_creator_name(text: str) -> str | None:
    """/remove_player НикИгрока"""
    parts = text.split()
    if len(parts) < 2:
        return None
    name = " ".join(parts[1:]).strip()
    return name if name else None


def parse_join_args(
    text: str,
) -> tuple[str, int, str, int] | tuple[None, None, None, None]:
    """
    /join НикИгрока ABC 284 сила_марша — справа: сила (несколько токенов), штат, тег, всё остальное — имя.
    """
    parts = text.split()
    if len(parts) < 5:
        return None, None, None, None
    max_k = len(parts) - 3
    for k in range(1, max_k + 1):
        suffix = " ".join(parts[-k:])
        power = parse_march_power(suffix)
        if power is None:
            continue
        if len(parts) - k - 2 < 1:
            continue
        st_tok = parts[-k - 1]
        state = parse_state(st_tok)
        if state is None:
            continue
        tag = parse_tag(parts[-k - 2])
        if tag is None:
            continue
        name = " ".join(parts[1 : -k - 2]).strip()
        if not name:
            continue
        return tag, state, name, power
    return None, None, None, None


async def resolve_clan_for_viewer(user_id: int) -> tuple[str, int] | None:
    """Клан для просмотра /clan: создатель или участник с привязкой после /join."""
    c = await get_clan_by_creator(user_id)
    if c is not None:
        return c[0], c[1]
    sp = await get_self_player(user_id)
    if sp is not None:
        return sp[0], sp[1]
    return None


async def cmd_start(message: Message) -> None:
    await message.answer(
        "Команды создателя (у каждого создателя только один клан; тег и штат задаются "
        "один раз при создании):\n"
        "/create_clan ABC 284 — зарегистрировать клан: тег (3 буквы) и штат "
        f"({STATE_MIN}–{STATE_MAX}).\n"
        "/delete_clan — удалить свой клан и всех игроков.\n"
        "/add_player НикИгрока 12_345_678 — добавить игрока в свой клан.\n"
        "/update_player НикИгрока 12,345,678 — обновить силу марша.\n"
        "/remove_player НикИгрока — удалить игрока.\n"
        "/clan — список игроков клана (создатель или любой участник после /join); "
        "/clan 10 — топ-10.\n\n"
        "Для всех:\n"
        "/join МойНик ABC 284 12_345_678 — вступить в клан (нужны тег и штат клана). "
        "Если создатель уже добавил тебя в список — укажи то же имя: тогда привяжется твой Telegram.\n"
        "/my_power 12_345_678 — обновить свою силу после /join.\n\n"
        "Сила марша: целое число, можно с _ , ."
    )


async def cmd_create_clan(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(
            f"Формат: /create_clan ABC 284\n"
            f"Тег — три латинские заглавные буквы, штат — число от {STATE_MIN} до {STATE_MAX}."
        )
        return
    tag = parse_tag(parts[1])
    if tag is None:
        await message.answer(
            "Неверный тег. Нужны ровно три латинские заглавные буквы, например ABC."
        )
        return
    state = parse_state(parts[2])
    if state is None:
        await message.answer(
            f"Неверный штат. Укажи число от {STATE_MIN} до {STATE_MAX} (например 284)."
        )
        return
    ok, err = await create_clan(tag, state, uid)
    if ok:
        await message.answer(
            f"Клан {tag} / штат {state} сохранён. "
            "Только ты можешь управлять этим кланом, смотреть список и удалять его."
        )
        return
    if err == "creator_has_clan":
        await message.answer(
            "У тебя уже есть клан. Удали его командой /delete_clan, "
            "если нужно создать другой (тег и штат задаются только при /create_clan)."
        )
        return
    if err == "clan_exists":
        existing = await get_clan(tag, state)
        if existing and existing[2] == uid:
            await message.answer(f"Клан {tag} (штат {state}) уже зарегистрирован тобой.")
        else:
            await message.answer(
                f"Клан с тегом {tag} и штатом {state} уже существует."
            )
        return
    await message.answer("Не удалось сохранить клан. Попробуй позже.")


async def cmd_delete_clan(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split()
    if len(parts) != 1:
        await message.answer("Формат: /delete_clan — без аргументов (удаляется твой клан).")
        return
    ok, err = await delete_clan_by_creator(uid)
    if ok:
        await message.answer("Твой клан и все игроки удалены из базы.")
        return
    if err == "no_clan":
        await message.answer("У тебя нет зарегистрированного клана.")
        return
    await message.answer("Ошибка при удалении.")


async def cmd_add_player(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    parsed = parse_creator_name_power(message.text or "")
    if parsed[0] is None:
        await message.answer(
            "Формат: /add_player НикИгрока 12345678\n"
            "Имя и сила марша; клан — тот, что ты создал через /create_clan."
        )
        return
    name, power = parsed
    ok, err = await add_player_by_creator(uid, name, power)
    if ok:
        c = await get_clan_by_creator(uid)
        tag, state = (c[0], c[1]) if c else ("?", "?")
        await message.answer(
            f"Игрок «{name}» добавлен: клан {tag}, штат {state}. Сила марша: {power:,}."
        )
        return
    if err == "no_clan":
        await message.answer(
            "Сначала создай клан: /create_clan ABC 284"
        )
        return
    if err == "not_owner":
        await message.answer(
            "Добавлять игроков может только создатель клана."
        )
        return
    if err == "player_exists":
        await message.answer(
            f"Игрок «{name}» уже есть в этом клане. Используй /update_player."
        )
        return
    await message.answer("Ошибка при сохранении.")


async def cmd_update_player(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    parsed = parse_creator_name_power(message.text or "")
    if parsed[0] is None:
        await message.answer(
            "Формат: /update_player НикИгрока 789012\n"
            "Имя и новая сила марша."
        )
        return
    name, power = parsed
    ok, err = await update_player_by_creator(uid, name, power)
    if ok:
        c = await get_clan_by_creator(uid)
        tag, state = (c[0], c[1]) if c else ("?", "?")
        await message.answer(
            f"Игрок «{name}» ({tag}/{state}): сила марша {power:,}."
        )
        return
    if err == "no_clan":
        await message.answer("Такого клана нет в базе.")
        return
    if err == "not_owner":
        await message.answer("Менять игроков может только создатель клана.")
        return
    if err == "no_player":
        await message.answer("Такого игрока в списке нет. Сначала /add_player.")
        return
    await message.answer("Ошибка при обновлении.")


async def cmd_clan(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split()
    if len(parts) > 2:
        await message.answer(
            "Формат: /clan — все игроки по убыванию силы.\n"
            "/clan 10 — топ-10. Доступно создателю клана или участникам, вступившим через /join."
        )
        return
    limit: int | None = None
    if len(parts) == 2:
        try:
            n = int(parts[1])
        except ValueError:
            await message.answer(
                "Опционально укажи число: /clan 15 — показать топ-15."
            )
            return
        if n < 1:
            await message.answer("Укажи число не меньше 1.")
            return
        limit = n
    resolved = await resolve_clan_for_viewer(uid)
    if resolved is None:
        await message.answer(
            "Список клана видят создатель и участники, которые вступили через /join. "
            "Создай клан: /create_clan ABC 284 или вступи: /join Ник ABC 284 сила_марша."
        )
        return
    tag, state = resolved
    rows = await list_players(tag, state, limit=limit)
    if rows is None:
        await message.answer("Такого клана нет в базе.")
        return
    if not rows:
        await message.answer(f"Клан {tag} / штат {state}: пока нет игроков.")
        return
    header = f"Клан {tag}, штат {state}"
    if limit is not None:
        header += f" (топ {limit})"
    header += ", по убыванию силы марша:\n"
    lines = [header]
    for name, p in rows:
        lines.append(f"• {name} — {p:,}")
    await message.answer("\n".join(lines))


async def cmd_remove_player(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    name = parse_remove_creator_name(message.text or "")
    if name is None:
        await message.answer(
            "Формат: /remove_player НикИгрока"
        )
        return
    ok, err = await remove_player_by_creator(uid, name)
    if ok:
        c = await get_clan_by_creator(uid)
        tag, state = (c[0], c[1]) if c else ("?", "?")
        await message.answer(f"Игрок «{name}» удалён (клан {tag}, штат {state}).")
        return
    if err == "no_clan":
        await message.answer("Такого клана нет в базе.")
        return
    if err == "not_owner":
        await message.answer("Удалять игроков может только создатель клана.")
        return
    if err == "no_player":
        await message.answer("Такого игрока в списке нет.")
        return
    await message.answer("Ошибка при удалении.")


async def cmd_join(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    tag, state, name, power = parse_join_args(message.text or "")
    if tag is None:
        await message.answer(
            "Формат: /join МойНик ABC 284 12_345_678\n"
            "Имя в игре, тег клана (3 буквы), штат (число), сила марша."
        )
        return
    ok, code = await join_self(tag, state, name, power, uid)
    if ok:
        if code == "linked_existing":
            await message.answer(
                f"Профиль «{name}» привязан к твоему Telegram (клан {tag}, штат {state}). "
                f"Сила марша: {power:,}.\n"
                "Доступны /clan и /my_power."
            )
        else:
            await message.answer(
                f"Ты внесён в клан {tag}, штат {state}, как «{name}». Сила марша: {power:,}.\n"
                "Обновлять силу можно командой /my_power (без имени и клана)."
            )
        return
    if code == "no_clan":
        await message.answer(
            f"Клана {tag} с штатом {state} нет. Попроси создателя добавить клан через /create_clan."
        )
        return
    if code == "already_linked":
        await message.answer(
            "Твой Telegram уже привязан к персонажу. Обновляй силу через /my_power."
        )
        return
    if code == "name_claimed_by_other":
        await message.answer(
            "Это имя в клане уже привязано к другому аккаунту Telegram."
        )
        return
    if code == "player_exists":
        await message.answer(
            "Игрок с таким именем в этом клане уже есть. Попроси создателя или выбери другое имя."
        )
        return
    await message.answer("Ошибка при регистрации.")


async def cmd_my_power(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "Формат: /my_power 12_345_678\n"
            "Укажи только новую силу марша (после того как зарегистрировался через /join)."
        )
        return
    suffix = " ".join(parts[1:])
    power = parse_march_power(suffix)
    if power is None:
        await message.answer("Неверное число. Пример: /my_power 12_345_678")
        return
    ok, err = await update_self_power(uid, power)
    if ok:
        row = await get_self_player(uid)
        if row:
            t, st, nm, _ = row
            await message.answer(
                f"Сила марша обновлена: {power:,}.\n"
                f"Персонаж «{nm}», клан {t}, штат {st}."
            )
        else:
            await message.answer(f"Сила марша обновлена: {power:,}.")
        return
    if err == "no_self":
        await message.answer(
            "Ты ещё не зарегистрирован через /join. Сначала вступи в клан с именем и штатом."
        )
        return
    await message.answer("Ошибка при обновлении.")


async def main() -> None:
    if not BOT_TOKEN:
        log.error("Задайте переменную окружения BOT_TOKEN.")
        sys.exit(1)
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_start, Command("help"))
    dp.message.register(cmd_create_clan, Command("create_clan"))
    dp.message.register(cmd_delete_clan, Command("delete_clan"))
    dp.message.register(cmd_add_player, Command("add_player"))
    dp.message.register(cmd_update_player, Command("update_player"))
    dp.message.register(cmd_remove_player, Command("remove_player"))
    dp.message.register(cmd_clan, Command("clan"))
    dp.message.register(cmd_join, Command("join"))
    dp.message.register(cmd_my_power, Command("my_power"))
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
