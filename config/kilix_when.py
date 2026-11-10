"""Direct Ask answers about dates, clocks in other places, and day counts.

These are closed reads of the system clock and the zone database. A request
that is not entirely one of them returns 3 so Ask can keep its normal route.
Nothing here sets a clock, a timezone, or an alarm.
"""

from __future__ import annotations

import calendar
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kilix_asktext import clean  # noqa: E402

NOT_WHEN = 3

_MONTHS = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): index for index, name in enumerate(calendar.month_abbr) if name})
_MONTHS["sept"] = 9
_WEEKDAYS = {name.lower(): index for index, name in enumerate(calendar.day_name)}
_WEEKDAYS.update({name.lower(): index for index, name in enumerate(calendar.day_abbr)})
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WEEKDAY = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_FIXED = {
    "christmas": (12, 25), "christmas day": (12, 25), "xmas": (12, 25),
    "christmas eve": (12, 24), "new year's eve": (12, 31), "new years eve": (12, 31),
    "new year's day": (1, 1), "new years day": (1, 1), "new year's": (1, 1),
    "new years": (1, 1), "the new year": (1, 1), "new year": (1, 1),
    "halloween": (10, 31), "valentine's day": (2, 14), "valentines day": (2, 14),
    "st patrick's day": (3, 17), "st. patrick's day": (3, 17),
    "independence day": (7, 4), "the fourth of july": (7, 4), "july fourth": (7, 4),
    "pi day": (3, 14), "april fools": (4, 1), "april fools day": (4, 1),
    "april fool's day": (4, 1), "earth day": (4, 22), "boxing day": (12, 26),
    "the end of the year": (12, 31), "the end of this year": (12, 31),
}
_ORDINAL = r"(?:st|nd|rd|th)?"
_DATE_WORDS = (
    rf"(?P<iso>\d{{4}}-\d{{1,2}}-\d{{1,2}})"
    rf"|(?P<us>\d{{1,2}}/\d{{1,2}}/\d{{4}})"
    rf"|(?:the )?(?P<d1>\d{{1,2}}){_ORDINAL}(?: of)? (?P<m1>{_MONTH})\.?,?(?: (?P<y1>\d{{4}}))?"
    rf"|(?P<m2>{_MONTH})\.? (?:the )?(?P<d2>\d{{1,2}}){_ORDINAL},?(?: (?P<y2>\d{{4}}))?"
)
_DATE_RE = re.compile(_DATE_WORDS, re.IGNORECASE)
_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "a couple of": 2, "a few": 3,
}
_COUNT = r"(?P<n>\d{1,5}|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + ")"
_SPAN_UNIT = r"(?P<unit>days?|weeks?|months?|years?)"

# Common names that are not the last part of an IANA zone name.
_ZONE_ALIAS = {
    "utc": "UTC", "gmt": "UTC", "zulu": "UTC", "z": "UTC",
    "est": "America/New_York", "edt": "America/New_York", "eastern": "America/New_York",
    "eastern time": "America/New_York", "new york city": "America/New_York",
    "nyc": "America/New_York", "boston": "America/New_York", "miami": "America/New_York",
    "washington": "America/New_York", "washington dc": "America/New_York",
    "atlanta": "America/New_York", "philadelphia": "America/New_York",
    "cst": "America/Chicago", "cdt": "America/Chicago", "central": "America/Chicago",
    "central time": "America/Chicago", "dallas": "America/Chicago",
    "houston": "America/Chicago", "austin": "America/Chicago", "texas": "America/Chicago",
    "mst": "America/Denver", "mdt": "America/Denver", "mountain": "America/Denver",
    "mountain time": "America/Denver", "salt lake city": "America/Denver",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "pacific": "America/Los_Angeles", "pacific time": "America/Los_Angeles",
    "california": "America/Los_Angeles", "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "san diego": "America/Los_Angeles",
    "portland": "America/Los_Angeles", "las vegas": "America/Los_Angeles",
    "hawaii": "Pacific/Honolulu", "alaska": "America/Anchorage",
    "uk": "Europe/London", "england": "Europe/London", "britain": "Europe/London",
    "scotland": "Europe/London", "bst": "Europe/London", "ireland": "Europe/Dublin",
    "cet": "Europe/Paris", "cest": "Europe/Paris", "france": "Europe/Paris",
    "germany": "Europe/Berlin", "munich": "Europe/Berlin", "frankfurt": "Europe/Berlin",
    "spain": "Europe/Madrid", "barcelona": "Europe/Madrid", "italy": "Europe/Rome",
    "milan": "Europe/Rome", "netherlands": "Europe/Amsterdam", "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo", "poland": "Europe/Warsaw", "switzerland": "Europe/Zurich",
    "geneva": "Europe/Zurich", "greece": "Europe/Athens", "turkey": "Europe/Istanbul",
    "ukraine": "Europe/Kyiv", "kiev": "Europe/Kyiv", "russia": "Europe/Moscow",
    "india": "Asia/Kolkata", "ist": "Asia/Kolkata", "delhi": "Asia/Kolkata",
    "new delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata", "calcutta": "Asia/Kolkata", "chennai": "Asia/Kolkata",
    "china": "Asia/Shanghai", "beijing": "Asia/Shanghai", "shenzhen": "Asia/Shanghai",
    "japan": "Asia/Tokyo", "jst": "Asia/Tokyo", "osaka": "Asia/Tokyo",
    "korea": "Asia/Seoul", "south korea": "Asia/Seoul", "kst": "Asia/Seoul",
    "vietnam": "Asia/Ho_Chi_Minh", "hanoi": "Asia/Ho_Chi_Minh", "saigon": "Asia/Ho_Chi_Minh",
    "thailand": "Asia/Bangkok", "indonesia": "Asia/Jakarta", "philippines": "Asia/Manila",
    "pakistan": "Asia/Karachi", "israel": "Asia/Jerusalem", "tel aviv": "Asia/Jerusalem",
    "uae": "Asia/Dubai", "abu dhabi": "Asia/Dubai", "saudi arabia": "Asia/Riyadh",
    "iran": "Asia/Tehran", "egypt": "Africa/Cairo", "nigeria": "Africa/Lagos",
    "kenya": "Africa/Nairobi", "south africa": "Africa/Johannesburg",
    "cape town": "Africa/Johannesburg", "brazil": "America/Sao_Paulo",
    "rio": "America/Sao_Paulo", "rio de janeiro": "America/Sao_Paulo",
    "argentina": "America/Argentina/Buenos_Aires", "mexico": "America/Mexico_City",
    "chile": "America/Santiago", "colombia": "America/Bogota", "peru": "America/Lima",
    "montreal": "America/Toronto", "ottawa": "America/Toronto", "calgary": "America/Edmonton",
    "aest": "Australia/Sydney", "aedt": "Australia/Sydney", "canberra": "Australia/Sydney",
    "new zealand": "Pacific/Auckland", "nz": "Pacific/Auckland",
    "wellington": "Pacific/Auckland",
}
_PLACE = r"(?P<place>[a-z][a-z .'/_-]{0,40})"
_ZONE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+){0,2}\Z")


class WhenError(Exception):
    """A recognized question that could not be answered."""


def _number(text: str) -> int:
    text = text.lower()
    return _NUMBER_WORDS[text] if text in _NUMBER_WORDS else int(text)


def _now() -> datetime:
    return datetime.now().astimezone()


def _long(day: date) -> str:
    return day.strftime("%A, %Y-%m-%d")


def _zone(place: str):
    """The zone for a city, country, or abbreviation, or None."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

    key = " ".join(place.lower().replace("_", " ").split())
    key = re.sub(r"^the ", "", key)
    name = _ZONE_ALIAS.get(key)
    if name is None:
        if "/" in place and _ZONE_NAME.fullmatch(place.strip()):
            wanted = place.strip().lower()
            name = next((zone for zone in available_timezones() if zone.lower() == wanted), None)
        else:
            matches = sorted(
                zone for zone in available_timezones()
                if "/" in zone and not zone.startswith(("Etc/", "posix/", "right/", "SystemV/"))
                and zone.rsplit("/", 1)[1].replace("_", " ").lower() == key
            )
            name = matches[0] if matches else None
    if name is None:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _offset(moment: datetime) -> str:
    delta = moment.utcoffset() or timedelta(0)
    minutes = int(delta.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _time_in(place: str) -> str | None:
    zone = _zone(place)
    if zone is None:
        return None
    there = datetime.now(zone)
    here = _now()
    text = f"{there.strftime('%H:%M')} {there.strftime('%A, %Y-%m-%d')} in {zone.key} ({_offset(there)})"
    ahead = (there.utcoffset() or timedelta(0)) - (here.utcoffset() or timedelta(0))
    minutes = int(ahead.total_seconds() // 60)
    if minutes:
        span = f"{abs(minutes) // 60}h" + (f"{abs(minutes) % 60:02d}m" if minutes % 60 else "")
        text += f", {span} {'ahead of' if minutes > 0 else 'behind'} here"
    return text


def _named_date(text: str, today: date, *, past: bool = False) -> date | None:
    """A date from words. A date with no year is the next one, or the last with past."""
    text = re.sub(r"^(?:on|the date) ", "", text.strip().lower())
    if text in ("today", "now"):
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text == "yesterday":
        return today - timedelta(days=1)
    if text == "the day after tomorrow":
        return today + timedelta(days=2)
    if text == "the day before yesterday":
        return today - timedelta(days=2)
    weekday = re.fullmatch(rf"(?:(next|this|last) )?({_WEEKDAY})", text)
    if weekday:
        target = _WEEKDAYS[weekday.group(2)]
        if weekday.group(1) == "last" or (past and weekday.group(1) is None):
            return today - timedelta(days=(today.weekday() - target) % 7 or 7)
        return today + timedelta(days=(target - today.weekday()) % 7 or 7)
    year_match = re.fullmatch(r"(.+?),? (\d{4})", text)
    fixed_key, fixed_year = (year_match.group(1), int(year_match.group(2))) \
        if year_match and year_match.group(1) in _FIXED else (text, None)
    if fixed_key in _FIXED:
        month, day = _FIXED[fixed_key]
        return _resolve(fixed_year, month, day, today, past)
    if text in ("thanksgiving", "thanksgiving day"):
        for year in ((today.year - 1, today.year) if past else (today.year, today.year + 1)):
            first = date(year, 11, 1)
            candidate = first + timedelta(days=(3 - first.weekday()) % 7 + 21)
            if (past and candidate <= today) or (not past and candidate >= today):
                found = candidate
                if not past:
                    return found
        return found if past else None
    match = _DATE_RE.fullmatch(text)
    if match is None:
        return None
    try:
        if match["iso"]:
            year, month, day = (int(part) for part in match["iso"].split("-"))
            return date(year, month, day)
        if match["us"]:
            month, day, year = (int(part) for part in match["us"].split("/"))
            return date(year, month, day)
        if match["m1"]:
            year = int(match["y1"]) if match["y1"] else None
            return _resolve(year, _MONTHS[match["m1"].lower()], int(match["d1"]), today, past)
        year = int(match["y2"]) if match["y2"] else None
        return _resolve(year, _MONTHS[match["m2"].lower()], int(match["d2"]), today, past)
    except ValueError as error:
        raise WhenError("that is not a date on the calendar") from error


def _resolve(year: int | None, month: int, day: int, today: date, past: bool) -> date:
    try:
        if year is not None:
            return date(year, month, day)
        candidate = date(today.year, month, day)
        if past and candidate > today:
            return date(today.year - 1, month, day)
        if not past and candidate < today:
            return date(today.year + 1, month, day)
        return candidate
    except ValueError as error:
        raise WhenError("that is not a date on the calendar") from error


def _shift(today: date, amount: int, unit: str) -> date:
    unit = unit.lower().rstrip("s")
    if unit == "day":
        return today + timedelta(days=amount)
    if unit == "week":
        return today + timedelta(weeks=amount)
    months = amount * (12 if unit == "year" else 1)
    index = today.year * 12 + (today.month - 1) + months
    year, month = divmod(index, 12)
    if not 1 <= year <= 9998:
        raise WhenError("that is outside the calendar")
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(today.day, last))


def _span(days: int) -> str:
    count = abs(days)
    text = f"{count} day" + ("" if count == 1 else "s")
    if count >= 14:
        weeks, rest = divmod(count, 7)
        text += f" ({weeks} weeks" + (f" and {rest} day" + ("" if rest == 1 else "s") if rest else "") + ")"
    return text


def _calendar(text: str, today: date) -> str | None:
    match = re.fullmatch(
        rf"(?:show (?:me )?|open |print |display )?(?:a |the |this month's )?calendar"
        rf"(?: (?:for|of) (?:(?P<this>this month|today)|(?P<next>next month)"
        rf"|(?P<month>{_MONTH})(?: (?P<year>\d{{4}}))?|(?P<only_year>\d{{4}})))?",
        text, re.IGNORECASE)
    if match is None:
        return None
    if match["only_year"]:
        year = int(match["only_year"])
        if not 1 <= year <= 9999:
            raise WhenError("that year is outside the calendar")
        return calendar.TextCalendar(calendar.SUNDAY).formatyear(year, 2, 1, 1, 3).rstrip()
    year, month = today.year, today.month
    if match["next"]:
        shifted = _shift(today.replace(day=1), 1, "month")
        year, month = shifted.year, shifted.month
    elif match["month"]:
        month = _MONTHS[match["month"].lower()]
        if match["year"]:
            year = int(match["year"])
        elif month < today.month:
            year += 1
    if not 1 <= year <= 9999:
        raise WhenError("that year is outside the calendar")
    return calendar.TextCalendar(calendar.SUNDAY).formatmonth(year, month).rstrip()


def answer(text: str) -> tuple[int, str]:
    """Return a status and the text to print. Status 3 is not a date question."""
    text = clean(text)
    if not text:
        return NOT_WHEN, ""
    low = text.lower()
    now = _now()
    today = now.date()
    what = r"(?:what(?:'s| is)|tell me|give me|show me)"

    if re.fullmatch(
            rf"(?:{what} )?(?:the )?(?:day|day of the week|weekday)(?: is it| is today)?(?: today)?"
            rf"|what day (?:is it|is today|are we on)(?: today)?|what(?:'s| is) today"
            rf"|(?:{what} )?(?:today's|the|the current) date(?: today)?|what date is it(?: today)?"
            rf"|{what} the date today", low):
        return 0, _long(today)
    if re.fullmatch(
            rf"(?:{what} )?(?:the )?(?:current )?time(?: now| here)?|what time is it(?: now| here)?", low):
        zone = now.tzname() or ""
        return 0, now.strftime("%Y-%m-%d %H:%M:%S") + (f" {zone}" if zone else "")
    if re.fullmatch(rf"what year is it|(?:{what} )?the (?:current )?year|what year are we in", low):
        return 0, str(today.year)
    if re.fullmatch(rf"what month is it|(?:{what} )?the (?:current )?month|what month are we in", low):
        return 0, today.strftime("%B %Y")
    if re.fullmatch(
            rf"what week(?: number)? is it|(?:{what} )?the (?:current )?week(?: number)?"
            rf"|what week(?: number)? (?:are we in|of the year is it)", low):
        iso = today.isocalendar()
        return 0, f"week {iso.week} of {iso.year}"
    if re.fullmatch(rf"what day of the year is it|(?:{what} )?the day of the year", low):
        total = 366 if calendar.isleap(today.year) else 365
        return 0, f"day {today.timetuple().tm_yday} of {total}"
    if re.fullmatch(
            rf"(?:{what} )?(?:the )?(?:current )?(?:unix|epoch)(?: time)?(?: ?stamp)?"
            rf"|(?:{what} )?(?:the )?(?:current )?timestamp", low):
        return 0, str(int(time.time()))
    if re.fullmatch(
            r"what (?:time ?zone|tz) (?:am i in|is this|is it|am i on)"
            rf"|(?:{what} )?(?:my|the|this) (?:current )?time ?zone", low):
        return 0, _local_zone(now)

    relative = re.fullmatch(
        rf"(?:what(?:'s| is| was) |what day is |what day was )?(?:the date |the day )?"
        r"(?P<word>tomorrow|yesterday|the day after tomorrow|the day before yesterday)"
        r"(?:'s date)?", low)
    if relative:
        return 0, _long(_named_date(relative["word"], today))
    relative = re.fullmatch(
        r"what (?:day|date) (?:is|was|will) (?:it )?(?:be )?"
        r"(?P<word>tomorrow|yesterday|the day after tomorrow|the day before yesterday)", low)
    if relative:
        return 0, _long(_named_date(relative["word"], today))

    shifted = re.fullmatch(
        rf"(?:what(?:'s| is| will be) |what (?:day|date) (?:is|will it be) )?(?:the (?:date|day) )?"
        rf"(?:in )?{_COUNT} {_SPAN_UNIT} (?:from (?:now|today)|later|ahead|from now on)"
        rf"|(?:what(?:'s| is| will be) |what (?:day|date) (?:is|will it be) )(?:the (?:date|day) )?"
        rf"in (?P<n2>\d{{1,5}}) (?P<unit2>days?|weeks?|months?|years?)", low)
    if shifted:
        amount = _number(shifted["n"] or shifted["n2"])
        return 0, _long(_shift(today, amount, shifted["unit"] or shifted["unit2"]))
    shifted = re.fullmatch(
        rf"(?:what(?:'s| is| was) |what (?:day|date) was (?:it )?)?(?:the (?:date|day) )?"
        rf"{_COUNT} {_SPAN_UNIT} ago", low)
    if shifted:
        return 0, _long(_shift(today, -_number(shifted["n"]), shifted["unit"]))

    until = re.fullmatch(
        r"how (?:many (?P<unit>days|weeks|sleeps)|long)(?: are there| is it| do i have| left)?"
        r" (?:until|till|til|to|before) (?P<when>.+)"
        r"|(?:days|countdown) (?:until|till|to) (?P<when2>.+)"
        r"|when is (?P<when3>.+)", low)
    if until:
        named = until["when"] or until["when2"] or until["when3"]
        target = _named_date(named, today)
        if target is not None:
            days = (target - today).days
            if until["when3"]:
                suffix = "today" if days == 0 else f"in {_span(days)}"
                return 0, f"{_long(target)}, {suffix}"
            if days == 0:
                return 0, f"that is today, {_long(target)}"
            if until["unit"] == "weeks":
                return 0, f"{days / 7:.1f} weeks until {_long(target)}"
            return 0, f"{_span(days)} until {_long(target)}"
    since = re.fullmatch(
        r"how (?:many days|long)(?: has it been| have passed| is it)? since (?P<when>.+)"
        r"|days since (?P<when2>.+)", low)
    if since:
        target = _named_date(since["when"] or since["when2"], today, past=True)
        if target is not None:
            days = (today - target).days
            if days < 0:
                return 0, f"that is {_span(days)} from now, {_long(target)}"
            return 0, f"{_span(days)} since {_long(target)}"
    between = re.fullmatch(
        r"how many days (?:are (?:there )?)?(?:between|from) (?P<a>.+?) (?:and|to|until) (?P<b>.+)", low)
    if between:
        first = _named_date(between["a"], today)
        second = _named_date(between["b"], first or today) if first is not None else None
        if first is not None and second is not None:
            return 0, f"{_span((second - first).days)} from {_long(first)} to {_long(second)}"

    weekday = re.fullmatch(
        r"what day(?: of the week)? (?:is|was|will|does|did) (?:it )?(?:be )?(?:on )?(?P<when>.+?)"
        r"(?: fall on| land on| be)?"
        r"|what(?:'s| is| was) the day of the week (?:of|on|for) (?P<when2>.+)"
        r"|what day(?: of the week)? (?:does|did|will) (?P<when3>.+?) (?:fall|land) on", low)
    if weekday:
        named = weekday["when"] or weekday["when2"] or weekday["when3"]
        target = _named_date(named, today)
        if target is not None:
            return 0, _long(target)

    leap = re.fullmatch(
        r"is (?:(?P<year>\d{1,4})|it|this year|this|next year) a leap year", low)
    if leap:
        year = int(leap["year"]) if leap["year"] else today.year + (1 if "next" in low else 0)
        if not 1 <= year <= 9999:
            raise WhenError("that year is outside the calendar")
        return 0, f"{year} is a leap year" if calendar.isleap(year) else f"{year} is not a leap year"
    month_days = re.fullmatch(
        rf"how many days (?:are (?:there )?)?in (?:(?P<month>{_MONTH})(?: (?P<year>\d{{4}}))?"
        rf"|(?P<this>this month)|(?P<this_year>this year|the year|a year)|(?P<only_year>\d{{4}}))", low)
    if month_days:
        if month_days["this_year"] or month_days["only_year"]:
            year = int(month_days["only_year"]) if month_days["only_year"] else today.year
            return 0, f"{366 if calendar.isleap(year) else 365} days in {year}"
        year = int(month_days["year"]) if month_days["year"] else today.year
        month = today.month if month_days["this"] else _MONTHS[month_days["month"].lower()]
        return 0, f"{calendar.monthrange(year, month)[1]} days in {calendar.month_name[month]} {year}"

    there = re.fullmatch(
        rf"(?:what time is it|what(?:'s| is) the (?:current |local )?time|(?:the |current |local )?time"
        rf"|what(?:'s| is) the (?:current )?date|what day is it)(?: right now| now)? (?:in|at|for) {_PLACE}"
        rf"|(?:what(?:'s| is) the )?{_PLACE.replace('place', 'place2')} time(?: now)?"
        rf"|(?:what(?:'s| is) the )?(?:time|time ?zone) (?:in|for|of) {_PLACE.replace('place', 'place3')}",
        low)
    if there:
        place = there["place"] or there["place2"] or there["place3"]
        found = _time_in(place)
        if found is not None:
            return 0, found

    shown = _calendar(low, today)
    if shown is not None:
        return 0, shown
    return NOT_WHEN, ""


def _local_zone(now: datetime) -> str:
    name = ""
    try:
        target = Path("/etc/localtime").resolve()
        parts = target.parts
        if "zoneinfo" in parts:
            name = "/".join(parts[parts.index("zoneinfo") + 1:])
    except OSError:
        name = ""
    short = now.tzname() or ""
    label = name or short or "local time"
    if name and short and short != name:
        label += f" ({short}, {_offset(now)})"
    else:
        label += f" ({_offset(now)})"
    return label


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["--ask"]:
        print("usage: kilix_when.py --ask TEXT", file=sys.stderr)
        return 2
    try:
        status, text = answer(" ".join(argv[1:]))
    except WhenError as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    if text:
        print(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
