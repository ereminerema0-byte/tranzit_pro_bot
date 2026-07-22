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
# Ads always show the contact of the person who posted.
# CONTACT_USERNAME is intentionally NOT used in announcements (was wrongly
# substituting one fixed hub username for every cargo). Kept only so old
# Railway env vars do not break process startup.
CONTACT_USERNAME = (os.getenv("CONTACT_USERNAME") or "").strip()

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

from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from db import init_db, add_user, get_user_role, get_driver_id, get_logistician_id, add_cargo, get_cargo_by_route, get_logistician_cargo, add_vehicle, get_vehicles_by_route, get_driver_vehicles, add_subscription, get_subscribers_for_route

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
    "Бейсик": "🇰🇿"
}

def get_city_with_flag(city_name):
    if not city_name or city_name == "Не указано":
        return "Не указано"
    
    city = city_name.strip()
    
    # Точное совпадение
    if city in CITY_FLAGS:
        return f"{CITY_FLAGS[city]} {city}"
    
    # Поиск без учёта регистра
    city_lower = city.lower()
    for key, flag in CITY_FLAGS.items():
        if key.lower() == city_lower:
            return f"{flag} {city}"
    
    # Дополнительная логика по окончанию слова
    if any(city_lower.endswith(x) for x in ["ск", "град", "бург", "ов", "ино", "ево", "ка", "ль", "мь"]):
        return f"🇷🇺 {city}"
    if any(city_lower.endswith(x) for x in ["он", "арё", "ат", "ент", "ан"]):
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
    builder.add(types.KeyboardButton(text="🔄 Сменить роль"))
    builder.adjust(2, 2, 1)
    return builder.as_markup(resize_keyboard=True)

def get_logistician_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="📦 Разместить груз"))
    builder.add(types.KeyboardButton(text="🔍 Найти груз"))
    builder.add(types.KeyboardButton(text="🚛 Найти свободные машины"))
    builder.add(types.KeyboardButton(text="📋 Мои объявления"))
    builder.add(types.KeyboardButton(text="🔄 Сменить роль"))
    builder.adjust(2, 2, 1)
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

@dp.message(F.text == "🔍 Найти груз")
async def driver_search_cargo_start(message: types.Message, state: FSMContext):
    await message.answer("Введите город отправления для поиска груза:")
    await state.set_state(DriverStates.searching_cargo_origin)

@dp.message(DriverStates.searching_cargo_origin)
async def driver_search_cargo_origin(message: types.Message, state: FSMContext):
    await state.update_data(search_origin=message.text)
    await message.answer("Введите город назначения для поиска груза:")
    await state.set_state(DriverStates.searching_cargo_destination)

@dp.message(DriverStates.searching_cargo_destination)
async def driver_search_cargo_destination(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    origin = user_data['search_origin']
    destination = message.text
    cargo_list = get_cargo_by_route(origin, destination)
    if cargo_list:
        response = "Найденные грузы:\n"
        for cargo in cargo_list:
            response += f"\nОткуда: {cargo[2]}\nКуда: {cargo[3]}\nТип: {cargo[4]}\nВес: {cargo[5]} кг\nОбъем: {cargo[6]} м³\nЦена: {cargo[7]}\nДата: {cargo[8]}\nКонтакт: {cargo[9]}\n---"
    else:
        response = "Грузов по вашему направлению не найдено."
    
    role = get_user_role(message.from_user.id)
    if role == 'logistician':
        await message.answer(response, reply_markup=get_logistician_main_keyboard())
        await state.set_state(LogisticianStates.main_menu)
    else:
        await message.answer(response, reply_markup=get_driver_main_keyboard())
        await state.set_state(DriverStates.main_menu)

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
    add_vehicle(
        driver_id,
        user_data['body_type'],
        float(capacity),
        user_data['origin'],
        user_data['destination'],
        user_data['date'],
        author_contact,
    )
    await message.answer("Ваше объявление о свободной машине размещено!", reply_markup=get_driver_main_keyboard())
    await state.set_state(DriverStates.main_menu)

    # Auto-publish to channel
    origin_f = f"{user_data.get('origin_flag', '')} {user_data['origin']}".strip()
    dest_f = f"{user_data.get('destination_flag', '')} {user_data['destination']}".strip()
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
# --- Logistician Handlers ---

@dp.message(F.text == "📦 Разместить груз")
async def logistician_add_cargo_start(message: types.Message, state: FSMContext):
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
    add_cargo(
        logistician_id,
        user_data['origin'],
        user_data['destination'],
        user_data['cargo_type'],
        float(weight),
        float(volume),
        user_data['price'],
        user_data['date'],
        user_contact,
    )
    await message.answer("Ваш груз размещен!", reply_markup=get_logistician_main_keyboard())
    await state.set_state(LogisticianStates.main_menu)

    # Auto-publish to channel (same price as in DB — no hidden offset)
    origin_f = f"{user_data.get('origin_flag', '')} {user_data['origin']}".strip()
    dest_f = f"{user_data.get('destination_flag', '')} {user_data['destination']}".strip()
    price_display = user_data['price']
    channel_message = (
        f"📦 *Новый груз*\n\n"
        f"📍 *Откуда:* {escape_md(origin_f)}\n"
        f"📍 *Куда:* {escape_md(dest_f)}\n"
        f"🏷️ *Тип груза:* {escape_md(user_data['cargo_type'])}\n"
        f"⚖️ *Вес:* {escape_md(weight)} кг\n"
        f"📏 *Объем:* {escape_md(volume)} м³\n"
        f"💰 *Цена:* {escape_md(price_display)}\n"
        f"📅 *Дата готовности:* {escape_md(user_data['date'])}\n"
        f"{format_publish_contact(user_contact)}\n\n"
        f"🤖 *Хотите быстро найти подходящий груз?*\n"
        f"Напишите боту: @tranzit_pro_bot"
    )
    try:
        await bot.send_message(CHANNEL_ID, channel_message, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Failed to publish to channel: {e}")

    notice = build_cargo_subscriber_notice(
        user_data['origin'],
        user_data['destination'],
        user_data['cargo_type'],
        f"{weight} кг",
        price_display,
        user_contact,
    )
    await notify_route_subscribers(
        user_data['origin'],
        user_data['destination'],
        notice,
        author_telegram_id=message.from_user.id,
    )

@dp.message(LogisticianStates.choosing_placement_mode, F.text == "Одним сообщением")
async def logistician_add_cargo_single_msg(message: types.Message, state: FSMContext):
    await message.answer(
    "Отправьте информацию о грузе **одним сообщением** в свободной форме.\n\n"
    "**Пример:**\n"
    "📍 Откуда: Алматы\n"
    "📍 Куда: Ташкент\n"
    "📦 Груз: Рулонная бумага\n"
    "⚖️ Вес: 22 тонны\n"
    "🚚 Кузов: Тент\n"
    "💰 Фрахт: 1200$\n"
    "📋 Условия: Наличные, перечисление, груз готов",
    parse_mode="Markdown"
)
    await state.set_state(LogisticianStates.single_message_input)

@dp.message(LogisticianStates.choosing_placement_mode, F.text == "Назад")
async def logistician_add_cargo_back(message: types.Message, state: FSMContext):
    await message.answer("Главное меню", reply_markup=get_logistician_main_keyboard())
    await state.set_state(LogisticianStates.main_menu)

# Labels that start a new field in free-form cargo ads (used as stop markers).
_CARGO_FIELD_LABELS = (
    "откуда", "куда", "груз", "тип груза", "вес", "кузов", "тип кузова",
    "фрахт", "цена", "стоимость", "условия", "контакт", "телефон", "дата",
)


def _strip_field_noise(value: str) -> str:
    """Clean a single field value: one line, no leading emoji noise."""
    if not value:
        return ""
    # Take only the first line so multi-line capture cannot leak other fields
    s = str(value).splitlines()[0].strip()
    # Drop leading location/package emoji and bullet markers
    s = re.sub(r"^[\s📍🔹📦⚖️🚚💰📞🏷️📅\-•*]+", "", s)
    s = s.strip(" \t,;|")
    return s


def _extract_labeled_field(text: str, labels) -> str:
    """Extract value after 'Label:' up to end of line (never past the next field)."""
    if not text:
        return ""
    for label in labels:
        # Optional emoji/bullet prefix before the label (📍 Откуда: …)
        pat = (
            rf"(?:^|\n)\s*(?:[📍🔹📦⚖️🚚💰📞🏷️📅\-•*]+\s*)?"
            rf"{re.escape(label)}\s*:\s*(.+?)(?:\s*$|\n)"
        )
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = _strip_field_noise(m.group(1))
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
    s = s.strip(" \t,;")
    return s or "Не указано"


def _guess_cargo_type(text_lower: str) -> str:
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
    if "бор" in text_lower:
        return "Бор"
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
            r"(?:Груз|Вес|Кузов|Фрахт|Цена|Стоимость|Условия|Контакт)\s*:|$)",
            full_text,
            re.IGNORECASE | re.DOTALL,
        )
        if route:
            if not origin:
                origin = _strip_field_noise(route.group(1))
            if not destination:
                destination = _strip_field_noise(route.group(2))

    if not origin or not destination:
        # Free form: City → City / City - City
        alt_route = re.search(
            r"([А-ЯЁA-Z][а-яёa-zA-ZЁё\-]*(?:\s+[А-ЯЁA-Zа-яёa-zA-ZЁё\-]+)?)"
            r"\s*[:\-→]+\s*"
            r"([А-ЯЁA-Z][а-яёa-zA-ZЁё\-]*(?:\s+[А-ЯЁA-Zа-яёa-zA-ZЁё\-]+)?)",
            full_text,
        )
        if alt_route:
            if not origin:
                origin = _strip_field_noise(alt_route.group(1))
            if not destination:
                destination = _strip_field_noise(alt_route.group(2))

    # First line with two city-like tokens (e.g. "Самарканд Ташкент")
    if not origin or not destination:
        first_line = full_text.splitlines()[0]
        cities = re.findall(r"([А-ЯЁA-Z][а-яёa-zA-ZЁё\-]+(?:\s+[А-ЯЁA-Zа-яёa-zA-ZЁё\-]+)?)", first_line)
        if len(cities) >= 2:
            if not origin:
                origin = _strip_field_noise(cities[0])
            if not destination:
                destination = _strip_field_noise(cities[-1])

    origin = _normalize_city_name(origin) if origin else "Не указано"
    destination = _normalize_city_name(destination) if destination else "Не указано"

    # --- Weight ---
    weight = "Не указано"
    weight_labeled = _extract_labeled_field(full_text, ("Вес",))
    weight_src = weight_labeled.lower() if weight_labeled else full_lower
    w = re.search(
        r"(\d{1,3}(?:[.,]\d{1,2})?)(?:\s*-\s*(\d{1,3}(?:[.,]\d{1,2})?))?\s*(т|тонн|тонна|тонны|тн)",
        weight_src,
    )
    if w:
        if w.group(2):
            weight = f"{w.group(1)}-{w.group(2)} т"
        else:
            weight = f"{w.group(1)} т"

    # --- Price / freight ---
    price = "Не указано"
    price_labeled = _extract_labeled_field(full_text, ("Фрахт", "Цена", "Стоимость"))
    price_src = price_labeled.lower() if price_labeled else full_lower
    p_dollar = re.search(r"(\d{3,5})\s*\$", price_src if "$" in price_src else full_text)
    if p_dollar:
        price = p_dollar.group(1) + "$"
    else:
        p2 = re.search(r"(?:фрахт|цена|стоимость)\s*:?\s*(\d{3,5})", full_lower)
        if p2:
            price = p2.group(1) + "$"
        elif price_labeled and re.search(r"\d{3,5}", price_labeled):
            price = re.search(r"(\d{3,5})", price_labeled).group(1) + "$"

    # --- Body type: prefer full labeled value (e.g. "Тент фура КК") ---
    body = _extract_labeled_field(full_text, ("Кузов", "Тип кузова"))
    if not body:
        if "тент" in full_lower:
            body = "Тент"
        elif "реф" in full_lower:
            body = "Реф"
        else:
            body = "Не указано"

    # --- Cargo: prefer labeled value, else keyword guess ---
    cargo = _extract_labeled_field(full_text, ("Груз", "Тип груза"))
    if not cargo:
        cargo = _guess_cargo_type(full_lower)
    else:
        guessed = _guess_cargo_type(cargo.lower())
        if guessed != "Не указано":
            cargo = guessed

    # --- Conditions ---
    conditions = []
    conditions_labeled = _extract_labeled_field(full_text, ("Условия",))
    cond_src = conditions_labeled.lower() if conditions_labeled else full_lower

    if "аванс" in cond_src:
        conditions.append("Аванс")
    if "налич" in cond_src:
        conditions.append("Наличные")
    if "перечисл" in cond_src or "безнал" in cond_src:
        conditions.append("Перечисление")
    if "срочно" in cond_src:
        conditions.append("Срочно")
    if "готов" in cond_src:
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
        "conditions": conditions_str,
        "price": price,
        "contact": contact,
    }


def format_cargo_message(c):
    origin_with_flag = get_city_with_flag(c.get("origin", "Не указано"))
    dest_with_flag = get_city_with_flag(c.get("destination", "Не указано"))

    return (
        f"🔹 *Откуда:* {escape_md(origin_with_flag)}\n"
        f"🔹 *Куда:* {escape_md(dest_with_flag)}\n"
        f"📦 *Груз:* {escape_md(c.get('cargo', 'Не указано'))}\n"
        f"⚖️ *Вес:* {escape_md(c.get('weight_str', 'Не указано'))}\n"
        f"🚚 *Кузов:* {escape_md(c.get('body', 'Не указано'))}\n"
        f"💰 *Фрахт:* {escape_md(c.get('price', 'Не указано'))}\n"
        f"🔹 *Условия:* {escape_md(c.get('conditions', 'Не указано'))}\n\n"
        f"{format_publish_contact(c.get('contact'))}\n"
        f"\n\n🤖 *Хотите быстро найти подходящий груз?*\nНапишите боту: @tranzit\\_pro\\_bot"
    )


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
    logistician_id = get_logistician_id(message.from_user.id)
    if logistician_id is None:
        await message.answer(
            "Сначала выберите роль логиста.",
            reply_markup=get_role_keyboard(),
        )
        await state.set_state(UserRole.choosing_role)
        return

    for c in parsed_cargoes:
        weight_val = parse_positive_float(c.get('weight_str', '')) or 0
        # Always store/show the poster contact — never a global hub username
        user_contact = resolve_author_contact(
            c.get('contact'),
            message.from_user,
        )
        c['contact'] = user_contact
        add_cargo(
            logistician_id,
            c['origin'],
            c['destination'],
            c['cargo'],
            weight_val,
            0,
            c.get('price', 'Не указано'),
            "В описании",
            user_contact,
        )

        channel_message = format_cargo_message(c) + f"\n🤖 @tranzit_pro_bot"
        try:
            await bot.send_message(CHANNEL_ID, channel_message, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to publish to channel: {e}")

        notice = build_cargo_subscriber_notice(
            c['origin'],
            c['destination'],
            c.get('cargo', 'Не указано'),
            c.get('weight_str', 'Не указано'),
            c.get('price', 'Не указано'),
            user_contact,
        )
        await notify_route_subscribers(
            c['origin'],
            c['destination'],
            notice,
            author_telegram_id=message.from_user.id,
        )

    await message.answer("Все объявления опубликованы!", reply_markup=get_logistician_main_keyboard())
    await state.set_state(LogisticianStates.main_menu)

@dp.message(LogisticianStates.confirming_cargo, F.text == "Нет, ввести заново")
async def reject_single_msg_cargo(message: types.Message, state: FSMContext):
    await message.answer("Хорошо, отправьте информацию о грузе заново одним сообщением.")
    await state.set_state(LogisticianStates.single_message_input)

@dp.message(F.text == "🚛 Найти свободные машины")
async def search_vehicles_start(message: types.Message, state: FSMContext):
    await message.answer("Введите город отправления:")
    await state.set_state(LogisticianStates.searching_vehicles_origin)

@dp.message(LogisticianStates.searching_vehicles_origin)
async def search_vehicles_origin(message: types.Message, state: FSMContext):
    await state.update_data(search_origin=message.text)
    await message.answer("Введите город назначения:")
    await state.set_state(LogisticianStates.searching_vehicles_destination)

@dp.message(LogisticianStates.searching_vehicles_destination)
async def search_vehicles_destination(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    origin = user_data['search_origin']
    destination = message.text
    vehicles = get_vehicles_by_route(origin, destination)
    if vehicles:
        response = "Найденные машины:\n"
        for vehicle in vehicles:
            response += f"\nТип кузова: {vehicle[2]}\nГрузоподъёмность: {vehicle[3]} т\nОткуда: {vehicle[4]}\nКуда: {vehicle[5]}\nДата: {vehicle[6]}\nКонтакт: {vehicle[7]}\n---"
    else:
        response = "Машин по вашему направлению не найдено."
    await message.answer(response, reply_markup=get_logistician_main_keyboard())
    await state.set_state(LogisticianStates.main_menu)

# --- Fallback ---
@dp.message()
async def echo_handler(message: types.Message):
    logging.info(f"Unhandled message: {message.text} from {message.from_user.id}")
    await message.answer("Извините, я не понял эту команду. Пожалуйста, используйте кнопки меню.")

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
