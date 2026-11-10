"""Model answers for Ask: a question nothing else in Ask answers.

Ask runs this last, after Needle has said no job does the request. It asks
the local Ollama service, and only with a model measured against Ask's
general-knowledge set. The model sizer must say the model fits this computer.
Nothing here downloads, runs a command the reply names, or answers without
saying the answer came from a model.

Setup registers the Kilix content asset with Ollama. The asset is installed,
with its licence agreement, by `kilix models install`; setup only checks that
install with `kilix models verify` and uploads the verified blobs.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NOT_USED = 3
OLLAMA = "http://127.0.0.1:11434"
URL_OVERRIDE = "KILIX_ASK_OLLAMA_URL"  # loopback only; tests point it at a fake service
SETTING = "ask-model-answers.json"
SETTING_SCHEMA = "kilix.ask.model-answers/v1"
ANSWER_TIMEOUT = 180  # a cold CPU load of 3.4 GB plus the reply
CHECK_TIMEOUT = 3

# The prompt and sampler measured on 2026-11-10; changing either needs a new
# measurement against the frozen set before it ships.
SYSTEM = ("You answer general-knowledge questions in a terminal. Answer in one or two short sentences. "
          "If you are not sure of the answer, or the question cannot be answered, reply exactly: I don't know.")
OPTIONS = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.5,
           "repeat_penalty": 1.0, "num_predict": 128, "num_ctx": 4096}


@dataclass(frozen=True)
class Layer:
    media: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Admitted:
    name: str        # the Ollama name setup creates; its own, so a user's pull is never replaced
    display: str     # the name answers are labelled with
    asset: str       # the Kilix content asset that holds the blobs
    version: str     # the asset version `kilix models verify` must report
    weights: Layer   # the GGUF; its digest is the identity Ask checks
    license: Layer
    params: Layer
    renderer: str
    requires: str
    parameters: int

    @property
    def model_bytes(self) -> int:
        return self.weights.size + self.license.size + self.params.size


# Measured and admitted models, best first. Our own weights go first once one
# passes the bar; none has yet.
ADMITTED = (
    Admitted(
        name="kilix/qwen3.5:4b", display="qwen3.5:4b", asset="qwen3.5-4b",
        version="2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd",
        weights=Layer("application/vnd.ollama.image.model",
                      "81fb60c7daa80fc1123380b98970b320ae233409f0f71a72ed7b9b0d62f40490", 3389971840),
        license=Layer("application/vnd.ollama.image.license",
                      "7339fa418c9ad3e8e12e74ad0fd26a9cc4be8703f9c110728a992b193be85cb2", 11355),
        params=Layer("application/vnd.ollama.image.params",
                     "9371364b27a52acac9d87f88bd93c9db1174d8d6ec57f6888925cdc1788871ff", 65),
        renderer="qwen3.5", requires="0.17.1", parameters=4_700_000_000,
    ),
)


class ModelError(RuntimeError):
    """A failure to report as one line."""


def service_url() -> str:
    value = os.environ.get(URL_OVERRIDE, "")
    if re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):[0-9]{1,5}", value):
        return value
    return OLLAMA


def _opener():
    # Ollama is on loopback; a configured proxy must never see the request.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(url: str, path: str, body=None, *, method: str | None = None,
             timeout: float = CHECK_TIMEOUT, limit: int = 1 << 20):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    with _opener().open(request, timeout=timeout) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ModelError("the local model service sent too much data")
    return json.loads(payload) if payload else {}


# The saved setting. Model answers are on unless the user turned them off.

def _setting_path() -> Path:
    from kilix_sdk.paths import config_dir
    return Path(config_dir()) / SETTING


def enabled() -> bool:
    try:
        data = json.loads(_setting_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return not (isinstance(data, dict) and data.get("schema") == SETTING_SCHEMA
                and data.get("enabled") is False)


def set_enabled(value: bool) -> None:
    path = _setting_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"schema": SETTING_SCHEMA, "enabled": value}) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


# What the local service holds.

def registered(url: str, model: Admitted) -> bool:
    """True when Ollama holds `model.name` built from exactly the measured weights,
    with the chat renderer and reply parser it was measured with."""
    try:
        tags = _request(url, "/api/tags")
        names = {row.get("name") for row in tags.get("models", []) if isinstance(row, dict)}
        if model.name not in names:
            return False
        shown = _request(url, "/api/show", {"model": model.name})
    except (OSError, ValueError, http.client.HTTPException):
        return False
    modelfile = shown.get("modelfile") if isinstance(shown, dict) else None
    if not isinstance(modelfile, str):
        return False
    sources = re.findall(r"^FROM\s+(\S+)\s*$", modelfile, re.MULTILINE)
    lines = set(modelfile.splitlines())
    return (len(sources) == 1 and sources[0].endswith("sha256-" + model.weights.sha256)
            and f"RENDERER {model.renderer}" in lines and f"PARSER {model.renderer}" in lines)


def service_up(url: str) -> bool:
    try:
        return isinstance(_request(url, "/api/version").get("version"), str)
    except (OSError, ValueError, http.client.HTTPException, AttributeError):
        return False


def fits(models: list[Admitted]) -> Admitted | None:
    """The model the sizer picks among `models`, or None when none fits."""
    if not models:
        return None
    import system_llm
    rows = [{"id": m.name, "installed_bytes": m.model_bytes, "parameters": m.parameters} for m in models]
    _report, chosen, _note = system_llm.recommend(rows)
    return next((m for m in models if m.name == chosen), None)


def choose(url: str) -> tuple[Admitted | None, str]:
    """The model to answer with, or None and a one-line reason (empty to say nothing).

    A computer the sizer says no admitted model fits hears nothing about models.
    """
    if not enabled():
        return None, ""
    best = fits(list(ADMITTED))
    if best is None:
        return None, ""
    if not service_up(url):
        return None, "no model answer: the local model service (Ollama) is not running"
    present = [m for m in ADMITTED if registered(url, m)]
    if not present:
        size = best.model_bytes / 1024 ** 3
        return None, (f"a local model can answer questions like this ({best.display}, {size:.1f} GiB):"
                      " kilix ask --model-answers setup")
    chosen = fits(present)
    if chosen is None:
        return None, "no model answer: the model sizer says the local model does not fit in free memory now"
    return chosen, ""


# Answering.

_BOLD = re.compile(r"\*\*(\S(?:.*?\S)?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(\S(?:[^*\n]*?\S)?)\*(?![\w*])")


def plain(text: str) -> str:
    """Markdown emphasis does not render in a terminal; keep the words."""
    return _ITALIC.sub(r"\1", _BOLD.sub(r"\1", text)).strip()


def declined(text: str) -> bool:
    return re.fullmatch(r"i (?:don'?t|do not) know\.?", text.strip().casefold()) is not None


def reply(question: str, model: Admitted, url: str, *, seed: int | None = None) -> str:
    options = dict(OPTIONS)
    if seed is not None:
        options["seed"] = seed  # measurements only; Ask sends no seed
    body = {"model": model.name, "stream": False, "think": False, "options": options,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]}
    try:
        data = _request(url, "/api/chat", body, timeout=ANSWER_TIMEOUT)
    except urllib.error.HTTPError as error:
        raise ModelError(f"the local model failed (HTTP {error.code})") from error
    except (OSError, http.client.HTTPException) as error:
        raise ModelError("the local model did not answer in time" if "timed out" in str(error)
                         else "the local model service stopped answering") from error
    except ValueError as error:
        raise ModelError("the local model sent an unreadable reply") from error
    message = data.get("message") if isinstance(data, dict) else None
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ModelError("the local model sent an empty reply")
    return text


def label(model: Admitted) -> str:
    return f"Model answer ({model.display}, on this computer). It can be wrong."


def ask(question: str, url: str) -> int:
    model, reason = choose(url)
    if model is None:
        if reason:
            print(f"kilix ask: {reason}", file=sys.stderr)
        return NOT_USED
    print(f"kilix ask: asking the local model ({model.display}) ...", file=sys.stderr, flush=True)
    try:
        text = reply(question, model, url)
    except ModelError as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    if declined(text):
        print("kilix ask: the local model does not know.", file=sys.stderr)
        return NOT_USED
    print(plain(text))
    print(label(model))
    return 0


# Setup: register an installed, licence-agreed content asset with Ollama.

def _verify(model: Admitted) -> Path:
    result = subprocess.run(
        [str(HERE.parent / "kilix"), "models", "verify", model.asset],
        capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL, check=False)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = None
    if (result.returncode != 0 or not isinstance(report, dict)
            or report.get("verified") != model.asset or report.get("version") != model.version):
        raise ModelError(f"{model.asset} is not installed here. Install it first, which shows its licence"
                         f" and asks you to agree: kilix models install {model.asset}")
    from kilix_sdk import content
    catalog = content.default_catalog()
    spec = next((a for a in catalog.assets if a.asset_id == model.asset), None)
    if spec is None:
        raise ModelError(f"{model.asset} is not in this Kilix's catalog")
    return Path(content.Installer(report["root"]).asset_destination(spec))


def _upload(url: str, layer: Layer, path: Path) -> None:
    digest = "sha256:" + layer.sha256
    try:
        with _opener().open(urllib.request.Request(url + "/api/blobs/" + digest, method="HEAD"),
                            timeout=CHECK_TIMEOUT):
            return
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise ModelError(f"the local model service refused the blob check (HTTP {error.code})") from error
    if path.stat().st_size != layer.size:
        raise ModelError(f"{path.name} changed size since it was verified")
    host = urllib.parse.urlsplit(url)
    connection = http.client.HTTPConnection(host.hostname, host.port, timeout=600)
    try:
        with path.open("rb") as handle:
            connection.request("POST", "/api/blobs/" + digest, body=handle,
                               headers={"Content-Length": str(layer.size)})
            response = connection.getresponse()
            response.read()
        if response.status not in (200, 201):
            raise ModelError(f"the local model service rejected {path.name} (HTTP {response.status})")
    finally:
        connection.close()


def register(url: str, model: Admitted, folder: Path, *, say=print) -> None:
    """Upload the asset's blobs from `folder` and create `model.name` from them."""
    blobs = folder / "blobs"
    for layer in (model.weights, model.license, model.params):
        say(f"Copying {layer.sha256[:12]} ({layer.size / 1024 ** 2:,.1f} MiB) to the local model service ...")
        _upload(url, layer, blobs / layer.sha256)
    parameters = json.loads((blobs / model.params.sha256).read_text(encoding="utf-8"))
    licence = (blobs / model.license.sha256).read_text(encoding="utf-8")
    body = {"model": model.name, "files": {"model.gguf": "sha256:" + model.weights.sha256},
            "parameters": parameters, "license": [licence], "renderer": model.renderer,
            "parser": model.renderer, "requires": model.requires, "stream": False}
    try:
        _request(url, "/api/create", body, timeout=600)
    except urllib.error.HTTPError as error:
        detail = error.read(2000).decode("utf-8", "replace").strip()
        raise ModelError(f"the local model service could not create {model.name}: {detail or error.code}") from error
    if not registered(url, model):
        raise ModelError(f"{model.name} was created but does not match the measured weights")


def setup(url: str) -> int:
    model = ADMITTED[0]
    if not service_up(url):
        print("kilix ask: the local model service (Ollama) is not running", file=sys.stderr)
        return 1
    if registered(url, model):
        print(f"{model.display} is already set up for Ask.")
        return 0
    print(f"Checking the installed {model.asset} files and licence receipt ...")
    try:
        folder = _verify(model)
        register(url, model, folder)
    except ModelError as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(f"kilix ask: setup failed: {error}", file=sys.stderr)
        return 1
    print(f"{model.display} is set up. Ask uses it for questions nothing else answers.")
    return 0


def status(url: str) -> int:
    print("Model answers: " + ("on" if enabled() else "off (kilix ask --model-answers on)"))
    if not service_up(url):
        print("Local model service: not running")
        return 0
    for model in ADMITTED:
        state = "set up" if registered(url, model) else "not set up (kilix ask --model-answers setup)"
        print(f"{model.display}: {state}")
    chosen = fits(list(ADMITTED))
    print("Model sizer: " + (f"{chosen.display} fits this computer" if chosen else "no listed model fits now"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kilix ask --model-answers")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ask", nargs=argparse.REMAINDER)
    group.add_argument("--setting", choices=("on", "off", "setup", "status"))
    options = parser.parse_args(argv)
    url = service_url()
    try:
        if options.ask is not None:
            return ask(" ".join(options.ask), url)
        if options.setting == "setup":
            return setup(url)
        if options.setting == "status":
            return status(url)
        set_enabled(options.setting == "on")
        print("Model answers are " + options.setting + ".")
        return 0
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
