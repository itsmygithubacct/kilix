"""The direct Ask answers added beside weather, sums, and machine facts.

Each module answers a closed family and returns 3 for anything else, so a
request that changes something still reaches Needle.
"""
import datetime
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import kilix_about  # noqa: E402
import kilix_asktext  # noqa: E402
import kilix_machine  # noqa: E402
import kilix_self  # noqa: E402
import kilix_tools  # noqa: E402
import kilix_when  # noqa: E402

MODULES = (kilix_when, kilix_self, kilix_machine, kilix_tools, kilix_about)
# Tuesday 2026-11-10 09:30 in a fixed zone eight hours behind UTC.
NOW = datetime.datetime(2026, 11, 10, 9, 30, 0,
                        tzinfo=datetime.timezone(datetime.timedelta(hours=-8), "PST"))

# Requests that act, or that another job owns. None of the new modules may answer one.
NOT_OURS = (
    "close pane 3", "close the other panes", "split right and run htop", "open the calculator",
    "open the file manager", "start codex in /tmp", "launch a grok session",
    "set the volume to 30", "mute", "set the brightness to 40", "set an alarm in 10 minutes",
    "install kilix-amp", "rename all jpgs to lowercase", "find pdf files in Downloads",
    "write me a bash one-liner to rename all jpgs to lowercase",
    "Pick up the focus bug session with omp in kilix 95, split right",
    "Pick back up codex session aa41907e in the catalog, split right, and ask it to finish.",
    "list files in ~/projects/../secrets", "what's in /etc/fstab", "how big is /etc",
    "how big is ~/../root", "what's in ~/./../etc", "close the tab", "open a new tab",
    "set the shortcut for a new tab to ctrl+t", "start an agent", "install playalong",
    "delete the newest file in downloads", "kill the process using the most memory",
    "restart ssh", "stop docker", "is nginx running? and show its errors from today",
    "is a-program-that-is-not-installed running", "what's the weather in Paris",
    "when is the meeting", "what time is it in narnia", "100 usd in eur",
    "convert report.pdf to text", "hide the clock", "send hello to pane 2",
    "what does this error mean", "who wrote hamlet", "reboot", "shut down the computer",
    "connect to the wifi", "turn bluetooth off", "empty the trash", "",
    "select pane 2 and pane 3", "choose the dark theme and close the settings",
)


class Answers(unittest.TestCase):
    def ask(self, module, text):
        return module.answer(text)

    def first(self, text):
        for module in MODULES:
            status, out = module.answer(text)
            if status != 3:
                return module.__name__, status, out
        return None, 3, ""

    def test_nothing_new_answers_a_command(self):
        for request in NOT_OURS:
            self.assertEqual(self.first(request), (None, 3, ""), request)

    def test_courtesy_is_removed_and_the_request_is_kept(self):
        cases = {
            "Hey Kilix, could you tell me what time it is please?": "what time is it",
            "ok kilix what is two plus two": "what is two plus two",
            "please set the volume to 30, thanks": "set the volume to 30",
            "do you know what the weather in Paris is": "what is the weather in Paris",
            "I was wondering what the date is": "what is the date",
            "do you know if 2028 is a leap year?": "is 2028 a leap year",
            "can you tell me if ssh is running": "is ssh running",
            "what is kilix": "what is kilix",
            "reverse the word kilix": "reverse the word kilix",
            "tell me a joke": "tell me a joke",
            "show me the panes": "show me the panes",
            "close the other panes, kilix": "close the other panes",
            "please": "please",
        }
        for typed, plain in cases.items():
            self.assertEqual(kilix_asktext.clean(typed), plain, typed)

    def test_dates_and_clocks(self):
        cases = {
            "what day is it": "Tuesday, 2026-11-10",
            "what's the date today?": "Tuesday, 2026-11-10",
            "what year is it": "2026",
            "what month is it": "November 2026",
            "what week number is it": "week 46 of 2026",
            "what's tomorrow's date": "Wednesday, 2026-11-11",
            "what was the date 10 days ago": "Saturday, 2026-10-31",
            "what is the date in 3 weeks from now": "Tuesday, 2026-12-01",
            "what's the date in 45 days": "Friday, 2026-12-25",
            "how many days until christmas": "45 days (6 weeks and 3 days) until Friday, 2026-12-25",
            "how long until new year's": "52 days (7 weeks and 3 days) until Friday, 2027-01-01",
            "when is thanksgiving": "Thursday, 2026-11-26, in 16 days (2 weeks and 2 days)",
            "how many days since january 1": "313 days (44 weeks and 5 days) since Thursday, 2026-01-01",
            "how many days between march 1 2027 and april 15 2027":
                "45 days (6 weeks and 3 days) from Monday, 2027-03-01 to Thursday, 2027-04-15",
            "what day of the week is july 4 2027": "Sunday, 2027-07-04",
            "what day was july 4 1776": "Thursday, 1776-07-04",
            "what day is friday": "Friday, 2026-11-13",
            "is 2028 a leap year": "2028 is a leap year",
            "is this year a leap year": "2026 is not a leap year",
            "how many days in february": "28 days in February 2026",
            "what time is it": "2026-11-10 09:30:00 PST",
        }
        with mock.patch.object(kilix_when, "_now", return_value=NOW):
            for question, expected in cases.items():
                self.assertEqual(kilix_when.answer(question), (0, expected), question)
            status, shown = kilix_when.answer("calendar for february 2028")
            self.assertEqual(status, 0)
            self.assertIn("February 2028", shown)
            self.assertIn("29", shown)
            with self.assertRaises(kilix_when.WhenError):
                kilix_when.answer("what day of the week is february 30")

    def test_other_places(self):
        with mock.patch.object(kilix_when, "_now", return_value=NOW):
            for question, zone in (("what time is it in Tokyo", "Asia/Tokyo"),
                                   ("tokyo time", "Asia/Tokyo"),
                                   ("what time is it in new york", "America/New_York"),
                                   ("time in india", "Asia/Kolkata"),
                                   ("what's the time in UTC", "UTC")):
                status, shown = kilix_when.answer(question)
                self.assertEqual(status, 0, question)
                self.assertIn(f" in {zone} (UTC", shown, question)

    def test_worked_sums_and_units(self):
        cases = {
            "square root of 144": "12", "what is the square root of 2": "1.414213562",
            "10 factorial": "3628800", "2 to the power of 10": "1024", "what is 3 squared": "9",
            "half of 90": "45", "what is a third of 99": "33", "what is 17 mod 5": "2",
            "what is pi times 2": "6.283185307", "sin of 30 degrees": "sin(30°) = 0.5",
            "log of 100": "2", "average of 4, 8 and 15": "9", "round 3.14159 to 2 places": "3.14",
            "what is 1e6 / 3": "333333.3333", "is 97 prime": "yes, 97 is prime",
            "prime factors of 360": "2 × 2 × 2 × 3 × 3 × 5", "gcd of 12 and 18": "6",
            "what's 0x1f in decimal": "31", "255 in hex": "0xff", "what is 255 in binary": "0b11111111",
            "1994 in roman numerals": "MCMXCIV", "25% off 80": "60",
            "what percent of 200 is 50": "25%", "what is 20% tip on 45 dollars": "9.00 tip at 20%, 54.00 total",
            "convert 5 miles to km": "8.04672 km", "how many feet in a mile": "5280 ft",
            "how many ounces in a pound": "16 oz", "how many seconds in a day": "86400 s",
            "10 kg in pounds": "22.046226 lb", "how many cups in a gallon": "16 cups",
            "how many minutes in a week": "10080 min", "3 hours 20 minutes in seconds": "12000 s",
            "60 mph in km/h": "96.56064 km/h", "what is 5 feet 10 inches in cm": "177.8 cm",
            "300 kelvin in fahrenheit": "80.33 °F", "how many ounces are in a liter": "33.814023 fl oz",
        }
        for question, expected in cases.items():
            self.assertEqual(kilix_tools.answer(question), (0, expected), question)
        for question in ("5 kg in miles", "square root of -4", "2000 factorial", "log of 0"):
            with self.assertRaises(kilix_tools.ToolError, msg=question):
                kilix_tools.answer(question)

    def test_text_codes_and_chance(self):
        cases = {
            "how many words in 'the quick brown fox'": "4", "how many characters in hello": "5",
            "base64 encode hello": "aGVsbG8=", "base64 decode aGVsbG8=": "hello",
            "md5 of hello": "5d41402abc4b2a76b9719d911017c592",
            "reverse the word kilix": "xilik", "uppercase hello world": "HELLO WORLD",
            "how do you spell necessary": "N-E-C-E-S-S-A-R-Y",
            "what is the ascii code for A": "65 (0x41, U+0041)",
            "what is exit code 127": "exit 127: command not found",
            "what is signal 9": "SIGKILL (9): killed at once; it cannot be caught or ignored",
            "what does HTTP 404 mean": "HTTP 404 Not Found: Nothing matches the given URI",
            "what is ENOENT": "ENOENT (2): No such file or directory",
            "what port does ssh use": "ssh: 22/tcp",
            "what is the hex color for red": "#ff0000, rgb(255, 0, 0)",
        }
        for question, expected in cases.items():
            self.assertEqual(kilix_tools.answer(question), (0, expected), question)
        status, shown = kilix_tools.answer("what does chmod 755 mean")
        self.assertEqual((status, shown.splitlines()[0]), (0, "rwxr-xr-x"))
        for _ in range(40):
            self.assertIn(kilix_tools.answer("flip a coin")[1], ("heads", "tails"))
            self.assertIn(int(kilix_tools.answer("roll a die")[1]), range(1, 7))
            self.assertIn(int(kilix_tools.answer("pick a number between 3 and 5")[1]), (3, 4, 5))
            self.assertIn(kilix_tools.answer("pick pizza or tacos")[1], ("pizza", "tacos"))
            total = int(kilix_tools.answer("roll 2d6")[1].split()[0])
            self.assertIn(total, range(2, 13))
        password = kilix_tools.answer("generate a password")[1]
        self.assertEqual(len(password), 20)
        self.assertNotEqual(password, kilix_tools.answer("generate a password")[1])
        self.assertEqual(len(kilix_tools.answer("give me a uuid")[1]), 36)

    def test_this_computer(self):
        self.assertEqual(kilix_machine.answer("what's my hostname"), (0, socket.gethostname()))
        self.assertEqual(kilix_machine.answer("what directory am i in"), (0, os.getcwd()))
        self.assertEqual(kilix_machine.answer("where is my home folder"), (0, str(Path.home())))
        for question in ("what kernel am i running", "what os is this", "what cpu do i have",
                         "how many cores do i have", "how much ram do i have",
                         "how many processes are running", "when did i last reboot",
                         "what's using the most memory", "how many files are in this folder"):
            status, shown = kilix_machine.answer(question)
            self.assertEqual(status, 0, question)
            self.assertTrue(shown.strip(), question)
        # A process answer names the program, never its arguments.
        _status, shown = kilix_machine.answer("what's using the most cpu")
        for line in shown.splitlines():
            self.assertRegex(line, r"^.{1,40} \(pid \d+\): [\d.]+% of one core$")
        with self.assertRaises(kilix_machine.MachineError):
            kilix_machine.answer("is port 70000 open")

    def test_typed_folders_under_home(self):
        home = Path.home()
        with mock.patch.object(kilix_machine, "_run", return_value="2048\t/x\n"), \
                mock.patch.object(Path, "exists", return_value=True):
            self.assertEqual(kilix_machine.answer("how big is ~/Downloads"), (0, f"2.0 KiB in {home / 'Downloads'}"))
            self.assertEqual(kilix_machine.answer("how big is ~/a b/c.d/"), (0, f"2.0 KiB in {home / 'a b/c.d'}"))
            self.assertEqual(kilix_machine.answer("how big is ~"), (0, f"2.0 KiB in {home}"))
        for request in ("how big is ~/../root", "how big is ~/a/../../etc", "what's in ~/./../etc"):
            self.assertEqual(kilix_machine.answer(request), (3, ""), request)

    def test_a_slow_folder_size_says_so_and_names_the_command(self):
        too_slow = kilix_machine.MachineError("du took too long")
        with mock.patch.object(kilix_machine, "_run", side_effect=too_slow):
            status, shown = kilix_machine.answer("how big is my home folder")
        self.assertEqual(status, 0)
        self.assertIn("too big to size within 10 seconds", shown)
        self.assertIn(f"du -sh {Path.home()}", shown)

    def test_about_ask(self):
        for question in ("what can you do?", "help", "what should i ask you", "?"):
            status, shown = kilix_about.answer(question)
            self.assertEqual(status, 0, question)
            self.assertIn("how many days until Christmas", shown)
            self.assertLessEqual(max(map(len, shown.splitlines())), 80)
        self.assertIn("terminal", kilix_about.answer("what is kilix")[1])
        self.assertEqual(kilix_about.answer("thanks")[1], "You're welcome.")
        self.assertEqual(kilix_about.answer("tell me a joke")[0], 0)
        self.assertEqual(kilix_about.answer("help me close the pane")[0], 3)


if __name__ == "__main__":
    unittest.main()
