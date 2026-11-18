"""One-shot city weather for the CLI and Ask Kilix, without changing the widget.

The engine owns the shared provider transport, units and condition codes. Only
the bounded child contacts the provider; cancelling the command reaps it too.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FETCH_TIMEOUT = 20
NOT_WEATHER = 3


@dataclass(frozen=True)
class Query:
    city: str           # '' asks about the ZIP saved for the Weather widget
    unit: str = ''
    when: str = ''      # '' now, or one of WHENS
    what: str = ''      # '' conditions, or one of WHATS


WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
WHENS = ('today', 'tomorrow', 'weekend', 'week', 'yesterday', *WEEKDAYS, *(f'next {d}' for d in WEEKDAYS),
         *(f'days {n}' for n in range(2, 7)))
WHATS = ('rain', 'raining', 'snow', 'snowing', 'umbrella', 'sunrise', 'sunset', 'humidity', 'wind')
RAIN_CODES = frozenset((*range(51, 68), 80, 81, 82, 95, 96, 99))
SNOW_CODES = frozenset((71, 73, 75, 77, 85, 86))
FORECAST_DAYS = 8
_DAY = '|'.join(WEEKDAYS)
_COUNTS = {'2': 2, 'two': 2, 'couple of': 2, 'couple': 2, '3': 3, 'three': 3, '4': 4, 'four': 4,
           '5': 5, 'five': 5, '6': 6, 'six': 6}
# Day words are taken off the request before the city is read, so
# "weather in Tokyo this weekend" asks about Tokyo, not "Tokyo this weekend".
_WHEN_WORDS = (
    (r'(?:for |on |over )?(?:the )?(?:this |next |coming )?week ?end', 'weekend'),
    (r'(?:for |over |in |within )?(?:the )?(?:next|coming) (?:7|seven|few) days'
     r'|(?:for |over |in )?(?:the )?(?:this |next |coming )?week', 'week'),
    (r'(?:for |over |in |within )?(?:the )?(?:next|coming) (' + '|'.join(_COUNTS) + r') days', 'days'),
    (r'(?:for |on )?(?:tomorrow|tomorow|tmrw|tmr)(?: (?:morning|afternoon|evening|night))?', 'tomorrow'),
    (r'(?:for |on )?(?:yesterday|last night)', 'yesterday'),
    (r'tonight|this (?:morning|afternoon|evening)|(?:for |on )?today|later(?: today)?', 'today'),
    (rf'(?:on |for )?next ({_DAY})', 'next'),
    (rf'(?:on |for |this |coming |this coming )?({_DAY})', 'day'),
    (r'(?:right )?now|currently|at the moment|at present', ''),
)
_PLACE_WORDS = r'(?:outside|out there|out|around here|here|where i am|in my area|locally|near me)'
_TAIL = r'(?:\s+(?:in|for|at|near|of|around)\s+(?P<city>.+?))?'
_ASK = r'(?:(?:(?:can|could|would) you\s+)?(?:tell me|show me|give me|check|get|look up)\s+)?'
_WH = r"(?:(?:what is|what's|whats|how is|how's|hows|what will|how will)\s+)?(?:the\s+)?"
_TOPICS = (
    # (pattern, what, needs a place, day or 'outside' when there is no city)
    (rf"{_ASK}{_WH}(?:current\s+)?(?:weather|forecast|weather forecast|weather report)(?:\s+(?:like|be|be like))?{_TAIL}",
     '', False),
    (rf"{_ASK}{_WH}(?:current\s+)?temperature(?:\s+(?:like|be))?{_TAIL}", '', True),
    # Embedded questions left by the courtesy cleaner: "(tell me) what the weather is like".
    (rf"(?:what|how) the (?:weather|forecast|weather forecast)(?: is| will be| looks| is going to be)(?: like)?{_TAIL}",
     '', False),
    (rf"(?:what|how hot|how cold|how warm) the temperature (?:is|will be){_TAIL}", '', True),
    (rf"(?:if|whether) it(?:'s| is|'ll| will| is going to| is gonna) (?:rain|be raining|raining|be rainy|pour)"
     rf"{_TAIL}", 'rain', False),
    (rf"(?:if|whether) it(?:'s| is|'ll| will| is going to| is gonna) (?:snow|be snowing|snowing){_TAIL}", 'snow', False),
    (rf"(?:if|whether) i (?:need|should (?:bring|take)) (?:an |my )?(?:umbrella|raincoat){_TAIL}", 'umbrella', False),
    (rf"(?:when|what time) (?:the )?(?:sun ?rise|sun ?set) is{_TAIL}", 'sun', False),
    (rf"how (?:hot|cold|warm|chilly|nice) (?:is it|will it be|is it going to be|is it gonna be){_TAIL}", '', True),
    (rf"(?:is it|will it be|is it going to be|is it gonna be) (?:hot|cold|warm|chilly|freezing|nice|sunny|cloudy|foggy)"
     rf"{_TAIL}", '', False),
    (rf"do i need (?:a )?(?:jacket|coat|sweater|jumper|sunscreen){_TAIL}", '', False),
    (rf"is it (?:raining|rainy|drizzling|pouring|wet){_TAIL}", 'raining', False),
    (rf"(?:will it|is it going to|is it gonna|does it look like it(?:'s| is) going to) "
     rf"(?:rain|be raining|be rainy|pour|drizzle){_TAIL}", 'rain', False),
    (rf"{_ASK}{_WH}(?:any |a )?(?:chance of rain|rain chance|chance of showers|rain forecast|rain){_TAIL}", 'rain', False),
    (rf"is there (?:any )?(?:rain|a chance of rain)(?: forecast| coming| expected)?{_TAIL}", 'rain', False),
    (rf"(?:do i need|should i (?:bring|take|grab|carry)|will i need) (?:an |my )?(?:umbrella|brolly|raincoat|rain jacket)"
     rf"{_TAIL}", 'umbrella', False),
    (rf"is it snowing{_TAIL}", 'snowing', False),
    (rf"(?:will it|is it going to|is it gonna) (?:snow|be snowing|be snowy){_TAIL}", 'snow', False),
    (rf"{_ASK}{_WH}(?:any |a )?(?:chance of snow|snow forecast|snow){_TAIL}", 'snow', False),
    (rf"(?:(?:when|what time) (?:is|will be|'s) )?(?:the )?(?:sunrise|sun ?rise)(?: time)?{_TAIL}", 'sunrise', False),
    (rf"(?:when|what time) (?:does|will) the sun (?:rise|come up){_TAIL}", 'sunrise', False),
    (rf"(?:(?:when|what time) (?:is|will be|'s) )?(?:the )?(?:sunset|sun ?set)(?: time)?{_TAIL}", 'sunset', False),
    (rf"(?:when|what time) (?:does|will) the sun (?:set|go down){_TAIL}", 'sunset', False),
    (rf"{_ASK}{_WH}(?:current\s+)?humidity(?: level)?(?:\s+like)?{_TAIL}", 'humidity', False),
    (rf"how humid (?:is it|will it be|is it going to be){_TAIL}", 'humidity', False),
    (rf"is it humid{_TAIL}", 'humidity', False),
    (rf"{_ASK}{_WH}(?:current\s+)?wind(?: speed)?(?:\s+like)?{_TAIL}", 'wind', False),
    (rf"how (?:windy|breezy) (?:is it|will it be|is it going to be){_TAIL}", 'wind', False),
    (rf"is it (?:windy|breezy){_TAIL}", 'wind', False),
)


def city_name(value: str) -> str:
    value = value.strip()
    if not 2 <= len(value) <= 120 or value.count(',') > 1:
        raise ValueError('Use a city name, optionally followed by a country or state: Paris, France.')
    if not all(c.isalpha() or unicodedata.category(c).startswith('M') or c in " .,'’-" for c in value):
        raise ValueError('Use a city name, optionally followed by a country or state: Paris, France.')
    if any(not part.strip(" .'-’") for part in value.split(',')):
        raise ValueError('Enter a city name and a country or state after the comma.')
    return ' '.join(value.split())


_NOT_PLACE = re.compile(
    r'(?i)(?:^|\s)(?:then|app|apps|widget|bar|menu|window|windows|tab|tabs|pane|panes|panel|card|settings|'
    r'terminal|desktop|screen|file|folder|logs?|computer|machine|laptop|here)(?:\s|,|$)')


def plausible_city(city: str) -> bool:
    """A place name, not the rest of a compound command."""
    if _NOT_PLACE.search(city) or re.fullmatch(r'(?i)(?:the|a|an|my|this|that|it|me)', city.strip()):
        return False
    name, _, qualifier = city.partition(',')
    return len(name.split()) <= 5 and len(qualifier.split()) <= 4


def _when(text: str) -> tuple[str, str]:
    """Take one day phrase off the request; return (request, when)."""
    for pattern, kind in _WHEN_WORDS:
        match = re.search(rf'(?:^|\s){pattern}(?=\s|,|$)', text, re.I)
        if match is None:
            continue
        rest = (text[:match.start()] + ' ' + text[match.end():]).strip(' ,')
        rest = ' '.join(rest.split())
        if kind == 'day':
            return rest, match[1].lower()
        if kind == 'next':
            return rest, 'next ' + match[1].lower()
        if kind == 'days':
            return rest, f'days {_COUNTS[match[1].lower()]}'
        return rest, kind
    return text, ''


def ask_query(text: str) -> Query | None:
    """Recognize a complete weather question, leaving ordinary commands to Needle."""
    text = text.strip().replace('’', "'")
    text = re.sub(r'[?!.]+$', '', text).strip()
    text = re.sub(r'^please\s+|,?\s+please$', '', text, flags=re.I).strip()
    unit = ''
    units = re.search(r'\s+in\s+(celsius|fahrenheit)\b', text, re.I)
    if units:
        unit, text = units[1].lower(), (text[:units.start()] + text[units.end():]).strip()
    text, when = _when(text)
    if not when:
        text, when = _when(text)  # "Tokyo now" after "in Celsius" was taken
    outside = bool(re.search(rf'(?:^|\s){_PLACE_WORDS}$', text, re.I))
    if outside:
        text = re.sub(rf'\s+{_PLACE_WORDS}$', '', text, flags=re.I).strip()
    match, what, guarded = None, '', False
    for pattern, kind, needs_place in _TOPICS:
        match = re.fullmatch(pattern, text, re.I)
        if match:
            what, guarded = kind, needs_place
            break
    city = match['city'] if match else None
    if what == 'sun':
        what = 'sunrise' if re.search(r'(?i)sun ?rise', text) else 'sunset'
    if what in ('rain', 'snow') and re.match(r"(?i)(?:if|whether) it(?:'s| is) (?:raining|snowing)", text) \
            and not when:
        what += 'ing'  # "if it is raining" asks about now
    if match is None and not re.match(
            r'(?i)(?:open|launch|install|uninstall|hide|show|toggle|set|enable|disable|turn|close|start|stop|'
            r'refresh|update|reload|what|how|can|could|would|tell|give|get|check|please)\b', text):
        match = re.fullmatch(r'(.+?)\s+(?:current\s+)?(?:weather|forecast)', text, re.I)
        city = match[1] if match else None
        # "pull up the weather" is a command, not a city called "pull up the".
        if city and re.search(r'(?:^|\s)(?:the|my|a|an|me|up|this|that|your|some|of|to|for|it)(?:\s|$)', city):
            return None
    if match is None:
        return None
    city = (city or '').strip()
    if re.fullmatch(_PLACE_WORDS, city, re.I):
        city, outside = '', True
    if city and not plausible_city(city):
        return None
    if not city and guarded and not (outside or when):
        # "what's the temperature" and "how hot is it" read this computer's sensors.
        return None
    # "the temperature in fahrenheit" names a display unit, not a city.
    if city.casefold() in {"celsius", "fahrenheit"}:
        return None
    if when == 'yesterday':
        raise ValueError('Ask has current conditions and forecasts, not past weather.')
    if re.search(r'(?i)^(?:the )?(?:next|coming) \S+ days$', city):
        raise ValueError('Ask has forecasts for up to 7 days, for example: weather for the next 3 days.')
    if re.search(r'\b(?:tomorrow|yesterday|tonight|forecast|next|week|last)\b', city, re.I):
        raise ValueError('Name the day once, for example: weather in Tokyo tomorrow.')
    if re.search(r'\s+and\s+', city, re.I):
        raise ValueError('Ask for one city at a time, for example: weather in Tokyo.')
    if not when and re.search(r'(?i)\bforecast\b', match[0]):
        when = 'week'
    if when == 'today' and what in ('raining', 'snowing'):
        what = what[:4]
    return Query(city_name(city) if city else '', unit, when, what)


def provider():
    # The pinned engine ships this pure-Python module; no display or native
    # engine build is needed for a read-only weather query.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from kitty.kilix_chrome import weather
    return weather


def place_name(place: dict) -> str:
    values = []
    for key in ('name', 'admin1', 'country'):
        value = ''.join(c for c in str(place.get(key, '')) if c.isprintable())[:80]
        if value and value not in values:
            values.append(value)
    return ', '.join(values)


def normalized(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if c.isalnum())


def resolve_city(query: str, weather) -> dict:
    geo = weather.json_request('https://geocoding-api.open-meteo.com/v1/search?' + urlencode({
        'name': city_name(query), 'count': 100, 'language': 'en', 'format': 'json',
    }))
    results = geo.get('results', [])
    if not isinstance(results, list):
        raise ValueError('Invalid city lookup response; try again later.')
    places = [p for p in results if isinstance(p, dict) and isinstance(p.get('name'), str)
              and str(p.get('feature_code', '')).startswith('PPL') and p.get('country_code')]
    if not places:
        raise ValueError('City not found. Try a full city name with its country or state.')
    # Prefer exact names over prefix matches (York must not select New York).
    name = normalized(query.split(',')[0].strip())
    exact = [p for p in places if normalized(p['name']) == name]
    places = exact or places

    def population(place):
        value = place.get('population', 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    places.sort(key=population, reverse=True)
    if len(places) > 1:
        largest, runner_up = population(places[0]), population(places[1])
        if largest < 100_000 or largest < 3 * max(1, runner_up):
            choices = '; '.join(place_name(p) for p in places[:3])
            raise ValueError('Several cities match. Include a country or state: ' + choices + '.')
    return places[0]


NO_PLACE = ('No weather location is saved. Name a city, as in: weather in Paris, '
            'or set a ZIP from the Weather widget (right-click Weather).')


def resolve_saved(weather) -> dict:
    """The place for the ZIP the Weather widget uses; read, never written."""
    zip_code = weather.chrome_value('KILIX_WEATHER_ZIP', '').strip()
    if not weather.valid_zip(zip_code):
        raise ValueError(NO_PLACE)
    geo = weather.json_request('https://geocoding-api.open-meteo.com/v1/search?' + urlencode({
        'name': zip_code, 'countryCode': 'US', 'count': 100, 'language': 'en', 'format': 'json',
    }))
    results = geo.get('results', [])
    if not isinstance(results, list):
        raise ValueError('Invalid ZIP lookup response; try again later.')
    matches = [r for r in results if isinstance(r, dict) and r.get('country_code') == 'US'
               and isinstance(r.get('name'), str)
               and isinstance(r.get('postcodes'), list) and zip_code in r['postcodes']]
    if not matches:
        raise ValueError(f'The saved ZIP {zip_code} was not found by the weather provider.')
    return matches[0]


CURRENT = 'temperature_2m,weather_code'
CURRENT_MORE = CURRENT + ',relative_humidity_2m,apparent_temperature,wind_speed_10m,wind_direction_10m,' \
    'wind_gusts_10m,precipitation'
DAILY = ('weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,'
         'snowfall_sum,sunrise,sunset,wind_speed_10m_max,wind_gusts_10m_max,relative_humidity_2m_mean')
COMPASS = ('N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW')


def wants_current(query: Query) -> bool:
    if query.when == 'today' and not query.what:
        return True  # "weather today": conditions now, then today's forecast
    return not query.when and query.what in ('', 'humidity', 'wind', 'raining', 'snowing')


def wants_daily(query: Query) -> bool:
    return not wants_current(query) or query.when == 'today'


class Units:
    def __init__(self, weather, unit: str):
        self.unit = unit or weather.chrome_value('KILIX_TEMPERATURE_UNIT', 'fahrenheit')
        self.wind = 'mph' if self.unit == 'fahrenheit' else 'km/h'

    def temperature(self, celsius: float) -> str:
        return f'{celsius:.0f}°C' if self.unit == 'celsius' else f'{celsius * 9 / 5 + 32:.0f}°F'

    def speed(self, value: float) -> str:
        return f'{value:.0f} {self.wind}'


def place_zone(place: dict, data: dict):
    for name in (place.get('timezone'), data.get('timezone')):
        try:
            return ZoneInfo(str(name))
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return timezone.utc


def day_rows(weather, daily: object, zone) -> list[dict]:
    """Validated daily rows, in the place's own calendar."""
    if not isinstance(daily, dict) or not isinstance(daily.get('time'), list):
        raise ValueError('The forecast is unavailable; try again later.')
    count = len(daily['time'])
    bounds = {
        'time': (1, 1e11), 'weather_code': (0, 999), 'temperature_2m_max': (-100, 70),
        'temperature_2m_min': (-100, 70), 'precipitation_probability_max': (0, 100),
        'precipitation_sum': (0, 2000), 'snowfall_sum': (0, 1000), 'sunrise': (1, 1e11), 'sunset': (1, 1e11),
        'wind_speed_10m_max': (0, 500), 'wind_gusts_10m_max': (0, 600), 'relative_humidity_2m_mean': (0, 100),
    }
    rows = []
    for index in range(count):
        row = {}
        for key, (low, high) in bounds.items():
            values = daily.get(key)
            if not isinstance(values, list) or len(values) != count:
                raise ValueError('The forecast is incomplete; try again later.')
            value = values[index]
            # Polar days have no sunrise or sunset; probabilities can be missing.
            row[key] = None if value is None and key not in ('time', 'weather_code', 'temperature_2m_max',
                                                             'temperature_2m_min') \
                else weather.number(value, low, high)
        if not row['weather_code'].is_integer():
            raise ValueError('Invalid weather condition code; try again later.')
        row['date'] = datetime.fromtimestamp(row['time'], zone).date()
        rows.append(row)
    if not rows:
        raise ValueError('The forecast is unavailable; try again later.')
    return rows


def pick_days(query: Query, rows: list[dict]) -> list[dict]:
    when = query.when or 'today'
    if when == 'today':
        return rows[:1]
    if when == 'tomorrow':
        return rows[1:2]
    if when == 'week':
        return rows[:7]
    if when.startswith('days '):
        return rows[:int(when.removeprefix('days '))]  # starting today, as "next 7 days" does
    if when == 'weekend':
        days = [r for r in rows[:7] if r['date'].weekday() >= 5]
        if days and days[0]['date'].weekday() == 6 and len(days) > 1 and days[1]['date'].weekday() == 5:
            days = days[:1]  # Sunday today: this weekend is today
        return days[:2]
    name = when.removeprefix('next ')
    target = WEEKDAYS.index(name)
    start = 1 if when.startswith('next ') else 0
    return [r for r in rows[start:start + 7] if r['date'].weekday() == target][:1]


def day_label(row: dict, rows: list[dict]) -> str:
    stamp = row['date'].strftime('%a %-d %b')
    if row['date'] == rows[0]['date']:
        return 'Today, ' + stamp
    if len(rows) > 1 and row['date'] == rows[1]['date']:
        return 'Tomorrow, ' + stamp
    return stamp


def day_line(weather, row: dict, rows: list[dict], units: Units) -> str:
    parts = [weather.CONDITIONS.get(int(row['weather_code']), 'Unknown conditions'),
             f"high {units.temperature(row['temperature_2m_max'])}",
             f"low {units.temperature(row['temperature_2m_min'])}"]
    if row['precipitation_probability_max'] is not None:
        parts.append(f"{row['precipitation_probability_max']:.0f}% chance of rain")
    if row['wind_speed_10m_max'] is not None:
        parts.append(f"wind up to {units.speed(row['wind_speed_10m_max'])}")
    return f"{day_label(row, rows)}: " + ', '.join(parts)


def rain_verdict(row: dict, what: str) -> str:
    chance = row['precipitation_probability_max']
    amount = row['precipitation_sum'] or 0
    wet = int(row['weather_code']) in RAIN_CODES
    if what == 'snow':
        snow = row['snowfall_sum'] or 0
        if snow > 0 or int(row['weather_code']) in SNOW_CODES:
            return f'Yes, snow is forecast, about {snow:.1f} cm.' if snow else 'Yes, snow is forecast.'
        return 'No snow is forecast.'
    likely = (chance is not None and chance >= 60) or (wet and amount >= 1)
    possible = (chance is not None and chance >= 30) or wet
    detail = (f'{chance:.0f}% chance' if chance is not None else 'chance unknown') + \
        (f', about {amount:.0f} mm' if amount >= 1 else '')
    if what == 'umbrella':
        word = 'Yes, take an umbrella' if likely else 'Maybe take one' if possible else 'No umbrella needed'
    else:
        word = 'Yes, rain is likely' if likely else 'Maybe: rain is possible' if possible else 'Rain is unlikely'
    return f'{word} ({detail}).'


def sun_time(row: dict, key: str, zone) -> str:
    if row[key] is None:
        return 'none (the sun does not ' + ('rise' if key == 'sunrise' else 'set') + ' that day)'
    return datetime.fromtimestamp(row[key], zone).strftime('%H:%M %Z')


def current_lines(weather, place: dict, data: dict, query: Query, units: Units, zone) -> list[str]:
    current = data.get('current')
    if not isinstance(current, dict):
        raise ValueError('Current conditions are unavailable; try again later.')
    celsius = weather.number(current.get('temperature_2m'), -100, 70)
    code = weather.number(current.get('weather_code'), 0, 999)
    observed = weather.number(current.get('time'), 1, 1e11)
    if not code.is_integer():
        raise ValueError('Invalid weather condition code; try again later.')
    condition = weather.CONDITIONS.get(int(code), 'Unknown conditions')
    age = time.time() - observed
    stale = ' · STALE' if age > weather.STALE_SECONDS or age < -1800 else ''
    where = place_name(place)
    if query.what in ('raining', 'snowing'):
        precipitation = weather.number(current.get('precipitation'), 0, 2000)
        codes = RAIN_CODES if query.what == 'raining' else SNOW_CODES
        falling = int(code) in codes or (query.what == 'raining' and precipitation > 0 and int(code) not in SNOW_CODES)
        verb = 'raining' if query.what == 'raining' else 'snowing'
        first = (f'Yes, it is {verb} in {where} now: {condition}.' if falling else
                 f'No, it is not {verb} in {where} now: {condition}, {units.temperature(celsius)}.')
    elif query.what == 'humidity':
        humidity = weather.number(current.get('relative_humidity_2m'), 0, 100)
        feels = weather.number(current.get('apparent_temperature'), -100, 80)
        first = (f'{where}: humidity {humidity:.0f}% · {units.temperature(celsius)}, '
                 f'feels like {units.temperature(feels)}')
    elif query.what == 'wind':
        speed = weather.number(current.get('wind_speed_10m'), 0, 500)
        gusts = weather.number(current.get('wind_gusts_10m'), 0, 600)
        bearing = weather.number(current.get('wind_direction_10m'), 0, 360)
        first = (f'{where}: wind {units.speed(speed)} from the {COMPASS[round(bearing / 22.5) % 16]}, '
                 f'gusts to {units.speed(gusts)}')
    else:
        first = f'{where}: {units.temperature(celsius)} · {condition}'
    observed_at = datetime.fromtimestamp(observed, zone).strftime('%Y-%m-%d %H:%M %Z')
    return [first + stale, f'Conditions at {observed_at}', 'Current conditions are weather-model estimates.']


def fetch_city(query: Query) -> str:
    weather = provider()
    place = resolve_city(query.city, weather) if query.city else resolve_saved(weather)
    latitude = weather.number(place.get('latitude'), -90, 90)
    longitude = weather.number(place.get('longitude'), -180, 180)
    units = Units(weather, query.unit)
    current, daily = wants_current(query), wants_daily(query)
    params = {'latitude': latitude, 'longitude': longitude, 'temperature_unit': 'celsius',
              'timeformat': 'unixtime', 'timezone': 'GMT', 'forecast_days': 1}
    if current:
        params['current'] = CURRENT if not query.what else CURRENT_MORE
    if query.what:
        params['wind_speed_unit'] = 'mph' if units.wind == 'mph' else 'kmh'
    if daily:
        params.update(daily=DAILY, timezone='auto', forecast_days=FORECAST_DAYS,
                      wind_speed_unit='mph' if units.wind == 'mph' else 'kmh')
    data = weather.json_request('https://api.open-meteo.com/v1/forecast?' + urlencode(params))
    zone = place_zone(place, data)
    lines = current_lines(weather, place, data, query, units, zone) if current else []
    if daily:
        rows = day_rows(weather, data.get('daily'), zone)
        days = pick_days(query, rows)
        if not days:
            raise ValueError('Forecasts reach seven days ahead; ask about a nearer day.')
        if not lines:
            lines.append(place_name(place))
        for row in days:
            label = day_label(row, rows)
            if query.what in ('sunrise', 'sunset'):
                lines.append(f"{query.what.capitalize()} {label[0].lower() + label[1:] if label.startswith(('Today', 'Tomorrow')) else 'on ' + label}: "
                             f"{sun_time(row, query.what, zone)}")
            elif query.what in ('rain', 'snow', 'umbrella'):
                lines.append(f'{label}: {rain_verdict(row, query.what)}')
                if len(days) == 1:
                    lines.append('  ' + day_line(weather, row, rows, units).split(': ', 1)[1])
            elif query.what == 'humidity':
                lines.append(f"{label}: average humidity {row['relative_humidity_2m_mean']:.0f}%"
                             if row['relative_humidity_2m_mean'] is not None else f'{label}: humidity unknown')
            elif query.what == 'wind':
                lines.append(f"{label}: wind up to {units.speed(row['wind_speed_10m_max'])}, gusts to "
                             f"{units.speed(row['wind_gusts_10m_max'])}" if row['wind_speed_10m_max'] is not None
                             else f'{label}: wind unknown')
            else:
                lines.append(day_line(weather, row, rows, units))
        lines.append('Forecasts are weather-model estimates.')
    return '\n'.join((*lines, weather.ATTRIBUTION, 'https://open-meteo.com/ · Free API for non-commercial use'))


def worker(query: Query) -> int:
    try:
        print(fetch_city(query))
        return 0
    except HTTPError as error:
        message = ('Weather provider is busy; try later.' if error.code == 429 else
                   f'Weather provider unavailable (HTTP {error.code}).')
    except (URLError, OSError, TimeoutError):
        message = 'Weather unavailable: offline or request timed out.'
    except (ValueError, KeyError, TypeError) as error:
        message = str(error)
    except ImportError:
        message = 'Weather provider is missing; check the Kilix engine installation.'
    print('Weather: ' + message, file=sys.stderr)
    return 1


def run_query(query: Query) -> int:
    child = None
    try:
        args = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker']
        if query.when:
            args.extend(('--when', query.when))
        if query.what:
            args.extend(('--what', query.what))
        if not query.city:
            args.append('--saved')
        if query.unit:
            args.extend(('--unit', query.unit))
        child = subprocess.Popen([*args, '--', *([query.city] if query.city else [])],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = child.communicate(timeout=FETCH_TIMEOUT)
        sys.stdout.write(out.decode('utf-8', errors='replace'))
        sys.stderr.write(err.decode('utf-8', errors='replace'))
        return 0 if child.returncode == 0 else 1
    except subprocess.TimeoutExpired:
        print('Weather request timed out; try again later.', file=sys.stderr)
    except KeyboardInterrupt:
        print('Weather request cancelled.', file=sys.stderr)
    except OSError:
        print('Could not start the weather lookup; try again.', file=sys.stderr)
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate()
    return 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] == ['--ask']:
            query = ask_query(' '.join(argv[1:]))
            return NOT_WEATHER if query is None else run_query(query)
        parser = argparse.ArgumentParser(description='Current weather for a city, without changing the saved widget ZIP.')
        parser.add_argument('--unit', choices=('celsius', 'fahrenheit'), default='',
                            help='override the saved temperature unit for this lookup')
        parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
        parser.add_argument('--saved', action='store_true', help=argparse.SUPPRESS)
        parser.add_argument('--when', choices=WHENS, default='', help=argparse.SUPPRESS)
        parser.add_argument('--what', choices=WHATS, default='', help=argparse.SUPPRESS)
        parser.add_argument('city', nargs='*', help='city name, optionally followed by a country or state')
        options = parser.parse_args(argv)
        if bool(options.city) == options.saved:
            parser.error('name one city')
        city = '' if options.saved else city_name(' '.join(options.city))
        query = Query(city, options.unit, options.when, options.what)
        return worker(query) if options.worker else run_query(query)
    except ValueError as error:
        print('Weather: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
