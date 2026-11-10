"""Direct Ask answers about Ask itself: what it can do, and small talk.

A request that is not entirely one of them returns 3 so Ask can keep its
normal route. Nothing here runs a command or reads the machine.
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kilix_asktext import clean  # noqa: E402

NOT_ABOUT = 3

CAPABILITIES = """\
Ask answers these directly, with no model and nothing to confirm:

  time and dates   what time is it in Tokyo? / how many days until Christmas?
                   what day is July 4 2027? / show me a calendar
  sums             what is 15% of 80? / square root of 144 / 20% tip on 45
                   is 97 prime? / 255 in hex / average of 4, 8 and 15
  units            5 miles in km / how many cups in a gallon? / 72 f in c
                   5 feet 10 inches in cm / 3 hours 20 minutes in seconds
  this computer    what cpu do I have? / how much memory is free?
                   what's using the most memory? / which disks are mounted?
                   is ssh running? / what's listening on port 8080?
  files            what's in this folder? / how big is ~/Downloads?
                   what's the newest file in downloads?
  network          what's my ip? / what wifi am I on? / am I on a vpn?
  hardware         what's the temperature? / what's the battery?
                   what's the volume? / what's the brightness?
  weather          what's the weather? / will it rain tomorrow in Paris?
                   weather this weekend / when is sunset?
  text and codes   base64 encode hello / how many words in "..."?
                   what does chmod 755 mean? / what is exit code 137?
  chance           flip a coin / roll 2d6 / pick a number between 1 and 10
                   generate a password / give me a uuid
  Kilix            what panes are open? / list sessions / how do I split a pane?

These change something, and say so: set the volume to 30, set the
brightness to 40, set an alarm in 10 minutes.

Anything else goes to Needle, which shows a plan and acts only on a typed
yes: close the other panes, open the file manager, start a codex agent.

kilix ask --repl stays open for more questions. kilix ask --voice listens."""

ABOUT_KILIX = """\
Kilix is the terminal this is running in: a GPU-drawn terminal with panes,
tabs, sessions, and apps, and the desktop shell of Plebian-OS. Ask is its
plain-language front door. Try: kilix ask "what can you do?\""""

ABOUT_ASK = """\
I am Kilix Ask. I answer questions about time, sums, units, and this
computer directly, and hand anything that would change something to
Needle, which shows you its plan first. Try: what can you do?"""

_JOKES = (
    "There are 10 kinds of people: those who read binary and those who don't.",
    "A SQL query walks into a bar, sees two tables, and asks: may I join you?",
    "Why do programmers mix up Halloween and Christmas? Because OCT 31 = DEC 25.",
    "I would tell you a UDP joke, but you might not get it.",
    "There are two hard problems in computing: cache invalidation, naming things, and off-by-one errors.",
    "A programmer's partner says: get a loaf of bread, and if they have eggs, get a dozen. "
    "They come home with twelve loaves.",
    "To understand recursion, you must first understand recursion.",
    "The cloud is just someone else's computer, having someone else's bad day.",
    "It works on my machine. So we shipped the machine.",
    "How many programmers does it take to change a light bulb? None. That is a hardware problem.",
)

_HELP = re.compile(
    r"help|\?|what can (?:you|i|kilix|ask|it) (?:do|ask|say|answer)(?: for me| here)?"
    r"|what (?:do|can) you (?:know|understand|answer|help with)|what (?:should|could|can) i ask(?: you)?"
    r"|what are (?:you|your) (?:capable of|capabilities|features|skills|commands)"
    r"|(?:show|list|give me)(?: me)?(?: the| some)? (?:examples|commands|capabilities|features|things you can do)"
    r"|how (?:do i|does (?:this|ask|kilix ask)|do you) (?:use (?:this|you|ask|kilix ask)|work)"
    r"|what (?:questions|things) can (?:i ask|you answer)|examples",
    re.IGNORECASE,
)
_WHO = re.compile(
    r"who are you|what are you|what(?:'s| is) your name|who is this|what is this"
    r"|are you (?:an? )?(?:ai|robot|bot|human|person|real|llm|model)|what is (?:kilix )?ask",
    re.IGNORECASE,
)
_KILIX = re.compile(r"what(?:'s| is) kilix|tell me about kilix|what does kilix do", re.IGNORECASE)
_HELLO = re.compile(
    r"(?:hello|hi|hey|howdy|yo|greetings|hiya)(?: there| kilix| ask)?"
    r"|good (?P<part>morning|afternoon|evening|night|day)(?: kilix)?"
    r"|are you (?:there|awake|listening|alive|working|ok|okay)|ping|you there",
    re.IGNORECASE,
)
_THANKS = re.compile(
    r"(?:thanks|thank you|thx|ty|cheers|much appreciated)(?: (?:a lot|so much|very much|kilix|ask))?"
    r"|(?:good|nice|great) (?:job|work|one)|well done",
    re.IGNORECASE,
)
_HOW = re.compile(r"how are you(?: doing| today)?|how(?:'s| is) it going|what(?:'s| is) up", re.IGNORECASE)
_JOKE = re.compile(
    r"(?:tell|give|say)(?: me)? (?:a|another|one more) (?:joke|pun|funny)|(?:a |another )?joke"
    r"|make me laugh|say something funny|do you know (?:a|any) jokes?",
    re.IGNORECASE,
)
_BYE = re.compile(r"(?:good ?bye|bye|see you|later|good night|goodnight)(?: kilix)?", re.IGNORECASE)


def answer(text: str) -> tuple[int, str]:
    """Return a status and the text to print. Status 3 is not about Ask."""
    raw = " ".join(text.split())
    if raw == "?":
        return 0, CAPABILITIES
    text = clean(text)
    if not text:
        return NOT_ABOUT, ""
    if _HELP.fullmatch(text):
        return 0, CAPABILITIES
    if _KILIX.fullmatch(text):
        return 0, ABOUT_KILIX
    if _WHO.fullmatch(text):
        return 0, ABOUT_ASK
    hello = _HELLO.fullmatch(text)
    if hello:
        if hello["part"] and hello["part"] != "day":
            return 0, f"Good {hello['part'].lower()}. Ask me something, or try: what can you do?"
        return 0, "Hello. Ask me something, or try: what can you do?"
    if _THANKS.fullmatch(text):
        return 0, "You're welcome."
    if _HOW.fullmatch(text):
        return 0, "Running fine. Ask me something, or try: what can you do?"
    if _BYE.fullmatch(text):
        return 0, "Goodbye."
    if _JOKE.fullmatch(text):
        return 0, secrets.choice(_JOKES)
    return NOT_ABOUT, ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["--ask"]:
        print("usage: kilix_about.py --ask TEXT", file=sys.stderr)
        return 2
    status, text = answer(" ".join(argv[1:]))
    if text:
        print(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
