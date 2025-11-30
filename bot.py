import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
from config import TELEGRAM_TOKEN, CHECK_INTERVAL, CHANNEL_USERNAME
from flights import get_countries, get_cities_by_country, get_flights_for_date, get_flightable_directions, find_city_by_name, is_valid_iata_code
from db import init_db, add_search, get_all_searches, update_price, get_user_searches, get_user_subscriptions
import aiosqlite
from typing import Optional

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Главное меню (ReplyKeyboardMarkup)
def get_main_menu() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="Найти билет"), KeyboardButton(text="Подписаться на билет")],
        [KeyboardButton(text="Список подписок"), KeyboardButton(text="История поиска")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# Клавиатура для подписки на канал
def get_channel_subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription")]
    ])

# Инлайн-кнопка "Назад"
def get_back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="back_to_menu")]
    ])

# Inline-кнопка "В меню"
def get_inline_menu_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="В меню", callback_data="back_to_menu")]
    ])

class FlightSearch(StatesGroup):
    choosing_origin_country = State()
    choosing_origin_city = State()
    choosing_destination_country = State()
    choosing_destination_city = State()
    choosing_passengers = State()
    choosing_transfers = State()
    choosing_date = State()

# Страны СНГ с названиями
CIS_COUNTRIES = [
    {"code": "RU", "name": "Россия"},
    {"code": "KZ", "name": "Казахстан"},
    {"code": "BY", "name": "Беларусь"},
    {"code": "AZ", "name": "Азербайджан"},
    {"code": "AM", "name": "Армения"},
    {"code": "KG", "name": "Кыргызстан"},
    {"code": "MD", "name": "Молдова"},
    {"code": "TJ", "name": "Таджикистан"},
    {"code": "TM", "name": "Туркменистан"},
    {"code": "UZ", "name": "Узбекистан"},
    {"code": "UA", "name": "Украина"}
]

# Все страны (дополняется через API)
ALL_COUNTRIES = CIS_COUNTRIES + [
    {"code": "BR", "name": "Бразилия"},
    {"code": "TH", "name": "Таиланд"},
    {"code": "US", "name": "США"},
    {"code": "TR", "name": "Турция"},
    {"code": "ES", "name": "Испания"}
]

async def check_channel_subscription(user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        print(f"Ошибка проверки подписки: {e}")
        return False

async def require_subscription_check(user_id: int, message: types.Message = None, callback: types.CallbackQuery = None):
    """Проверяет подписку и показывает сообщение если не подписан"""
    if await check_channel_subscription(user_id):
        return True
    
    subscription_message = (
        "📢 Для использования бота необходимо подписаться на наш канал!\n\n"
        "После подписки нажмите кнопку '✅ Я подписался'"
    )
    
    if message:
        await message.answer(subscription_message, reply_markup=get_channel_subscription_keyboard())
    elif callback:
        await callback.message.answer(subscription_message, reply_markup=get_channel_subscription_keyboard())
        await callback.answer()
    
    return False

async def get_extended_countries(is_origin: bool = False) -> list:
    api_countries = await get_countries()
    api_country_dict = {c.code: c.name for c in api_countries}
    extended = ALL_COUNTRIES.copy()
    for code, name in api_country_dict.items():
        if not any(c['code'] == code for c in extended):
            extended.append({"code": code, "name": name})
    return extended

async def find_country_by_name_or_code(input_text: str, countries: list) -> dict:
    input_text = input_text.strip().lower()
    for country in countries:
        if input_text == country['code'].lower() or input_text == country['name'].lower():
            return country
    return None

def create_country_keyboard(countries: list, page: int = 0) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    start_idx = page * 4
    end_idx = min(start_idx + 4, len(countries))
    for country in countries[start_idx:end_idx]:
        keyboard.inline_keyboard.append([InlineKeyboardButton(
            text=country['name'],
            callback_data=f"country_{country['code']}"
        )])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="Назад", callback_data=f"prev_page_country_{page-1}"))
    if end_idx < len(countries):
        nav_buttons.append(InlineKeyboardButton(text="Далее", callback_data=f"next_page_country_{page+1}"))
    if nav_buttons:
        keyboard.inline_keyboard.append(nav_buttons)
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="В меню", callback_data="back_to_menu")])
    return keyboard

def create_city_keyboard(cities: list, page: int = 0) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    start_idx = page * 4
    end_idx = min(start_idx + 4, len(cities))
    for city in cities[start_idx:end_idx]:
        keyboard.inline_keyboard.append([InlineKeyboardButton(
            text=f"{city.name} ({city.code})",
            callback_data=f"city_{city.code}"
        )])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="Назад", callback_data=f"prev_page_city_{page-1}"))
    if end_idx < len(cities):
        nav_buttons.append(InlineKeyboardButton(text="Далее", callback_data=f"next_page_city_{page+1}"))
    if nav_buttons:
        keyboard.inline_keyboard.append(nav_buttons)
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="В меню", callback_data="back_to_menu")])
    return keyboard

def validate_date(date_str: str) -> bool:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def format_date(iso_date: str) -> str:
    try:
        return datetime.fromisoformat(iso_date.replace("Z", "+00:00")).strftime("%d %B %Y, %H:%M")
    except:
        return iso_date

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    print(f"Chat ID: {message.chat.id}")
    await state.clear()
    
    if not await check_channel_subscription(message.from_user.id):
        greeting_text = (
            "👋 Привет! Я бот для поиска авиабилетов.\n\n"
            "📢 Для доступа к функциям бота необходимо подписаться на наш канал.\n\n"
            "После подписки нажмите кнопку '✅ Я подписался'"
        )
        await message.answer(greeting_text, reply_markup=get_channel_subscription_keyboard())
        return
    
    greeting_text = (
        "👋 Привет! Я бот для поиска авиабилетов.\n\n"
        "Вот что я умею:\n"
        "✈️ Найти билеты по направлениям и дате вылета (включая пересадки)\n"
        "💰 Подписаться на билеты и получать уведомления о снижении цены на 10% и более\n"
        "📜 Просмотреть историю ваших поисков\n"
        "📌 Посмотреть и управлять подписками\n\n"
        "Выберите действие ниже, чтобы начать:"
    )
    await message.answer(greeting_text, reply_markup=get_main_menu())

@dp.callback_query(lambda c: c.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery, state: FSMContext):
    if await check_channel_subscription(callback.from_user.id):
        await callback.message.edit_text(
            "✅ Отлично! Теперь вы можете использовать все функции бота.",
            reply_markup=None
        )
        greeting_text = (
            "👋 Привет! Я бот для поиска авиабилетов.\n\n"
            "Вот что я умею:\n"
            "✈️ Найти билеты по направлениям и дате вылета (включая пересадки)\n"
            "💰 Подписаться на билеты и получать уведомления о снижении цены на 10% и более\n"
            "📜 Просмотреть историю ваших поисков\n"
            "📌 Посмотреть и управлять подписками\n\n"
            "Выберите действие ниже, чтобы начать:"
        )
        await callback.message.answer(greeting_text, reply_markup=get_main_menu())
        await callback.answer()
    else:
        await callback.answer("❌ Вы еще не подписались на канал. Пожалуйста, подпишитесь и попробуйте снова.", show_alert=True)

@dp.message(Command("unsubscribe"))
async def unsubscribe(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    try:
        sub_id = int(message.text.split()[1])
        async with aiosqlite.connect("flights.db") as db:
            cursor = await db.execute(
                "SELECT id FROM searches WHERE id = ? AND chat_id = ? AND is_subscription = 1",
                (sub_id, message.chat.id)
            )
            result = await cursor.fetchone()
            if not result:
                await message.answer("❌ Подписка с таким ID не найдена.", reply_markup=get_main_menu())
                return
            await db.execute(
                "DELETE FROM searches WHERE id = ? AND chat_id = ? AND is_subscription = 1",
                (sub_id, message.chat.id)
            )
            await db.commit()
        await message.answer(f"✅ Подписка {sub_id} удалена.", reply_markup=get_main_menu())
    except (IndexError, ValueError):
        await message.answer("Укажите ID подписки: /unsubscribe <id>", reply_markup=get_main_menu())

@dp.message(F.text == "Назад")
async def back_to_menu(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    await state.clear()
    await message.answer("Вы вернулись в главное меню:", reply_markup=get_main_menu())

@dp.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu_callback(callback: types.CallbackQuery, state: FSMContext):
    if not await require_subscription_check(callback.from_user.id, callback=callback):
        return
        
    await state.clear()
    await callback.message.edit_text("Вы вернулись в главное меню:", reply_markup=None)
    await callback.message.answer("Выберите действие:", reply_markup=get_main_menu())
    await callback.answer()

@dp.message(F.text == "Найти билет")
async def start_search(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    await state.set_state(FlightSearch.choosing_origin_country)
    await state.update_data(is_subscription=False)
    countries = await get_extended_countries(is_origin=True)
    await message.answer("Выберите страну отправления или введите название/код (например, Россия или RU):",
                        reply_markup=create_country_keyboard(countries))

@dp.message(F.text == "Подписаться на билет")
async def start_subscription(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    searches = await get_user_searches(message.chat.id)
    countries = await get_extended_countries(is_origin=True)
    await state.update_data(is_subscription=True)
    if not searches:
        await state.set_state(FlightSearch.choosing_origin_country)
        await message.answer("У вас нет истории поисков. Выберите страну отправления или введите название/код (например, Россия или RU):",
                            reply_markup=create_country_keyboard(countries))
        return
    for search in searches:
        response = (
            f"ID: {search['id']}\n"
            f"✈️ {search['origin']} → {search['destination']}\n"
            f"Цена: {search['price']} ₽\n"
            f"Дата вылета: {search['departure_date']}\n"
            f"Аэропорт отправления: {search['origin_airport']}\n"
            f"Аэропорт прибытия: {search['destination_airport']}\n"
            f"Пассажиров: {search['passengers']}\n"
            f"[Ссылка]({search['ticket_link']})\n"
            f"Дата поиска: {search['created_at']}\n"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подписаться", callback_data=f"subscribe_{search['origin']}_{search['destination']}_{search['departure_date']}_{search['passengers']}")]
        ])
        await message.answer(response, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=keyboard)
    await message.answer("Или выберите новое направление:", reply_markup=create_country_keyboard(countries))
    await state.set_state(FlightSearch.choosing_origin_country)

@dp.message(F.text == "Список подписок")
async def list_subscriptions(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    subscriptions = await get_user_subscriptions(message.chat.id)
    if not subscriptions:
        await message.answer("У вас нет активных подписок.", reply_markup=get_main_menu())
        return
    for sub in subscriptions:
        response = (
            f"ID: {sub['id']}\n"
            f"✈️ {sub['origin']} → {sub['destination']}\n"
            f"Цена: {sub['price']} ₽\n"
            f"Дата вылета: {sub['departure_date']}\n"
            f"Аэропорт отправления: {sub['origin_airport']}\n"
            f"Аэропорт прибытия: {sub['destination_airport']}\n"
            f"Пассажиров: {sub['passengers']}\n"
            f"[Ссылка]({sub['ticket_link']})\n"
            f"Дата подписки: {sub['created_at']}\n"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отписаться", callback_data=f"unsubscribe_{sub['id']}")],
            [InlineKeyboardButton(text="Обновить цену", callback_data=f"refresh_price_{sub['id']}")]
        ])
        await message.answer(response, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=keyboard)
    await message.answer("Выберите действие:", reply_markup=get_main_menu())

@dp.message(F.text == "История поиска")
async def list_history(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    searches = await get_user_searches(message.chat.id)
    if not searches:
        await message.answer("У вас нет истории поисков.", reply_markup=get_main_menu())
        return
    for search in searches:
        response = (
            f"ID: {search['id']}\n"
            f"✈️ {search['origin']} → {search['destination']}\n"
            f"Цена: {search['price']} ₽\n"
            f"Дата вылета: {search['departure_date']}\n"
            f"Аэропорт отправления: {search['origin_airport']}\n"
            f"Аэропорт прибытия: {search['destination_airport']}\n"
            f"Пассажиров: {search['passengers']}\n"
            f"[Ссылка]({search['ticket_link']})\n"
            f"Дата поиска: {search['created_at']}\n"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Удалить", callback_data=f"delete_from_history_{search['id']}")]
        ])
        await message.answer(response, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=keyboard)
    await message.answer("Выберите действие:", reply_markup=get_main_menu())

@dp.message(FlightSearch.choosing_origin_country)
async def process_origin_country_text(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    input_text = message.text.strip()
    countries = await get_extended_countries(is_origin=True)
    country = await find_country_by_name_or_code(input_text, countries)
    if not country:
        await message.answer("❌ Страна не найдена. Введите название или код страны (например, Россия или RU):",
                            reply_markup=get_back_button())
        return
    await state.update_data(origin_country=country['code'])
    await state.set_state(FlightSearch.choosing_origin_city)
    cities = await get_cities_by_country(country['code'], is_origin=True)
    if not cities:
        await message.answer("❌ Нет доступных городов отправления для этой страны. Попробуйте другую:",
                            reply_markup=get_main_menu())
        await state.set_state(FlightSearch.choosing_origin_country)
        return
    await message.answer(f"Вы выбрали страну отправления: {country['name']}\n"
                        "Выберите город отправления или введите код/название (например, Москва или MOW):",
                        reply_markup=create_city_keyboard(cities))

@dp.message(FlightSearch.choosing_destination_country)
async def process_destination_country_text(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    input_text = message.text.strip()
    countries = await get_extended_countries(is_origin=False)
    country = await find_country_by_name_or_code(input_text, countries)
    if not country:
        await message.answer("❌ Страна не найдена. Введите название или код страны (например, Испания или ES):",
                            reply_markup=get_back_button())
        return
    await state.update_data(destination_country=country['code'])
    await state.set_state(FlightSearch.choosing_destination_city)
    
    cities = await get_cities_by_country(country['code'])
    
    if not cities:
        await message.answer("❌ Нет доступных городов прибытия для этой страны. Попробуйте другую:",
                            reply_markup=get_main_menu())
        await state.set_state(FlightSearch.choosing_destination_country)
        return
    await message.answer(f"Вы выбрали страну прибытия: {country['name']}\n"
                        "Выберите город прибытия или введите код/название (например, Мадрид или MAD):",
                        reply_markup=create_city_keyboard(cities))

@dp.message(FlightSearch.choosing_origin_city)
async def process_origin_city_text(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    input_text = message.text.strip()
    data = await state.get_data()
    country_code = data.get("origin_country")
    cities = await get_cities_by_country(country_code, is_origin=True)
    
    if is_valid_iata_code(input_text.upper()):
        city = next((c for c in cities if c.code == input_text.upper()), None)
        if not city:
            await message.answer(f"❌ Код города {input_text.upper()} не найден в стране. Попробуйте снова (например, Москва или MOW):",
                                reply_markup=get_back_button())
            return
    else:
        city = await find_city_by_name(input_text, country_code)
        if not city:
            await message.answer(f"❌ Город '{input_text}' не найден в стране. Попробуйте снова (например, Москва или MOW):",
                                reply_markup=get_back_button())
            return
    
    await state.update_data(origin_city=city.code)
    await state.set_state(FlightSearch.choosing_destination_country)
    countries = await get_extended_countries(is_origin=False)
    await message.answer(f"Вы выбрали город отправления: {city.code}\n"
                        "Выберите страну прибытия или введите название/код (например, Испания или ES):",
                        reply_markup=create_country_keyboard(countries))

@dp.message(FlightSearch.choosing_destination_city)
async def process_destination_city(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    input_text = message.text.strip()
    data = await state.get_data()
    country_code = data.get("destination_country")
    
    cities = await get_cities_by_country(country_code)
    
    if is_valid_iata_code(input_text.upper()):
        city = next((c for c in cities if c.code == input_text.upper()), None)
        if not city:
            await message.answer(f"❌ Код города {input_text.upper()} не найден в стране. Попробуйте снова (например, Мадрид или MAD):",
                                reply_markup=get_back_button())
            return
    else:
        city = await find_city_by_name(input_text, country_code)
        if not city:
            await message.answer(f"❌ Город '{input_text}' не найден в стране. Попробуйте снова (например, Мадрид или MAD):",
                                reply_markup=get_back_button())
            return
    
    await state.update_data(destination_city=city.code)
    await state.set_state(FlightSearch.choosing_passengers)
    await message.answer("Введите количество пассажиров (1–9):", reply_markup=get_back_button())

@dp.message(FlightSearch.choosing_passengers)
async def process_passengers(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    try:
        passengers = int(message.text)
        if not 1 <= passengers <= 9:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите число от 1 до 9:", reply_markup=get_back_button())
        return
    await state.update_data(passengers=passengers)
    await state.set_state(FlightSearch.choosing_transfers)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Только прямые", callback_data="transfers_0")],
        [InlineKeyboardButton(text="До 1 пересадки", callback_data="transfers_1")],
        [InlineKeyboardButton(text="До 2 пересадок", callback_data="transfers_2")],
        [InlineKeyboardButton(text="Все варианты", callback_data="transfers_any")]
    ])
    await message.answer("Выберите количество пересадок:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith("transfers_"))
async def process_transfers(callback: types.CallbackQuery, state: FSMContext):
    if not await require_subscription_check(callback.from_user.id, callback=callback):
        return
        
    transfers_str = callback.data.split("_")[1]
    transfers = None if transfers_str == "any" else int(transfers_str)
    await state.update_data(transfers=transfers)
    await state.set_state(FlightSearch.choosing_date)
    await callback.message.edit_text("Введите дату вылета в формате YYYY-MM-DD (например, 2025-11-15):",
                                    reply_markup=get_back_button())
    await callback.answer()

@dp.message(FlightSearch.choosing_date)
async def process_date(message: types.Message, state: FSMContext):
    if not await require_subscription_check(message.from_user.id, message=message):
        return
        
    departure_date = message.text.strip()
    if not validate_date(departure_date):
        await message.answer(
            "❌ Неверный формат даты. Введите в формате YYYY-MM-DD (например, 2025-11-15):",
            reply_markup=get_back_button()
        )
        return

    data = await state.get_data()
    origin_city = data.get("origin_city")
    destination_city = data.get("destination_city")
    passengers = data.get("passengers")
    transfers = data.get("transfers")
    is_subscription = data.get("is_subscription", False)

    if not origin_city or not destination_city:
        await message.answer("❌ Ошибка: не указан город отправления или прибытия. Начните поиск заново.", reply_markup=get_main_menu())
        await state.clear()
        return

    parsing_message = await message.answer("🔍 Бот собирает информацию о рейсах, пожалуйста, подождите...")

    prices = await get_flights_for_date(origin_city, destination_city, departure_date, passengers, transfers)
    if not prices:
        await bot.edit_message_text(
            text=f"❌ Не удалось найти билеты из {origin_city} в {destination_city} на {departure_date}.\n"
                 f"Попробуйте другую дату:",
            chat_id=message.chat.id,
            message_id=parsing_message.message_id
        )
        # Остаёмся в состоянии choosing_date, чтобы ждать новую дату
        await message.answer("Введите новую дату вылета (YYYY-MM-DD):", reply_markup=get_back_button())
        return

    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=parsing_message.message_id)
    except Exception as e:
        print(f"Ошибка удаления сообщения: {e}")

    chunk_size = 5
    for i in range(0, len(prices), chunk_size):
        chunk = prices[i:i + chunk_size]
        response = f"Доступные билеты из {origin_city} в {destination_city} на {departure_date} ({passengers} пассажиров, часть {i // chunk_size + 1}):\n\n"
        for price in chunk:
            response += (
                f"✈️ {price.origin} → {price.destination} ({'прямой' if price.transfers == 0 else f'с {price.transfers} пересадкой(ами)'})\n"
                f"Цена: {price.price} ₽\n"
                f"Дата вылета: {format_date(price.departure_date)}\n"
                f"Аэропорт отправления: {price.origin_airport}\n"
                f"Аэропорт прибытия: {price.destination_airport}\n"
                f"Пассажиров: {price.passengers}\n"
                f"[Ссылка]({price.ticket_link})\n\n"
            )
            await add_search(
                message.chat.id,
                price.origin,
                price.destination,
                price.price,
                price.departure_date,
                price.origin_airport,
                price.destination_airport,
                price.ticket_link,
                price.passengers,
                is_subscription=False
            )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подписаться на это направление", callback_data=f"subscribe_{origin_city}_{destination_city}_{departure_date}_{passengers}")]
        ])
        await message.answer(response, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=keyboard)

    if is_subscription:
        price = prices[0]
        search_id = await add_search(
            message.chat.id,
            price.origin,
            price.destination,
            price.price,
            price.departure_date,
            price.origin_airport,
            price.destination_airport,
            price.ticket_link,
            price.passengers,
            is_subscription=True
        )
        await message.answer(
            f"✅ Подписка на {origin_city} → {destination_city} на {departure_date} оформлена (ID: {search_id})!\n"
            "Вы получите уведомление, если цена снизится на 10% или больше.",
            reply_markup=get_main_menu()
        )
    else:
        await message.answer("Поиск завершён. Выберите действие:", reply_markup=get_main_menu())

    await state.clear()

@dp.callback_query(lambda c: c.data.startswith("country_") or c.data.startswith("next_page_country_") or c.data.startswith("prev_page_country_"))
async def process_country_selection(callback: types.CallbackQuery, state: FSMContext):
    if not await require_subscription_check(callback.from_user.id, callback=callback):
        return
        
    data = await state.get_data()
    current_state = await state.get_state()
    countries = await get_extended_countries(is_origin=(current_state == FlightSearch.choosing_origin_country.state))

    if callback.data.startswith("next_page_country_") or callback.data.startswith("prev_page_country_"):
        page = int(callback.data.split("_")[-1])
        if current_state == FlightSearch.choosing_origin_country.state:
            await callback.message.edit_text("Выберите страну отправления или введите название/код (например, Россия или RU):",
                                            reply_markup=create_country_keyboard(countries, page))
        else:
            await callback.message.edit_text("Выберите страну прибытия или введите название/код (например, Испания или ES):",
                                            reply_markup=create_country_keyboard(countries, page))
        await callback.answer()
        return

    country_code = callback.data.split("_")[1]
    if not any(c['code'] == country_code for c in countries):
        await callback.message.answer("❌ Неверная страна. Попробуйте снова:", reply_markup=get_inline_menu_button())
        await callback.answer()
        return

    if current_state == FlightSearch.choosing_origin_country.state:
        await state.update_data(origin_country=country_code)
        await state.set_state(FlightSearch.choosing_origin_city)
        cities = await get_cities_by_country(country_code, is_origin=True)
        if not cities:
            await callback.message.answer("❌ Нет доступных городов отправления для этой страны. Попробуйте другую:",
                                        reply_markup=get_inline_menu_button())
            await state.set_state(FlightSearch.choosing_origin_country)
            await callback.message.edit_text("Выберите страну отправления или введите название/код (например, Россия или RU):",
                                            reply_markup=create_country_keyboard(countries))
            await callback.answer()
            return
        country_name = next((c['name'] for c in countries if c['code'] == country_code), country_code)
        await callback.message.edit_text(f"Вы выбрали страну отправления: {country_name}\n"
                                        "Выберите город отправления или введите код/название (например, Москва или MOW):",
                                        reply_markup=create_city_keyboard(cities))
    else:
        await state.update_data(destination_country=country_code)
        await state.set_state(FlightSearch.choosing_destination_city)
        
        cities = await get_cities_by_country(country_code)
        
        if not cities:
            await callback.message.answer("❌ Нет доступных городов прибытия для этой страны. Попробуйте другую:",
                                        reply_markup=get_inline_menu_button())
            await state.set_state(FlightSearch.choosing_destination_country)
            await callback.message.edit_text("Выберите страну прибытия или введите название/код (например, Испания или ES):",
                                            reply_markup=create_country_keyboard(countries))
            await callback.answer()
            return
        country_name = next((c['name'] for c in countries if c['code'] == country_code), country_code)
        await callback.message.edit_text(f"Вы выбрали страну прибытия: {country_name}\n"
                                        "Выберите город прибытия или введите код/название (например, Мадрид или MAD):",
                                        reply_markup=create_city_keyboard(cities))
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("city_") or c.data.startswith("next_page_city_") or c.data.startswith("prev_page_city_"))
async def process_city_selection(callback: types.CallbackQuery, state: FSMContext):
    if not await require_subscription_check(callback.from_user.id, callback=callback):
        return
        
    data = await state.get_data()
    current_state = await state.get_state()

    if callback.data.startswith("next_page_city_") or callback.data.startswith("prev_page_city_"):
        page = int(callback.data.split("_")[-1])
        if current_state == FlightSearch.choosing_origin_city.state:
            country_code = data.get("origin_country")
            cities = await get_cities_by_country(country_code, is_origin=True)
            await callback.message.edit_text("Выберите город отправления или введите код/название (например, Москва или MOW):",
                                            reply_markup=create_city_keyboard(cities, page))
        else:
            country_code = data.get("destination_country")
            cities = await get_cities_by_country(country_code)
            await callback.message.edit_text("Выберите город прибытия или введите код/название (например, Мадрид или MAD):",
                                            reply_markup=create_city_keyboard(cities, page))
        await callback.answer()
        return

    city_code = callback.data.split("_")[1]
    if current_state == FlightSearch.choosing_origin_city.state:
        cities = await get_cities_by_country(data.get("origin_country"), is_origin=True)
    else:
        cities = await get_cities_by_country(data.get("destination_country"))
    if not any(c.code == city_code for c in cities):
        await callback.message.answer("❌ Неверный код города. Попробуйте снова:", reply_markup=get_inline_menu_button())
        await callback.answer()
        return

    if current_state == FlightSearch.choosing_origin_city.state:
        await state.update_data(origin_city=city_code)
        await state.set_state(FlightSearch.choosing_destination_country)
        countries = await get_extended_countries(is_origin=False)
        await callback.message.edit_text(f"Вы выбрали город отправления: {city_code}\n"
                                        "Выберите страну прибытия или введите название/код (например, Испания или ES):",
                                        reply_markup=create_country_keyboard(countries))
    else:
        await state.update_data(destination_city=city_code)
        await state.set_state(FlightSearch.choosing_passengers)
        try:
            await callback.message.delete()
        except Exception as e:
            print(f"Ошибка удаления сообщения: {e}")
        await callback.message.answer("Введите количество пассажиров (1–9):",
                                     reply_markup=get_back_button())
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("subscribe_"))
async def subscribe_direction(callback: types.CallbackQuery, state: FSMContext):
    if not await require_subscription_check(callback.from_user.id, callback=callback):
        return
        
    try:
        _, origin, destination, departure_date, passengers = callback.data.split("_")
        passengers = int(passengers)
    except ValueError:
        await callback.message.answer("❌ Ошибка обработки подписки. Попробуйте снова.", reply_markup=get_main_menu())
        await callback.answer()
        return

    async with aiosqlite.connect("flights.db") as db:
        cursor = await db.execute(
            "SELECT id FROM searches WHERE chat_id = ? AND origin = ? AND destination = ? AND departure_date = ? AND passengers = ? AND is_subscription = 1",
            (callback.message.chat.id, origin, destination, departure_date, passengers)
        )
        if await cursor.fetchone():
            await callback.message.answer(f"❌ Вы уже подписаны на направление {origin} → {destination} на {departure_date}.",
                                        reply_markup=get_main_menu())
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer()
            return

    prices = await get_flights_for_date(origin, destination, departure_date, passengers, max_transfers=None)
    if not prices:
        await callback.message.answer(f"❌ Не удалось найти билеты для {origin} → {destination} на {departure_date}.",
                                    reply_markup=get_main_menu())
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer()
        return

    price = prices[0]
    search_id = await add_search(
        callback.message.chat.id,
        price.origin,
        price.destination,
        price.price,
        price.departure_date,
        price.origin_airport,
        price.destination_airport,
        price.ticket_link,
        price.passengers,
        is_subscription=True
    )
    await callback.message.answer(
        f"✅ Подписка на {origin} → {destination} на {departure_date} оформлена!\n"
        "Вы получите уведомление, если цена снизится на 10% или больше.",
        reply_markup=get_main_menu()
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("delete_from_history_"))
async def delete_from_history(callback: types.CallbackQuery, state: FSMContext):
    if not await require_subscription_check(callback.from_user.id, callback=callback):
        return
        
    search_id = int(callback.data.split("_")[-1])
    async with aiosqlite.connect("flights.db") as db:
        cursor = await db.execute(
            "SELECT id FROM searches WHERE id = ? AND chat_id = ? AND is_subscription = 0",
            (search_id, callback.message.chat.id)
        )
        result = await cursor.fetchone()
        if not result:
            await callback.message.answer("❌ Запись поиска не найдена.", reply_markup=get_main_menu())
            await callback.answer()
            return
        await db.execute(
            "DELETE FROM searches WHERE id = ? AND chat_id = ? AND is_subscription = 0",
            (search_id, callback.message.chat.id)
        )
        await db.commit()
    await callback.message.edit_text("✅ Запись поиска удалена.")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("unsubscribe_"))
async def unsubscribe_callback(callback: types.CallbackQuery, state: FSMContext):
    if not await require_subscription_check(callback.from_user.id, callback=callback):
        return
        
    sub_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect("flights.db") as db:
        cursor = await db.execute(
            "SELECT id FROM searches WHERE id = ? AND chat_id = ? AND is_subscription = 1",
            (sub_id, callback.message.chat.id)
        )
        result = await cursor.fetchone()
        if not result:
            await callback.message.answer("❌ Подписка с таким ID не найдена.", reply_markup=get_main_menu())
            await callback.answer()
            return
        await db.execute(
            "DELETE FROM searches WHERE id = ? AND chat_id = ? AND is_subscription = 1",
            (sub_id, callback.message.chat.id)
        )
        await db.commit()
    await callback.message.edit_text(f"✅ Подписка {sub_id} удалена.")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("refresh_price_"))
async def refresh_price_callback(callback: types.CallbackQuery, state: FSMContext):
    if not await require_subscription_check(callback.from_user.id, callback=callback):
        return
        
    search_id = int(callback.data.split("_")[-1])
    async with aiosqlite.connect("flights.db") as db:
        cursor = await db.execute(
            "SELECT origin, destination, departure_date, passengers, is_subscription FROM searches WHERE id = ? AND chat_id = ?",
            (search_id, callback.message.chat.id)
        )
        result = await cursor.fetchone()
        if not result:
            await callback.message.answer("❌ Запись не найдена.", reply_markup=get_main_menu())
            await callback.answer()
            return
        origin, destination, departure_date, passengers, is_subscription = result
        search = {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "passengers": passengers,
            "is_subscription": is_subscription
        }
    flights = await get_flights_for_date(origin, destination, departure_date, passengers, max_transfers=None)
    if not flights:
        await callback.message.edit_text("❌ Не удалось обновить цену. Попробуйте позже.")
        await callback.answer()
        return
    new_price = flights[0].price
    new_departure_date = flights[0].departure_date
    new_origin_airport = flights[0].origin_airport
    new_destination_airport = flights[0].destination_airport
    new_ticket_link = flights[0].ticket_link
    new_passengers = flights[0].passengers
    await update_price(
        search_id,
        new_price,
        new_departure_date,
        new_origin_airport,
        new_destination_airport,
        new_ticket_link,
        new_passengers
    )
    response = (
        f"ID: {search_id}\n"
        f"✈️ {origin} → {destination} ({'прямой' if flights[0].transfers == 0 else f'с {flights[0].transfers} пересадкой(ами)'})\n"
        f"Цена: {new_price} ₽\n"
        f"Дата вылета: {new_departure_date}\n"
        f"Аэропорт отправления: {new_origin_airport}\n"
        f"Аэропорт прибытия: {new_destination_airport}\n"
        f"Пассажиров: {new_passengers}\n"
        f"[Ссылка]({new_ticket_link})\n"
        f"Цена обновлена: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отписаться" if search['is_subscription'] else "Удалить", 
                             callback_data=f"{'unsubscribe' if search['is_subscription'] else 'delete_from_history'}_{search_id}")],
        [InlineKeyboardButton(text="Обновить цену", callback_data=f"refresh_price_{search_id}")]
    ])
    await callback.message.edit_text(response, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=keyboard)
    await callback.answer()

async def check_prices_loop():
    while True:
        try:
            searches = await get_all_searches()
            for s in searches:
                search_id, chat_id, origin, destination, last_price, departure_date, origin_airport, destination_airport, ticket_link, passengers, is_subscription, created_at = s
                try:
                    # Проверяем подписку пользователя перед отправкой уведомления
                    if not await check_channel_subscription(chat_id):
                        continue
                        
                    flights = await get_flights_for_date(origin, destination, departure_date, passengers, max_transfers=None)
                    if flights and flights[0].price < last_price * 0.9:
                        await bot.send_message(
                            chat_id,
                            f"💸 Цена на {origin} → {destination} ({'прямой' if flights[0].transfers == 0 else f'с {flights[0].transfers} пересадкой(ами)'})\n"
                            f"снизилась на {int((1 - flights[0].price / last_price) * 100)}%!\n"
                            f"С {last_price} ₽ до {flights[0].price} ₽\n"
                            f"Дата вылета: {format_date(flights[0].departure_date)}\n"
                            f"Аэропорт отправления: {flights[0].origin_airport}\n"
                            f"Аэропорт прибытия: {flights[0].destination_airport}\n"
                            f"Пассажиров: {flights[0].passengers}\n"
                            f"[Ссылка]({flights[0].ticket_link})",
                            parse_mode="Markdown",
                            disable_web_page_preview=True
                        )
                        await update_price(
                            search_id,
                            flights[0].price,
                            flights[0].departure_date,
                            flights[0].origin_airport,
                            flights[0].destination_airport,
                            flights[0].ticket_link,
                            flights[0].passengers
                        )
                except aiohttp.ClientError:
                    pass
        except Exception:
            pass
        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    await init_db()
    asyncio.create_task(check_prices_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
