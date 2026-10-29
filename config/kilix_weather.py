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
    city: str
    unit: str = ''


def city_name(value: str) -> str:
    value = value.strip()
    if not 2 <= len(value) <= 120 or value.count(',') > 1:
        raise ValueError('Use a city name, optionally followed by a country or state: Paris, France.')
    if not all(c.isalpha() or unicodedata.category(c).startswith('M') or c in " .,'’-" for c in value):
        raise ValueError('Use a city name, optionally followed by a country or state: Paris, France.')
    if any(not part.strip(" .'-’") for part in value.split(',')):
        raise ValueError('Enter a city name and a country or state after the comma.')
    return ' '.join(value.split())


def ask_query(text: str) -> Query | None:
    """Recognize a complete weather question, leaving ordinary commands to Needle."""
    text = text.strip().replace('’', "'")
    text = re.sub(r'[?!.]+$', '', text).strip()
    text = re.sub(r'^please\s+|,?\s+please$', '', text, flags=re.I).strip()
    prefix = r'(?:(?:can|could|would) you\s+)?(?:tell me|show me|give me|check|get|look up)\s+'
    noun = r'(?:the\s+)?(?:current\s+)?(?:weather|temperature)(?:\s+like)?'
    patterns = (
        rf'(?:{prefix})?{noun}\s+(?:in|for|at|of)\s+(.+)',
        rf"(?:what is|what's|how is|how's)\s+{noun}\s+(?:in|for|at|of)\s+(.+)",
        r'how (?:hot|cold|warm) is it in\s+(.+)',
    )
    match = next((m for pattern in patterns if (m := re.fullmatch(pattern, text, re.I))), None)
    if match is None and not re.match(
            r'(?i)(?:open|launch|install|uninstall|hide|show|toggle|set|enable|disable|turn|'
            r'what|how|can|could|would|tell|give|get|check|please)\b', text):
        match = re.fullmatch(r'(.+?)\s+(?:current\s+)?weather', text, re.I)
    if match is None:
        return None
    city = match[1].strip()
    unit = ''
    # Accept either order: "Tokyo now in Celsius" or "Tokyo in Celsius now".
    for _ in range(2):
        city = re.sub(r'\s+(?:right now|now|today)$', '', city, flags=re.I).strip()
        units = re.search(r'\s+in\s+(celsius|fahrenheit)$', city, re.I)
        if units:
            unit, city = units[1].lower(), city[:units.start()].strip()
    if re.search(r'\b(?:tomorrow|yesterday|tonight|forecast|next|week)\b', city, re.I):
        raise ValueError('City lookups show current conditions. Try: weather in Tokyo.')
    if re.search(r'\s+and\s+', city, re.I):
        raise ValueError('Ask for one city at a time, for example: weather in Tokyo.')
    return Query(city_name(city), unit)


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


def fetch_city(query: Query) -> str:
    weather = provider()
    place = resolve_city(query.city, weather)
    latitude = weather.number(place.get('latitude'), -90, 90)
    longitude = weather.number(place.get('longitude'), -180, 180)
    data = weather.json_request('https://api.open-meteo.com/v1/forecast?' + urlencode({
        'latitude': latitude, 'longitude': longitude, 'current': 'temperature_2m,weather_code',
        'temperature_unit': 'celsius', 'timeformat': 'unixtime', 'timezone': 'GMT', 'forecast_days': 1,
    }))
    current = data.get('current')
    if not isinstance(current, dict):
        raise ValueError('Current conditions are unavailable; try again later.')
    celsius = weather.number(current.get('temperature_2m'), -100, 70)
    code = weather.number(current.get('weather_code'), 0, 999)
    observed = weather.number(current.get('time'), 1, 1e11)
    if not code.is_integer():
        raise ValueError('Invalid weather condition code; try again later.')
    unit = query.unit or weather.chrome_value('KILIX_TEMPERATURE_UNIT', 'fahrenheit')
    temperature = f'{celsius:.0f}°C' if unit == 'celsius' else f'{celsius * 9 / 5 + 32:.0f}°F'
    try:
        zone = ZoneInfo(str(place.get('timezone', 'UTC')))
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    observed_at = datetime.fromtimestamp(observed, zone).strftime('%Y-%m-%d %H:%M %Z')
    age = time.time() - observed
    stale = ' · STALE' if age > weather.STALE_SECONDS or age < -1800 else ''
    return '\n'.join((
        f'{place_name(place)}: {temperature} · {weather.CONDITIONS.get(int(code), "Unknown conditions")}{stale}',
        f'Conditions at {observed_at}', 'Current conditions are weather-model estimates.',
        weather.ATTRIBUTION, 'https://open-meteo.com/ · Free API for non-commercial use',
    ))


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
        if query.unit:
            args.extend(('--unit', query.unit))
        child = subprocess.Popen([*args, '--', query.city],
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
        parser.add_argument('city', nargs='+', help='city name, optionally followed by a country or state')
        options = parser.parse_args(argv)
        query = Query(city_name(' '.join(options.city)), options.unit)
        return worker(query) if options.worker else run_query(query)
    except ValueError as error:
        print('Weather: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
