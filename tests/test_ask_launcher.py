"""`kilix ask`: city weather or one request to installed Needle, never an install."""
import json
import os
import pathlib
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402
from test_ask_model import SIZER, FakeOllama, modelfile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Stands in for the catalog launcher: answers `install kilix-needle` with the
# fake app's path, or refuses as an uninstalled app does, and logs the call.
CONTENT_APP = '''\
import json, os, sys
log = os.environ["ASK_TEST_LOG"]
with open(log, "a") as stream:
    stream.write(json.dumps({"content_app": sys.argv[1:],
                             "auto_install": os.environ.get("KILIX_APP_AUTO_INSTALL")}) + "\\n")
if sys.argv[1:3] == ["install", "kilix-help-llm"]:
    if os.environ.get("ASK_TEST_HELP_MISSING"):
        print("kilix app kilix-help-llm: not installed", file=sys.stderr)
        raise SystemExit(1)
    print(os.environ["ASK_TEST_HELP"])
    raise SystemExit(0)
if sys.argv[1:] != ["install", "kilix-needle"] or os.environ.get("ASK_TEST_MISSING"):
    print("kilix app kilix-needle: not installed", file=sys.stderr)
    raise SystemExit(1)
print(os.environ["ASK_TEST_NEEDLE"])
'''

NEEDLE = '''\
#!/usr/bin/env python3
import json, os, sys
with open(os.environ["ASK_TEST_LOG"], "a") as stream:
    stream.write(json.dumps({"needle": sys.argv[1:], "root": os.environ.get("KILIX_CONTENT_ROOT"),
                             "app": os.environ.get("KILIX_APP_ID"),
                             "backend_env": os.environ.get("KILIX_ASK_COMMANDS_BACKEND")}) + "\\n")
print("route: panes (stage 0)")
raise SystemExit(int(os.environ.get("ASK_TEST_STATUS", "0")))
'''

HELP = '''\
#!/usr/bin/env python3
import json, os, sys
with open(os.environ["ASK_TEST_LOG"], "a") as stream:
    stream.write(json.dumps({"help": sys.argv[1:]}) + "\\n")
print("Help search: list panes")
raise SystemExit(int(os.environ.get("ASK_TEST_HELP_STATUS", "0")))
'''


class AskLauncherTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        home = self.root / "checkout"
        (home / "config").mkdir(parents=True)
        for name in ("kilix", "kilix-settings"):
            shutil.copy2(ROOT / name, home / name)
        for name in ("kitty.conf", "kilix.env"):
            shutil.copy2(ROOT / "config" / name, home / "config" / name)
        shutil.copytree(ROOT / "config" / "kilix_sdk", home / "config" / "kilix_sdk")
        shutil.copy2(ROOT / "config" / "kilix_weather.py", home / "config" / "kilix_weather.py")
        shutil.copy2(ROOT / "config" / "kilix_calc.py", home / "config" / "kilix_calc.py")
        shutil.copy2(ROOT / "config" / "kilix_facts.py", home / "config" / "kilix_facts.py")
        shutil.copy2(ROOT / "config" / "kilix_sensors.py", home / "config" / "kilix_sensors.py")
        shutil.copy2(ROOT / "config" / "kilix_volume.py", home / "config" / "kilix_volume.py")
        shutil.copy2(ROOT / "config" / "kilix_local.py", home / "config" / "kilix_local.py")
        shutil.copy2(ROOT / "config" / "kilix_alarm.py", home / "config" / "kilix_alarm.py")
        for name in ("kilix_asktext.py", "kilix_when.py", "kilix_machine.py",
                     "kilix_tools.py", "kilix_about.py", "kilix_self.py",
                     "kilix_model.py", "system_llm.py"):
            shutil.copy2(ROOT / "config" / name, home / "config" / name)
        sensor = (ROOT / "third_party" / "kilix-telemetry" / "src"
                  / "kilix_telemetry" / "sensors.py")
        sensor_home = (home / "third_party" / "kilix-telemetry" / "src"
                       / "kilix_telemetry" / "sensors.py")
        sensor_home.parent.mkdir(parents=True)
        shutil.copy2(sensor, sensor_home)
        (home / "config" / "content_app.py").write_text(CONTENT_APP)
        needle = self.root / "needle"
        needle.write_text(NEEDLE)
        needle.chmod(0o755)
        help_bin = self.root / "kilix-help-llm"
        help_bin.write_text(HELP)
        help_bin.chmod(0o755)
        self.launcher = home / "kilix"
        self.log = self.root / "calls.jsonl"
        storage = self.root / "storage"
        # Model answers: no live Ollama or installed sizer is reachable from here.
        # Tests that want them start a fake service and point the sizer at a fake.
        sizer = self.root / "sizer"
        sizer.write_text(SIZER)
        sizer.chmod(0o755)
        self.sizer = str(sizer)
        self.env = sandbox_env(**{
            # No voice daemon or installed tool can be found from here.
            "PATH": "/usr/bin:/bin", "HOME": str(self.root / "home"),
            "KILIX_STORAGE_HOME": str(storage),
            "KILIX_VOICE_PREFIX": str(self.root / "voice-prefix"),
            "ASK_TEST_LOG": str(self.log), "ASK_TEST_NEEDLE": str(needle),
            "ASK_TEST_HELP": str(help_bin),
            "KILIX_ASK_OLLAMA_URL": "http://127.0.0.1:9",
            "PLEBIAN_MODEL_SIZER": str(self.root / "no-sizer"),
        })

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run_ask(self, *arguments, **env):
        return subprocess.run([str(self.launcher), "ask", *arguments], env={**self.env, **env},
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=60)

    def weather_provider(self):
        """Real launcher/worker/parser, with only provider HTTP replaced."""
        for name in ('__init__.py', 'kilix_chrome/__init__.py',
                     'kilix_chrome/settings.py', 'kilix_chrome/weather.py'):
            target = self.launcher.parent / 'src/kitty' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / 'src/kitty' / name, target)
        provider = self.launcher.parent / 'src/kitty/kilix_chrome/weather.py'
        with provider.open('a') as stream:
            stream.write('''
def json_request(url):
    with open(os.environ['ASK_TEST_LOG'], 'a') as log:
        log.write(json.dumps({'weather': url}) + '\\n')
    if os.environ.get('ASK_TEST_WEATHER_ERROR'):
        raise URLError('offline fixture')
    if 'geocoding-api' in url and 'countryCode=US' in url:
        return {'results': [{'name': 'Seattle', 'admin1': 'Washington', 'country': 'United States',
                             'country_code': 'US', 'postcodes': ['98101'],
                             'latitude': 47.6, 'longitude': -122.3, 'timezone': 'America/Los_Angeles'}]}
    if 'geocoding-api' in url:
        return {'results': [{'name': 'Tokyo', 'country': 'Japan', 'country_code': 'JP',
                             'feature_code': 'PPLC', 'population': 10000000,
                             'latitude': 35.68, 'longitude': 139.69, 'timezone': 'Asia/Tokyo'}]}
    if 'daily=' in url:
        start = int(time.time()) // 86400 * 86400 - 9 * 3600
        days = range(8)
        return {'timezone': 'Asia/Tokyo', 'daily': {
            'time': [start + d * 86400 for d in days], 'weather_code': [61] * 8,
            'temperature_2m_max': [15] * 8, 'temperature_2m_min': [5] * 8,
            'precipitation_probability_max': [80] * 8, 'precipitation_sum': [6] * 8,
            'snowfall_sum': [0] * 8, 'sunrise': [start + d * 86400 + 6 * 3600 for d in days],
            'sunset': [start + d * 86400 + 16 * 3600 for d in days],
            'wind_speed_10m_max': [10] * 8, 'wind_gusts_10m_max': [20] * 8,
            'relative_humidity_2m_mean': [70] * 8}}
    return {'current': {'temperature_2m': 20, 'weather_code': 0, 'time': time.time()}}
''')

    def test_city_weather_works_without_needle_and_does_not_fall_through_on_failure(self):
        self.weather_provider()
        for error, expected in (('', 0), ('1', 1)):
            with self.subTest(error=error):
                result = self.run_ask("What's the weather in Tokyo?", ASK_TEST_MISSING='1',
                                      ASK_TEST_WEATHER_ERROR=error)
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected == 0:
                    self.assertIn('Tokyo, Japan: 68°F', result.stdout)
                    self.assertIn('Conditions at ', result.stdout)
                else:
                    self.assertIn('offline', result.stderr)
                    self.assertNotIn('Tokyo, Japan:', result.stdout)
        self.assertEqual(len(self.calls()), 3)
        self.assertTrue(all('weather' in call for call in self.calls()), self.calls())

    def test_forecasts_and_saved_place_weather_work_without_needle(self):
        self.weather_provider()
        settings = self.root / "weather-settings.conf"
        settings.write_text("KILIX_WEATHER_ZIP=98101\nKILIX_TEMPERATURE_UNIT=celsius\n")
        before = settings.read_bytes()
        for request, expected in (
            ("will it rain tomorrow in Tokyo?", "Yes, rain is likely (80% chance, about 6 mm)."),
            ("weather in tokyo this weekend", "Forecasts are weather-model estimates."),
            ("when is sunset in Tokyo", "Sunset today"),
            ("could you tell me what the weather is like outside", "Seattle, Washington, United States: 20°C"),
            ("do I need an umbrella today, please", "Yes, take an umbrella"),
        ):
            with self.subTest(request=request):
                result = self.run_ask(request, ASK_TEST_MISSING="1",
                                      GPU_TERMINAL_SETTINGS_FILE=str(settings))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
        self.assertTrue(all("weather" in call for call in self.calls()), self.calls())
        self.assertEqual(settings.read_bytes(), before)
        settings.write_text("KILIX_TEMPERATURE_UNIT=celsius\n")
        result = self.run_ask("weather tomorrow", ASK_TEST_MISSING="1",
                              GPU_TERMINAL_SETTINGS_FILE=str(settings))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Name a city", result.stderr)
        self.assertFalse(any("needle" in call for call in self.calls()), self.calls())

    def test_kilix_shortcut_questions_are_answered_without_needle(self):
        result = self.run_ask("could you tell me the shortcut for a new tab?", ASK_TEST_MISSING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("New tab: ctrl+shift+t", result.stdout)
        self.assertFalse(any("needle" in call for call in self.calls()), self.calls())
        result = self.run_ask("set the shortcut for a new tab to ctrl+t", ASK_TEST_MISSING="1")
        self.assertNotIn("New tab:", result.stdout)

    def test_arithmetic_is_answered_without_needle(self):
        for request, expected in (
            ("what is three times five?", "15"),
            ("what is 5% of 512.54", "25.627"),
            ("7x7+95/2", "96.5"),
            ("what is two plus two-thousand?", "2002"),
            ("what is two plus two thousand?", "2002"),
            ("what is twenty-one thousand plus three?", "21003"),
            ("what is two hundred and fifty plus two?", "252"),
            ("what is two and two?", "4"),
            ("what is two and two hundred plus one?", "203"),
            ("what is two hundred and two hundred plus one?", "401"),
            ("what is two hundred and fifty thousand plus one?", "250001"),
            ("what is two thousand and two?", "2002"),
            ("what is 10**12 + 1?", "1000000000001"),
            ("what is 2**53+1?", "9007199254740993"),
        ):
            with self.subTest(request=request):
                result = self.run_ask(request, ASK_TEST_MISSING="1")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
        opened = self.run_ask("open the calculator", ASK_TEST_MISSING="1")
        self.assertEqual(opened.returncode, 1, opened.stderr)
        self.assertIn("not installed", opened.stderr)
        self.assertTrue(self.calls())
        self.assertTrue(all("needle" not in call for call in self.calls()))

    def test_calculator_prints_exact_integers_and_conversion_units(self):
        # `kilix calculator EXPRESSION` execs this file. The copied launcher is
        # not a built tree, so the test calls the same program directly.
        calc = [sys.executable, "-B", str(self.launcher.parent / "config" / "kilix_calc.py")]
        for request, expected in (
            ("10**12+1", "1000000000001"),
            ("2**53+1", "9007199254740993"),
            ("2**60", "1152921504606846976"),
            ("2**1024", str(2 ** 1024)),
            ("how many MB in 1.5 GB?", "1500 MB"),
            ("32 f to c", "0 °C"),
            ("5 km in m", "5000 m"),
        ):
            with self.subTest(request=request):
                result = subprocess.run(
                    [*calc, request],
                    env=self.env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
        over = subprocess.run(
            [*calc, "10000**1024"],
            env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(over.returncode, 1, over.stdout)
        self.assertIn("too long", over.stderr)

    def test_howto_lookup_does_not_pause_and_clamps_its_status(self):
        for code in ("1", "2", "69"):
            with self.subTest(code=code):
                result = self.run_ask(
                    "how do I list panes?", ASK_TEST_MISSING="1", ASK_TEST_HELP_STATUS=code)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("Press Enter", result.stdout + result.stderr)
                self.assertNotIn("closing", (result.stdout + result.stderr).lower())
        install = [call for call in self.calls() if "content_app" in call]
        self.assertTrue(install)
        self.assertTrue(all(call["content_app"][:2] == ["install", "kilix-help-llm"] for call in install))
        self.assertTrue(all(call["auto_install"] == "0" for call in install))
        self.assertTrue(all("run" not in call["content_app"] for call in install))
        missing = self.run_ask(
            "how do I list panes?", ASK_TEST_MISSING="1", ASK_TEST_HELP_MISSING="1")
        self.assertEqual(missing.returncode, 1, missing.stdout)
        self.assertIn("kilix-help-llm", missing.stderr)
        self.assertNotIn("Press Enter", missing.stderr)

    def test_repl_stays_open_after_an_answer_and_a_refusal(self):
        result = subprocess.run(
            [str(self.launcher), "ask", "--repl"],
            input="what is three times five?\nopen the calculator\nwhat is 7x7+95/2\n",
            env={**self.env, "ASK_TEST_MISSING": "1"},
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["15", "96.5"])
        self.assertIn("not installed", result.stderr)
        self.assertEqual([call.get("needle") for call in self.calls()], [None])

    def test_repl_quit_ends_even_when_the_last_answer_failed(self):
        result = subprocess.run(
            [str(self.launcher), "ask", "--repl", "--yes"],
            input="close tab 2\nquit\nwhat is three times five?\n",
            env={**self.env, "ASK_TEST_STATUS": "1"},
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("15", result.stdout)
        needle = [call["needle"] for call in self.calls() if "needle" in call]
        self.assertEqual(needle, [["ask", "--yes", "--", "close tab 2"]])

    def test_repl_exit_is_not_a_request(self):
        result = subprocess.run(
            [str(self.launcher), "ask", "--repl", "what is three times five?"],
            input="  Exit  \nwhat is 7x7+95/2\n",
            env={**self.env, "ASK_TEST_MISSING": "1"},
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "15")
        self.assertEqual(self.calls(), [])

    def test_repl_on_a_terminal_prompts_again(self):
        status, output = self.run_tty(
            ["--repl"],
            [(b"Ask Kilix: ", b"what is three times five?\n"),
             (b"Ask Kilix: ", b"exit\n")],
            ASK_TEST_MISSING="1")
        self.assertEqual(status, 0, output)
        self.assertIn("15", output)
        self.assertEqual(output.count("Ask Kilix: "), 2)
        self.assertEqual(self.calls(), [])

    def test_repl_reads_a_final_line_without_a_newline(self):
        result = subprocess.run(
            [str(self.launcher), "ask", "--repl"],
            input="what is three times five?\nwhat is 2 plus 2",
            env={**self.env, "ASK_TEST_MISSING": "1"},
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["15", "4"])
        self.assertNotIn("usage", result.stderr)
        only = subprocess.run(
            [str(self.launcher), "ask", "--repl"],
            input="what is 2 plus 2",
            env={**self.env, "ASK_TEST_MISSING": "1"},
            capture_output=True, text=True, timeout=60)
        self.assertEqual(only.returncode, 0, only.stderr)
        self.assertEqual(only.stdout.strip(), "4")

    def test_a_request_without_repl_does_not_read_another_line(self):
        result = subprocess.run(
            [str(self.launcher), "ask", "what is three times five?"],
            input="what is 7x7+95/2\n",
            env={**self.env, "ASK_TEST_MISSING": "1"},
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "15")

    def test_direct_reads_do_not_use_needle(self):
        clock = self.run_ask("what time is it?", ASK_TEST_MISSING="1")
        self.assertEqual(clock.returncode, 0, clock.stderr)
        self.assertRegex(clock.stdout, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
        memory = self.run_ask("how much memory is free?", ASK_TEST_MISSING="1")
        self.assertEqual(memory.returncode, 0, memory.stderr)
        self.assertIn("available of", memory.stdout)
        swap = self.run_ask("show swap", ASK_TEST_MISSING="1")
        self.assertEqual(swap.returncode, 0, swap.stderr)
        self.assertNotIn("available of", swap.stdout)
        self.assertTrue(
            "free of" in swap.stdout or swap.stdout.strip() == "no swap",
            swap.stdout,
        )
        swap_again = self.run_ask("how much swap is free?", ASK_TEST_MISSING="1")
        self.assertEqual(swap_again.returncode, 0, swap_again.stderr)
        self.assertEqual(swap_again.stdout.strip(), swap.stdout.strip())
        disk = self.run_ask("how much disk is free?", ASK_TEST_MISSING="1")
        self.assertEqual(disk.returncode, 0, disk.stderr)
        self.assertIn("on /", disk.stdout)
        cpu = self.run_ask("what is the cpu load?", ASK_TEST_MISSING="1")
        self.assertEqual(cpu.returncode, 0, cpu.stderr)
        self.assertRegex(cpu.stdout, r"load [\d.]+, [\d.]+, [\d.]+ on \d+ CPUs")
        sysfs = self.root / "sys"
        for relative, text in (
            ("class/thermal/thermal_zone0/type", "x86_pkg_temp\n"),
            ("class/thermal/thermal_zone0/temp", "72000\n"),
            ("class/hwmon/hwmon2/name", "nvme\n"),
            ("class/hwmon/hwmon2/temp1_label", "Composite\n"),
            ("class/hwmon/hwmon2/temp1_input", "51000\n"),
            ("class/hwmon/hwmon2/fan1_label", "Controller\n"),
            ("class/hwmon/hwmon2/fan1_input", "3210\n"),
        ):
            path = sysfs / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        settings = self.root / "settings.conf"
        settings.write_text("KILIX_TEMPERATURE_UNIT=celsius\n")
        sensor = {
            "ASK_TEST_MISSING": "1",
            "KILIX_SENSORS_SYSFS": str(sysfs),
            "GPU_TERMINAL_SETTINGS_FILE": str(settings),
        }
        temperature = self.run_ask("what is the temperature?", **sensor)
        self.assertEqual(temperature.returncode, 0, temperature.stderr)
        self.assertEqual(temperature.stdout.strip(), "CPU / zone 0: 72.0°C")
        nvme = self.run_ask("nvme temperature", **sensor)
        self.assertEqual(nvme.stdout.strip(), "NVMe / Composite: 51.0°C")
        for request in ("what's the fan speed?", "how fast is the fan?", "is the fan running",
                        "how fast are my fans spinning", "fans"):
            with self.subTest(request=request):
                fan = self.run_ask(request, **sensor)
                self.assertEqual(fan.stdout.strip(), "NVMe / Controller: 3210 RPM")
        fahrenheit = self.run_ask("what is the temperature in fahrenheit?", **sensor)
        self.assertEqual(fahrenheit.stdout.strip(), "CPU / zone 0: 161.6°F")
        mine = self.run_ask("what's my temperature?", **sensor)
        self.assertEqual(mine.returncode, 1, mine.stdout)
        self.assertIn("not installed", mine.stderr)
        units = self.run_ask("how many MB in 1.5 GB?", ASK_TEST_MISSING="1")
        self.assertEqual(units.returncode, 0, units.stderr)
        self.assertEqual(units.stdout.strip(), "1500 MB")
        help_result = self.run_ask("how do I list panes?", ASK_TEST_MISSING="1")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Help search: list panes", help_result.stdout)
        panes = self.run_ask("what panes are open?", ASK_TEST_MISSING="1")
        self.assertEqual(panes.returncode, 1, panes.stdout)
        self.assertIn("kilix ask:", panes.stderr)
        self.assertTrue(all("needle" not in call for call in self.calls()))

    def test_volume_is_checked_and_set_without_needle(self):
        missing = self.run_ask("what is the volume?", ASK_TEST_MISSING="1")
        self.assertEqual(missing.returncode, 1, missing.stdout)
        self.assertIn("volume control is not installed", missing.stderr)
        state = self.root / "volume-state"
        state.write_text("40\n")
        binary = self.root / "kilix-volume"
        binary.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "path = os.environ['ASK_TEST_VOLUME_STATE']\n"
            "text = open(path).read().strip() or '40'\n"
            "parts = text.split()\n"
            "level, muted = parts[0], len(parts) > 1\n"
            "def store(level, muted):\n"
            "    open(path, 'w').write(level + (' muted' if muted else '') + '\\n')\n"
            "cmd = sys.argv[1:]\n"
            "if cmd == ['get']:\n"
            "    print(f'{level}%' + (' muted' if muted else ''))\n"
            "elif cmd[:1] == ['set'] and len(cmd) == 2 and cmd[1].isdigit():\n"
            "    store(cmd[1], muted)\n"
            "elif cmd == ['mute']:\n"
            "    store(level, True)\n"
            "elif cmd == ['unmute']:\n"
            "    store(level, False)\n"
            "elif cmd == ['sinks']:\n"
            "    if os.environ.get('ASK_TEST_VOLUME_SINKS') == 'none':\n"
            "        print('  10%  Other  alsa_output.other')\n"
            "    else:\n"
            "        print(f'* {level}%' + (' muted' if muted else '') + '  Desk speakers  alsa_output.desk')\n"
            "else:\n"
            "    print('kilix-volume: bad command', file=sys.stderr)\n"
            "    raise SystemExit(2)\n"
        )
        binary.chmod(0o755)
        env = {
            "ASK_TEST_MISSING": "1",
            "KILIX_VOLUME_BIN": str(binary),
            "ASK_TEST_VOLUME_STATE": str(state),
        }
        for request, expected in (
            ("what is the volume?", "40%"),
            ("set the volume to 30", "30%"),
            ("mute", "30% muted"),
            ("unmute the volume", "30%"),
            ("turn the volume up", "40%"),
            ("volume down", "30%"),
        ):
            with self.subTest(request=request):
                result = self.run_ask(request, **env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
        speakers = self.run_ask("which speakers are on?", **env)
        self.assertEqual(speakers.returncode, 0, speakers.stderr)
        self.assertEqual(speakers.stdout.strip(), "Desk speakers")
        default = self.run_ask("what's the default sink?", **env)
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(default.stdout.strip(), "Desk speakers")
        missing_default = self.run_ask(
            "which speakers are on?", **env, ASK_TEST_VOLUME_SINKS="none",
        )
        self.assertEqual(missing_default.returncode, 1, missing_default.stdout)
        self.assertIn("no default output", missing_default.stderr)
        opened = self.run_ask("open the volume mixer", **env)
        self.assertEqual(opened.returncode, 1, opened.stdout)
        self.assertIn("not installed", opened.stderr)
        help_result = self.run_ask("how do I set the volume?", **env)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Help search: list panes", help_result.stdout)
        self.assertTrue(all("needle" not in call for call in self.calls()))

    def test_local_reads_do_not_use_needle(self):
        root = self.root / "backlight"
        intel = root / "intel_backlight"
        other = root / "acpi_video0"
        intel.mkdir(parents=True)
        other.mkdir()
        (intel / "max_brightness").write_text("200\n", encoding="utf-8")
        (intel / "brightness").write_text("50\n", encoding="utf-8")
        (other / "max_brightness").write_text("100\n", encoding="utf-8")
        (other / "brightness").write_text("10\n", encoding="utf-8")
        bright = {
            "ASK_TEST_MISSING": "1",
            "KILIX_BACKLIGHT_ROOT": str(root),
        }
        current = self.run_ask("how bright is the screen?", **bright)
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertEqual(current.stdout.strip(), "25%")
        changed = self.run_ask("set the brightness to 40", **bright)
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertEqual(changed.stdout.strip(), "40%")
        self.assertEqual((intel / "brightness").read_text(encoding="utf-8").strip(), "80")
        self.assertEqual((other / "brightness").read_text(encoding="utf-8").strip(), "10")
        clamped = self.run_ask("please set the brightness to 150.", **bright)
        self.assertEqual(clamped.returncode, 0, clamped.stderr)
        self.assertEqual(clamped.stdout.strip(), "100%")
        self.assertEqual((intel / "brightness").read_text(encoding="utf-8").strip(), "200")
        dark = self.run_ask("set the brightness to 0", **bright)
        self.assertEqual(dark.stdout.strip(), "0%")
        missing_light = self.run_ask(
            "how bright is the screen?",
            ASK_TEST_MISSING="1",
            KILIX_BACKLIGHT_ROOT=str(self.root / "no-backlight"),
        )
        self.assertEqual(missing_light.returncode, 1, missing_light.stdout)
        self.assertIn("no backlight", missing_light.stderr)
        help_result = self.run_ask("how do I set the brightness?", **bright)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Help search: list panes", help_result.stdout)
        connect_help = self.run_ask("how do I connect to the network?", **bright)
        self.assertEqual(connect_help.returncode, 0, connect_help.stderr)
        self.assertIn("Help search: list panes", connect_help.stdout)

        log = self.root / "nmcli.log"
        nmcli = self.root / "nmcli"
        nmcli.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "open(os.environ['ASK_TEST_NMCLI_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "mode = os.environ.get('ASK_TEST_NMCLI_MODE', 'up')\n"
            "if mode == 'down':\n"
            "    print('wlan0:wifi:disconnected:')\n"
            "elif mode == 'wifi-down':\n"
            "    print('wlan0:wifi:disconnected:')\n"
            "    print('eth0:ethernet:connected:Desk')\n"
            "    print('docker0:bridge:connected:bridge')\n"
            "else:\n"
            "    print('lo:loopback:unmanaged:')\n"
            "    print('wlan0:wifi:connected:Cafe\\\\:Guest')\n"
            "    print('eth0:ethernet:disconnected:')\n"
            "    print('docker0:bridge:connected:bridge')\n",
            encoding="utf-8",
        )
        nmcli.chmod(0o755)
        network = {
            "ASK_TEST_MISSING": "1",
            "KILIX_NMCLI": str(nmcli),
            "ASK_TEST_NMCLI_LOG": str(log),
        }
        named = self.run_ask("what network am I on?", **network)
        self.assertEqual(named.returncode, 0, named.stderr)
        self.assertEqual(named.stdout.strip(), "Cafe:Guest\nbridge")
        online = self.run_ask("am I online?", **network)
        self.assertEqual(online.stdout.strip(), "online")
        wifi_up = self.run_ask("is the wifi up?", **network)
        self.assertEqual(wifi_up.stdout.strip(), "online")
        wifi_down = self.run_ask("is the wifi up?", **network, ASK_TEST_NMCLI_MODE="wifi-down")
        self.assertEqual(wifi_down.stdout.strip(), "offline")
        hyphen = self.run_ask("is the wi-fi connected?", **network, ASK_TEST_NMCLI_MODE="wifi-down")
        self.assertEqual(hyphen.stdout.strip(), "offline")
        other_up = self.run_ask("am I online?", **network, ASK_TEST_NMCLI_MODE="wifi-down")
        self.assertEqual(other_up.stdout.strip(), "online")
        self.assertNotIn("connect", log.read_text(encoding="utf-8"))
        down = self.run_ask("am I connected?", **network, ASK_TEST_NMCLI_MODE="down")
        self.assertEqual(down.stdout.strip(), "offline")
        named_down = self.run_ask("what network am I on?", **network, ASK_TEST_NMCLI_MODE="down")
        self.assertEqual(named_down.stdout.strip(), "not connected")
        sysfs = self.root / "net"
        for name, state in (("lo", "up"), ("wlan0", "up"), ("eth0", "down")):
            device = sysfs / name
            device.mkdir(parents=True)
            (device / "operstate").write_text(state + "\n", encoding="utf-8")
        fallback = {
            "ASK_TEST_MISSING": "1",
            "KILIX_NMCLI": str(self.root / "missing-nmcli"),
            "KILIX_NET_SYS": str(sysfs),
        }
        link = self.run_ask("what's my network?", **fallback)
        self.assertEqual(link.returncode, 0, link.stderr)
        self.assertEqual(link.stdout.strip(), "wlan0")
        (sysfs / "wlan0" / "operstate").write_text("down\n", encoding="utf-8")
        quiet_link = self.run_ask("show the network", **fallback)
        self.assertEqual(quiet_link.stdout.strip(), "not connected")
        (sysfs / "wlan0" / "wireless").mkdir()
        (sysfs / "eth0" / "operstate").write_text("up\n", encoding="utf-8")
        wifi_quiet = self.run_ask("is the wifi up?", **fallback)
        self.assertEqual(wifi_quiet.stdout.strip(), "offline")
        link_up = self.run_ask("am I online?", **fallback)
        self.assertEqual(link_up.stdout.strip(), "online")
        (sysfs / "wlan0" / "operstate").write_text("up\n", encoding="utf-8")
        wifi_link = self.run_ask("is the wi-fi up?", **fallback)
        self.assertEqual(wifi_link.stdout.strip(), "online")

        absent = self.run_ask(
            "what's playing?",
            ASK_TEST_MISSING="1",
            KILIX_AMP_SOCKET=str(self.root / "missing-amp.sock"),
        )
        self.assertEqual(absent.returncode, 0, absent.stderr)
        self.assertEqual(absent.stdout.strip(), "nothing is playing")
        self.assertFalse((self.root / "missing-amp.sock").exists())

        amp_dir = self.root / "amp"
        amp_dir.mkdir(mode=0o700)
        amp_dir.chmod(0o700)
        amp_path = amp_dir / "control.sock"
        track_path = self.root / "track.json"
        base = {
            "protocol": 2, "ok": True, "state": "playing", "title": "Night Drive",
            "file": "/music/night.flac", "pos": 1.0, "len": 180.0, "volume": 50,
            "repeat": 0, "shuffle": False, "index": 0, "count": 1,
            "source_type": "file", "codec": "pcm", "profile": 0, "sample_rate": 44100,
            "channels": 2, "bitrate": 0, "threads": 0, "ready": True,
            "model_ready": True, "live": False, "buffering": False, "seekable": True,
            "ended": False, "degraded": False, "reconnect_required": False,
            "wire_valid": False, "wire_pts_ms": None, "wire_epoch": None,
            "source_error_code": 0, "source_error_message": "",
            "source_error_recoverable": False,
        }

        def write_track(**updates):
            track_path.write_text(json.dumps({**base, **updates}), encoding="utf-8")

        write_track()
        stop = threading.Event()
        errors = []
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(amp_path))
        os.chmod(amp_path, 0o600)
        server.listen(8)
        server.settimeout(0.05)

        def serve_amp():
            while not stop.is_set():
                try:
                    client, _addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                with client:
                    client.settimeout(1)
                    try:
                        data = bytearray()
                        while b"\n" not in data and len(data) <= 8192:
                            chunk = client.recv(8192)
                            if not chunk:
                                break
                            data.extend(chunk)
                        if not data:
                            continue
                        request = json.loads(data)
                        if request.get("cmd") == "ping":
                            reply = {
                                "protocol": 2, "ok": True, "max_protocol": 2,
                                "encodec": False, "live_sources": False,
                            }
                        else:
                            reply = json.loads(track_path.read_text(encoding="utf-8"))
                        client.sendall(json.dumps(reply).encode() + b"\n")
                    except (BrokenPipeError, ConnectionResetError, socket.timeout):
                        pass
                    except Exception as failure:
                        errors.append(repr(failure))

        thread = threading.Thread(target=serve_amp)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.close)
        self.addCleanup(stop.set)
        playing_env = {
            "ASK_TEST_MISSING": "1",
            "KILIX_AMP_SOCKET": str(amp_path),
            "KILIX_TUI_UTILS_DIR":
                "/home/pleb/.local/gpu_terminal/sources/kilix-desktops/kilix-tui-utils",
        }
        playing = self.run_ask("what's playing?", **playing_env)
        self.assertEqual(playing.returncode, 0, (playing.stderr, errors))
        self.assertEqual(playing.stdout.strip(), "Night Drive", errors)
        write_track(state="paused")
        paused = self.run_ask("now playing", **playing_env)
        self.assertEqual(paused.stdout.strip(), "paused: Night Drive", errors)
        write_track(state="playing", title="", file="/music/night drive.flac")
        named_file = self.run_ask("what's the song?", **playing_env)
        self.assertEqual(named_file.stdout.strip(), "night drive.flac", errors)
        write_track(state="stopped", title="Night Drive")
        stopped = self.run_ask("what's playing?", **playing_env)
        self.assertEqual(stopped.stdout.strip(), "nothing is playing", errors)

        silent = self.run_ask("is the microphone on?", ASK_TEST_MISSING="1")
        self.assertEqual(silent.returncode, 0, silent.stderr)
        self.assertEqual(silent.stdout.strip(), "not listening")
        voice_dir = self.root / "voice-session"
        voice_dir.mkdir(mode=0o700)
        voice_path = voice_dir / "control.sock"
        voice_state = self.root / "voice.json"
        voice_state.write_text(json.dumps({
            "ok": True,
            "status": {"listening": True, "speaking": True, "model": "whisper"},
        }), encoding="utf-8")
        voice_stop = threading.Event()
        voice_server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        voice_server.bind(str(voice_path))
        voice_server.listen(4)
        voice_server.settimeout(0.05)

        def serve_voice():
            while not voice_stop.is_set():
                try:
                    client, _addr = voice_server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                with client:
                    try:
                        raw = client.recv(65536)
                        request = json.loads(raw.decode("utf-8"))
                        if request.get("op") != "status":
                            errors.append(repr(request))
                        reply = voice_state.read_text(encoding="utf-8")
                        client.sendall(reply.encode("utf-8"))
                    except (BrokenPipeError, ConnectionResetError, socket.timeout):
                        pass
                    except Exception as failure:
                        errors.append(repr(failure))

        voice_thread = threading.Thread(target=serve_voice)
        voice_thread.start()
        self.addCleanup(voice_thread.join, 3)
        self.addCleanup(voice_server.close)
        self.addCleanup(voice_stop.set)
        heard = self.run_ask(
            "is the microphone on?",
            ASK_TEST_MISSING="1",
            KILIX_VOICE_SESSION=str(voice_dir),
        )
        self.assertEqual(heard.returncode, 0, (heard.stderr, errors))
        self.assertEqual(heard.stdout.strip(), "listening, speaking, whisper")
        voice_state.write_text(json.dumps({
            "ok": True, "status": {"listening": False, "speaking": False, "model": ""},
        }), encoding="utf-8")
        idle = self.run_ask(
            "is the mic off?",
            ASK_TEST_MISSING="1",
            KILIX_VOICE_SESSION=str(voice_dir),
        )
        self.assertEqual(idle.stdout.strip(), "not listening")
        self.assertTrue(all("needle" not in call for call in self.calls()))

    def test_meaning_of_life_is_answered_without_needle(self):
        sys.path.insert(0, str(ROOT / "config"))
        self.addCleanup(sys.path.remove, str(ROOT / "config"))
        import kilix_facts
        for question in ("What is the meaning of life?", "what's the meaning of life",
                         "meaning of life",
                         "What is the meaning of life, the universe, and everything?"):
            asked = self.run_ask(question, ASK_TEST_MISSING="1")
            self.assertEqual(asked.returncode, 0, (question, asked.stderr))
            self.assertEqual(asked.stdout.rstrip("\n"), kilix_facts.MEANING_OF_LIFE, question)
        self.assertIn("42", kilix_facts.MEANING_OF_LIFE)
        self.assertLessEqual(max(map(len, kilix_facts.MEANING_OF_LIFE.splitlines())), 72)
        self.assertEqual(self.calls(), [])
        # A longer sentence that only mentions it is not the fixed question.
        for question in ("what is the meaning of life in the pane title",
                         "open the meaning of life"):
            self.assertIsNone(kilix_facts.classify(question), question)

    def test_worked_answers_do_not_use_needle(self):
        expected = {
            "what day of the week is july 4 2027?": "Sunday, 2027-07-04",
            "is 2028 a leap year": "2028 is a leap year",
            "how many days in february 2028?": "29 days in February 2028",
            "square root of 144": "12",
            "what is 2 to the power of 10?": "1024",
            "what's 0x1f in decimal": "31",
            "255 in hex": "0xff",
            "how many feet in a mile?": "5280 ft",
            "what is 5 feet 10 inches in cm": "177.8 cm",
            "3 hours 20 minutes in seconds": "12000 s",
            "is 91 prime": "no, 91 is not prime (7 × 13)",
            "what is 20% tip on 45 dollars": "9.00 tip at 20%, 54.00 total",
            "base64 encode hello": "aGVsbG8=",
            "how many words in 'the quick brown fox'": "4",
            "reverse the word kilix": "xilik",
            "what is exit code 127": "exit 127: command not found",
            "what does HTTP 404 mean": "HTTP 404 Not Found: Nothing matches the given URI",
            "what's my hostname": socket.gethostname(),
        }
        for question, answer in expected.items():
            asked = self.run_ask(question, ASK_TEST_MISSING="1")
            self.assertEqual(asked.returncode, 0, (question, asked.stderr))
            self.assertEqual(asked.stdout.strip(), answer, question)
        coin = self.run_ask("flip a coin", ASK_TEST_MISSING="1")
        self.assertIn(coin.stdout.strip(), ("heads", "tails"))
        helped = self.run_ask("what can you do?", ASK_TEST_MISSING="1")
        self.assertEqual(helped.returncode, 0, helped.stderr)
        self.assertIn("how many days until Christmas", helped.stdout)
        wrong = self.run_ask("5 kg in miles", ASK_TEST_MISSING="1")
        self.assertEqual(wrong.returncode, 1)
        self.assertIn("not the same kind of unit", wrong.stderr)
        self.assertEqual(self.calls(), [])

    def test_courtesy_is_removed_for_direct_answers_and_kept_for_needle(self):
        polite = self.run_ask("Hey Kilix, could you tell me what 2 plus 2 is, please?",
                              ASK_TEST_MISSING="1")
        self.assertEqual((polite.returncode, polite.stdout.strip()), (0, "4"), polite.stderr)
        leap = self.run_ask("do you know if 2028 is a leap year?", ASK_TEST_MISSING="1")
        self.assertEqual((leap.returncode, leap.stdout.strip()), (0, "2028 is a leap year"))
        named = self.run_ask("what is kilix", ASK_TEST_MISSING="1")
        self.assertEqual(named.returncode, 0, named.stderr)
        self.assertIn("terminal", named.stdout)
        self.log.unlink(missing_ok=True)
        typed = "Could you close the other panes, please?"
        acted = self.run_ask("--yes", typed)
        self.assertEqual(acted.returncode, 0, acted.stderr)
        needle = [call["needle"] for call in self.calls() if "needle" in call]
        self.assertEqual(needle, [["ask", "--yes", "--", typed]])

    def test_new_answers_leave_commands_to_needle(self):
        for request in ("close pane 3", "open the calculator", "split right and run htop",
                        "pick up the focus bug session with omp, split right",
                        "rename all jpgs to lowercase", "list files in ~/projects/../secrets",
                        "what's in /etc/fstab", "is dictation-daemon-that-is-not-here running",
                        "start codex in /tmp", "set the wallpaper to red"):
            self.log.unlink(missing_ok=True)
            asked = self.run_ask("--yes", request)
            needle = [call["needle"] for call in self.calls() if "needle" in call]
            self.assertEqual(needle, [["ask", "--yes", "--", request]], (request, asked.stdout))

    def test_machine_facts_do_not_use_needle(self):
        supply = self.root / "power"
        for name, kind, status, capacity in (
            ("BAT0", "Battery", "Discharging", "72"),
            ("BAT1", "Battery", "Charging", "40"),
            ("AC", "Mains", "online", "100"),
        ):
            device = supply / name
            device.mkdir(parents=True)
            (device / "type").write_text(kind + "\n", encoding="utf-8")
            (device / "status").write_text(status + "\n", encoding="utf-8")
            (device / "capacity").write_text(capacity + "\n", encoding="utf-8")
        uptime = self.root / "uptime"
        uptime.write_text("90061.22 12.0\n", encoding="utf-8")
        version = self.root / "VERSION"
        version.write_text("0.2.3\n", encoding="utf-8")
        dpkg_log = self.root / "dpkg.log"
        dpkg = self.root / "dpkg-query"
        dpkg.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "open(os.environ['ASK_TEST_DPKG_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1:2] != ['-W'] or 'Status-Status' not in sys.argv[2] or len(sys.argv) != 4:\n"
            "    raise SystemExit(2)\n"
            "name = sys.argv[3]\n"
            "if name == 'bash':\n"
            "    print('installed\\t5.2.15-2')\n"
            "else:\n"
            "    raise SystemExit(1)\n",
            encoding="utf-8",
        )
        dpkg.chmod(0o755)
        ip_log = self.root / "ip.log"
        ip = self.root / "ip"
        ip.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "open(os.environ['ASK_TEST_IP_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1:] != ['-4', 'route', 'get', '192.0.2.1']:\n"
            "    raise SystemExit(2)\n"
            "print('192.0.2.1 via 192.0.2.2 dev wlan0 src 192.168.4.20 uid 1000')\n",
            encoding="utf-8",
        )
        ip.chmod(0o755)
        run_id = "20261106T120000.000000Z-0123456789abcdef0123456789abcdef"
        runs = self.root / "agent-runs"
        run_dir = runs / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "events.jsonl").write_text(
            "hello from the session\nsecond line\n", encoding="utf-8",
        )
        session_log = self.root / "session.log"
        session = self.root / "kilix-session"
        session.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "open(os.environ['ASK_TEST_SESSION_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "cmd = sys.argv[1:]\n"
            "if cmd == ['list', '--limit', '20']:\n"
            "    if os.environ.get('ASK_TEST_SESSION_MODE') == 'empty':\n"
            "        print(json.dumps({'runs': []}))\n"
            "    else:\n"
            "        print(json.dumps({'runs': [{'run_id': os.environ['ASK_TEST_RUN'],\n"
            "            'provider': 'grok', 'title': 'ask', 'effective_status': 'queued'}]}))\n"
            "elif cmd == ['launch', 'grok', '--new-tab']:\n"
            "    print(json.dumps({'run_id': os.environ['ASK_TEST_RUN'],\n"
            "        'run_dir': os.environ['ASK_TEST_RUN_DIR'],\n"
            "        'launch': {'pane': {'pane_id': 84}}}))\n"
            "else:\n"
            "    raise SystemExit(2)\n",
            encoding="utf-8",
        )
        session.chmod(0o755)
        env = {
            "ASK_TEST_MISSING": "1",
            "KILIX_BATTERY_SUPPLY_DIR": str(supply),
            "KILIX_UPTIME_FILE": str(uptime),
            "KILIX_VERSION_FILE": str(version),
            "KILIX_DPKG_QUERY": str(dpkg),
            "ASK_TEST_DPKG_LOG": str(dpkg_log),
            "KILIX_IP_BIN": str(ip),
            "ASK_TEST_IP_LOG": str(ip_log),
            "KILIX_SESSION_BIN": str(session),
            "ASK_TEST_SESSION_LOG": str(session_log),
            "ASK_TEST_RUN": run_id,
            "ASK_TEST_RUN_DIR": str(run_dir),
            "KILIX_AGENT_RUNS_HOME": str(runs),
        }
        battery = self.run_ask("what's the battery?", **env)
        self.assertEqual(battery.returncode, 0, battery.stderr)
        self.assertEqual(
            battery.stdout.strip(),
            "BAT0: 72% discharging\nBAT1: 40% charging",
        )
        up = self.run_ask("how long has the machine been up?", **env)
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertEqual(up.stdout.strip(), "1d 1h 1m")
        installed = self.run_ask("is the bash package installed?", **env)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(installed.stdout.strip(), "bash 5.2.15-2")
        absent = self.run_ask("is missingpkg installed?", **env)
        self.assertEqual(absent.stdout.strip(), "missingpkg is not installed")
        self.assertNotIn("install ", dpkg_log.read_text(encoding="utf-8"))
        address = self.run_ask("what's my ip address?", **env)
        self.assertEqual(address.returncode, 0, address.stderr)
        self.assertEqual(address.stdout.strip(), "192.168.4.20")
        self.assertEqual(
            ip_log.read_text(encoding="utf-8").strip(),
            "-4 route get 192.0.2.1",
        )
        public = self.run_ask("what's my public ip?", **env)
        self.assertEqual(public.returncode, 1, public.stdout)
        self.assertIn("not installed", public.stderr)
        self.assertEqual(ip_log.read_text(encoding="utf-8").count("route get"), 1)
        numbered = self.run_ask("what version is kilix?", **env)
        self.assertEqual(numbered.returncode, 0, numbered.stderr)
        self.assertEqual(numbered.stdout.strip(), "0.2.3")
        listed = self.run_ask("list sessions", **env)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(listed.stdout.strip(), f"{run_id} grok ask queued")
        empty = self.run_ask("what sessions are running?", **env, ASK_TEST_SESSION_MODE="empty")
        self.assertEqual(empty.stdout.strip(), "no sessions")
        read = self.run_ask(f"read session {run_id}", **env)
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertEqual(read.stdout.strip(), "hello from the session\nsecond line")
        log_path = self.run_ask(f"what's the log path for session {run_id}", **env)
        self.assertEqual(log_path.stdout.strip(), str(run_dir / "events.jsonl"))
        missing = self.run_ask(
            "read session 20261106T120001.000000Z-0123456789abcdef0123456789abcdef",
            **env,
        )
        self.assertEqual(missing.returncode, 1, missing.stdout)
        self.assertIn("no session", missing.stderr)
        launched = self.run_ask("launch a grok session", **env)
        self.assertEqual(launched.returncode, 0, launched.stderr)
        self.assertEqual(
            launched.stdout.strip(),
            f"run {run_id}\npane 84\nlog {run_dir / 'events.jsonl'}",
        )
        recorded = session_log.read_text(encoding="utf-8")
        self.assertIn("launch grok --new-tab", recorded)
        self.assertNotIn("foreground", recorded)
        self.assertNotIn("attach", recorded)
        self.assertNotIn("stop", recorded)
        help_result = self.run_ask("how do I launch a grok session?", **env)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Help search: list panes", help_result.stdout)
        self.assertEqual(recorded, session_log.read_text(encoding="utf-8"))
        detached = self.run_ask("detach the session", **env)
        self.assertEqual(detached.returncode, 1, detached.stdout)
        self.assertIn("not installed", detached.stderr)
        self.assertTrue(all("needle" not in call for call in self.calls()))

    def test_city_weather_hold_and_voice_use_the_same_readable_result_path(self):
        self.weather_provider()
        for error in ('', '1'):
            status, output = self.run_tty(['--hold', '--', 'weather in Tokyo'], b'\n',
                                          ASK_TEST_WEATHER_ERROR=error)
            self.assertEqual(status, 1 if error else 0, output)
            self.assertEqual(output.count('Press Enter to close.'), 1)
        self.dictation('printf "%s\\n" "weather in Paris"\n')
        status, output = self.run_tty(
            ['--voice'], [(b'Request (edit, then Enter): ', b'\x15weather in Tokyo in Celsius\n')])
        self.assertEqual(status, 0, output)
        self.assertIn('Tokyo, Japan: 20°C', output)
        self.assertTrue(all('weather' in call for call in self.calls()), self.calls())

    def test_weather_command_does_not_initialize_or_change_saved_widget_settings(self):
        self.weather_provider()
        settings = self.root / 'city-settings.conf'
        saved = '# keep this\nKILIX_WEATHER_ZIP=00501\nKILIX_WEATHER_REFRESH=manual\n'
        settings.write_text(saved)
        result = subprocess.run([str(self.launcher), 'weather', '--unit', 'celsius', '--', 'Tokyo'],
                                env={**self.env, 'GPU_TERMINAL_SETTINGS_FILE': str(settings)},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Tokyo, Japan: 20°C', result.stdout)
        self.assertEqual(settings.read_text(), saved)
        self.assertFalse((self.root / 'storage').exists())

    def run_tty(self, arguments, keys, **env):
        """Run with a terminal on stdin and stdout; returns (status, output).
        `keys` answers "Press Enter", or is [(prompt, keys), ...] in order; a
        callable answer is given the process instead of written."""
        answers = list(keys) if isinstance(keys, list) else [(b"Press Enter", keys)]
        master, slave = os.openpty()
        process = subprocess.Popen([str(self.launcher), "ask", *arguments], env={**self.env, **env},
                                   stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        os.close(slave)
        output, deadline = b"", time.monotonic() + 60
        seen = 0
        try:
            while time.monotonic() < deadline:
                ready, _w, _x = select.select([master], [], [], 0.2)
                if ready:
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                if answers and answers[0][0] in output[seen:]:
                    prompt, reply = answers.pop(0)
                    seen = output.index(prompt, seen) + len(prompt)
                    if callable(reply):
                        reply(process)
                    else:
                        os.write(master, reply)
                if process.poll() is not None and not ready:
                    break
            status = process.wait(timeout=10)
        finally:
            os.close(master)
            if process.poll() is None:
                process.kill()
        return status, output.decode(errors="replace")

    def test_a_request_runs_the_installed_app_interactively_and_never_installs(self):
        result = self.run_ask("close", "tab 2")
        self.assertEqual(result.returncode, 0, result.stderr)
        install, needle = self.calls()
        self.assertEqual(install, {"content_app": ["install", "kilix-needle"], "auto_install": "0"})
        self.assertEqual(needle["needle"], ["ask", "--interactive", "--", "close tab 2"])
        self.assertEqual(needle["app"], "kilix-needle")
        self.assertTrue(needle["root"].endswith("desktop-apps"), needle["root"])

    def test_the_apps_status_is_the_exit_status(self):
        self.assertEqual(self.run_ask("hide the clock", ASK_TEST_STATUS="1").returncode, 1)

    def test_model_selection_is_forwarded_without_changing_the_request(self):
        request = 'open notepad; keep "$VALUE" and `words` literal'
        for model in ('needle3', 'lfm2', 'lfm3', 'grammar'):
            for option in (('--model', model), ('--model=' + model,), ('--commands-backend', model)):
                with self.subTest(option=option):
                    result = self.run_ask(*option, '--', request, KILIX_ASK_COMMANDS_BACKEND='lfm2')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.calls()[-1]['needle'],
                                     ['ask', '--interactive', '--commands-backend', model, '--', request])

    def test_environment_preference_and_explicit_local_candidate_survive_the_launcher(self):
        candidate = '/a path/$literal;not-a-command/candidate.json'
        result = self.run_ask('--commands-candidate', candidate, 'open calculator',
                              KILIX_ASK_COMMANDS_BACKEND='needle3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1]['backend_env'], 'needle3')
        self.assertEqual(self.calls()[-1]['needle'],
                         ['ask', '--interactive', '--commands-candidate=' + candidate, '--', 'open calculator'])

    def test_invalid_model_options_never_contact_the_installed_app(self):
        for arguments in (('--model',), ('--model', 'unknown', 'open calculator'),
                          ('--model=', 'open calculator'), ('--commands-candidate',),
                          ('--commands-candidate=', 'open calculator')):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.run_ask(*arguments).returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_text_after_the_separator_is_the_request_even_when_it_looks_like_an_option(self):
        result = self.run_ask("--hold", "--", "--yes close tab 2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1]["needle"], ["ask", "--interactive", "--", "--yes close tab 2"])

    def test_yes_skips_the_interactive_prompt(self):
        result = self.run_ask("--yes", "close", "tab 2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1]["needle"], ["ask", "--yes", "--", "close tab 2"])

    def test_yes_keeps_an_explicit_model(self):
        result = self.run_ask("--model", "needle3", "--yes", "--", "hide the clock")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1]["needle"],
                         ["ask", "--yes", "--commands-backend", "needle3", "--", "hide the clock"])

    def test_yes_without_a_request_is_usage(self):
        result = self.run_ask("--yes")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.calls(), [])

    def test_yes_cannot_combine_with_voice(self):
        result = self.run_ask("--yes", "--voice")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("cannot be combined with --voice", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_help_names_yes(self):
        result = self.run_ask("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--yes", result.stdout)
        self.assertIn("--repl", result.stdout)
        self.assertIn("stay and ask again", result.stdout)
        self.assertIn("acts only on a typed yes", result.stdout)
        self.assertIn("unsure route still does not run", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_an_uninstalled_app_is_named_not_built(self):
        result = self.run_ask("split right", ASK_TEST_MISSING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("app install kilix-needle", result.stderr)
        self.assertEqual([call.get("needle") for call in self.calls()], [None])

    def test_usage_errors_exit_2_and_run_nothing(self):
        for arguments in ((), ("   ",), ("--verbose", "x"), ("--voice", "split right"),
                          ("--voice",), ("--repl",), ("--repl", "--yes")):
            with self.subTest(arguments):
                self.assertEqual(self.run_ask(*arguments).returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_help_describes_the_verb_and_the_launcher_help_lists_it(self):
        result = self.run_ask("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--voice", result.stdout)
        general = subprocess.run([str(self.launcher), "--help"], env=self.env,
                                 capture_output=True, text=True, timeout=30)
        self.assertIn("Also: ask 'REQUEST'", general.stdout)

    def test_help_names_the_saved_selection_as_the_default(self):
        # Needle resolves --model, then the environment, then the saved
        # selection; lfm2 is only the default when nothing is saved.
        result = self.run_ask("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("(default: lfm2)", result.stdout)
        self.assertNotIn("lfm2 (default)", result.stdout)
        self.assertIn("overrides KILIX_ASK_COMMANDS_BACKEND", result.stdout)
        self.assertIn("saved Ask selection (lfm2 when none is saved)", result.stdout)
        self.assertIn("kilix-needle workflows select --model lfm2|lfm3|needle3|grammar", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_hold_waits_for_enter_on_every_status(self):
        for code in ("0", "1"):
            with self.subTest(code):
                status, output = self.run_tty(["--hold", "--", "close tab 2"], b"\n",
                                              ASK_TEST_STATUS=code)
                self.assertEqual(status, int(code), output)
                self.assertIn("route: panes", output)
                self.assertEqual(output.count("Press Enter to close."), 1, output)

    def test_hold_waits_for_enter_on_usage_and_setup_errors_too(self):
        cases = ((["--hold", "--voice", "split right"], {}, 2),
                 (["--bogus", "--hold", "x"], {}, 2),
                 (["--hold", "--", "split right"], {"ASK_TEST_MISSING": "1"}, 1))
        for arguments, env, code in cases:
            with self.subTest(arguments):
                status, output = self.run_tty(arguments, b"\n", **env)
                self.assertEqual(status, code, output)
                self.assertEqual(output.count("Press Enter to close."), 1, output)
        self.assertEqual([call.get("needle") for call in self.calls()], [None])

    def test_hold_waits_for_enter_after_help_and_an_interrupt_too(self):
        for arguments in (["--hold", "--help"], ["--help", "--hold"]):
            with self.subTest(arguments):
                status, output = self.run_tty(arguments, b"\n")
                self.assertEqual(status, 0, output)
                self.assertIn("acts only on a typed yes", output)
                self.assertEqual(output.count("Press Enter to close."), 1, output)
        interrupt = lambda process: process.send_signal(signal.SIGINT)  # noqa: E731
        # Interrupted at the request prompt: held, then closed by Enter or by a
        # second interrupt, with the interrupt's status either way.
        for second in (b"\n", interrupt):
            with self.subTest(second):
                status, output = self.run_tty(["--hold"], [(b"Ask Kilix: ", interrupt),
                                                           (b"Press Enter", second)])
                self.assertEqual(status, 1, output)
                self.assertEqual(output.count("Press Enter to close."), 1, output)
        self.assertEqual(self.calls(), [])

    def test_only_0_1_and_2_come_back(self):
        for code, expected in (("2", 2), ("130", 1), ("9", 1)):
            with self.subTest(code):
                self.assertEqual(self.run_ask("close tab 2", ASK_TEST_STATUS=code).returncode, expected)

    def test_no_job_says_where_to_look_next_and_is_still_a_miss(self):
        miss = self.run_ask("who won the world cup in 1998", ASK_TEST_STATUS="3")
        self.assertEqual(miss.returncode, 1, miss.stderr)
        self.assertIn('kilix help-search "who won the world cup in 1998"', miss.stderr)
        self.assertIn('kilix ask "what can you do?"', miss.stderr)
        self.assertEqual(miss.stdout, "route: panes (stage 0)\n")  # Needle's own output, unchanged
        # Passages are not shown: the guides are not searched here.
        self.assertEqual([call for call in self.calls() if "help" in call
                          or "kilix-help-llm" in call.get("content_app", [])], [])
        self.assertEqual([call["needle"] for call in self.calls() if "needle" in call],
                         [["ask", "--interactive", "--", "who won the world cup in 1998"]])
        for text, shown in (('say "hi" to me', "kilix help-search 'say \"hi\" to me'"),
                            ("cost of $5 and it's \"cheap\"", 'kilix help-search "your question"')):
            with self.subTest(text):
                self.assertIn(shown, self.run_ask("--", text, ASK_TEST_STATUS="3").stderr)
        # Any other failure keeps Needle's own message and adds nothing.
        for code in ("1", "9"):
            with self.subTest(code):
                failed = self.run_ask("close tab 2", ASK_TEST_STATUS=code)
                self.assertEqual(failed.returncode, 1)
                self.assertNotIn("help-search", failed.stderr)

    def model_service(self):
        """A fake local Ollama holding the set-up model, and a sizer that says it fits."""
        ollama = FakeOllama()
        self.addCleanup(ollama.close)
        ollama.models["kilix/qwen3.5:4b"] = modelfile()
        return ollama, {"KILIX_ASK_OLLAMA_URL": ollama.url, "PLEBIAN_MODEL_SIZER": self.sizer}

    def test_a_question_no_job_does_gets_a_labelled_model_answer(self):
        ollama, env = self.model_service()
        answered = self.run_ask("who wrote Don Quixote?", ASK_TEST_STATUS="3", **env)
        self.assertEqual(answered.returncode, 0, answered.stderr)
        self.assertEqual(answered.stdout, "route: panes (stage 0)\n"
                         "Miguel de Cervantes wrote Don Quixote.\n"
                         "Model answer (qwen3.5:4b, on this computer). It can be wrong.\n")
        self.assertNotIn("help-search", answered.stderr)
        [chat] = ollama.posted("/api/chat")
        self.assertEqual(chat["messages"][-1]["content"], "who wrote Don Quixote?")
        # A bare "I don't know" is still a miss, with the usual pointers.
        ollama.reply = "I don't know."
        miss = self.run_ask("who wrote Don Quixote?", ASK_TEST_STATUS="3", **env)
        self.assertEqual(miss.returncode, 1)
        self.assertIn("the local model does not know", miss.stderr)
        self.assertIn('kilix help-search "who wrote Don Quixote?"', miss.stderr)
        # A failing service is an error, and the pointers are not added to it.
        ollama.chat_status = 500
        failed = self.run_ask("who wrote Don Quixote?", ASK_TEST_STATUS="3", **env)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("the local model failed (HTTP 500)", failed.stderr)
        self.assertNotIn("help-search", failed.stderr)

    def test_the_model_is_asked_only_after_needle_finds_no_job(self):
        ollama, env = self.model_service()
        for arguments, status in ((["what time is it?"], "0"), (["close tab 2"], "0"),
                                  (["close tab 2"], "1"), (["--yes", "close tab 2"], "2")):
            with self.subTest(arguments=arguments, status=status):
                self.run_ask(*arguments, ASK_TEST_STATUS=status, **env)
        self.assertEqual(ollama.posted("/api/chat"), [])

    def test_model_answers_can_be_skipped_once_or_turned_off(self):
        ollama, env = self.model_service()
        once = self.run_ask("--no-model-answers", "who wrote Don Quixote?", ASK_TEST_STATUS="3", **env)
        self.assertEqual(once.returncode, 1)
        self.assertIn("help-search", once.stderr)
        self.assertEqual(ollama.requests, [])
        off = self.run_ask("--model-answers", "off", **env)
        self.assertEqual((off.returncode, off.stdout), (0, "Model answers are off.\n"))
        self.assertEqual(self.run_ask("who wrote Don Quixote?", ASK_TEST_STATUS="3", **env).returncode, 1)
        self.assertEqual(ollama.requests, [])
        self.assertIn("Model answers: off", self.run_ask("--model-answers=status", **env).stdout)
        self.assertEqual(self.run_ask("--model-answers", "on", **env).returncode, 0)
        self.assertEqual(self.run_ask("who wrote Don Quixote?", ASK_TEST_STATUS="3", **env).returncode, 0)
        bad = self.run_ask("--model-answers", "maybe", **env)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("must be on, off, setup or status", bad.stderr)

    def test_without_a_fitting_model_the_miss_is_unchanged(self):
        # The default test environment has no sizer: nothing about models is said.
        miss = self.run_ask("who wrote Don Quixote?", ASK_TEST_STATUS="3")
        self.assertEqual(miss.returncode, 1)
        self.assertNotIn("model", miss.stderr)
        self.assertIn("help-search", miss.stderr)

    def dictation(self, script):
        """The copied launcher with its `kilix dictate` call replaced by `script`.

        The call must discard on interrupt: Ctrl-C cancels the request, so the
        utterance is aborted rather than decoded for nothing.
        """
        launch = self.launcher.read_text()
        call = '$("$_self" dictate --discard-on-interrupt)'
        self.assertEqual(launch.count(call), 1)
        dictate = self.root / "dictate"
        dictate.write_text("#!/bin/sh\n" + script)
        dictate.chmod(0o755)
        self.launcher.write_text(launch.replace(call, f'$("{dictate}")'))

    def test_failed_dictation_stops_even_with_a_partial_transcript(self):
        self.dictation('printf "%s\\n" "close tab"\nexit 9\n')
        status, output = self.run_tty(["--voice", "--hold"], b"\n")
        self.assertEqual(status, 1, output)
        self.assertIn("nothing was transcribed", output)
        self.assertNotIn("Request (edit, then Enter)", output)
        self.assertEqual(self.calls(), [])

    def test_a_dictated_request_can_be_corrected_and_goes_as_one_argument(self):
        self.dictation('printf "%s\\n" "close tab 2"\n')
        status, output = self.run_tty(
            ["--voice"], [(b"Request (edit, then Enter): ", b"\x15split right; rm -rf ~\n")])
        self.assertEqual(status, 0, output)
        self.assertEqual(self.calls()[-1]["needle"],
                         ["ask", "--interactive", "--", "split right; rm -rf ~"])

    def test_voice_without_a_transcript_names_the_setup_and_runs_nothing(self):
        status, output = self.run_tty(["--voice", "--hold"], b"\n")
        self.assertEqual(status, 1, output)
        self.assertIn("nothing was transcribed", output)
        self.assertIn("voice doctor", output)
        self.assertEqual(self.calls(), [])

    def test_repl_voice_leaves_quietly_after_an_answer(self):
        count = self.root / "dictate-count"
        self.dictation(
            f'n=$(cat "{count}" 2>/dev/null || echo 0)\n'
            'n=$((n + 1))\n'
            f'printf "%s\\n" "$n" > "{count}"\n'
            'if [ "$n" = 1 ]; then printf "%s\\n" "what is three times five?"; exit 0; fi\n'
            'exit 1\n'
        )
        status, output = self.run_tty(
            ["--repl", "--voice"],
            [(b"Request (edit, then Enter): ", b"\n")],
            ASK_TEST_MISSING="1")
        self.assertEqual(status, 0, output)
        self.assertIn("15", output)
        self.assertNotIn("voice doctor", output)
        self.assertNotIn("nothing was transcribed", output)

    def test_repl_voice_with_nothing_on_the_first_turn_names_the_setup(self):
        self.dictation("exit 1\n")
        status, output = self.run_tty(["--repl", "--voice", "--hold"], b"\n")
        self.assertEqual(status, 1, output)
        self.assertIn("nothing was transcribed", output)
        self.assertIn("voice doctor", output)
        self.assertEqual(self.calls(), [])

    def test_repl_voice_confirm_eof_leaves(self):
        self.dictation('printf "%s\\n" "what is three times five?"\n')
        status, output = self.run_tty(
            ["--repl", "--voice"],
            [(b"Request (edit, then Enter): ", b"\x15\x04")],
            ASK_TEST_MISSING="1")
        self.assertEqual(status, 0, output)
        self.assertNotIn("15", output)
        self.assertEqual(self.calls(), [])

    def test_set_an_alarm_launches_kilix_alarm(self):
        missing = self.run_ask("set an alarm", ASK_TEST_MISSING="1")
        self.assertEqual(missing.returncode, 1, missing.stdout)
        self.assertIn("alarm is not installed", missing.stderr)
        log = self.root / "alarm-log"
        binary = self.root / "kilix-alarm"
        binary.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['ASK_TEST_ALARM_LOG'], 'a') as stream:\n"
            "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "print('alarm')\n"
        )
        binary.chmod(0o755)
        env = {
            "ASK_TEST_MISSING": "1",
            "KILIX_ALARM_BIN": str(binary),
            "ASK_TEST_ALARM_LOG": str(log),
        }
        opened = self.run_ask("set an alarm", **env)
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.assertEqual(opened.stdout.strip(), "alarm")
        timed = self.run_ask("set an alarm for 7:30pm", **env)
        self.assertEqual(timed.returncode, 0, timed.stderr)
        soon = self.run_ask("set an alarm in 5 minutes with a loud beep", **env)
        self.assertEqual(soon.returncode, 0, soon.stderr)
        hum = self.run_ask("please set an alarm for 7am with a quiet hum", **env)
        self.assertEqual(hum.returncode, 0, hum.stderr)
        lines = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(lines[0], [])
        self.assertEqual(lines[1], ["--at", "19:30"])
        self.assertEqual(lines[2], ["--in", "300", "--style", "alert"])
        self.assertEqual(lines[3], ["--at", "07:00", "--style", "hum"])
        invalid = self.run_ask("set an alarm for 25:99", **env)
        self.assertEqual(invalid.returncode, 1, invalid.stdout)
        self.assertIn("not valid", invalid.stderr)
        help_result = self.run_ask("how do I set an alarm?", ASK_TEST_MISSING="1")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Help search: list panes", help_result.stdout)
        other = self.run_ask("set an alarm and make coffee", ASK_TEST_MISSING="1")
        self.assertEqual(other.returncode, 1, other.stdout)
        self.assertIn("not installed", other.stderr)
        self.assertEqual(len(lines), 4)


if __name__ == "__main__":
    unittest.main()
