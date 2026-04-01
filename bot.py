"""
Telegram-бот: кланы (тег из 3 латинских заглавных букв), игроки и сила марша.
Игроками управляет только тот, кто первым зарегистрировал клан в боте.
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
    add_player,
    create_clan,
    get_clan,
    init_db,
    list_players,
    remove_player,
    update_player,
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
    (например 12_345_678, 12,345,678, 12.345.678 — все трактуются как группы разрядов).
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


def parse_add_update_args(text: str) -> tuple[str, str, int] | tuple[None, None, None]:
    """
    /cmd TAG имя сила_марша — имя всё между тегом и хвостом, который парсится как сила марша.
    Сила: целое неотрицательное число; в одном или нескольких токенах допускаются _ , . и пробелы
    между группами (например 12_345_678 или 12 345 678).
    """
    parts = text.split()
    if len(parts) < 4:
        return None, None, None
    tag = parse_tag(parts[1])
    if tag is None:
        return None, None, None
    max_k = len(parts) - 2
    for k in range(max_k, 0, -1):
        suffix = " ".join(parts[-k:])
        power = parse_march_power(suffix)
        if power is None:
            continue
        name = " ".join(parts[2:-k]).strip()
        if not name:
            continue
        return tag, name, power
    return None, None, None


def parse_remove_player_args(text: str) -> tuple[str, str] | tuple[None, None]:
    """Ожидается: /remove_player TAG имя игрока (без числа в конце)."""
    parts = text.split()
    if len(parts) < 3:
        return None, None
    tag = parse_tag(parts[1])
    if tag is None:
        return None, None
    name = " ".join(parts[2:]).strip()
    if not name:
        return None, None
    return tag, name


async def cmd_start(message: Message) -> None:
    await message.answer(
        "Команды:\n"
        "/create_clan ABC — зарегистрировать клан (тег: три латинские заглавные буквы). "
        "Только ты сможешь управлять игроками этого клана.\n\n"
        "/add_player ABC НикИгрока 12_345_678 — добавить игрока и силу марша "
        "(число можно писать с _ , . как разделителями разрядов).\n"
        "/update_player ABC НикИгрока 12,345,678 — обновить силу марша.\n"
        "/remove_player ABC НикИгрока — удалить игрока из списка.\n\n"
        "/clan ABC — все игроки клана по убыванию силы марша.\n"
        "/clan ABC 10 — только топ-10 по силе марша.\n\n"
        "Клан с одним тегом может быть только один."
    )


async def cmd_create_clan(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer(
            "Укажи тег клана: /create_clan ABC (ровно три латинские заглавные буквы)."
        )
        return
    tag = parse_tag(parts[1])
    if tag is None:
        await message.answer(
            "Неверный тег. Нужны ровно три латинские заглавные буквы, например ABC."
        )
        return
    ok, err = await create_clan(tag, uid)
    if ok:
        await message.answer(
            f"Клан {tag} сохранён. Только ты можешь добавлять, менять и удалять игроков этого клана."
        )
        return
    if err == "clan_exists":
        existing = await get_clan(tag)
        if existing and existing[1] == uid:
            await message.answer(f"Клан {tag} уже зарегистрирован тобой.")
        else:
            await message.answer(
                f"Клан с тегом {tag} уже существует. Создать его может только другой игрок раньше."
            )
        return
    await message.answer("Не удалось сохранить клан. Попробуй позже.")


async def cmd_add_player(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    tag, name, power = parse_add_update_args(message.text or "")
    if tag is None:
        await message.answer(
            "Формат: /add_player ABC НикИгрока 12345678\n"
            "Последний аргумент — сила марша (целое число). "
            "Допускаются разделители: _ , . (например 12_345_678 или 12.345.678)."
        )
        return
    ok, err = await add_player(tag, name, power, uid)
    if ok:
        await message.answer(
            f"Игрок «{name}» добавлен в клан {tag}. Сила марша: {power:,}."
        )
        return
    if err == "no_clan":
        await message.answer(f"Клана {tag} нет в базе. Сначала /create_clan {tag}.")
        return
    if err == "not_owner":
        await message.answer(
            "Добавлять игроков может только создатель клана (тот, кто первым зарегистрировал его в боте)."
        )
        return
    if err == "player_exists":
        await message.answer(
            f"Игрок «{name}» в клане {tag} уже есть. Используй /update_player."
        )
        return
    await message.answer("Ошибка при сохранении.")


async def cmd_update_player(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    tag, name, power = parse_add_update_args(message.text or "")
    if tag is None:
        await message.answer(
            "Формат: /update_player ABC НикИгрока 789012\n"
            "Последний аргумент — новая сила марша (целое число, можно с _ , .)."
        )
        return
    ok, err = await update_player(tag, name, power, uid)
    if ok:
        await message.answer(
            f"Игрок «{name}» в клане {tag}: сила марша обновлена на {power:,}."
        )
        return
    if err == "no_clan":
        await message.answer(f"Клана {tag} нет в базе.")
        return
    if err == "not_owner":
        await message.answer(
            "Менять игроков может только создатель клана."
        )
        return
    if err == "no_player":
        await message.answer(
            f"Игрока «{name}» в клане {tag} нет. Сначала /add_player."
        )
        return
    await message.answer("Ошибка при обновлении.")


async def cmd_clan(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2 or len(parts) > 3:
        await message.answer(
            "Формат: /clan ABC — все игроки по убыванию силы марша.\n"
            "/clan ABC N — только N сильнейших (N — целое число от 1)."
        )
        return
    tag = parse_tag(parts[1])
    if tag is None:
        await message.answer("Неверный тег клана.")
        return
    limit: int | None = None
    if len(parts) == 3:
        try:
            n = int(parts[2])
        except ValueError:
            await message.answer(
                "Второй аргумент — целое число: сколько игроков показать (топ по силе). "
                "Пример: /clan ABC 15"
            )
            return
        if n < 1:
            await message.answer("Укажи число не меньше 1 или вызови /clan ABC без лимита.")
            return
        limit = n
    rows = await list_players(tag, limit=limit)
    if rows is None:
        await message.answer(f"Клана {tag} нет в базе.")
        return
    if not rows:
        await message.answer(f"Клан {tag}: пока нет игроков в списке.")
        return
    header = f"Клан {tag}"
    if limit is not None:
        header += f" (топ {limit})"
    header += ", по убыванию силы марша:\n"
    lines = [header]
    for name, p in rows:
        lines.append(f"• {name} — {p:,}")
    await message.answer("\n".join(lines))


async def cmd_remove_player(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    tag, name = parse_remove_player_args(message.text or "")
    if tag is None:
        await message.answer(
            "Формат: /remove_player ABC НикИгрока\n"
            "Имя — всё после тега (можно с пробелами)."
        )
        return
    ok, err = await remove_player(tag, name, uid)
    if ok:
        await message.answer(f"Игрок «{name}» удалён из клана {tag}.")
        return
    if err == "no_clan":
        await message.answer(f"Клана {tag} нет в базе.")
        return
    if err == "not_owner":
        await message.answer(
            "Удалять игроков может только создатель клана."
        )
        return
    if err == "no_player":
        await message.answer(f"Игрока «{name}» в клане {tag} нет в списке.")
        return
    await message.answer("Ошибка при удалении.")


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
    dp.message.register(cmd_add_player, Command("add_player"))
    dp.message.register(cmd_update_player, Command("update_player"))
    dp.message.register(cmd_remove_player, Command("remove_player"))
    dp.message.register(cmd_clan, Command("clan"))
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
