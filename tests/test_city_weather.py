"""Named-city routing, provider selection and bounded worker lifecycle."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'config'))

import kilix_weather as city  # noqa: E402


def place(name='Paris', population=2_000_000, **kw):
    return dict(name=name, country='France', country_code='FR', admin1='Ile-de-France',
                latitude=48.85, longitude=2.35, feature_code='PPLC',
                population=population, timezone='Europe/Paris', **kw)


class CityWeatherTests(unittest.TestCase):
    def setUp(self):
        self.provider = city.provider()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Path(self.temp.name) / 'settings.conf'
        self.settings.write_text('KILIX_WEATHER_ZIP=00501\nKILIX_TEMPERATURE_UNIT=celsius\n')
        env = patch.dict(os.environ, {'GPU_TERMINAL_SETTINGS_FILE': str(self.settings)}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_common_questions_and_explicit_units(self):
        cases = {
            "What's the weather in Tokyo?": ('Tokyo', ''),
            'weather in Paris, France': ('Paris, France', ''),
            'Please show me the weather in New York today.': ('New York', '', 'today'),
            'Could you tell me the current weather for São Paulo, Brazil?': ('São Paulo, Brazil', ''),
            'How is the weather like in London right now?': ('London', ''),
            'How hot is it in Cairo in Fahrenheit now?': ('Cairo', 'fahrenheit'),
            'weather in Sydney now in Celsius, please!': ('Sydney', 'celsius'),
            'New York weather': ('New York', ''),
            'Tell me the weather of San Francisco': ('San Francisco', ''),
            'weather in Washington, D.C.': ('Washington, D.C', ''),
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(city.ask_query(question), city.Query(*expected))

    def test_other_commands_stay_with_needle_and_never_start_a_worker(self):
        with patch.object(city, 'run_query') as run:
            for text in ('open the weather app', 'install weather', 'hide the weather',
                         'close tab 2', '--yes close tab 2',
                         'set weather refresh to 30', 'show weather', 'open weather',
                         'refresh the weather', 'turn off the weather widget', 'show the forecast',
                         'set the weather zip to 98101', 'close the weather card',
                         "what's the temperature", 'how hot is it', 'temperature',
                         "what's the temperature in fahrenheit", 'what is the cpu temperature',
                         'rain sounds', 'play rain sounds', 'make it rain', 'is it running',
                         'check the forecast in the weather app, then put the calendar up in the top bar',
                         'pull up the weather', 'bring up the weather', 'what is the weather on my screen',
                         'what the temperature is'):
                with self.subTest(text=text):
                    self.assertEqual(city.main(['--ask', text]), city.NOT_WEATHER)
            run.assert_not_called()

    def test_invalid_questions_do_not_start_networking_or_claim_a_forecast(self):
        with patch.object(city, 'run_query') as run, contextlib.redirect_stderr(io.StringIO()):
            for text in ('weather in Paris yesterday', 'weather in Paris and London',
                         'weather in Paris; touch /tmp/city-weather-test',
                         'weather in $(id)', 'weather in Paris, Texas, US',
                         'weather in \x1b[31mParis'):
                with self.subTest(text=text):
                    self.assertEqual(city.main(['--ask', text]), 1)
            run.assert_not_called()
        for value in ('', 'x', 'a' * 121, '..', ',Paris', 'Paris,', '12345'):
            with self.assertRaises(ValueError):
                city.city_name(value)

    def test_days_and_topics_are_read_before_the_city(self):
        cases = {
            "what's the weather": ('', '', '', ''),
            'weather': ('', '', '', ''),
            'what is the weather outside': ('', '', '', ''),
            "what's the temperature outside": ('', '', '', ''),
            'is it cold outside': ('', '', '', ''),
            'weather in tokyo this weekend': ('tokyo', '', 'weekend', ''),
            'weather tmrw': ('', '', 'tomorrow', ''),
            'will it rain tomorrow in Paris': ('Paris', '', 'tomorrow', 'rain'),
            'is it going to rain': ('', '', '', 'rain'),
            'is it raining in London': ('London', '', '', 'raining'),
            'is it raining today': ('', '', 'today', 'rain'),
            "what's the forecast for London": ('London', '', 'week', ''),
            'what will the weather be like on saturday in Rome': ('Rome', '', 'saturday', ''),
            'weather next friday in Oslo': ('Oslo', '', 'next friday', ''),
            'weather next week': ('', '', 'week', ''),
            'how cold will it be tonight': ('', '', 'today', ''),
            'when is sunset': ('', '', '', 'sunset'),
            'what time is sunrise in Denver tomorrow': ('Denver', '', 'tomorrow', 'sunrise'),
            'when does the sun go down': ('', '', '', 'sunset'),
            'do i need an umbrella in Seattle': ('Seattle', '', '', 'umbrella'),
            "what's the humidity in Miami": ('Miami', '', '', 'humidity'),
            "what's the wind speed in Chicago in celsius": ('Chicago', 'celsius', '', 'wind'),
            'will it snow this week in Denver': ('Denver', '', 'week', 'snow'),
            'how hot is it in Cairo tomorrow': ('Cairo', '', 'tomorrow', ''),
            'Oslo weather this weekend': ('Oslo', '', 'weekend', ''),
            'The Hague weather': ('The Hague', '', '', ''),
            'what the weather is like outside': ('', '', '', ''),
            'if it will rain tomorrow': ('', '', 'tomorrow', 'rain'),
            'if it is raining': ('', '', '', 'raining'),
            'what the forecast is for Paris': ('Paris', '', 'week', ''),
            'whether i need an umbrella in Oslo': ('Oslo', '', '', 'umbrella'),
            'when the sunset is': ('', '', '', 'sunset'),
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(city.ask_query(question), city.Query(*expected))

    def test_saved_zip_is_read_and_never_written(self):
        before = self.settings.read_bytes()
        zip_place = dict(name='Holtsville', admin1='New York', country='United States', country_code='US',
                         postcodes=['00501'], latitude=40.8, longitude=-73.0, timezone='America/New_York')
        current = {'current': {'temperature_2m': 10, 'weather_code': 2, 'time': time.time()}}
        with patch.object(self.provider, 'json_request', side_effect=[{'results': [zip_place]}, current]) as request:
            answer = city.fetch_city(city.Query(''))
        self.assertIn('Holtsville, New York, United States: 10°C · Partly cloudy', answer)
        geo = parse_qs(urlsplit(request.call_args_list[0].args[0]).query)
        self.assertEqual((geo['name'], geo['countryCode']), (['00501'], ['US']))
        self.assertEqual(self.settings.read_bytes(), before)

    def test_no_saved_zip_says_how_to_name_a_place_without_networking(self):
        self.settings.write_text('KILIX_TEMPERATURE_UNIT=celsius\n')
        with patch.object(self.provider, 'json_request') as request:
            with self.assertRaisesRegex(ValueError, 'Name a city'):
                city.fetch_city(city.Query('', when='tomorrow'))
        request.assert_not_called()

    def forecast(self, **overrides):
        start = 1794297600  # Tue 10 Nov 2026 00:00 in Los Angeles
        daily = {
            'time': [start + day * 86400 for day in range(8)],
            'weather_code': [61, 0, 3, 3, 71, 0, 0, 0],
            'temperature_2m_max': [12, 14, 10, 9, 1, 15, 16, 17],
            'temperature_2m_min': [6, 7, 4, 3, -2, 8, 9, 10],
            'precipitation_probability_max': [90, 5, 40, 10, 70, 0, 0, None],
            'precipitation_sum': [8, 0, 0.5, 0, 0, 0, 0, 0],
            'snowfall_sum': [0, 0, 0, 0, 3.5, 0, 0, 0],
            'sunrise': [start + day * 86400 + 7 * 3600 for day in range(8)],
            'sunset': [start + day * 86400 + 16 * 3600 + 40 * 60 for day in range(8)],
            'wind_speed_10m_max': [20, 10, 12, 8, 30, 5, 5, 5],
            'wind_gusts_10m_max': [40, 20, 22, 15, 55, 9, 9, 9],
            'relative_humidity_2m_mean': [90, 60, 70, 75, 85, 50, 50, 50],
        }
        daily.update(overrides)
        return {'timezone': 'America/Los_Angeles', 'daily': daily}

    def ask(self, query):
        seattle = {**place('Seattle'), 'country': 'United States', 'country_code': 'US', 'admin1': 'Washington',
                   'timezone': 'America/Los_Angeles'}
        with patch.object(self.provider, 'json_request', side_effect=[{'results': [seattle]}, self.forecast()]) \
                as request:
            answer = city.fetch_city(query)
        return answer, parse_qs(urlsplit(request.call_args.args[0]).query)

    def test_day_forecasts_rain_snow_and_sun_times(self):
        answer, params = self.ask(city.Query('Seattle', when='tomorrow'))
        self.assertIn('Tomorrow, Wed 11 Nov: Clear, high 14°C, low 7°C, 5% chance of rain, wind up to 10 km/h', answer)
        self.assertNotIn('Today,', answer)
        self.assertEqual(params['timezone'], ['auto'])
        self.assertEqual(params['forecast_days'], ['8'])
        self.assertIn('weather-model estimates', answer)
        self.assertIn('Open-Meteo (CC BY 4.0)', answer)
        answer, _ = self.ask(city.Query('Seattle', what='rain'))
        self.assertIn('Today, Tue 10 Nov: Yes, rain is likely (90% chance, about 8 mm).', answer)
        answer, _ = self.ask(city.Query('Seattle', when='thursday', what='umbrella'))
        self.assertIn('Thu 12 Nov: Maybe take one (40% chance).', answer)
        answer, _ = self.ask(city.Query('Seattle', when='friday', what='rain'))
        self.assertIn('Fri 13 Nov: Rain is unlikely (10% chance).', answer)
        answer, _ = self.ask(city.Query('Seattle', when='saturday', what='snow'))
        self.assertIn('Sat 14 Nov: Yes, snow is forecast, about 3.5 cm.', answer)
        answer, _ = self.ask(city.Query('Seattle', when='weekend'))
        self.assertEqual([line[:10] for line in answer.splitlines()[1:3]], ['Sat 14 Nov', 'Sun 15 Nov'])
        answer, _ = self.ask(city.Query('Seattle', what='sunset'))
        self.assertIn('Sunset today, Tue 10 Nov: 16:40 PST', answer)
        answer, _ = self.ask(city.Query('Seattle', when='next tuesday', what='sunrise'))
        self.assertIn('Sunrise on Tue 17 Nov: 07:00 PST', answer)
        answer, _ = self.ask(city.Query('Seattle', when='week'))
        self.assertEqual(sum(line.endswith(('km/h',)) for line in answer.splitlines()), 7)
        self.assertNotIn('Tue 17 Nov', answer)

    def test_fahrenheit_reports_wind_in_mph(self):
        answer, params = self.ask(city.Query('Seattle', 'fahrenheit', when='tomorrow'))
        self.assertIn('high 57°F, low 45°F', answer)
        self.assertIn('wind up to 10 mph', answer)
        self.assertEqual(params['wind_speed_unit'], ['mph'])

    def test_short_or_bad_forecasts_are_errors_not_answers(self):
        seattle = {**place('Seattle'), 'timezone': 'America/Los_Angeles'}
        for daily in (self.forecast(temperature_2m_max=[12]), self.forecast(weather_code=[1.5] * 8),
                      self.forecast(temperature_2m_min=[None] * 8), {'daily': 'bad'}):
            with patch.object(self.provider, 'json_request', side_effect=[{'results': [seattle]}, daily]):
                with self.assertRaises(ValueError):
                    city.fetch_city(city.Query('Seattle', when='tomorrow'))

    def test_largest_major_city_exact_name_and_qualifier(self):
        paris = place()
        small = {**paris, 'population': 25_000, 'country': 'United States', 'country_code': 'US'}
        prefix = {**paris, 'name': 'Parisville', 'population': 10_000_000}
        with patch.object(self.provider, 'json_request', return_value={'results': [prefix, small, paris]}) as request:
            self.assertEqual(city.resolve_city('Paris', self.provider), paris)
            self.assertEqual(city.resolve_city('Paris, France', self.provider), paris)
            params = parse_qs(urlsplit(request.call_args.args[0]).query)
            self.assertEqual(params['name'], ['Paris, France'])
            self.assertNotIn('countryCode', params)

    def test_ambiguous_small_unknown_and_malformed_locations_never_fetch_conditions(self):
        for response in (
                {'results': [place('Springfield', 160_000), place('Springfield', 150_000)]},
                {'results': [place('Paris', 2000), place('Paris', 1000)]},
                {'results': [{**place(), 'feature_code': 'AIRP'}]},
                {'results': []}, {'results': 'bad'}):
            with patch.object(self.provider, 'json_request', return_value=response) as request:
                with self.assertRaises(ValueError):
                    city.fetch_city(city.Query('Paris'))
                self.assertEqual(request.call_count, 1)

    def test_current_conditions_units_timestamp_attribution_and_no_saved_state_changes(self):
        before = self.settings.read_bytes()
        cache = self.settings.parent / 'kilix/weather.json'
        cache.parent.mkdir()
        cache.write_text('{"zip":"00501","reading":null}')
        cached = cache.read_bytes()
        current = {'current': {'temperature_2m': 10, 'weather_code': 2, 'time': time.time()}}
        for query, temperature in ((city.Query('Paris'), '10°C'),
                                   (city.Query('Paris', 'fahrenheit'), '50°F')):
            with patch.object(self.provider, 'json_request', side_effect=[{'results': [place()]}, current]) as request:
                answer = city.fetch_city(query)
            self.assertIn('Paris, Ile-de-France, France: ' + temperature, answer)
            self.assertIn('Partly cloudy', answer)
            self.assertIn('Conditions at ', answer)
            self.assertIn('weather-model estimates', answer)
            self.assertIn('Open-Meteo (CC BY 4.0)', answer)
            self.assertNotIn('STALE', answer)
            params = parse_qs(urlsplit(request.call_args.args[0]).query)
            self.assertEqual(params['latitude'], ['48.85'])
            self.assertEqual(params['longitude'], ['2.35'])
            self.assertEqual(params['timeformat'], ['unixtime'])
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(cache.read_bytes(), cached)

    def test_stale_future_and_invalid_numeric_data(self):
        for age in (7201, -1801):
            current = {'current': {'temperature_2m': 10, 'weather_code': 2, 'time': time.time() - age}}
            with patch.object(self.provider, 'json_request', side_effect=[{'results': [place()]}, current]):
                self.assertIn('STALE', city.fetch_city(city.Query('Paris')))
        for key, value in (('temperature_2m', float('nan')), ('temperature_2m', True),
                           ('temperature_2m', 101), ('weather_code', 2.5), ('time', None)):
            current = {'temperature_2m': 10, 'weather_code': 2, 'time': time.time(), key: value}
            with patch.object(self.provider, 'json_request', side_effect=[{'results': [place()]}, {'current': current}]):
                with self.assertRaises(ValueError):
                    city.fetch_city(city.Query('Paris'))

    def test_provider_errors_are_reported_without_an_answer(self):
        for error, message in ((URLError('offline'), 'offline'), (TimeoutError(), 'timed out'),
                               (HTTPError('https://example.invalid', 429, 'busy', {}, None), 'busy'),
                               (ValueError('City not found.'), 'City not found')):
            err, out = io.StringIO(), io.StringIO()
            with patch.object(city, 'fetch_city', side_effect=error), \
                    contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                self.assertEqual(city.worker(city.Query('Paris')), 1)
            self.assertIn(message, err.getvalue())
            self.assertEqual(out.getvalue(), '')

    def test_timeout_and_cancel_kill_and_reap_the_only_child(self):
        for failure in (subprocess.TimeoutExpired('weather', 20), KeyboardInterrupt()):
            child = Mock()
            child.communicate.side_effect = [failure, (b'', b'')]
            child.poll.return_value = None
            with patch.object(city.subprocess, 'Popen', return_value=child) as popen, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(city.run_query(city.Query('Tokyo')), 1)
            popen.assert_called_once()
            child.kill.assert_called_once()
            self.assertEqual(child.communicate.call_count, 2)
            self.assertEqual(child.communicate.call_args_list[0].kwargs, {'timeout': city.FETCH_TIMEOUT})

    def test_worker_arguments_are_literal_and_failure_is_not_success(self):
        child = Mock(returncode=1)
        child.poll.return_value = 1
        child.communicate.return_value = (b'', b'Weather unavailable\n')
        with patch.object(city.subprocess, 'Popen', return_value=child) as popen, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(city.run_query(city.Query("Xi'an, China", 'celsius')), 1)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[-3:], ['celsius', '--', "Xi'an, China"])
        self.assertNotIn('shell', popen.call_args.kwargs)
        child.kill.assert_not_called()

    def test_deadline_reaps_a_real_stalled_process(self):
        popen = subprocess.Popen
        children = []

        def stalled(_argv, **kwargs):
            child = popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            children.append(child)
            return child

        started = time.monotonic()
        with patch.object(city.subprocess, 'Popen', side_effect=stalled), \
                patch.object(city, 'FETCH_TIMEOUT', 0.2), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(city.run_query(city.Query('Tokyo')), 1)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)
        self.assertNotEqual(children[0].returncode, 0)
        self.assertTrue(children[0].stdout.closed)
        self.assertTrue(children[0].stderr.closed)


if __name__ == '__main__':
    unittest.main()
