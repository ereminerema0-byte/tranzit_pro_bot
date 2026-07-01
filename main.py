import asyncio
import logging
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from db import init_db, add_user, get_user_role, get_driver_id, get_logistician_id, add_cargo, get_cargo_by_route, get_logistician_cargo, add_vehicle, get_vehicles_by_route, get_driver_vehicles, add_subscription, get_subscribers_for_route

import os

# Bot configuration
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@tranzitpro1")
CONTACT_USERNAME = os.getenv("CONTACT_USERNAME", "@Oleg34381")

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
    
    # Основной поиск по словарю
    if city_name in CITY_FLAGS:
        return f"{CITY_FLAGS[city_name]} {city_name}"
    
    # Дополнительная логика по окончанию слова
    name_low = city_name.lower()
    if any(name_low.endswith(x) for x in ["ск", "град", "бург", "ов", "ино", "ево", "ка", "ль", "мь"]):
        return f"🇷🇺 {city_name}"
    if any(name_low.endswith(x) for x in ["он", "арё", "ат", "ент", "ан"]):
        return f"🇺🇿 {city_name}"
    
    return city_name

# Configure logging
logging.basicConfig(level=logging.INFO)

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
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_logistician_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="📦 Разместить груз"))
    builder.add(types.KeyboardButton(text="🔍 Найти груз"))
    builder.add(types.KeyboardButton(text="🚛 Найти свободные машины"))
    builder.add(types.KeyboardButton(text="📋 Мои объявления"))
    builder.adjust(2)
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
    await state.update_data(capacity=message.text)
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
    add_vehicle(
        driver_id,
        user_data['body_type'],
        float(user_data['capacity']),
        user_data['origin'],
        user_data['destination'],
        user_data['date'],
        message.text
    )
    await message.answer("Ваше объявление о свободной машине размещено!", reply_markup=get_driver_main_keyboard())
    await state.set_state(DriverStates.main_menu)

    # Auto-publish to channel
    origin_f = f"{user_data.get('origin_flag', '')} {user_data['origin']}".strip()
    dest_f = f"{user_data.get('destination_flag', '')} {user_data['destination']}".strip()
    channel_message = (
        f"🚚 *Свободная машина*\n\n"
        f"📍 *Откуда:* {origin_f}\n"
        f"📍 *Куда:* {dest_f}\n"
        f"📦 *Тип кузова:* {user_data['body_type']}\n"
        f"⚖️ *Грузоподъёмность:* {user_data['capacity']} т\n"
        f"📅 *Дата готовности:* {user_data['date']}\n"
        f"📞 *Контакт:* {CONTACT_USERNAME}\n\n"
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
    await state.update_data(weight=message.text)
    await message.answer("Введите объем груза в м³:")
    await state.set_state(LogisticianStates.adding_cargo_volume)

@dp.message(LogisticianStates.adding_cargo_volume)
async def logistician_add_cargo_volume(message: types.Message, state: FSMContext):
    await state.update_data(volume=message.text)
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
    add_cargo(
        logistician_id,
        user_data['origin'],
        user_data['destination'],
        user_data['cargo_type'],
        float(user_data['weight']),
        float(user_data['volume']),
        user_data['price'],
        user_data['date'],
        message.text
    )
    await message.answer("Ваш груз размещен!", reply_markup=get_logistician_main_keyboard())
    await state.set_state(LogisticianStates.main_menu)

    # Auto-publish to channel
    origin_f = f"{user_data.get('origin_flag', '')} {user_data['origin']}".strip()
    dest_f = f"{user_data.get('destination_flag', '')} {user_data['destination']}".strip()
    price_value = str(user_data['price']).replace('$', '').replace(',', '.').strip()
    try:  
        price_num = int(float(price_value))
        price_display = f"{price_num - 200}$"
    except:
        price_display = user_data['price']
    channel_message = (
            f"📦 *Новый груз*\n\n"
            f"📍 *Откуда:* {origin_f}\n"
            f"📍 *Куда:* {dest_f}\n"
            f"🏷️ *Тип груза:* {user_data['cargo_type']}\n"
            f"⚖️ *Вес:* {user_data['weight']} кг\n"
            f"📏 *Объем:* {user_data['volume']} м³\n"
            f"💰 *Цена:* {price_display}\n"
            f"📅 *Дата готовности:* {user_data['date']}\n"
            f"📞 *Контакт:* {CONTACT_USERNAME}\n\n"
            f"\n\n🤖 *Хотите быстро найти подходящий груз?*\nНапишите боту: @tranzit_pro_bot"
    
        )
    try:
        await bot.send_message(CHANNEL_ID, channel_message, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Failed to publish to channel: {e}")

@dp.message(LogisticianStates.choosing_placement_mode, F.text == "Одним сообщением")
async def logistician_add_cargo_single_msg(message: types.Message, state: FSMContext):
    await message.answer(
        "Отправьте информацию о грузе одним сообщением в свободной форме.\n\n"
        "Пример:\n"
        "Андижон Нижний Новгород\n"
        "Юк памидор\n"
        "22 тонна\n"
        "Реф керак\n"
        "Выгрузка склад\n"
        "Аванс бор\n"
        "Срочно\n"
        "973439606"
    )
    await state.set_state(LogisticianStates.single_message_input)

@dp.message(LogisticianStates.choosing_placement_mode, F.text == "Назад")
async def logistician_add_cargo_back(message: types.Message, state: FSMContext):
    await message.answer("Главное меню", reply_markup=get_logistician_main_keyboard())
    await state.set_state(LogisticianStates.main_menu)

import re

def parse_cargo_block(text):
    if not text or not text.strip():
        return None

    full_text = text.strip()
    full_lower = full_text.lower()

    origin = "Не указано"
    destination = "Не указано"
    cargo = "Не указано"
    weight = "Не указано"
    body = "Не указано"
    price = "Не указано"
    conditions = []
    contact = CONTACT_USERNAME

    # Откуда и Куда
    route = re.search(r'Откуда\s*:\s*(.+?)\s*Куда\s*:\s*(.+)', full_text, re.IGNORECASE)
    if route:
        origin = route.group(1).strip()
        destination = route.group(2).strip()
    if not route:
        route = re.search(r'( +?)\s*[:\-→]\s*( +)', full_text, re.IGNORECASE)
    if route:
        origin = route.group(1).strip()
        destination = route.group(2).strip()

    # Запасной вариант поиска маршрута
    if origin == "Не указано":
        alt_route = re.search(r'([А-ЯЁA-Z][а-яёa-z\s-]+?)\s*[:\-→]\s*([А-ЯЁA-Z][а-яёa-z\s-]+)', full_text, re.IGNORECASE)
        if alt_route:
            origin = alt_route.group(1).strip()
            destination = alt_route.group(2).strip()

    # Вес
    w = re.search(r'(\d{1,3}(?:[.,]\d{1,2})?)\s*(т|тонн|тонна|тн)', full_lower)
    if w:
        weight = w.group(1) + " т"

    # Цена
    p = re.search(r'(?:фрахт|цена|фрaхт|стоимость) *(\d{3,5})', full_lower)
    if p:
        price = p.group(1) + "$"
    else:
        p2 = re.search(r'(\d{3,5})\s*\$', full_lower)
        if p2:
            price = p2.group(1) + "$"

    # Кузов
    if "тент" in full_lower:
        body = "Тент"
    elif "реф" in full_lower:
        body = "Реф"

    # Груз
    if "салафан" in full_lower:
        cargo = "Салафан"

    # Условия
    if "аванс" in full_lower:
        conditions.append("Есть аванс")
    if "налич" in full_lower:
        conditions.append("Наличные")
    if "срочно" in full_lower:
        conditions.append("Срочно")

    conditions_str = ", ".join(conditions) if conditions else "Не указано"

    return {
        "origin": origin,
        "destination": destination,
        "cargo": cargo,
        "weight_str": weight,
        "body": body,
        "conditions": conditions_str,
        "price": price,
        "contact": contact
    }
                  
def format_cargo_message(c):
    origin_with_flag = get_city_with_flag(c['origin'])
    dest_with_flag = get_city_with_flag(c )
    
    # Отнимаем 200$ от указанной цены
    try:
        price_num = int(''.join(filter(str.isdigit, str(c ))))
        final_price = max(price_num - 200, 0)
        price_text = f"{final_price}$"
    except:
        price_text = c.get('price', "Не указано")

    return (
        f"📦 *Новое объявление о грузе:*\n\n"
        f"🔹 *Откуда:* {origin_with_flag}\n"
        f"🔹 *Куда:* {dest_with_flag}\n"
        f"🔹 *Груз:* {c }\n"
        f"🔹 *Вес:* {c }\n"
        f"🔹 *Кузов:* {c }\n"
        f"💰 *Фрахт:* {price_text}\n"
        f"🔹 *Условия:* {c }\n\n"
        f"📞 *Контакт:* {c.get('contact', CONTACT_USERNAME)}\n"
        f"\n\n🤖 *Хотите быстро найти подходящий груз?*\nНапишите боту: @tranzit\\_pro\\_bot"
    )

@dp.message(LogisticianStates.single_message_input)
async def process_single_message_cargo(message: types.Message, state: FSMContext):
    text = message.text.strip()
    blocks = [b.strip() for b in text.split('\n\n') if b.strip()]
    
    if not blocks:
        await message.answer("Не удалось найти объявления. Попробуйте отправить ещё раз.")
        return

    parsed_cargoes = []
    for block in blocks:
        parsed = parse_cargo_block(block)
        if parsed:
            parsed_cargoes.append(parsed)

    if not parsed_cargoes:
        await message.answer("Не удалось распознать данные. Попробуйте отправить в другом формате.")
        return

    await state.update_data(parsed_cargoes=parsed_cargoes)

    response_text = "📋 *Распознанные объявления:*\n\n"
    for i, c in enumerate(parsed_cargoes, 1):
        response_text += f"--- Объявление #{i+1} ---\n{format_cargo_message(c)}\n\n"
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
    
    for c in parsed_cargoes:
        add_cargo(logistician_id, c['origin'], c['destination'], c['cargo'], 0, 0, c['conditions'], "В описании", c['contact'])
        channel_message = format_cargo_message(c) + f"\n🤖 @tranzit_pro_bot"
        try:
            await bot.send_message(CHANNEL_ID, channel_message, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to publish to channel: {e}")
            
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
