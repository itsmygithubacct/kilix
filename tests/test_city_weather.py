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
            'Please show me the weather in New York today.': ('New York', ''),
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
                         'what is the weather', 'close tab 2', '--yes close tab 2',
                         'set weather refresh to 30', 'show weather', 'open weather'):
                with self.subTest(text=text):
                    self.assertEqual(city.main(['--ask', text]), city.NOT_WEATHER)
            run.assert_not_called()

    def test_invalid_questions_do_not_start_networking_or_claim_a_forecast(self):
        with patch.object(city, 'run_query') as run, contextlib.redirect_stderr(io.StringIO()):
            for text in ('weather in Paris tomorrow', 'weather in Paris and London',
                         'weather in Paris; touch /tmp/city-weather-test',
                         'weather in $(id)', 'weather in Paris, Texas, US',
                         'weather in \x1b[31mParis'):
                with self.subTest(text=text):
                    self.assertEqual(city.main(['--ask', text]), 1)
            run.assert_not_called()
        for value in ('', 'x', 'a' * 121, '..', ',Paris', 'Paris,', '12345'):
            with self.assertRaises(ValueError):
                city.city_name(value)

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
