import asyncio
import logging
import os
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from db import (
    init_db, add_user, get_user_role, get_driver_id, get_logistician_id,
    add_cargo, get_cargo_by_route, get_logistician_cargo, add_vehicle,
    get_vehicles_by_route, get_driver_vehicles, add_subscription,
    get_subscribers_for_route
)

# Bot configuration — берём токен из переменной окружения Railway
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

CHANNEL_ID = "@tranzitpro1"
CONTACT_USERNAME = "@Oleg34381"

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
    if not city_name:
        return "Не указано"
    if city_name in CITY_FLAGS:
        return f"{CITY_FLAGS[city_name]} {city_name}"
    name_low = city_name.lower()
    if any(name_low.endswith(x) for x in ["ск", "град", "бург", "ов", "ино", "ево", "ка", "ль", "мь"]):
        return f"🇷🇺 {city_name}"
    if any(name_low.endswith(x) for x in ["он", "арё", "ат", "ент", "ан"]):
        return f"🇺🇿 {city_name}"
    return city_name

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

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

def get_role_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🟢 Я водитель"))
    builder.add(types.KeyboardButton(text="🔵 Я логист"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True…
  # ==================== START & ROLE ====================

@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    add_user(message.from_user.id, message.from_user.username or "")
    role = get_user_role(message.from_user.id)
    if role:
        if role == "driver":
            await state.set_state(DriverStates.main_menu)
            await message.answer(
                "🚛 *TR PRO BOT*\n\nС возвращением! Выбери действие:",
                reply_markup=get_driver_main_keyboard(),
                parse_mode="Markdown"
            )
        else:
            await state.set_state(LogisticianStates.main_menu)
            await message.answer(
                "🚛 *TR PRO BOT*\n\nС возвращением! Выбери действие:",
                reply_markup=get_logistician_main_keyboard(),
                parse_mode="Markdown"
            )
    else:
        await state.set_state(UserRole.choosing_role)
        await message.answer(
            "🚛 *TR PRO BOT*\n\nДобро пожаловать!\nВыбери свою роль:",
            reply_markup=get_role_keyboard(),
            parse_mode="Markdown"
        )

@dp.message(UserRole.choosing_role, F.text.in_(["🟢 Я водитель", "🔵 Я логист"]))
async def role_chosen(message: types.Message, state: FSMContext):
    if message.text == "🟢 Я водитель":
        add_user(message.from_user.id, message.from_user.username or "", role="driver")
        await state.set_state(DriverStates.main_menu)
        await message.answer(
            "🟢 *Режим водителя активирован*\n\nВыбери действие:",
            reply_markup=get_driver_main_keyboard(),
            parse_mode="Markdown"
        )
    else:
        add_user(message.from_user.id, message.from_user.username or "", role="logistician")
        await state.set_state(LogisticianStates.main_menu)
        await message.answer(
            "🔵 *Режим логиста активирован*\n\nВыбери действие:",
            reply_markup=get_logistician_main_keyboard(),
            parse_mode="Markdown"
        )

# ==================== DRIVER: SEARCH CARGO ====================

@dp.message(DriverStates.main_menu, F.text == "🔍 Найти груз")
async def driver_search_cargo_start(message: types.Message, state: FSMContext):
    await state.set_state(DriverStates.searching_cargo_origin)
    await message.answer(
        "📍 *Поиск груза*\n\nВведи *город отправления* (откуда забрать груз):",
        parse_mode="Markdown"
    )

@dp.message(DriverStates.searching_cargo_origin)
async def driver_search_cargo_origin(message: types.Message, state: FSMContext):
    await state.update_data(origin=message.text.strip())
    await state.set_state(DriverStates.searching_cargo_destination)
    await message.answer(
        f"📍 Откуда: *{message.text.strip()}*\n\nТеперь введи *город назначения* (куда везти):",
        parse_mode="Markdown"
    )

@dp.message(DriverStates.searching_cargo_destination)
async def driver_search_cargo_destination(message: types.Message, state: FSMContext):
    data = await state.get_data()
    origin = data.get("origin")
    destination = message.text.strip()
    await state.set_state(DriverStates.main_menu)
    results = get_cargo_by_route(origin, destination)
    if not results:
        await message.answer(
            f"😔 Грузов по маршруту *{origin} → {destination}* не найдено.\n\n"
            f"Попробуй подписаться на это направление — мы пришлём уведомление, "
            f"когда появится подходящий груз.",
            reply_markup=get_driver_main_keyboard(),
            parse_mode="Markdown"
        )
        return
    response = f"📦 *Грузы по маршруту {origin} → {destination}:*\n\n"
    for idx, cargo in enumerate(results[:10], 1):
        origin_flag = get_city_with_flag(cargo[2])
        dest_flag = get_city_with_flag(cargo[3])
        response += (
            f"*{idx}.* {origin_flag} → {dest_flag}\n"
            f"📦 {cargo[4]}\n"
            f"⚖️ Вес: {cargo[5]} | 📏 Объём: {cargo[6]}\n"
            f"💰 Цена: {…
      # ==================== DRIVER: MY VEHICLES ====================

@dp.message(DriverStates.main_menu, F.text == "📋 Мои объявления")
async def driver_my_vehicles(message: types.Message, state: FSMContext):
    driver_id = get_driver_id(message.from_user.id)
    if not driver_id:
        await message.answer("У тебя пока нет объявлений.", reply_markup=get_driver_main_keyboard())
        return
    vehicles = get_driver_vehicles(driver_id)
    if not vehicles:
        await message.answer("У тебя пока нет объявлений о свободных машинах.", reply_markup=get_driver_main_keyboard())
        return
    response = "📋 *Твои объявления о свободных машинах:*\n\n"
    for idx, v in enumerate(vehicles, 1):
        response += (
            f"*{idx}.* {get_city_with_flag(v[1])} → {get_city_with_flag(v[2])}\n"
            f"🚛 Кузов: {v[3]}\n"
            f"⚖️ {v[4]} | 📅 {v[5]}\n"
            f"📞 {v[6]}\n"
            f"━━━━━━━━━━━━━\n"
        )
    await message.answer(response, reply_markup=get_driver_main_keyboard(), parse_mode="Markdown")

# ==================== LOGISTICIAN: MAIN MENU ====================

@dp.message(LogisticianStates.main_menu, F.text == "📦 Разместить груз")
async def logistician_add_cargo_start(message: types.Message, state: FSMContext):
    await state.set_state(LogisticianStates.choosing_placement_mode)
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="✍️ Вручную пошагово"))
    builder.add(types.KeyboardButton(text="🤖 Распознать из текста"))
    builder.adjust(2)
    await message.answer(
        "📦 *Разместить груз*\n\nВыбери способ:",
        reply_markup=builder.as_markup(resize_keyboard=True),
        parse_mode="Markdown"
    )

@dp.message(LogisticianStates.choosing_placement_mode, F.text == "🤖 Распознать из текста")
async def logistician_single_message_mode(message: types.Message, state: FSMContext):
    await state.set_state(LogisticianStates.single_message_input)
    await message.answer(
        "🤖 *Распознавание груза из текста*\n\n"
        "Пришли одно сообщение со всей информацией:\n\n"
        "*Пример:*\n"
        "`Ташкент → Москва, 20 тонн, 90 м³, продукты, 5000$, 25.06.2026, +998901234567`",
        parse_mode="Markdown"
    )

@dp.message(LogisticianStates.single_message_input)
async def logistician_parse_message(message: types.Message, state: FSMContext):
    text = message.text
    origin_match = re.search(r'([А-Яа-яЁёA-Za-z\s]+)\s*→\s*([А-Яа-яЁёA-Za-z\s]+?)(?:,|$)', text)
    if not origin_match:
        await message.answer(
            "❌ Не удалось распознать маршрут. Используй формат: `Город1 → Город2`\n\n"
            "Попробуй ещё раз или выбери ручной режим.",
            parse_mode="Markdown"
        )
        return
    origin = origin_match.group(1).strip()
    destination = origin_match.group(2).strip()
    remaining = text[origin_match.end():]
    weight_match = re.search(r'(\d+\s*(?:тонн|т|kg|кг))', remaining, re.IGNORECASE)
    volume_match = re.search(r'(\d+\s*(?:м³|м3|m³))', remaining, re.IGNORECASE)
    price_match = re.search(r'(\d+\s*(?:\$|usd|eur|€|руб|rub|сум))', remaining, re.IGNORECASE)
    date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{2,4})', remaining)
    cargo_type = "Общий груз"
    if any(w in remaining.lower() for w in ["продукт", "еда", "овощ", "фрукт"]):
        cargo_type = "Продукты"
    elif any(w in remaining.lower() for w in ["строит", "материал"]):
        cargo_type = "Стройматериалы"
    elif any(w in remaining.lower() for w in ["мебел"]):
        cargo_type = "Мебель"
    elif any(w in remaining.lower() for w in ["техника", "оборудование"]):
        cargo_type = "Техника"
    weight = weight_match.group(1) if weight_match else "не указано"
    volume = volume_match.group(1) if volume_match else "не указано"
    price = price_match.group(1) if price_match else "договорная"
    date = date_match.group(1) if date_match else "не указана"
    await state.update_data(
        origin=origin, destination=destination, cargo_type=cargo_ty…
