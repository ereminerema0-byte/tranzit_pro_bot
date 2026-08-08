import asyncio
import logging
import os
import re
import sys

# Configure logging early so fail-fast messages are visible
logging.basicConfig(level=logging.INFO)

# Bot configuration
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@tranzitpro1")
# Public @username of the channel for user-facing messages (optional).
# If empty, bot tries CHANNEL_ID when it looks like @name, else getChat.
CHANNEL_USERNAME = (os.getenv("CHANNEL_USERNAME") or "").strip()
# Min unique channel invites required before a non-admin can post ads.
try:
    MIN_CHANNEL_INVITES = max(0, int((os.getenv("MIN_CHANNEL_INVITES") or "5").strip()))
except ValueError:
    MIN_CHANNEL_INVITES = 5
# Set POST_REQUIREMENTS_ENABLED=0 to temporarily disable the gate.
POST_REQUIREMENTS_ENABLED = (os.getenv("POST_REQUIREMENTS_ENABLED") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
# Ads always show the contact of the person who posted.
# CONTACT_USERNAME is intentionally NOT used in announcements (was wrongly
# substituting one fixed hub username for every cargo). Kept only so old
# Railway env vars do not break process startup.
CONTACT_USERNAME = (os.getenv("CONTACT_USERNAME") or "").strip()

# Manual payment for cargo ads (logisticians only). Admin confirms transfer.
def _parse_admin_id(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logging.error("ADMIN_ID должен быть числом (Telegram user id), получено: %r", raw)
        return None


ADMIN_ID = _parse_admin_id(os.getenv("ADMIN_ID"))


def _parse_free_post_usernames(raw) -> frozenset:
    """Comma/space-separated Telegram @usernames (case-insensitive)."""
    names = set()
    for part in re.split(r"[,;\s]+", (raw or "").strip()):
        name = part.strip().lstrip("@").lower()
        if name:
            names.add(name)
    return frozenset(names)


def _parse_free_post_ids(raw) -> frozenset:
    """Comma/space-separated Telegram numeric user ids."""
    ids = set()
    for part in re.split(r"[,;\s]+", (raw or "").strip()):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logging.error(
                "FREE_POST_USER_IDS: ожидался числовой Telegram id, получено: %r",
                part,
            )
    return frozenset(ids)


# Users who may post cargo/vehicles freely (no channel invites, no cargo payment).
# Default includes @Oleg34381. Override via FREE_POST_USERNAMES / FREE_POST_USER_IDS.
# If FREE_POST_USERNAMES is set in env (even empty), that value replaces the default.
_free_usernames_env = os.getenv("FREE_POST_USERNAMES")
FREE_POST_USERNAMES = (
    _parse_free_post_usernames(_free_usernames_env)
    if _free_usernames_env is not None
    else frozenset({"oleg34381"})
)
FREE_POST_USER_IDS = _parse_free_post_ids(os.getenv("FREE_POST_USER_IDS") or "")
# telegram_id -> last seen @username (lowercased) for free-poster checks by id-only paths
_username_by_id: dict[int, str] = {}

# Price for ONE cargo ad. Multi-ad batch = count × this label (same unit text).
AD_POST_PRICE = (os.getenv("AD_POST_PRICE") or "50 000 сум").strip()
# Bank/card/phone details shown to the logistician before publish.
PAYMENT_DETAILS = (
    os.getenv("PAYMENT_DETAILS")
    or "Уточните реквизиты у администратора бота."
).strip()
# Set PAYMENT_ENABLED=0 to temporarily allow free cargo posts for everyone.
PAYMENT_ENABLED = (os.getenv("PAYMENT_ENABLED") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

# Fail-fast: do not start without a valid bot token
if not TOKEN or not str(TOKEN).strip():
    logging.error("Задайте BOT_TOKEN в переменных окружения")
    sys.exit(1)
TOKEN = str(TOKEN).strip()

if not os.getenv("CHANNEL_ID"):
    logging.warning(
        "CHANNEL_ID не задан, используется значение по умолчанию: %s",
        CHANNEL_ID,
    )

if PAYMENT_ENABLED and ADMIN_ID is None:
    logging.warning(
        "Оплата за грузы включена, но ADMIN_ID не задан — "
        "подтверждать переводы будет некому. Задайте ADMIN_ID в env."
    )

from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from db import (
    init_db,
    add_user,
    get_user_role,
    get_driver_id,
    get_logistician_id,
    add_cargo,
    get_cargo_by_route,
    get_logistician_cargo,
    add_vehicle,
    get_vehicles_by_route,
    get_driver_vehicles,
    add_subscription,
    get_subscribers_for_route,
    count_vehicles,
    create_pending_ad,
    get_pending_ad,
    update_pending_ad_status,
    list_pending_ads_for_admin,
    get_user_invite_link_row,
    save_user_invite_link,
    get_inviter_by_link_name,
    get_invite_count,
    record_channel_invite,
)

CITY_FLAGS = {
    "Андижон": "🇺🇿", "Наманган": "🇺🇿", "Ташкент": "🇺🇿", "Самарканд": "🇺🇿", "Бухара": "🇺🇿",
    "Фергана": "🇺🇿", "Кашкадарё": "🇺🇿", "Сурхандарё": "🇺🇿", "Хорезм": "🇺🇿", "Навои": "🇺🇿",
    "Джизак": "🇺🇿", "Сырдарья": "🇺🇿", "Каракалпакстан": "🇺🇿", "Охонгорон": "🇺🇿",
    "Москва": "🇷🇺", "Санкт-Петербург": "🇷🇺", "Нижний Новгород": "🇷🇺", "Екатеринбург": "🇷🇺",
    "Новосибирск": "🇷🇺", "Казань": "🇷🇺", "Барнаул": "🇷🇺", "Челябинск": "🇷🇺", "Самара": "🇷🇺",
    "Ростов": "🇷🇺", "Краснодар": "🇷🇺", "Воронеж": "🇷🇺", "Волгоград": "🇷🇺", "Уфа": "🇷🇺",
    "Пермь": "🇷🇺", "Красноярск": "🇷🇺", "Омск": "🇷🇺", "Тюмень": "🇷🇺", "Заринск": "🇷🇺",
    "Алматы": "🇰🇿", "Астана": "🇰🇿", "Шымкент": "🇰🇿", "Караганда": "🇰🇿",
    "Бишкек": "🇰🇬", "Ош": "🇰🇬",
    "Душанбе": "🇹🇯", "Худжанд": "🇹🇯",
    "Ашхабад": "🇹🇲",
    "Минск": "🇧🇾",
    "Берлин": "🇩🇪", "Гамбург": "🇩🇪",
    "Варшава": "🇵🇱",
    "Стамбул": "🇹🇷", "Анкара": "🇹🇷",
    "Пекин": "🇨🇳", "Шанхай": "🇨🇳", "Урумчи": "🇨🇳", "Кашгар": "🇨🇳",
    "Бейсик": "🇰🇿",
}

# Common misspellings / colloquial city names → canonical
CITY_ALIASES = {
    "алмата": "Алматы",
    "алма-ата": "Алматы",
    "алмаата": "Алматы",
    "нур-султан": "Астана",
    "нурсултан": "Астана",
    "спб": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "санкт петербург": "Санкт-Петербург",
    "югра": "Югра",
    "хмао": "ХМАО",
    "ханты-мансийск": "Ханты-Мансийск",
}

# Words that must never be treated as city names in free-form route parsing
_ROUTE_LABEL_BLOCKLIST = frozenset({
    "груз", "вес", "кузов", "фрахт", "цена", "стоимость", "оплата", "условия",
    "контакт", "телефон", "дата", "погрузка", "загрузка", "машины", "откуда",
    "куда", "темп", "температура", "режим", "тип", "кол-во", "количество",
})


def canonicalize_city(city_name: str) -> str:
    """Normalize city spelling and strip trailing dashes/noise."""
    if not city_name:
        return "Не указано"
    s = str(city_name).strip()
    s = re.sub(r"^[^\wА-Яа-яЁё]+", "", s, flags=re.UNICODE).strip()
    s = s.strip(" \t,;|.-–—")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return "Не указано"
    alias = CITY_ALIASES.get(s.casefold())
    if alias:
        return alias
    # Title-case known cities case-insensitively
    for key in CITY_FLAGS:
        if key.casefold() == s.casefold():
            return key
    return s


def _city_flag_lookup_keys(city: str):
    """Yield name variants used to resolve a flag (full, base, parenthetical)."""
    yield city
    # "Ташкент (Назарбек)" → base "Ташкент", note "Назарбек"
    m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", city)
    if m:
        yield m.group(1).strip()
        yield m.group(2).strip()
    # "АО (Югра)" / bare region names
    for part in re.split(r"[\s,/]+", city):
        part = part.strip("() ")
        if part and len(part) > 1:
            yield part


def get_city_with_flag(city_name):
    if not city_name or city_name == "Не указано":
        return "Не указано"

    city = canonicalize_city(city_name)
    if city == "Не указано":
        return "Не указано"

    if city in CITY_FLAGS:
        return f"{CITY_FLAGS[city]} {city}"

    for key_candidate in _city_flag_lookup_keys(city):
        cand = canonicalize_city(key_candidate)
        if cand in CITY_FLAGS:
            return f"{CITY_FLAGS[cand]} {city}"
        cand_lower = cand.casefold()
        for key, flag in CITY_FLAGS.items():
            if key.casefold() == cand_lower:
                return f"{flag} {city}"

    city_lower = city.casefold()
    # Russian regions / abbreviations often used as origin (АО Югра, ХМАО, …)
    if re.search(r"\b(югра|хмао|янао|ао)\b", city_lower):
        return f"🇷🇺 {city}"

    # Fallback by typical endings (use base name without parentheses)
    base = re.sub(r"\s*\([^)]*\)\s*$", "", city).strip() or city
    base_lower = base.casefold()
    if any(base_lower.endswith(x) for x in ["ск", "град", "бург", "ов", "ино", "ево", "ка", "ль", "мь"]):
        return f"🇷🇺 {city}"
    if any(base_lower.endswith(x) for x in ["он", "арё", "ат", "ент", "ан"]):
        return f"🇺🇿 {city}"

    return city


def escape_md(text) -> str:
    """Escape Telegram legacy Markdown special characters in user input."""
    if text is None:
        return ""
    s = str(text)
    for ch in ("\\", "*", "_", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


def parse_positive_float(text):
    """Parse a positive float from user text. Accepts ',' as decimal separator.
    Returns float or None if invalid / not positive.
    """
    if text is None:
        return None
    s = str(text).strip().replace(",", ".")
    if not s:
        return None
    try:
        val = float(s)
    except ValueError:
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        if not m:
            return None
        try:
            val = float(m.group(1))
        except ValueError:
            return None
    if val <= 0:
        return None
    return val


def extract_phone_contact(text):
    """Extract phone or @username from free text. Returns str or None."""
    if not text:
        return None
    s = str(text).strip()
    # Telegram username
    m = re.search(r"(@[A-Za-z0-9_]{5,})", s)
    if m:
        return m.group(1)
    # Phone: +998..., 9–12 digits with optional spaces/dashes
    m = re.search(r"(\+?\d[\d\s\-]{7,14}\d)", s)
    if m:
        phone = re.sub(r"[\s\-]", "", m.group(1))
        digits = phone.lstrip("+")
        if 9 <= len(digits) <= 12 and digits.isdigit():
            return phone
    return None


def _normalize_user_contact(user_contact) -> str:
    if user_contact is None:
        return ""
    s = str(user_contact).strip()
    if not s or s.lower() in ("не указано", "none", "-"):
        return ""
    return s


def resolve_author_contact(explicit_contact=None, telegram_user=None) -> str:
    """Contact of the person who posted the ad.

    Priority:
    1) phone / @username they entered or that was parsed from the text
    2) their Telegram @username
    Never uses a global hub username (CONTACT_USERNAME).
    """
    contact = _normalize_user_contact(explicit_contact)
    if contact:
        return contact
    if telegram_user is not None:
        username = getattr(telegram_user, "username", None)
        if username:
            return f"@{username}"
        # Last resort: first+last name so the ad still identifies the author
        parts = []
        first = getattr(telegram_user, "first_name", None) or ""
        last = getattr(telegram_user, "last_name", None) or ""
        if first:
            parts.append(str(first).strip())
        if last:
            parts.append(str(last).strip())
        if parts:
            return " ".join(parts)
    return ""


def format_publish_contact(user_contact) -> str:
    """Always show the poster's contact — never a hardcoded hub username."""
    user = _normalize_user_contact(user_contact)
    contact = user or "не указан"
    return f"📞 *Контакт:* {escape_md(contact)}"


async def notify_route_subscribers(
    origin,
    destination,
    summary_text: str,
    author_telegram_id=None,
):
    """Send cargo notification to drivers subscribed to origin→destination."""
    try:
        subscribers = get_subscribers_for_route(origin, destination)
    except Exception as e:
        logging.error("Failed to load subscribers for %s → %s: %s", origin, destination, e)
        return

    for tg_id in subscribers:
        if author_telegram_id is not None and tg_id == author_telegram_id:
            continue
        try:
            await bot.send_message(tg_id, summary_text, parse_mode="Markdown")
        except Exception as e:
            logging.error("Failed to notify subscriber %s: %s", tg_id, e)


def build_cargo_subscriber_notice(
    origin,
    destination,
    cargo_type,
    weight,
    price,
    user_contact,
) -> str:
    return (
        f"🔔 *Новый груз по вашей подписке*\n\n"
        f"📍 *Маршрут:* {escape_md(origin)} → {escape_md(destination)}\n"
        f"🏷️ *Тип:* {escape_md(cargo_type)}\n"
        f"⚖️ *Вес:* {escape_md(weight)}\n"
        f"💰 *Цена:* {escape_md(price)}\n"
        f"{format_publish_contact(user_contact)}"
    )


def is_admin(user_id) -> bool:
    return ADMIN_ID is not None and user_id == ADMIN_ID


def remember_telegram_user(user) -> None:
    """Cache @username for free-poster checks that only receive user_id."""
    if user is None:
        return
    uid = getattr(user, "id", None)
    uname = getattr(user, "username", None)
    if uid is None or not uname:
        return
    _username_by_id[int(uid)] = str(uname).lstrip("@").lower()


def is_free_poster(user_id, username=None) -> bool:
    """True for admin or whitelisted free posters (by id or @username)."""
    if is_admin(user_id):
        return True
    try:
        uid = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        uid = None
    if uid is not None and uid in FREE_POST_USER_IDS:
        return True
    uname = (username or "").strip().lstrip("@").lower()
    if not uname and uid is not None:
        uname = _username_by_id.get(uid, "")
    return bool(uname) and uname in FREE_POST_USERNAMES


def payment_required_for(user_id, username=None) -> bool:
    """Cargo ads are paid unless payment is off or the poster is free/admin."""
    if not PAYMENT_ENABLED:
        return False
    if is_free_poster(user_id, username=username):
        return False
    if ADMIN_ID is None:
        # Nobody can confirm payments — do not block users.
        return False
    return True


# --- Post-ad requirements: channel subscription + N invites ---

_MEMBER_STATUSES = frozenset({"member", "administrator", "creator", "restricted"})
_LEFT_STATUSES = frozenset({"left", "kicked"})
_channel_label_cache = None


def _normalize_channel_username(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("https://t.me/"):
        s = s[len("https://t.me/") :]
    elif s.startswith("t.me/"):
        s = s[len("t.me/") :]
    s = s.strip().lstrip("@").split("/")[0].split("?")[0]
    return f"@{s}" if s else ""


def channel_label_sync() -> str:
    """Best-effort channel name without API (for early messages)."""
    if CHANNEL_USERNAME:
        return _normalize_channel_username(CHANNEL_USERNAME) or CHANNEL_USERNAME
    ch = str(CHANNEL_ID or "").strip()
    if ch.startswith("@"):
        return ch
    return "канал"


async def get_channel_label() -> str:
    """Human-facing channel name: @username or title."""
    global _channel_label_cache
    if _channel_label_cache:
        return _channel_label_cache
    label = channel_label_sync()
    if label.startswith("@"):
        _channel_label_cache = label
        return label
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        if getattr(chat, "username", None):
            label = f"@{chat.username}"
        elif getattr(chat, "title", None):
            label = chat.title
    except Exception as e:
        logging.warning("Could not resolve channel label: %s", e)
    _channel_label_cache = label
    return label


def channel_url_for_button(label: str) -> str | None:
    """Public t.me URL if we know @username."""
    if label and label.startswith("@") and len(label) > 1:
        return f"https://t.me/{label[1:]}"
    uname = _normalize_channel_username(CHANNEL_USERNAME)
    if uname:
        return f"https://t.me/{uname[1:]}"
    ch = str(CHANNEL_ID or "").strip()
    if ch.startswith("@"):
        return f"https://t.me/{ch[1:]}"
    return None


def _member_status_value(member) -> str:
    status = getattr(member, "status", None)
    if status is None:
        return ""
    return status.value if hasattr(status, "value") else str(status)


async def is_user_subscribed(user_id) -> bool:
    """True if user is a member of CHANNEL_ID (or admin/creator)."""
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return _member_status_value(member) in _MEMBER_STATUSES
    except Exception as e:
        logging.warning("get_chat_member failed for %s: %s", user_id, e)
        return False


def invite_link_name_for(user_id: int) -> str:
    # Telegram invite link name max length is 32
    return f"ref{user_id}"[:32]


async def get_or_create_invite_link(user_id: int) -> str | None:
    """Personal channel invite link for tracking who invited whom."""
    row = get_user_invite_link_row(user_id)
    if row and row.get("invite_link"):
        return row["invite_link"]
    name = invite_link_name_for(user_id)
    try:
        link = await bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            name=name,
            creates_join_request=False,
        )
        url = link.invite_link
        save_user_invite_link(user_id, url, name)
        return url
    except Exception as e:
        logging.error(
            "Failed to create invite link for %s (bot must be channel admin "
            "with invite permission): %s",
            user_id,
            e,
        )
        return None


def format_post_requirements_text(
    *,
    subscribed: bool,
    invites: int,
    channel: str,
    invite_link: str | None = None,
) -> str:
    """Clear Russian message when post requirements are not met."""
    need = MIN_CHANNEL_INVITES
    sub_mark = "✅" if subscribed else "❌"
    inv_ok = invites >= need
    inv_mark = "✅" if inv_ok else "❌"
    lines = [
        "Чтобы размещать объявления, нужно:",
        "",
        f"{sub_mark} Подписаться на канал {channel}",
        f"{inv_mark} Пригласить минимум {need} человек в канал",
        f"Сейчас у вас приглашено: {invites} из {need}",
    ]
    if invite_link:
        lines.extend(
            [
                "",
                "Ваша персональная ссылка-приглашение:",
                invite_link,
                "",
                "Отправьте её друзьям. Когда они вступят в канал по этой "
                "ссылке — счётчик увеличится.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Нажмите «Моя ссылка», чтобы получить ссылку для приглашений.",
                "(Бот должен быть админом канала с правом приглашать.)",
            ]
        )
    if subscribed and inv_ok:
        lines = [
            "✅ Условия выполнены — можно размещать объявления!",
            f"Канал: {channel}",
            f"Приглашено: {invites} из {need}",
        ]
    return "\n".join(lines)


def post_requirements_keyboard(channel_url: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if channel_url:
        rows.append(
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_url)]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Проверить подписку",
                callback_data="postreq:check",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔗 Моя ссылка",
                callback_data="postreq:link",
            ),
            InlineKeyboardButton(
                text="📊 Мои приглашения",
                callback_data="postreq:invites",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def check_can_post_ads(user_id, username=None) -> tuple[bool, dict]:
    """Return (allowed, info). Free posters/admins always allowed."""
    channel = await get_channel_label()
    info = {
        "subscribed": True,
        "invites": 0,
        "channel": channel,
        "invite_link": None,
    }
    if is_free_poster(user_id, username=username):
        return True, info
    if not POST_REQUIREMENTS_ENABLED:
        return True, info

    subscribed = await is_user_subscribed(user_id)
    invites = get_invite_count(user_id)
    row = get_user_invite_link_row(user_id)
    invite_link = row["invite_link"] if row else None
    info.update(
        {
            "subscribed": subscribed,
            "invites": invites,
            "invite_link": invite_link,
        }
    )
    allowed = subscribed and invites >= MIN_CHANNEL_INVITES
    return allowed, info


async def deny_posting_if_needed(message: types.Message) -> bool:
    """If user cannot post, send requirements and return True (caller should stop)."""
    user = message.from_user
    remember_telegram_user(user)
    user_id = user.id
    allowed, info = await check_can_post_ads(
        user_id, username=getattr(user, "username", None)
    )
    if allowed:
        return False
    # Ensure invite link is ready when we block
    if not info.get("invite_link"):
        link = await get_or_create_invite_link(user_id)
        info["invite_link"] = link
    text = format_post_requirements_text(
        subscribed=info["subscribed"],
        invites=info["invites"],
        channel=info["channel"],
        invite_link=info.get("invite_link"),
    )
    url = channel_url_for_button(info["channel"])
    await message.answer(text, reply_markup=post_requirements_keyboard(url))
    return True


def format_amount_for_ads(ad_count: int) -> str:
    """Human-readable total: 1× price or N × price = total label."""
    unit = AD_POST_PRICE
    if ad_count <= 1:
        return unit
    return f"{ad_count} × {unit}"


def payment_instructions_text(ad_count: int, amount_label: str, pending_id: int) -> str:
    # Plain text (no Markdown): amount/details may contain *, _, etc.
    return (
        f"💳 Размещение груза — оплата\n\n"
        f"Объявлений: {ad_count}\n"
        f"К оплате: {amount_label}\n\n"
        f"{PAYMENT_DETAILS}\n\n"
        f"После перевода нажмите «Я оплатил» — администратор проверит "
        f"и опубликует объявление.\n"
        f"Заявка №{pending_id}"
    )


def user_payment_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Я оплатил",
                    callback_data=f"pad:paid:{pending_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"pad:cancel:{pending_id}",
                ),
            ]
        ]
    )


def admin_payment_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Оплата получена — опубликовать",
                    callback_data=f"pad:ok:{pending_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"pad:no:{pending_id}",
                ),
            ],
        ]
    )


def summarize_pending_items(items) -> str:
    """Short plain text preview of cargo items for payment/admin messages."""
    lines = []
    for i, item in enumerate(items, 1):
        kind = item.get("kind", "step")
        if kind == "parsed":
            origin = item.get("origin", "?")
            dest = item.get("destination", "?")
            cargo = item.get("cargo", "Не указано")
            price = item.get("price", "Не указано")
            weight = item.get("weight_str", "Не указано")
        else:
            origin = item.get("origin", "?")
            dest = item.get("destination", "?")
            cargo = item.get("cargo_type", "Не указано")
            price = item.get("price", "Не указано")
            weight = item.get("weight", "Не указано")
            if weight not in (None, "Не указано"):
                weight = f"{weight} кг"
        lines.append(
            f"{i}) {origin} → {dest}\n"
            f"   Груз: {cargo} | Вес: {weight} | Фрахт: {price}"
        )
    return "\n".join(lines) if lines else "(пусто)"


async def publish_cargo_item(item: dict, logistician_id: int, author_telegram_id: int):
    """Write one cargo to DB, channel and subscribers. item has kind step|parsed."""
    kind = item.get("kind", "step")
    if kind == "parsed":
        weight_val = parse_positive_float(item.get("weight_str", "")) or 0
        user_contact = item.get("contact") or "не указан"
        loading_date = item.get("loading_date") or "Не указано"
        add_cargo(
            logistician_id,
            item["origin"],
            item["destination"],
            item.get("cargo", "Не указано"),
            weight_val,
            0,
            item.get("price", "Не указано"),
            loading_date,
            user_contact,
        )
        channel_message = format_cargo_message(item) + f"\n🤖 @tranzit_pro_bot"
        try:
            await bot.send_message(CHANNEL_ID, channel_message, parse_mode="Markdown")
        except Exception as e:
            logging.error("Failed to publish to channel: %s", e)
        notice = build_cargo_subscriber_notice(
            item["origin"],
            item["destination"],
            item.get("cargo", "Не указано"),
            item.get("weight_str", "Не указано"),
            item.get("price", "Не указано"),
            user_contact,
        )
        await notify_route_subscribers(
            item["origin"],
            item["destination"],
            notice,
            author_telegram_id=author_telegram_id,
        )
        return

    # kind == "step"
    user_contact = item.get("contact") or "не указан"
    weight = item.get("weight")
    volume = item.get("volume")
    add_cargo(
        logistician_id,
        item["origin"],
        item["destination"],
        item["cargo_type"],
        float(weight),
        float(volume),
        item["price"],
        item["date"],
        user_contact,
    )
    origin_f = f"{item.get('origin_flag', '')} {item['origin']}".strip()
    dest_f = f"{item.get('destination_flag', '')} {item['destination']}".strip()
    price_display = item["price"]
    channel_message = (
        f"📦 *Новый груз*\n\n"
        f"📍 *Откуда:* {escape_md(origin_f)}\n"
        f"📍 *Куда:* {escape_md(dest_f)}\n"
        f"🏷️ *Тип груза:* {escape_md(item['cargo_type'])}\n"
        f"⚖️ *Вес:* {escape_md(weight)} кг\n"
        f"📏 *Объем:* {escape_md(volume)} м³\n"
        f"💰 *Цена:* {escape_md(price_display)}\n"
        f"📅 *Дата готовности:* {escape_md(item['date'])}\n"
        f"{format_publish_contact(user_contact)}\n\n"
        f"🤖 *Хотите быстро найти подходящий груз?*\n"
        f"Напишите боту: @tranzit_pro_bot"
    )
    try:
        await bot.send_message(CHANNEL_ID, channel_message, parse_mode="Markdown")
    except Exception as e:
        logging.error("Failed to publish to channel: %s", e)
    notice = build_cargo_subscriber_notice(
        item["origin"],
        item["destination"],
        item["cargo_type"],
        f"{weight} кг",
        price_display,
        user_contact,
    )
    await notify_route_subscribers(
        item["origin"],
        item["destination"],
        notice,
        author_telegram_id=author_telegram_id,
    )


async def publish_cargo_items(items, logistician_id: int, author_telegram_id: int):
    for item in items:
        await publish_cargo_item(item, logistician_id, author_telegram_id)


async def request_payment_or_publish(
    *,
    message: types.Message,
    logistician_id: int,
    items: list,
    free_success_text: str,
):
    """Publish immediately for admin/free mode, otherwise start manual payment."""
    if not items:
        await message.answer(
            "Нечего публиковать.",
            reply_markup=get_logistician_main_keyboard(),
        )
        return

    user = message.from_user
    remember_telegram_user(user)
    user_id = user.id
    if not payment_required_for(user_id, username=getattr(user, "username", None)):
        await publish_cargo_items(items, logistician_id, user_id)
        await message.answer(
            free_success_text,
            reply_markup=get_logistician_main_keyboard(),
        )
        return

    ad_count = len(items)
    amount_label = format_amount_for_ads(ad_count)
    pending_id = create_pending_ad(
        telegram_id=user_id,
        logistician_id=logistician_id,
        cargo_items=items,
        amount_label=amount_label,
    )
    preview = summarize_pending_items(items)
    text = (
        payment_instructions_text(ad_count, amount_label, pending_id)
        + f"\n\n📋 К размещению:\n{preview}"
    )
    await message.answer(
        text,
        reply_markup=user_payment_keyboard(pending_id),
    )
    await message.answer(
        "Главное меню (объявление появится после подтверждения оплаты).",
        reply_markup=get_logistician_main_keyboard(),
    )


# Initialize bot and dispatcher
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Define states for FSM
class UserRole(StatesGroup):
    choosing_role = State()

class DriverStates(StatesGroup):
    main_menu = State()
    searching_cargo_origin = State()
    searching_cargo_destination = State()
    subscribing_origin = State()
    subscribing_destination = State()
    adding_vehicle_origin = State()
    adding_vehicle_origin_country = State()
    adding_vehicle_destination = State()
    adding_vehicle_destination_country = State()
    adding_vehicle_body_type = State()
    adding_vehicle_capacity = State()
    adding_vehicle_date = State()
    adding_vehicle_contact = State()
    viewing_my_vehicles = State()

class LogisticianStates(StatesGroup):
    main_menu = State()
    choosing_placement_mode = State()
    single_message_input = State()
    confirming_cargo = State()
    adding_cargo_origin = State()
    adding_cargo_origin_country = State()
    adding_cargo_destination = State()
    adding_cargo_destination_country = State()
    adding_cargo_type = State()
    adding_cargo_weight = State()
    adding_cargo_volume = State()
    adding_cargo_price = State()
    adding_cargo_date = State()
    adding_cargo_contact = State()
    searching_vehicles_origin = State()
    searching_vehicles_destination = State()
    viewing_my_cargo = State()

# --- Keyboards ---

def get_role_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🟢 Я водитель"))
    builder.add(types.KeyboardButton(text="🔵 Я логист"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_driver_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🔍 Найти груз"))
    builder.add(types.KeyboardButton(text="🔔 Подписка на направления"))
    builder.add(types.KeyboardButton(text="🚚 Разместить свободную машину"))
    builder.add(types.KeyboardButton(text="📋 Мои объявления"))
    builder.add(types.KeyboardButton(text="📊 Мои приглашения"))
    builder.add(types.KeyboardButton(text="🔄 Сменить роль"))
    builder.adjust(2, 2, 2)
    return builder.as_markup(resize_keyboard=True)

def get_logistician_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="📦 Разместить груз"))
    builder.add(types.KeyboardButton(text="🔍 Найти груз"))
    builder.add(types.KeyboardButton(text="🚛 Найти свободные машины"))
    builder.add(types.KeyboardButton(text="📋 Мои объявления"))
    builder.add(types.KeyboardButton(text="📊 Мои приглашения"))
    builder.add(types.KeyboardButton(text="🔄 Сменить роль"))
    builder.adjust(2, 2, 2)
    return builder.as_markup(resize_keyboard=True)

def get_country_keyboard():
    builder = ReplyKeyboardBuilder()
    countries = [
        "🇷🇺 Россия", "🇺🇿 Узбекистан", "🇰🇿 Казахстан", "🇰🇬 Кыргызстан",
        "🇹🇯 Таджикистан", "🇹🇲 Туркменистан", "🇧🇾 Беларусь", "🇹🇷 Турция",
        "🇨🇳 Китай", "🇩🇪 Германия", "🇵🇱 Польша", "Другая"
    ]
    for country in countries:
        builder.add(types.KeyboardButton(text=country))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True)


def _clean_city_input(text) -> str:
    """Strip spaces and leading flag/emoji from a city typed by the user."""
    if not text:
        return ""
    s = str(text).strip()
    s = re.sub(r"^[^\wА-Яа-яЁё]+", "", s, flags=re.UNICODE).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def format_vehicle_card(vehicle) -> str:
    """vehicle row: id, driver_id, body_type, capacity, origin, destination, date, contact"""
    return (
        f"\n🚚 Тип кузова: {vehicle[2]}\n"
        f"⚖️ Грузоподъёмность: {vehicle[3]} т\n"
        f"📍 Откуда: {vehicle[4]}\n"
        f"📍 Куда: {vehicle[5]}\n"
        f"📅 Дата: {vehicle[6]}\n"
        f"📞 Контакт: {vehicle[7]}\n"
        f"---"
    )


def _fmt_num(value) -> str:
    """Pretty number: 24.0 → '24', 24.5 → '24.5'."""
    if value is None or value == "":
        return "0"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def format_cargo_card(cargo) -> str:
    """cargo row: id, logistician_id, origin, destination, type, weight, volume, price, date, contact"""
    lines = [
        f"\n📍 Откуда: {cargo[2]}",
        f"📍 Куда: {cargo[3]}",
        f"📦 Тип: {cargo[4]}",
        f"⚖️ Вес: {_fmt_num(cargo[5])} кг",
    ]
    try:
        volume = float(cargo[6]) if cargo[6] is not None else 0.0
    except (TypeError, ValueError):
        volume = None
    if volume not in (None, 0.0):
        lines.append(f"📏 Объем: {_fmt_num(volume)} м³")
    lines.extend(
        [
            f"💰 Цена: {cargo[7]}",
            f"📅 Дата: {cargo[8]}",
            f"📞 Контакт: {cargo[9]}",
            "---",
        ]
    )
    return "\n".join(lines)

# --- Handlers ---

@dp.message(CommandStart())
async def command_start_handler(message: types.Message, state: FSMContext):
    user_role = get_user_role(message.from_user.id)
    if user_role == 'driver':
        await message.answer("С возвращением, водитель!", reply_markup=get_driver_main_keyboard())
        await state.set_state(DriverStates.main_menu)
    elif user_role == 'logistician':
        await message.answer("С возвращением, логист!", reply_markup=get_logistician_main_keyboard())
        await state.set_state(LogisticianStates.main_menu)
    else:
        await message.answer("Привет! Я бот для грузоперевозок. Пожалуйста, выберите вашу роль:", reply_markup=get_role_keyboard())
        await state.set_state(UserRole.choosing_role)

@dp.message(F.text == "🟢 Я водитель")
async def set_role_driver(message: types.Message, state: FSMContext):
    add_user(message.from_user.id, "driver")
    await message.answer("Вы выбрали роль водителя.", reply_markup=get_driver_main_keyboard())
    await state.set_state(DriverStates.main_menu)

@dp.message(F.text == "🔵 Я логист")
async def set_role_logistician(message: types.Message, state: FSMContext):
    add_user(message.from_user.id, "logistician")
    await message.answer("Вы выбрали роль логиста.", reply_markup=get_logistician_main_keyboard())
    await state.set_state(LogisticianStates.main_menu)

# --- Driver Handlers ---

def _cargo_search_menu_labels():
    return {
        "📦 Разместить груз",
        "🔍 Найти груз",
        "🚛 Найти свободные машины",
        "📋 Мои объявления",
        "📊 Мои приглашения",
        "🔄 Сменить роль",
        "🟢 Я водитель",
        "🔵 Я логист",
        "🚚 Разместить свободную машину",
        "🔔 Подписка на направления",
        "❌ Отмена",
    }


def _main_keyboard_for_role(role):
    if role == "logistician":
        return get_logistician_main_keyboard()
    return get_driver_main_keyboard()


def _main_state_for_role(role):
    if role == "logistician":
        return LogisticianStates.main_menu
    return DriverStates.main_menu


@dp.message(F.text == "🔍 Найти груз")
async def driver_search_cargo_start(message: types.Message, state: FSMContext):
    await message.answer(
        "Введите *город отправления* для поиска груза (например: Шымкент):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown",
    )
    await state.set_state(DriverStates.searching_cargo_origin)


@dp.message(DriverStates.searching_cargo_origin, F.text == "❌ Отмена")
@dp.message(DriverStates.searching_cargo_destination, F.text == "❌ Отмена")
async def driver_search_cargo_cancel(message: types.Message, state: FSMContext):
    role = get_user_role(message.from_user.id)
    await message.answer("Поиск отменён.", reply_markup=_main_keyboard_for_role(role))
    await state.set_state(_main_state_for_role(role))


@dp.message(DriverStates.searching_cargo_origin)
async def driver_search_cargo_origin(message: types.Message, state: FSMContext):
    origin = _clean_city_input(message.text)
    if not origin:
        await message.answer("Введите город отправления текстом, например: Шымкент")
        return
    if message.text and message.text.strip() in _cargo_search_menu_labels():
        await message.answer(
            "Сначала введите город отправления или нажмите «❌ Отмена».",
            reply_markup=get_cancel_keyboard(),
        )
        return
    await state.update_data(search_origin=origin)
    await message.answer(
        "Введите *город назначения* (например: Ташкент):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown",
    )
    await state.set_state(DriverStates.searching_cargo_destination)


@dp.message(DriverStates.searching_cargo_destination)
async def driver_search_cargo_destination(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    origin = user_data.get("search_origin") or ""
    destination = _clean_city_input(message.text)
    role = get_user_role(message.from_user.id)
    if not destination:
        await message.answer("Введите город назначения текстом, например: Ташкент")
        return
    if message.text and message.text.strip() in _cargo_search_menu_labels():
        await message.answer(
            "Сначала введите город назначения или нажмите «❌ Отмена».",
            reply_markup=get_cancel_keyboard(),
        )
        return

    cargo_list = get_cargo_by_route(origin, destination)
    if cargo_list:
        response = f"Найденные грузы ({origin} → {destination}):\n"
        for cargo in cargo_list:
            response += format_cargo_card(cargo)
        # Telegram message limit ~4096; keep a safe head if the list is huge
        if len(response) > 4000:
            response = response[:3900].rstrip() + "\n\n…список обрезан, уточните направление."
    else:
        response = f"Грузов по направлению {origin} → {destination} не найдено."

    await message.answer(response, reply_markup=_main_keyboard_for_role(role))
    await state.set_state(_main_state_for_role(role))

@dp.message(F.text == "🔔 Подписка на направления")
async def driver_subscribe_start(message: types.Message, state: FSMContext):
    await message.answer("Введите город отправления для подписки:")
    await state.set_state(DriverStates.subscribing_origin)

@dp.message(DriverStates.subscribing_origin)
async def driver_subscribe_origin(message: types.Message, state: FSMContext):
    await state.update_data(subscribe_origin=message.text)
    await message.answer("Введите город назначения для подписки:")
    await state.set_state(DriverStates.subscribing_destination)

@dp.message(DriverStates.subscribing_destination)
async def driver_subscribe_destination(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    driver_id = get_driver_id(message.from_user.id)
    if driver_id is None:
        await message.answer(
            "Сначала выберите роль водителя.",
            reply_markup=get_role_keyboard(),
        )
        await state.set_state(UserRole.choosing_role)
        return
    origin = user_data['subscribe_origin']
    destination = message.text
    add_subscription(driver_id, origin, destination)
    await message.answer(f"Вы подписались на направление {origin} -> {destination}. Вы будете получать уведомления о новых грузах.", reply_markup=get_driver_main_keyboard())
    await state.set_state(DriverStates.main_menu)

@dp.message(F.text == "🚚 Разместить свободную машину")
async def driver_add_vehicle_start(message: types.Message, state: FSMContext):
    if await deny_posting_if_needed(message):
        return
    await message.answer("Введите город отправления:")
    await state.set_state(DriverStates.adding_vehicle_origin)

@dp.message(DriverStates.adding_vehicle_origin)
async def driver_add_vehicle_origin(message: types.Message, state: FSMContext):
    await state.update_data(origin=message.text)
    await message.answer("Выберите страну отправления:", reply_markup=get_country_keyboard())
    await state.set_state(DriverStates.adding_vehicle_origin_country)

@dp.message(DriverStates.adding_vehicle_origin_country)
async def driver_add_vehicle_origin_country(message: types.Message, state: FSMContext):
    flag = message.text.split()[0] if " " in message.text else ""
    await state.update_data(origin_flag=flag)
    await message.answer("Введите город назначения:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(DriverStates.adding_vehicle_destination)

@dp.message(DriverStates.adding_vehicle_destination)
async def driver_add_vehicle_destination(message: types.Message, state: FSMContext):
    await state.update_data(destination=message.text)
    await message.answer("Выберите страну назначения:", reply_markup=get_country_keyboard())
    await state.set_state(DriverStates.adding_vehicle_destination_country)

@dp.message(DriverStates.adding_vehicle_destination_country)
async def driver_add_vehicle_destination_country(message: types.Message, state: FSMContext):
    flag = message.text.split()[0] if " " in message.text else ""
    await state.update_data(destination_flag=flag)
    await message.answer("Введите тип кузова (например, тент, рефрижератор, фургон):", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(DriverStates.adding_vehicle_body_type)

@dp.message(DriverStates.adding_vehicle_body_type)
async def driver_add_vehicle_body_type(message: types.Message, state: FSMContext):
    await state.update_data(body_type=message.text)
    await message.answer("Введите грузоподъёмность в тоннах (например, 20):")
    await state.set_state(DriverStates.adding_vehicle_capacity)

@dp.message(DriverStates.adding_vehicle_capacity)
async def driver_add_vehicle_capacity(message: types.Message, state: FSMContext):
    capacity = parse_positive_float(message.text)
    if capacity is None:
        await message.answer("Введите число, например 20")
        return
    await state.update_data(capacity=capacity)
    await message.answer("Введите дату готовности машины (например, ДД.ММ.ГГГГ):")
    await state.set_state(DriverStates.adding_vehicle_date)

@dp.message(DriverStates.adding_vehicle_date)
async def driver_add_vehicle_date(message: types.Message, state: FSMContext):
    await state.update_data(date=message.text)
    await message.answer("Введите ваш контакт для связи (телефон или имя пользователя Telegram):")
    await state.set_state(DriverStates.adding_vehicle_contact)

@dp.message(DriverStates.adding_vehicle_contact)
async def driver_add_vehicle_contact(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    driver_id = get_driver_id(message.from_user.id)
    if driver_id is None:
        await message.answer(
            "Сначала выберите роль водителя.",
            reply_markup=get_role_keyboard(),
        )
        await state.set_state(UserRole.choosing_role)
        return
    capacity = user_data.get('capacity')
    if not isinstance(capacity, (int, float)):
        capacity = parse_positive_float(capacity)
    if capacity is None:
        await message.answer(
            "Некорректная грузоподъёмность. Введите число, например 20",
            reply_markup=get_driver_main_keyboard(),
        )
        await state.set_state(DriverStates.main_menu)
        return
    author_contact = resolve_author_contact(message.text, message.from_user)
    origin_city = _clean_city_input(user_data['origin']) or str(user_data['origin']).strip()
    dest_city = _clean_city_input(user_data['destination']) or str(user_data['destination']).strip()
    add_vehicle(
        driver_id,
        user_data['body_type'],
        float(capacity),
        origin_city,
        dest_city,
        user_data['date'],
        author_contact,
    )
    await message.answer("Ваше объявление о свободной машине размещено!", reply_markup=get_driver_main_keyboard())
    await state.set_state(DriverStates.main_menu)

    # Auto-publish to channel
    origin_f = f"{user_data.get('origin_flag', '')} {origin_city}".strip()
    dest_f = f"{user_data.get('destination_flag', '')} {dest_city}".strip()
    channel_message = (
        f"🚚 *Свободная машина*\n\n"
        f"📍 *Откуда:* {escape_md(origin_f)}\n"
        f"📍 *Куда:* {escape_md(dest_f)}\n"
        f"📦 *Тип кузова:* {escape_md(user_data['body_type'])}\n"
        f"⚖️ *Грузоподъёмность:* {escape_md(capacity)} т\n"
        f"📅 *Дата готовности:* {escape_md(user_data['date'])}\n"
        f"{format_publish_contact(author_contact)}\n\n"
        f"🤖 @tranzit_pro_bot"
    )
    try:
        await bot.send_message(CHANNEL_ID, channel_message, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Failed to publish to channel: {e}")

@dp.message(F.text == "📋 Мои объявления")
async def view_my_ads_router(message: types.Message, state: FSMContext):
    role = get_user_role(message.from_user.id)
    if role == 'driver':
        driver_id = get_driver_id(message.from_user.id)
        vehicles = get_driver_vehicles(driver_id)
        if vehicles:
            response = "Ваши объявления о машинах:\n"
            for vehicle in vehicles:
                response += f"\nТип кузова: {vehicle[2]}\nГрузоподъёмность: {vehicle[3]} т\nОткуда: {vehicle[4]}\nКуда: {vehicle[5]}\nДата: {vehicle[6]}\nКонтакт: {vehicle[7]}\n---"
        else:
            response = "У вас пока нет размещенных объявлений о машинах."
        await message.answer(response, reply_markup=get_driver_main_keyboard())
        await state.set_state(DriverStates.main_menu)
    else:
        logistician_id = get_logistician_id(message.from_user.id)
        cargo_list = get_logistician_cargo(logistician_id)
        if cargo_list:
            response = "Ваши объявления о грузах:\n"
            for cargo in cargo_list:
                response += f"\nОткуда: {cargo[2]}\nКуда: {cargo[3]}\nТип: {cargo[4]}\nВес: {cargo[5]} кг\nОбъем: {cargo[6]} м³\nЦена: {cargo[7]}\nДата: {cargo[8]}\nКонтакт: {cargo[9]}\n---"
        else:
            response = "У вас пока нет размещенных объявлений о грузах."
        await message.answer(response, reply_markup=get_logistician_main_keyboard())
        await state.set_state(LogisticianStates.main_menu)

@dp.message(F.text == "🔄 Сменить роль")
async def change_role(message: types.Message, state: FSMContext):
    await message.answer("Выберите новую роль:", reply_markup=get_role_keyboard())
    await state.set_state(UserRole.choosing_role)


@dp.message(F.text == "📊 Мои приглашения")
async def my_invites_button(message: types.Message, state: FSMContext):
    """Show subscription + invite progress and personal invite link."""
    user = message.from_user
    remember_telegram_user(user)
    user_id = user.id
    if is_free_poster(user_id, username=getattr(user, "username", None)):
        await message.answer(
            "Вы можете размещать объявления без "
            "подписки, приглашений и оплаты."
        )
        return
    channel = await get_channel_label()
    subscribed = await is_user_subscribed(user_id)
    invites = get_invite_count(user_id)
    link = await get_or_create_invite_link(user_id)
    text = format_post_requirements_text(
        subscribed=subscribed,
        invites=invites,
        channel=channel,
        invite_link=link,
    )
    url = channel_url_for_button(channel)
    await message.answer(text, reply_markup=post_requirements_keyboard(url))


# --- Logistician Handlers ---

@dp.message(F.text == "📦 Разместить груз")
async def logistician_add_cargo_start(message: types.Message, state: FSMContext):
    if await deny_posting_if_needed(message):
        return
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Пошагово"))
    builder.add(types.KeyboardButton(text="Одним сообщением"))
    builder.add(types.KeyboardButton(text="Назад"))
    builder.adjust(2)
    await message.answer("Выберите способ размещения:", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(LogisticianStates.choosing_placement_mode)

@dp.message(LogisticianStates.choosing_placement_mode, F.text == "Пошагово")
async def logistician_add_cargo_step_by_step(message: types.Message, state: FSMContext):
    await message.answer("Введите город отправления для груза:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(LogisticianStates.adding_cargo_origin)

@dp.message(LogisticianStates.adding_cargo_origin)
async def logistician_add_cargo_origin(message: types.Message, state: FSMContext):
    await state.update_data(origin=message.text)
    await message.answer("Выберите страну отправления:", reply_markup=get_country_keyboard())
    await state.set_state(LogisticianStates.adding_cargo_origin_country)

@dp.message(LogisticianStates.adding_cargo_origin_country)
async def logistician_add_cargo_origin_country(message: types.Message, state: FSMContext):
    flag = message.text.split()[0] if " " in message.text else ""
    await state.update_data(origin_flag=flag)
    await message.answer("Введите город назначения для груза:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(LogisticianStates.adding_cargo_destination)

@dp.message(LogisticianStates.adding_cargo_destination)
async def logistician_add_cargo_destination(message: types.Message, state: FSMContext):
    await state.update_data(destination=message.text)
    await message.answer("Выберите страну назначения:", reply_markup=get_country_keyboard())
    await state.set_state(LogisticianStates.adding_cargo_destination_country)

@dp.message(LogisticianStates.adding_cargo_destination_country)
async def logistician_add_cargo_destination_country(message: types.Message, state: FSMContext):
    flag = message.text.split()[0] if " " in message.text else ""
    await state.update_data(destination_flag=flag)
    await message.answer("Введите тип груза (например, паллеты, коробки):", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(LogisticianStates.adding_cargo_type)

@dp.message(LogisticianStates.adding_cargo_type)
async def logistician_add_cargo_type(message: types.Message, state: FSMContext):
    await state.update_data(cargo_type=message.text)
    await message.answer("Введите вес груза в кг:")
    await state.set_state(LogisticianStates.adding_cargo_weight)

@dp.message(LogisticianStates.adding_cargo_weight)
async def logistician_add_cargo_weight(message: types.Message, state: FSMContext):
    weight = parse_positive_float(message.text)
    if weight is None:
        await message.answer("Введите число, например 20")
        return
    await state.update_data(weight=weight)
    await message.answer("Введите объем груза в м³:")
    await state.set_state(LogisticianStates.adding_cargo_volume)

@dp.message(LogisticianStates.adding_cargo_volume)
async def logistician_add_cargo_volume(message: types.Message, state: FSMContext):
    volume = parse_positive_float(message.text)
    if volume is None:
        await message.answer("Введите число, например 20")
        return
    await state.update_data(volume=volume)
    await message.answer("Введите цену:")
    await state.set_state(LogisticianStates.adding_cargo_price)

@dp.message(LogisticianStates.adding_cargo_price)
async def logistician_add_cargo_price(message: types.Message, state: FSMContext):
    await state.update_data(price=message.text)
    await message.answer("Введите дату готовности груза:")
    await state.set_state(LogisticianStates.adding_cargo_date)

@dp.message(LogisticianStates.adding_cargo_date)
async def logistician_add_cargo_date(message: types.Message, state: FSMContext):
    await state.update_data(date=message.text)
    await message.answer("Введите контакт:")
    await state.set_state(LogisticianStates.adding_cargo_contact)

@dp.message(LogisticianStates.adding_cargo_contact)
async def logistician_add_cargo_contact(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    logistician_id = get_logistician_id(message.from_user.id)
    if logistician_id is None:
        await message.answer(
            "Сначала выберите роль логиста.",
            reply_markup=get_role_keyboard(),
        )
        await state.set_state(UserRole.choosing_role)
        return

    weight = user_data.get('weight')
    volume = user_data.get('volume')
    if not isinstance(weight, (int, float)):
        weight = parse_positive_float(weight)
    if not isinstance(volume, (int, float)):
        volume = parse_positive_float(volume)
    if weight is None or volume is None:
        await message.answer(
            "Некорректный вес или объём. Начните размещение заново.",
            reply_markup=get_logistician_main_keyboard(),
        )
        await state.set_state(LogisticianStates.main_menu)
        return

    user_contact = resolve_author_contact(message.text, message.from_user)
    item = {
        "kind": "step",
        "origin": user_data["origin"],
        "destination": user_data["destination"],
        "origin_flag": user_data.get("origin_flag", ""),
        "destination_flag": user_data.get("destination_flag", ""),
        "cargo_type": user_data["cargo_type"],
        "weight": float(weight),
        "volume": float(volume),
        "price": user_data["price"],
        "date": user_data["date"],
        "contact": user_contact,
    }
    await state.set_state(LogisticianStates.main_menu)
    await request_payment_or_publish(
        message=message,
        logistician_id=logistician_id,
        items=[item],
        free_success_text="Ваш груз размещен!",
    )

@dp.message(LogisticianStates.choosing_placement_mode, F.text == "Одним сообщением")
async def logistician_add_cargo_single_msg(message: types.Message, state: FSMContext):
    await message.answer(
        "Отправьте информацию о грузе **одним сообщением** в свободной форме.\n\n"
        "**Пример:**\n"
        "🇰🇿 Алматы\n"
        "🇺🇿 Ташкент\n"
        "Груз энергетик\n"
        "Вес 22 тонн\n"
        "2 машины\n"
        "тент реф +5\n"
        "погрузка сверху сбоку\n"
        "дата 25.07\n"
        "Груз готов\n"
        "Оплата нал 1200$\n\n"
        "Можно и с подписями: Откуда / Куда / Груз / Вес / Кузов / "
        "Кол-во машин / Дата погрузки / Погрузка (сверху/сбоку/сзади) / "
        "Температура / Фрахт / Условия.",
        parse_mode="Markdown",
    )
    await state.set_state(LogisticianStates.single_message_input)

@dp.message(LogisticianStates.choosing_placement_mode, F.text == "Назад")
async def logistician_add_cargo_back(message: types.Message, state: FSMContext):
    await message.answer("Главное меню", reply_markup=get_logistician_main_keyboard())
    await state.set_state(LogisticianStates.main_menu)

# Labels that start a new field in free-form cargo ads (used as stop markers).
_CARGO_FIELD_LABELS = (
    "откуда", "куда", "груз", "тип груза", "вес", "кузов", "тип кузова",
    "фрахт", "цена", "стоимость", "оплата", "условия", "контакт", "телефон",
    "дата", "дата погрузки", "погрузка", "способ погрузки", "машины",
    "кол-во машин", "количество машин", "температура", "темп", "режим",
    "температурный режим",
)

# Lines that look like field lines, not city names
_CITY_LINE_SKIP = re.compile(
    r"^(груз|вес|кузов|фрахт|цена|стоимость|оплата|условия|контакт|телефон|"
    r"дата|погрузк|машин|кол[\-\s]?во|темп|режим|тент|реф|изотерм|борт|"
    r"площадк|фур|налич|безнал|аванс|готов|срочн|сверху|сбоку|сзади|"
    r"оплата|нал\b|перечисл)",
    re.IGNORECASE,
)


def _strip_field_noise(value: str) -> str:
    """Clean a single field value: one line, no leading emoji noise."""
    if not value:
        return ""
    # Take only the first line so multi-line capture cannot leak other fields
    s = str(value).splitlines()[0].strip()
    # Drop leading location/package emoji and bullet markers
    s = re.sub(r"^[\s📍🔹📦⚖️🚚🚛💰📞🏷️📅🌡⬆️⬇️⬅️➡️\-•*]+", "", s)
    s = s.strip(" \t,;|")
    return s


def _extract_labeled_field(text: str, labels) -> str:
    """Extract value after 'Label:' or 'Label ' up to end of line."""
    if not text:
        return ""
    for label in labels:
        # Optional emoji/bullet prefix before the label (📍 Откуда: …)
        # Colon optional: "Груз энергетик", "Вес 22 тонн"
        pat = (
            rf"(?:^|\n)\s*(?:[📍🔹📦⚖️🚚🚛💰📞🏷️📅🌡⬆️\-•*]+\s*)?"
            rf"{re.escape(label)}\s*[:\-–—]?\s*(.+?)(?:\s*$|\n)"
        )
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = _strip_field_noise(m.group(1))
            # Avoid treating bare "готов" line as cargo when matched via partial labels
            if val:
                return val
    return ""


def _normalize_city_name(name: str) -> str:
    """City only — strip flags, commas, and any leaked extra fields."""
    s = _strip_field_noise(name)
    if not s:
        return "Не указано"
    # If a leaked label slipped in, cut before it
    lower = s.lower()
    for lab in _CARGO_FIELD_LABELS:
        idx = lower.find(lab + ":")
        if idx > 0:
            s = s[:idx].strip(" \t,;")
            lower = s.lower()
            break
        idx = lower.find(lab + " :")
        if idx > 0:
            s = s[:idx].strip(" \t,;")
            lower = s.lower()
            break
    s = s.strip(" \t,;.-–—")
    return canonicalize_city(s)


def _looks_like_route_place(name: str) -> bool:
    """True if token is a place, not a field label like 'Груз' / 'Вес'."""
    if not name:
        return False
    base = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    raw = base or name
    low = raw.casefold()
    if low in _ROUTE_LABEL_BLOCKLIST:
        return False
    # Reject pure numbers / prices
    if re.fullmatch(r"[\d\s.,$]+", raw):
        return False
    return True


def _extract_route_from_city_lines(text: str):
    """Parse multi-line free-form routes like:
    🇰🇿 Алмата-
    🇺🇿 Ташкент
    🇷🇺  АО (Югра)
    🇺🇿 Ташкент (Назарбек)
    """
    found = []
    # Place line: 1–4 words (incl. ALL-CAPS abbr like АО/ХМАО),
    # optional trailing (district/region note): "Ташкент (Назарбек)"
    _w = r"[А-ЯЁA-Za-zа-яё0-9][А-ЯЁA-Za-zа-яё0-9\-]*"
    place_re = re.compile(
        rf"^({_w}(?:\s+{_w}){{0,3}}(?:\s*\([^)]+\))?)$"
    )
    for raw in text.splitlines():
        s = re.sub(r"^[^\wА-Яа-яЁё]+", "", raw, flags=re.UNICODE).strip()
        s = s.strip(" \t,;|.-–—")
        if not s or _CITY_LINE_SKIP.search(s):
            continue
        # Skip explicit "Label: value" lines — those are fields, not cities
        if re.match(
            r"^(?:откуда|куда|груз|вес|кузов|фрахт|цена|стоимость|оплата|"
            r"условия|контакт|телефон|дата|погрузка|загрузка|машин|"
            r"темп|режим|кол[\-\s]?во)\s*[:\-–—]",
            s,
            re.IGNORECASE,
        ):
            continue
        m = place_re.match(s)
        if not m:
            continue
        place = m.group(1).strip()
        if not _looks_like_route_place(place):
            continue
        city = canonicalize_city(place)
        if city != "Не указано" and _looks_like_route_place(city):
            found.append(city)
        if len(found) >= 2:
            break
    if len(found) >= 2:
        return found[0], found[1]
    return "", ""


def _guess_cargo_type(text_lower: str) -> str:
    if "энергетик" in text_lower:
        return "Энергетик"
    if "сахар" in text_lower:
        return "Сахар"
    if "тахта" in text_lower:
        return "Тахта"
    if "дсп" in text_lower:
        return "ДСП"
    if "рулон" in text_lower or "бумаг" in text_lower:
        return "Рулонная бумага"
    if "лук" in text_lower:
        return "Лук"
    if "арбуз" in text_lower:
        return "Арбуз"
    if "пиломатериал" in text_lower or "кругляк" in text_lower or "цилиндровк" in text_lower or "оцилиндр" in text_lower:
        return "Пиломатериалы"
    if "запчаст" in text_lower:
        return "Запчасти"
    if "салафан" in text_lower:
        return "Прессованные салафаны"
    if "гранит" in text_lower:
        return "Гранит"
    if "масло" in text_lower:
        return "Масло"
    if "алюмин" in text_lower or "профиль" in text_lower:
        return "Алюминиевый профиль"
    if "бор" in text_lower and "борт" not in text_lower:
        return "Бор"
    if "мебел" in text_lower:
        return "Мебель"
    if "продукт" in text_lower or "еда" in text_lower or "пищев" in text_lower:
        return "Продукты"
    if "овощ" in text_lower or "фрукт" in text_lower:
        return "Овощи/фрукты"
    return "Не указано"


def _parse_body_types(full_text: str, full_lower: str) -> str:
    labeled = _extract_labeled_field(full_text, ("Кузов", "Тип кузова"))
    bodies = []
    src = (labeled + " " + full_lower).lower() if labeled else full_lower

    checks = [
        (r"реф(?:риж(?:ератор)?)?|рефриж", "Реф"),
        (r"тент", "Тент"),
        (r"изотерм", "Изотерм"),
        (r"бортов|борт\b", "Борт"),
        (r"площадк|открыт", "Площадка"),
        (r"контейнер", "Контейнер"),
        (r"цельнометалл|фургон", "Фургон"),
    ]
    for pat, name in checks:
        if re.search(pat, src):
            if name not in bodies:
                bodies.append(name)

    if labeled and not bodies:
        # Keep free text from label if no known keywords
        return labeled
    if labeled and bodies:
        # Prefer structured known types, append extra words from label if useful
        return " / ".join(bodies)
    return " / ".join(bodies) if bodies else "Не указано"


def _parse_vehicles_count(full_text: str, full_lower: str) -> str:
    labeled = _extract_labeled_field(
        full_text,
        ("Кол-во машин", "Количество машин", "Машины", "Кол-во", "Количество"),
    )
    if labeled:
        m = re.search(r"(\d{1,2})", labeled)
        if m:
            return m.group(1)
    m = re.search(
        r"(\d{1,2})\s*(?:маш(?:ин[аыу]?)?|а/?м\b|авто|фур[аы]?|тс\b)",
        full_lower,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"(?:кол[\-\s]?во|количество)\s*(?:маш(?:ин)?|а/?м|авто)?\s*[:\-]?\s*(\d{1,2})",
        full_lower,
    )
    if m:
        return m.group(1)
    return "Не указано"


def _parse_loading_date(full_text: str, full_lower: str) -> str:
    labeled = _extract_labeled_field(
        full_text,
        ("Дата погрузки", "Дата готовности", "Дата"),
    )
    # "Погрузка: сверху" is a method, not a date — only accept if looks like a date
    pog = _extract_labeled_field(full_text, ("Погрузка",))
    if pog and re.search(
        r"\d{1,2}[./]\d{1,2}|сегодня|завтра|послезавтра",
        pog,
        re.I,
    ):
        labeled = labeled or pog

    # Reject method-only / payment-only false positives
    if labeled and re.search(
        r"сверху|сбоку|сзади|верхн|боков|задн|оплата|нал|фрахт|\$",
        labeled,
        re.I,
    ):
        # Still keep if it also contains a real date token
        if not re.search(r"\d{1,2}[./]\d{1,2}|сегодня|завтра|послезавтра", labeled, re.I):
            labeled = ""

    candidates = []
    if labeled:
        candidates.append(labeled)
    candidates.append(full_text)

    for src in candidates:
        m = re.search(
            r"\b(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\b",
            src,
        )
        if m:
            return m.group(1)
        m = re.search(
            r"\b(сегодня|завтра|послезавтра|ежедневно|каждый день)\b",
            src,
            re.IGNORECASE,
        )
        if m:
            word = m.group(1)
            return word[:1].upper() + word[1:].lower()

    # Explicit "дата ..." on same line only (do not cross newlines via \s)
    m = re.search(
        r"(?:^|\n)\s*дата(?:[ \t]+погрузки)?[ \t]*[:\-–—]?[ \t]*([^\n,;]+)",
        full_lower,
    )
    if m:
        val = _strip_field_noise(m.group(1))
        if val and not re.search(r"сверху|сбоку|сзади|оплата|нал|\$", val, re.I):
            return val[:40]
    return "Не указано"


def _parse_loading_method(full_text: str, full_lower: str) -> str:
    labeled = _extract_labeled_field(
        full_text,
        ("Способ погрузки", "Погрузка", "Загрузка"),
    )
    src = ((labeled or "") + " " + full_lower).lower()
    methods = []
    if re.search(r"сверху|верхн", src):
        methods.append("сверху")
    if re.search(r"сбоку|боков", src):
        methods.append("сбоку")
    if re.search(r"сзади|задн", src):
        methods.append("сзади")
    if methods:
        return ", ".join(methods)
    if labeled and not re.search(r"\d{1,2}[./]\d{1,2}", labeled):
        # free text method without known keywords
        low = labeled.lower()
        if not re.search(r"сегодня|завтра|дата", low):
            return labeled
    return "Не указано"


def _parse_temperature(full_text: str, full_lower: str, body: str) -> str:
    """Temperature regime for reefer / explicit temp mentions."""
    labeled = _extract_labeled_field(
        full_text,
        (
            "Температурный режим",
            "Температура",
            "Темп. режим",
            "Темп режим",
            "Темп",
            "Режим",
        ),
    )
    if labeled:
        # Keep as-is if has digits or +/ -
        if re.search(r"\d|[+\-]", labeled):
            t = labeled.strip()
            if "°" not in t and re.search(r"\d", t):
                t = re.sub(r"\s*c\s*$", "", t, flags=re.I).strip() + "°C"
            return t

    # Patterns: +5, -18, +2+5, +2...+5, +2 - +5, 0+5
    patterns = [
        r"(?:темп(?:ератур\w*)?(?:\s*режим)?|режим)\s*[:\-]?\s*"
        r"([+\-]?\d{1,2}\s*(?:[.\-…~]+\s*[+\-]?\d{1,2})?)\s*°?\s*[cс]?",
        r"([+\-]\d{1,2}\s*[.\-…~]+\s*[+\-]?\d{1,2})\s*°?\s*[cс]?",
        r"([+\-]\d{1,2})\s*°\s*[cс]?",
        r"([+\-]\d{1,2})\s*[cс]\b",
        r"(?<![.\d])([+\-]\d{1,2})(?!\d)",
    ]
    for pat in patterns:
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            t = re.sub(r"\s+", "", m.group(1))
            t = t.replace("...", "-").replace("…", "-").replace("~", "-")
            if "°" not in t:
                t = t + "°C"
            return t

    # If reefer mentioned but no temp — leave unspecified
    return "Не указано"


def parse_cargo_block(text):
    if not text or not text.strip():
        return None

    full_text = text.strip()
    full_lower = full_text.lower()

    # Contact from free text (phone / @username); author Telegram used as fallback later
    contact = extract_phone_contact(full_text) or ""

    # --- Route: prefer per-line labels so "Куда" never swallows the rest of the ad ---
    origin = _extract_labeled_field(full_text, ("Откуда",))
    destination = _extract_labeled_field(full_text, ("Куда",))

    if not origin or not destination:
        # Same-line: Откуда: X Куда: Y  (Y stops at newline or next known label)
        route = re.search(
            r"Откуда\s*:\s*(.+?)\s*Куда\s*:\s*(.+?)(?=\n|"
            r"(?:Груз|Вес|Кузов|Фрахт|Цена|Стоимость|Условия|Контакт|"
            r"Дата|Погрузка|Машин|Темп)\s*:|$)",
            full_text,
            re.IGNORECASE | re.DOTALL,
        )
        if route:
            if not origin:
                origin = _strip_field_noise(route.group(1))
            if not destination:
                destination = _strip_field_noise(route.group(2))

    if not origin or not destination:
        # Free form: City → City / City - City on one line.
        # Do NOT treat bare "Label: value" (Груз: Тахта) as a route —
        # colon is only a route separator when left side is not a field label.
        _w = r"[А-ЯЁA-Za-zа-яё0-9][А-ЯЁA-Za-zа-яё0-9\-]*"
        alt_route = re.search(
            rf"({_w}(?:\s+{_w})?(?:\s*\([^)]+\))?)"
            rf"\s*(?:→|->|\s[-–—]\s)\s*"
            rf"({_w}(?:\s+{_w})?(?:\s*\([^)]+\))?)",
            full_text,
        )
        if alt_route:
            o_cand = _strip_field_noise(alt_route.group(1))
            d_cand = _strip_field_noise(alt_route.group(2))
            if _looks_like_route_place(o_cand) and _looks_like_route_place(d_cand):
                if not origin:
                    origin = o_cand
                if not destination:
                    destination = d_cand

    # Multi-line free form: first place lines (Алмата- / Ташкент / АО (Югра))
    # Prefer this over "first line two tokens" so parenthetical places win.
    if not origin or not destination:
        o2, d2 = _extract_route_from_city_lines(full_text)
        if o2 and not origin:
            origin = o2
        if d2 and not destination:
            destination = d2

    # First line with two city-like tokens (e.g. "Самарканд Ташкент")
    if not origin or not destination:
        first_line = full_text.splitlines()[0]
        _w = r"[А-ЯЁA-Za-zа-яё0-9][А-ЯЁA-Za-zа-яё0-9\-]*"
        cities = re.findall(
            rf"({_w}(?:\s+{_w})?(?:\s*\([^)]+\))?)",
            first_line,
        )
        cities = [c for c in cities if _looks_like_route_place(c)]
        if len(cities) >= 2:
            if not origin:
                origin = _strip_field_noise(cities[0])
            if not destination:
                destination = _strip_field_noise(cities[-1])

    origin = _normalize_city_name(origin) if origin else "Не указано"
    destination = _normalize_city_name(destination) if destination else "Не указано"
    if not _looks_like_route_place(origin):
        origin = "Не указано"
    if not _looks_like_route_place(destination):
        destination = "Не указано"

    # --- Weight ---
    weight = "Не указано"
    weight_labeled = _extract_labeled_field(full_text, ("Вес",))
    weight_src = weight_labeled.lower() if weight_labeled else full_lower
    w = re.search(
        r"(\d{1,3}(?:[.,]\d{1,2})?)(?:\s*-\s*(\d{1,3}(?:[.,]\d{1,2})?))?\s*"
        r"(т|тонн|тонна|тонны|тн)\b",
        weight_src,
    )
    if w:
        if w.group(2):
            weight = f"{w.group(1).replace(',', '.')}–{w.group(2).replace(',', '.')} т"
        else:
            weight = f"{w.group(1).replace(',', '.')} т"

    # --- Price / freight (keep dual rates: 3300$ (реф) / 3400$ (тент)) ---
    price = "Не указано"
    price_labeled = _extract_labeled_field(
        full_text, ("Фрахт", "Цена", "Стоимость", "Оплата")
    )
    # Reject pure payment method labels ("нал", "безнал") without digits
    if price_labeled and not re.search(r"\d", price_labeled):
        price_labeled = ""

    def _normalize_freight(raw: str) -> str:
        s = re.sub(r"\s+", " ", raw).strip(" \t,;|")
        # Ensure $ sticks to the number: "3300 $" → "3300$"
        s = re.sub(r"(\d)\s+\$", r"\1$", s)
        return s

    dual_freight = re.compile(
        r"(\d{3,5}\s*\$\s*(?:\([^)]+\))?\s*/\s*\d{3,5}\s*\$\s*(?:\([^)]+\))?)",
        re.IGNORECASE,
    )
    single_freight = re.compile(r"(\d{3,5})\s*\$")

    if price_labeled:
        dual = dual_freight.search(price_labeled)
        if dual:
            price = _normalize_freight(dual.group(1))
        elif single_freight.search(price_labeled):
            # Keep full labeled value when notes present: "3300$ (реф)"
            if re.search(r"\(|реф|тент|/|или", price_labeled, re.I):
                price = _normalize_freight(price_labeled)
            else:
                price = single_freight.search(price_labeled).group(1) + "$"
        elif re.search(r"\d{3,5}", price_labeled):
            price = re.search(r"(\d{3,5})", price_labeled).group(1) + "$"
    if price == "Не указано":
        dual = dual_freight.search(full_text)
        if dual:
            price = _normalize_freight(dual.group(1))
        else:
            p_dollar = single_freight.search(full_text)
            if p_dollar:
                price = p_dollar.group(1) + "$"
            else:
                p2 = re.search(
                    r"(?:фрахт|цена|стоимость|оплата)\s*[:\-]?\s*"
                    r"(?:нал(?:ичн\w*)?\s*)?(\d{3,5})",
                    full_lower,
                )
                if p2:
                    price = p2.group(1) + "$"

    # --- Body type(s) ---
    body = _parse_body_types(full_text, full_lower)

    # --- Temperature (esp. for reefer) ---
    temperature = _parse_temperature(full_text, full_lower, body)

    # --- Vehicles count ---
    vehicles_count = _parse_vehicles_count(full_text, full_lower)

    # --- Loading date & method ---
    loading_date = _parse_loading_date(full_text, full_lower)
    loading_method = _parse_loading_method(full_text, full_lower)

    # --- Cargo: prefer labeled value, else keyword guess ---
    cargo = _extract_labeled_field(full_text, ("Груз", "Тип груза"))
    # Avoid swallowing "Груз готов" as cargo type
    if cargo and re.fullmatch(r"готов\w*", cargo.strip(), re.IGNORECASE):
        cargo = ""
    if cargo:
        # Cut trailing condition words
        cargo = re.split(
            r"\b(?:готов|нал|оплата|тент|реф|вес)\b",
            cargo,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" \t,;.-")
    if not cargo:
        cargo = _guess_cargo_type(full_lower)
    else:
        guessed = _guess_cargo_type(cargo.lower())
        if guessed != "Не указано":
            cargo = guessed
        else:
            # Title-case short free text
            cargo = cargo.strip()
            if cargo and cargo[0].islower():
                cargo = cargo[0].upper() + cargo[1:]

    # --- Conditions / payment ---
    conditions = []
    conditions_labeled = _extract_labeled_field(full_text, ("Условия", "Оплата"))
    cond_src = (conditions_labeled.lower() + " " + full_lower) if conditions_labeled else full_lower

    if "аванс" in cond_src:
        conditions.append("Аванс")
    if re.search(r"\bнал(?:ичн\w*)?\b", cond_src) or "налич" in cond_src:
        conditions.append("Наличные")
    if "перечисл" in cond_src or "безнал" in cond_src:
        conditions.append("Перечисление")
    if "срочно" in cond_src:
        conditions.append("Срочно")
    if re.search(r"груз\s*готов|готов\b", cond_src):
        conditions.append("Груз готов")

    if conditions_labeled and conditions_labeled.lower() in ("не указано", "-", "нет", "—"):
        conditions_str = "Не указано"
    else:
        conditions_str = ", ".join(conditions) if conditions else "Не указано"

    return {
        "origin": origin,
        "destination": destination,
        "cargo": cargo,
        "weight_str": weight,
        "body": body,
        "temperature": temperature,
        "vehicles_count": vehicles_count,
        "loading_date": loading_date,
        "loading_method": loading_method,
        "conditions": conditions_str,
        "price": price,
        "contact": contact,
    }


def format_cargo_message(c):
    origin_with_flag = get_city_with_flag(c.get("origin", "Не указано"))
    dest_with_flag = get_city_with_flag(c.get("destination", "Не указано"))

    lines = [
        f"🔹 *Откуда:* {escape_md(origin_with_flag)}",
        f"🔹 *Куда:* {escape_md(dest_with_flag)}",
        f"📦 *Груз:* {escape_md(c.get('cargo', 'Не указано'))}",
        f"⚖️ *Вес:* {escape_md(c.get('weight_str', 'Не указано'))}",
        f"🚛 *Кол-во машин:* {escape_md(c.get('vehicles_count', 'Не указано'))}",
        f"🚚 *Кузов:* {escape_md(c.get('body', 'Не указано'))}",
    ]

    body = (c.get("body") or "").lower()
    temperature = c.get("temperature") or "Не указано"
    # Always show temp for reefer; also if explicitly parsed
    if temperature != "Не указано" or "реф" in body:
        lines.append(f"🌡 *Темп. режим:* {escape_md(temperature)}")

    lines.extend(
        [
            f"📅 *Дата погрузки:* {escape_md(c.get('loading_date', 'Не указано'))}",
            f"⬆️ *Погрузка:* {escape_md(c.get('loading_method', 'Не указано'))}",
            f"💰 *Фрахт:* {escape_md(c.get('price', 'Не указано'))}",
            f"🔹 *Условия:* {escape_md(c.get('conditions', 'Не указано'))}",
            "",
            format_publish_contact(c.get("contact")),
            "",
            "🤖 *Хотите быстро найти подходящий груз?*",
            "Напишите боту: @tranzit\\_pro\\_bot",
        ]
    )
    return "\n".join(lines)


@dp.message(LogisticianStates.single_message_input)
async def process_single_message_cargo(message: types.Message, state: FSMContext):
    text = message.text.strip()
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]

    if not blocks:
        await message.answer("Не удалось найти объявления. Попробуйте отправить ещё раз.")
        return

    parsed_cargoes = []
    for block in blocks:
        parsed = parse_cargo_block(block)
        if parsed:
            # Prefer contact from text; otherwise Telegram @username of the poster
            parsed["contact"] = resolve_author_contact(
                parsed.get("contact"),
                message.from_user,
            )
            parsed_cargoes.append(parsed)

    if not parsed_cargoes:
        await message.answer("Не удалось распознать данные. Попробуйте отправить в другом формате.")
        return

    await state.update_data(parsed_cargoes=parsed_cargoes)
    response_text = "📋 *Распознанные объявления:*\n\n"
    for i, c in enumerate(parsed_cargoes, 1):
        response_text += f"--- Объявление #{i} ---\n"
        response_text += format_cargo_message(c) + "\n\n"

    n = len(parsed_cargoes)
    remember_telegram_user(message.from_user)
    if payment_required_for(
        message.from_user.id,
        username=getattr(message.from_user, "username", None),
    ):
        amount = format_amount_for_ads(n)
        response_text += (
            f"Все верно? После подтверждения нужно будет оплатить размещение "
            f"(*{escape_md(amount)}* за {n} шт.) — публикация после проверки админом."
        )
    else:
        response_text += "Все верно? Каждое объявление будет опубликовано отдельно."
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Да, всё верно"))
    builder.add(types.KeyboardButton(text="Нет, ввести заново"))
    builder.adjust(2)

    await message.answer(response_text, reply_markup=builder.as_markup(resize_keyboard=True), parse_mode="Markdown")
    await state.set_state(LogisticianStates.confirming_cargo)

@dp.message(LogisticianStates.confirming_cargo, F.text == "Да, всё верно")
async def confirm_single_msg_cargo(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    parsed_cargoes = user_data.get('parsed_cargoes', [])
    # Clear immediately so a double-tap on «Да» cannot publish the same ads twice
    await state.update_data(parsed_cargoes=[])
    if not parsed_cargoes:
        await message.answer(
            "Нечего публиковать — отправьте объявление заново.",
            reply_markup=get_logistician_main_keyboard(),
        )
        await state.set_state(LogisticianStates.main_menu)
        return

    logistician_id = get_logistician_id(message.from_user.id)
    if logistician_id is None:
        await message.answer(
            "Сначала выберите роль логиста.",
            reply_markup=get_role_keyboard(),
        )
        await state.set_state(UserRole.choosing_role)
        return

    items = []
    for c in parsed_cargoes:
        # Always store/show the poster contact — never a global hub username
        user_contact = resolve_author_contact(
            c.get('contact'),
            message.from_user,
        )
        c = dict(c)
        c['contact'] = user_contact
        c['kind'] = 'parsed'
        items.append(c)

    await state.set_state(LogisticianStates.main_menu)
    n = len(items)
    free_text = (
        "Все объявления опубликованы!"
        if n > 1
        else "Ваш груз размещен!"
    )
    await request_payment_or_publish(
        message=message,
        logistician_id=logistician_id,
        items=items,
        free_success_text=free_text,
    )

@dp.message(LogisticianStates.confirming_cargo, F.text == "Нет, ввести заново")
async def reject_single_msg_cargo(message: types.Message, state: FSMContext):
    await message.answer("Хорошо, отправьте информацию о грузе заново одним сообщением.")
    await state.set_state(LogisticianStates.single_message_input)

@dp.message(F.text == "🚛 Найти свободные машины")
async def search_vehicles_start(message: types.Message, state: FSMContext):
    total = count_vehicles()
    await message.answer(
        "Введите *город отправления* (как в объявлении, например: Москва).\n"
        f"Сейчас в базе машин: {total}",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown",
    )
    await state.set_state(LogisticianStates.searching_vehicles_origin)


@dp.message(LogisticianStates.searching_vehicles_origin, F.text == "❌ Отмена")
@dp.message(LogisticianStates.searching_vehicles_destination, F.text == "❌ Отмена")
async def search_vehicles_cancel(message: types.Message, state: FSMContext):
    await message.answer("Поиск отменён.", reply_markup=get_logistician_main_keyboard())
    await state.set_state(LogisticianStates.main_menu)


@dp.message(LogisticianStates.searching_vehicles_origin)
async def search_vehicles_origin(message: types.Message, state: FSMContext):
    origin = _clean_city_input(message.text)
    if not origin:
        await message.answer("Введите город отправления текстом, например: Москва")
        return
    # Don't treat main-menu buttons as a city if keyboard is still open
    menu_labels = {
        "📦 Разместить груз",
        "🔍 Найти груз",
        "🚛 Найти свободные машины",
        "📋 Мои объявления",
        "📊 Мои приглашения",
        "🔄 Сменить роль",
        "🟢 Я водитель",
        "🔵 Я логист",
        "🚚 Разместить свободную машину",
        "🔔 Подписка на направления",
    }
    if message.text and message.text.strip() in menu_labels:
        await message.answer(
            "Сначала введите город отправления или нажмите «❌ Отмена».",
            reply_markup=get_cancel_keyboard(),
        )
        return
    await state.update_data(search_origin=origin)
    await message.answer(
        "Введите *город назначения* (например: Ташкент):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown",
    )
    await state.set_state(LogisticianStates.searching_vehicles_destination)


@dp.message(LogisticianStates.searching_vehicles_destination)
async def search_vehicles_destination(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    origin = user_data.get("search_origin") or ""
    destination = _clean_city_input(message.text)
    if not destination:
        await message.answer("Введите город назначения текстом, например: Ташкент")
        return
    if not origin:
        await message.answer(
            "Сессия поиска сброшена. Нажмите «🚛 Найти свободные машины» ещё раз.",
            reply_markup=get_logistician_main_keyboard(),
        )
        await state.set_state(LogisticianStates.main_menu)
        return

    vehicles = get_vehicles_by_route(origin, destination)
    if vehicles:
        response = f"Найдены машины по маршруту {origin} → {destination}:\n"
        for vehicle in vehicles:
            response += format_vehicle_card(vehicle)
    else:
        total = count_vehicles()
        if total == 0:
            response = (
                "В базе пока нет ни одной свободной машины.\n"
                "Объявления появляются, когда водители нажимают "
                "«🚚 Разместить свободную машину» в боте "
                "(посты только в канале в поиск не попадают)."
            )
        else:
            response = (
                f"Машин по маршруту {origin} → {destination} не найдено.\n"
                f"Всего машин в базе: {total}.\n"
                "Проверьте написание городов (регистр не важен) — "
                "нужно то же направление, что указал водитель."
            )
    await message.answer(response, reply_markup=get_logistician_main_keyboard())
    await state.set_state(LogisticianStates.main_menu)

# --- Manual payment callbacks (cargo ads) ---

def _parse_pending_callback(data: str):
    """Parse pad:<action>:<id> → (action, pending_id) or (None, None)."""
    if not data or not data.startswith("pad:"):
        return None, None
    parts = data.split(":")
    if len(parts) != 3:
        return None, None
    _, action, raw_id = parts
    try:
        return action, int(raw_id)
    except ValueError:
        return None, None


@dp.callback_query(F.data.startswith("pad:"))
async def pending_ad_callbacks(callback: CallbackQuery):
    action, pending_id = _parse_pending_callback(callback.data or "")
    if action is None or pending_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return

    pending = get_pending_ad(pending_id)
    if not pending:
        await callback.answer("Заявка не найдена", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    user_id = callback.from_user.id

    # --- User: cancel ---
    if action == "cancel":
        if user_id != pending["telegram_id"] and not is_admin(user_id):
            await callback.answer("Это не ваша заявка", show_alert=True)
            return
        if pending["status"] in ("published", "cancelled", "rejected"):
            await callback.answer("Заявка уже закрыта", show_alert=True)
            return
        ok = update_pending_ad_status(
            pending_id,
            "cancelled",
            expected_statuses=("awaiting_payment", "awaiting_admin"),
        )
        if not ok:
            await callback.answer("Не удалось отменить (уже обработана)", show_alert=True)
            return
        await callback.answer("Отменено")
        try:
            await callback.message.edit_text(
                f"❌ Заявка №{pending_id} отменена. Объявление не опубликовано."
            )
        except Exception:
            await callback.message.answer(
                f"❌ Заявка №{pending_id} отменена. Объявление не опубликовано."
            )
        return

    # --- User: I paid ---
    if action == "paid":
        if user_id != pending["telegram_id"]:
            await callback.answer("Это не ваша заявка", show_alert=True)
            return
        if pending["status"] != "awaiting_payment":
            status_msg = {
                "awaiting_admin": "Уже отправлено админу, ждите проверки",
                "published": "Уже опубликовано",
                "cancelled": "Заявка отменена",
                "rejected": "Заявка отклонена",
            }.get(pending["status"], "Заявка уже обработана")
            await callback.answer(status_msg, show_alert=True)
            return
        ok = update_pending_ad_status(
            pending_id,
            "awaiting_admin",
            expected_statuses=("awaiting_payment",),
        )
        if not ok:
            await callback.answer("Не удалось обновить статус", show_alert=True)
            return

        await callback.answer("Отправлено администратору")
        try:
            await callback.message.edit_text(
                f"✅ Заявка №{pending_id}: оплата отмечена.\n"
                f"Ожидайте проверки администратора — после подтверждения "
                f"объявление появится в канале."
            )
        except Exception:
            await callback.message.answer(
                f"✅ Заявка №{pending_id}: оплата отмечена. Ожидайте проверки."
            )

        if ADMIN_ID is None:
            logging.error("User marked paid but ADMIN_ID is not set")
            return

        uname = callback.from_user.username
        user_label = f"@{uname}" if uname else (callback.from_user.full_name or str(user_id))
        preview = summarize_pending_items(pending["items"])
        admin_text = (
            f"💰 Новая оплата за размещение\n\n"
            f"Заявка: №{pending_id}\n"
            f"От: {user_label} (id {user_id})\n"
            f"Объявлений: {pending['ad_count']}\n"
            f"Сумма: {pending.get('amount_label') or AD_POST_PRICE}\n\n"
            f"📋 Объявления:\n{preview}\n\n"
            f"Проверьте перевод и нажмите кнопку."
        )
        try:
            await bot.send_message(
                ADMIN_ID,
                admin_text,
                reply_markup=admin_payment_keyboard(pending_id),
            )
        except Exception as e:
            logging.error("Failed to notify admin about payment %s: %s", pending_id, e)
            await bot.send_message(
                user_id,
                "Не удалось уведомить администратора. Напишите ему вручную "
                f"и укажите заявку №{pending_id}.",
            )
        return

    # --- Admin: confirm payment → publish ---
    if action == "ok":
        if not is_admin(user_id):
            await callback.answer("Только для администратора", show_alert=True)
            return
        if pending["status"] == "published":
            await callback.answer("Уже опубликовано", show_alert=True)
            return
        if pending["status"] not in ("awaiting_admin", "awaiting_payment"):
            await callback.answer(
                f"Нельзя опубликовать (статус: {pending['status']})",
                show_alert=True,
            )
            return
        ok = update_pending_ad_status(
            pending_id,
            "published",
            expected_statuses=("awaiting_admin", "awaiting_payment"),
        )
        if not ok:
            await callback.answer("Уже обработано", show_alert=True)
            return

        try:
            await publish_cargo_items(
                pending["items"],
                pending["logistician_id"],
                pending["telegram_id"],
            )
        except Exception as e:
            logging.exception("Publish failed for pending %s: %s", pending_id, e)
            # Roll back status so admin can retry
            update_pending_ad_status(pending_id, "awaiting_admin")
            await callback.answer("Ошибка публикации, попробуйте ещё раз", show_alert=True)
            return

        await callback.answer("Опубликовано")
        try:
            await callback.message.edit_text(
                f"✅ Заявка №{pending_id}: оплата подтверждена, "
                f"объявления опубликованы ({pending['ad_count']} шт)."
            )
        except Exception:
            pass
        try:
            await bot.send_message(
                pending["telegram_id"],
                f"✅ Оплата по заявке №{pending_id} подтверждена.\n"
                f"Ваши объявления ({pending['ad_count']}) опубликованы!",
                reply_markup=get_logistician_main_keyboard(),
            )
        except Exception as e:
            logging.error("Failed to notify user about publish %s: %s", pending_id, e)
        return

    # --- Admin: reject ---
    if action == "no":
        if not is_admin(user_id):
            await callback.answer("Только для администратора", show_alert=True)
            return
        if pending["status"] in ("published", "cancelled", "rejected"):
            await callback.answer("Заявка уже закрыта", show_alert=True)
            return
        ok = update_pending_ad_status(
            pending_id,
            "rejected",
            expected_statuses=("awaiting_admin", "awaiting_payment"),
        )
        if not ok:
            await callback.answer("Уже обработано", show_alert=True)
            return
        await callback.answer("Отклонено")
        try:
            await callback.message.edit_text(
                f"❌ Заявка №{pending_id} отклонена. Объявления не опубликованы."
            )
        except Exception:
            pass
        try:
            await bot.send_message(
                pending["telegram_id"],
                f"❌ Заявка №{pending_id} отклонена администратором.\n"
                f"Если вы уже перевели оплату — напишите админу с номером заявки.\n"
                f"Объявления не опубликованы.",
                reply_markup=get_logistician_main_keyboard(),
            )
        except Exception as e:
            logging.error("Failed to notify user about reject %s: %s", pending_id, e)
        return

    await callback.answer("Неизвестное действие", show_alert=True)


@dp.callback_query(F.data.startswith("postreq:"))
async def post_requirements_callbacks(callback: CallbackQuery):
    """Inline buttons: check subscription, show link, show invite count."""
    user = callback.from_user
    remember_telegram_user(user)
    user_id = user.id
    action = (callback.data or "").split(":", 1)[-1]

    if is_free_poster(user_id, username=getattr(user, "username", None)):
        await callback.answer(
            "Ограничения не применяются — можно размещать свободно",
            show_alert=True,
        )
        return

    channel = await get_channel_label()
    url = channel_url_for_button(channel)

    if action == "check":
        subscribed = await is_user_subscribed(user_id)
        invites = get_invite_count(user_id)
        link = await get_or_create_invite_link(user_id)
        text = format_post_requirements_text(
            subscribed=subscribed,
            invites=invites,
            channel=channel,
            invite_link=link,
        )
        try:
            await callback.message.edit_text(
                text, reply_markup=post_requirements_keyboard(url)
            )
        except Exception:
            await callback.message.answer(
                text, reply_markup=post_requirements_keyboard(url)
            )
        if subscribed and invites >= MIN_CHANNEL_INVITES:
            await callback.answer("Готово! Можно размещать объявления ✅")
        elif not subscribed:
            await callback.answer("Сначала подпишитесь на канал", show_alert=True)
        else:
            await callback.answer(
                f"Приглашено {invites} из {MIN_CHANNEL_INVITES}",
                show_alert=True,
            )
        return

    if action == "link":
        link = await get_or_create_invite_link(user_id)
        if not link:
            await callback.answer(
                "Не удалось создать ссылку. Бот должен быть админом канала.",
                show_alert=True,
            )
            return
        invites = get_invite_count(user_id)
        await callback.message.answer(
            f"🔗 Ваша персональная ссылка для приглашения в {channel}:\n\n"
            f"{link}\n\n"
            f"Отправьте её друзьям. Когда они вступят в канал — "
            f"счётчик вырастет.\n"
            f"Сейчас приглашено: {invites} из {MIN_CHANNEL_INVITES}",
            reply_markup=post_requirements_keyboard(url),
        )
        await callback.answer("Ссылка отправлена")
        return

    if action == "invites":
        subscribed = await is_user_subscribed(user_id)
        invites = get_invite_count(user_id)
        link = await get_or_create_invite_link(user_id)
        text = format_post_requirements_text(
            subscribed=subscribed,
            invites=invites,
            channel=channel,
            invite_link=link,
        )
        await callback.message.answer(
            text, reply_markup=post_requirements_keyboard(url)
        )
        await callback.answer()
        return

    await callback.answer("Неизвестное действие")


@dp.chat_member()
async def on_channel_chat_member(event: types.ChatMemberUpdated):
    """Count channel joins via personal invite links created by the bot."""
    try:
        chat_id = event.chat.id
        # Match numeric id or @username channel
        channel_ref = str(CHANNEL_ID).strip()
        matched = False
        if channel_ref.startswith("@"):
            uname = (event.chat.username or "").lower()
            if uname and uname == channel_ref.lstrip("@").lower():
                matched = True
        else:
            try:
                matched = int(channel_ref) == chat_id
            except ValueError:
                matched = channel_ref == str(chat_id)
        if not matched:
            return

        old_status = _member_status_value(event.old_chat_member)
        new_status = _member_status_value(event.new_chat_member)
        # Only count real joins: was not a member → became a member
        was_member = old_status in _MEMBER_STATUSES
        is_member = new_status in _MEMBER_STATUSES
        if was_member or not is_member:
            return

        invited_user = event.new_chat_member.user
        if not invited_user or getattr(invited_user, "is_bot", False):
            return
        invited_id = invited_user.id

        invite_link = event.invite_link
        if not invite_link:
            return

        inviter_id = None
        link_name = getattr(invite_link, "name", None) or ""
        if link_name:
            inviter_id = get_inviter_by_link_name(link_name)
            if inviter_id is None and link_name.startswith("ref"):
                # Fallback: parse ref{user_id} even if DB row missing
                raw = link_name[3:]
                if raw.isdigit():
                    inviter_id = int(raw)

        if inviter_id is None:
            return

        if record_channel_invite(inviter_id, invited_id):
            count = get_invite_count(inviter_id)
            logging.info(
                "Invite credited: inviter=%s invited=%s total=%s",
                inviter_id,
                invited_id,
                count,
            )
            try:
                need = MIN_CHANNEL_INVITES
                if count >= need:
                    msg = (
                        f"🎉 Новый участник по вашей ссылке!\n"
                        f"Приглашено: {count} из {need} — "
                        f"можно размещать объявления "
                        f"(не забудьте сами быть подписаны на канал)."
                    )
                else:
                    msg = (
                        f"👋 Новый участник по вашей ссылке!\n"
                        f"Приглашено: {count} из {need}."
                    )
                await bot.send_message(inviter_id, msg)
            except Exception as e:
                logging.warning("Could not notify inviter %s: %s", inviter_id, e)
    except Exception as e:
        logging.error("chat_member handler error: %s", e)


@dp.message(Command("pending"))
async def admin_list_pending(message: types.Message):
    """Admin: list cargo ads waiting for payment confirmation."""
    if not is_admin(message.from_user.id):
        await message.answer("Команда только для администратора.")
        return
    rows = list_pending_ads_for_admin(limit=15)
    if not rows:
        await message.answer("Нет заявок, ожидающих подтверждения оплаты.")
        return
    await message.answer(f"Заявок на проверке: {len(rows)}")
    for row in rows:
        preview = summarize_pending_items(row["items"])
        text = (
            f"Заявка №{row['id']}\n"
            f"От id {row['telegram_id']}\n"
            f"Сумма: {row.get('amount_label') or AD_POST_PRICE}\n"
            f"Объявлений: {row['ad_count']}\n\n"
            f"{preview}"
        )
        await message.answer(
            text,
            reply_markup=admin_payment_keyboard(row["id"]),
        )


@dp.message(Command("payinfo"))
async def payinfo_command(message: types.Message):
    """Show current cargo posting price and payment details."""
    remember_telegram_user(message.from_user)
    if not PAYMENT_ENABLED or ADMIN_ID is None:
        await message.answer(
            "Сейчас размещение грузов без оплаты "
            "(оплата выключена или не задан ADMIN_ID)."
        )
        return
    if is_free_poster(
        message.from_user.id,
        username=getattr(message.from_user, "username", None),
    ):
        if is_admin(message.from_user.id):
            extra = (
                f"\n\n(Вы админ — размещаете бесплатно.)\n"
                f"Список заявок: /pending"
            )
        else:
            extra = "\n\n(Вам доступно бесплатное размещение.)"
    else:
        extra = ""
    await message.answer(
        f"💳 Размещение груза: {AD_POST_PRICE} за одно объявление.\n\n"
        f"{PAYMENT_DETAILS}\n\n"
        f"Машины (водители) — бесплатно."
        f"{extra}",
    )


# --- Fallback ---
@dp.message()
async def echo_handler(message: types.Message):
    logging.info(f"Unhandled message: {message.text} from {message.from_user.id}")
    await message.answer("Извините, я не понял эту команду. Пожалуйста, используйте кнопки меню.")

async def main():
    init_db()
    # Ensure polling mode: drop any leftover webhook and stale updates
    # (does not fix two simultaneous polling instances with the same token)
    await bot.delete_webhook(drop_pending_updates=True)
    # chat_member is required to count channel invites via personal links;
    # resolve_used_update_types() includes it because of @dp.chat_member.
    allowed = dp.resolve_used_update_types()
    logging.info(
        "Webhook cleared, starting polling "
        "(updates=%s, post_gate=%s, min_invites=%s, free_usernames=%s, free_ids=%s)",
        allowed,
        POST_REQUIREMENTS_ENABLED,
        MIN_CHANNEL_INVITES,
        sorted(FREE_POST_USERNAMES) if FREE_POST_USERNAMES else [],
        sorted(FREE_POST_USER_IDS) if FREE_POST_USER_IDS else [],
    )
    await dp.start_polling(bot, allowed_updates=allowed)

if __name__ == "__main__":
    asyncio.run(main())
