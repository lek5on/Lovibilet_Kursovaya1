import aiohttp
import certifi
import ssl
from typing import List, Optional, Dict
from datetime import datetime
from config import TRAVELPAYOUTS_TOKEN
from pydantic import BaseModel, ValidationError

class City(BaseModel):
    code: str
    name: str
    country_code: str
    cases: dict = {}

class Country(BaseModel):
    code: str
    name: str

class FlightPrice(BaseModel):
    origin: str
    destination: str
    price: int
    departure_date: str
    origin_airport: str
    destination_airport: str
    ticket_link: str
    passengers: int
    transfers: int = 0  # Количество пересадок

# Кэш для городов, стран и направлений
_cities_cache: Optional[List[City]] = None
_countries_cache: Optional[List[Country]] = None
_directions_cache: Dict[str, List[str]] = {}

def is_valid_iata_code(code: str) -> bool:
    return len(code) == 3 and code.isupper() and code.isalpha() and code.isascii()

def normalize_date(date_str: str) -> str:
    """Normalize ISO date (e.g., 2025-10-17T21:50:00+03:00) to YYYY-MM-DD."""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return date_str

def normalize_datetime(date_str: str) -> str:
    """Возвращает дату и время в формате YYYY-MM-DD HH:MM"""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return date_str

def generate_aviasales_link(origin: str, destination: str, departure_date: str, passengers: int) -> str:
    """
    Генерирует ссылку на Aviasales с нужным количеством пассажиров.
    departure_date: формат YYYY-MM-DD
    """
    try:
        dt = datetime.strptime(departure_date[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    
    ddmm = dt.strftime("%d%m")
    return f"https://www.aviasales.ru/search/{origin}{ddmm}{destination}{passengers}"

async def _load_cities() -> List[City]:
    global _cities_cache
    if _cities_cache is not None:
        return _cities_cache
    url = "https://api.travelpayouts.com/data/ru/cities.json"
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"Ошибка API (города): {resp.status} - {await resp.text()}")
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    print(f"Ошибка: cities.json не является списком, получен {type(data)}")
                    return []
                cities = []
                for c in data:
                    code = c.get("code", "").upper()
                    if is_valid_iata_code(code) and c.get("cases"):
                        cities.append(City(
                            code=code,
                            name=c["name"],
                            country_code=c["country_code"],
                            cases=c.get("cases", {})
                        ))
                _cities_cache = cities
                return cities
        except aiohttp.ClientError as e:
            print(f"Ошибка сети при получении городов: {e}")
            return []

async def _load_countries() -> List[Country]:
    global _countries_cache
    if _countries_cache is not None:
        return _countries_cache
    url = "https://api.travelpayouts.com/data/ru/countries.json"
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"Ошибка API (страны): {resp.status} - {await resp.text()}")
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    print(f"Ошибка: countries.json не является списком, получен {type(data)}")
                    return []
                countries = [Country(code=c["code"], name=c["name"]) for c in data]
                _countries_cache = countries
                return countries
        except aiohttp.ClientError as e:
            print(f"Ошибка сети при получении стран: {e}")
            return []

async def get_flightable_directions(origin: str) -> List[str]:
    global _directions_cache
    if origin in _directions_cache:
        return _directions_cache[origin]
    
    url = "https://api.travelpayouts.com/v1/city-directions"
    params = {
        "origin": origin,
        "currency": "rub",
        "token": TRAVELPAYOUTS_TOKEN
    }
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    print(f"Ошибка API (городские направления): {resp.status} - {await resp.text()}")
                    _directions_cache[origin] = []
                    return []
                data = await resp.json()
                directions = [d["destination"] for d in data.get("data", {}).values() if is_valid_iata_code(d["destination"])]
                _directions_cache[origin] = directions
                return directions
        except aiohttp.ClientError as e:
            print(f"Ошибка сети при получении направлений: {e}")
            _directions_cache[origin] = []
            return []

async def get_countries() -> List[Country]:
    countries = await _load_countries()
    return countries if countries else []

async def get_cities_by_country(country_code: str, is_origin: bool = False) -> List[City]:
    cities = await _load_cities()
    filtered = [city for city in cities if city.country_code.upper() == country_code.upper()]
    
    # Список популярных городов для разных стран
    popular_cities = {
        "RU": ["MOW", "LED", "AER", "KZN", "SVX", "OVB", "UFA", "ROV", "MRV", "VVO"],
        "KZ": ["ALA", "NQZ", "SCO", "GUW", "KGF"],
        "BY": ["MSQ", "GME", "VTB"],
        "AZ": ["GYD", "NAJ", "KVD"],
        "AM": ["EVN", "LWN"],
        "KG": ["FRU", "OSS"],
        "MD": ["RMO"],
        "TJ": ["DYU", "LBD"],
        "TM": ["KRW", "MYP"],
        "UZ": ["TAS", "SKD", "BHK"],
        "UA": ["KBP", "HRK", "ODS"],
        "US": ["JFK", "LAX", "SFO", "MIA", "ORD"],
        "TR": ["IST", "SAW", "AYT", "ADB", "DLM"],
        "BR": ["GRU", "GIG", "BSB", "REC", "FOR"],
        "TH": ["BKK", "HKT", "CNX", "DMK", "KBV"]
    }
    
    # Приоритет для популярных городов
    country_code = country_code.upper()
    if country_code in popular_cities:
        popular_codes = popular_cities[country_code]
        popular = [city for city in filtered if city.code in popular_codes]
        others = [city for city in filtered if city.code not in popular_codes]
        popular.sort(key=lambda x: popular_codes.index(x.code))  # Сортировка по порядку в popular_cities
        others.sort(key=lambda x: x.name)  # Остальные по алфавиту
        return popular + others
    else:
        filtered.sort(key=lambda x: x.name)
        return filtered

async def find_city_by_name(city_name: str, country_code: str) -> Optional[City]:
    cities = await _load_cities()
    city_name = city_name.strip()
    country_code = country_code.upper()
    
    # Проверяем, является ли ввод кодом IATA
    if is_valid_iata_code(city_name.upper()):
        for city in cities:
            if city.country_code.upper() == country_code and city.code == city_name.upper():
                return city
    
    # Проверяем совпадение по имени города и падежам
    city_name_lower = city_name.lower()
    for city in cities:
        if city.country_code.upper() == country_code:
            if city.name.lower() == city_name_lower:
                return city
            for case_value in city.cases.values():
                if case_value.lower() == city_name_lower:
                    return city
    return None

async def get_flights_for_date(origin: str, destination: str, departure_date: str, passengers: int, max_transfers: Optional[int] = None) -> List[FlightPrice]:
    departure_date = normalize_date(departure_date)
    try:
        datetime.strptime(departure_date, "%Y-%m-%d")
    except ValueError:
        return []

    # Убираем фильтр на прямые рейсы, чтобы API мог искать с пересадками
    # directions = await get_flightable_directions(origin)
    # if destination not in directions:
    #     return []

    url = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
    params = {
        "origin": origin,
        "destination": destination,
        "departure_at": departure_date,
        "currency": "rub",
        "one_way": "true",
        "token": TRAVELPAYOUTS_TOKEN
    }
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    prices = []

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    print(f"Ошибка API (цены): {resp.status} - {await resp.text()}")
                    return []
                data = await resp.json()
                flights = data.get("data", [])
                if not flights or not isinstance(flights, list):
                    return []

                for flight in flights:
                    price_val = flight.get("price")
                    transfers_count = flight.get("transfers", 0)
                    if price_val:
                        if max_transfers is not None and transfers_count > max_transfers:
                            continue
                        dep_time = normalize_datetime(flight.get("departure_at", departure_date))
                        ticket_link = generate_aviasales_link(origin, destination, dep_time, passengers)
                        prices.append(FlightPrice(
                            origin=origin,
                            destination=destination,
                            price=price_val * passengers,
                            departure_date=dep_time,
                            origin_airport=flight.get("origin_airport", origin),
                            destination_airport=flight.get("destination_airport", destination),
                            ticket_link=ticket_link,
                            passengers=passengers,
                            transfers=transfers_count
                        ))
                prices.sort(key=lambda x: x.price)
            return prices
        except Exception as e:
            print(f"Ошибка при получении цен: {e}")
            return []

async def get_min_price(origin: str, destination: str, departure_date: str, passengers: int, max_transfers: Optional[int] = None) -> Optional[FlightPrice]:
    prices = await get_flights_for_date(origin, destination, departure_date, passengers, max_transfers)
    return prices[0] if prices else None
