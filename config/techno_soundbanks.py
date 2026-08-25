"""Pinned, redistributable sample subsets offered to Kilix Techno.

The application stays network-free.  These records are consumed only by the
explicit ``kilix install`` path and describe exactly which upstream bytes are
downloaded, which voices survive curation, and what their canonical installed
footprint is after conversion to mono 16-bit/44.1 kHz WAV.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path


PACKS = (
    {
        "id": "techno-tr808-fischer",
        "directory": "tr808-fischer",
        "label": "Kilix Techno: TR-808 Fischer",
        "description": "Six classic 808 voices curated from TidalCycles",
        "license": "CC0-1.0",
        "source": "https://github.com/tidalcycles/sounds-tr808-fischer",
        "revision": "85fbecf1bec32553395625ea659e2a56dfd7c0e1",
        "download_bytes": 551_524,
        "installed_bytes": 551_524,
        "mode": "direct",
        "raw_base": "https://raw.githubusercontent.com/tidalcycles/sounds-tr808-fischer/85fbecf1bec32553395625ea659e2a56dfd7c0e1/",
        "files": (
            ("kick.wav", "bd8/BD5050.WAV", 132_346,
             "2ca93cd7a3b19f6ec7fe26fefe51b89de69b7be7a87047ffc8ecd396064a8504"),
            ("snare.wav", "sd8/SD5050.WAV", 44_146,
             "6dcbf8acd5cee6d6b7955d26c6b57d8ca413f1ae1c7caafaaec780d83f08d712"),
            ("clap.wav", "cp8/CP.WAV", 176_446,
             "376429bb81cb48d1f392a11cd066c32ffb7883d445b2488704ea52e46bb08286"),
            ("closed-hat.wav", "ch8/CH.WAV", 22_094,
             "c9f30ff2b4d73b03f41960e504e03c54e9a59697af666fe4d155bab9cd1ccae6"),
            ("open-hat.wav", "oh8/OH50.WAV", 44_146,
             "84ae3220fab2396380fb6ba6032bcec7c55a6d3f621d30a13c11fdeaa099aff7"),
            ("cowbell.wav", "cb8/CB.WAV", 132_346,
             "1468cbd6c75a1f23b50a15968796c256c74893a35aa3824c6663db82c271f28d"),
        ),
    },
    {
        "id": "techno-stargate",
        "directory": "stargate",
        "label": "Kilix Techno: Stargate",
        "description": "Six processed drum and sub voices from Stargate DAW",
        "license": "CC0-1.0",
        "source": "https://github.com/stargatedaw/stargate-sample-pack",
        "revision": "dbfd6ec52d4ed53b60bdbea5fc6adf295127c027",
        "download_bytes": 1_351_494,
        "installed_bytes": 449_192,
        "mode": "direct",
        "raw_base": "https://raw.githubusercontent.com/stargatedaw/stargate-sample-pack/dbfd6ec52d4ed53b60bdbea5fc6adf295127c027/",
        "prefix": "stargate-sample-pack/fugue-state-audio/drums/",
        "files": (
            ("kick.wav", "kicks/distkit-kick.wav", 252_668,
             "80f029e56cb0cf8e8df6b180f02af380e0410a43376e3c2d8f2efd81b9d1e8ab"),
            ("snare.wav", "snares/synthkit-snare.wav", 61_952,
             "177549cd845ecccef1a4a8d89bfa0ff3df7279e1e6016ff05534db0b3e1f541e"),
            ("closed-hat.wav", "hihats/distkit-hatclsd.wav", 70_304,
             "74077a134eb66bc4367f4b8a716956b16d724c5c21030c1d49fdabf0726b0895"),
            ("open-hat.wav", "hihats/distkit-hatopen.wav", 332_828,
             "6f47aa3f5755f3c8ad486841760b956d43e7e4f12057c2d3fdb2def300aeb8d7"),
            ("fm-perc.wav", "percussion/sdbkit-fmperc.wav", 117_296,
             "4525380378a65866f917185b8f4d2ea9910bc7212799b740a4febf572562435d"),
            ("sub-a.wav", "kicks/sdbkit-sub-a.wav", 516_446,
             "2ccab3dc777b7ce607bdb7dec396bb6feed658edb6b016b7068240c8f8780a51"),
        ),
    },
    {
        "id": "techno-scoredata-forge",
        "directory": "scoredata-forge",
        "label": "Kilix Techno: scoredata-forge",
        "description": "Deterministically generated growl and house-stab voices",
        "license": "CC0-1.0 samples; MIT generator",
        "source": "https://github.com/talkincode/scoredata-forge",
        "revision": "2ff340297d6e70fe5ce3fee257fcf1abe0ee273d",
        "download_bytes": 27_630,
        "installed_bytes": 137_680,
        "mode": "forge",
        "archive_url": "https://codeload.github.com/talkincode/scoredata-forge/tar.gz/2ff340297d6e70fe5ce3fee257fcf1abe0ee273d",
        "archive_sha256": "69f6f880b0340fc389587f68af6fce3516da003412d86251ef584f65df611c91",
        "outputs": (
            ("growl.wav", "recipes/dubstep-growl.toml",
             "dubstep-growl/1.0.0/samples/dubstep-growl_036_rage.wav"),
            ("house-stab.wav", "recipes/house-stab.toml",
             "house-stab/1.0.0/samples/house-stab_048_accent.wav"),
        ),
    },
    {
        "id": "techno-mechsounds",
        "directory": "mechsounds",
        "label": "Kilix Techno: MechSounds",
        "description": "Six bass, pad, percussion and glitch voices",
        "license": "CC0-1.0 sounds",
        "source": "https://johnoestmannmusic.com/mechsounds/",
        "revision": "260301",
        "download_bytes": 173_465_329,
        "installed_bytes": 1_197_582,
        "mode": "zip",
        "archive_url": "https://johnoestmannmusic.com/wp-content/uploads/2026/03/260301-MechSounds.zip",
        "archive_sha256": "d0817d9c2c1f05cef0ea06c29c51e519df72a23a4e1e2fbdd3681024dec9a6c1",
        "files": (
            ("pluck-bass.wav", "Samples/000 - Tonal/TNL-PLUKBASS.wav"),
            ("fuzz-bass.wav", "Samples/000 - Tonal/TNL-FUZZBASS.wav"),
            ("wobble.wav", "Samples/000 - Tonal/TNL-WOBLDISK.wav"),
            ("time-pad.wav", "Samples/001 - Pads and Chords/PAD-TIMETELL.wav"),
            ("industrial-hit.wav", "Samples/003 - Percussion/PRC-INDSTHIT.wav"),
            ("glitch-vox.wav", "Samples/004 - Sound Effects/SFX-GLITCHVOX.wav"),
        ),
    },
    {
        "id": "techno-karoryfer",
        "directory": "karoryfer",
        "label": "Kilix Techno: Karoryfer",
        "description": "Four tuned Cowsynth voices for bass, chord and texture",
        "license": "CC0-1.0",
        "source": "https://shop.karoryfer.com/pages/free-samples",
        "revision": "Cowsynth-v1.001",
        "download_bytes": 14_072_509,
        "installed_bytes": 3_711_926,
        "mode": "zip",
        "archive_url": "https://github.com/sfzinstruments/karoryfer.cowsynth/releases/download/v1.001/Karoryfer.Cowsynth.v1.001.zip",
        "archive_sha256": "2c80a928db69280d3f8f4d066383c9f6adc0d8ba086c6f1a1abe2c5eaa5fc239",
        "files": (
            ("a2.wav", "Cowsynth/tuned/a2.wav"),
            ("c4.wav", "Cowsynth/tuned/c4.wav"),
            ("f3.wav", "Cowsynth/tuned/f3.wav"),
            ("g2.wav", "Cowsynth/tuned/g2.wav"),
        ),
    },
    {
        "id": "techno-vcsl",
        "directory": "vcsl",
        "label": "Kilix Techno: VCSL",
        "description": "Four FM, struck-metal and ocean texture voices",
        "license": "CC0-1.0",
        "source": "https://github.com/sgossner/VCSL",
        "revision": "c1ea7bcc3c7309650ab0da9d15c9cd1fbc4a4c7e",
        "download_bytes": 6_080_321,
        "installed_bytes": 3_342_514,
        "mode": "direct",
        "raw_base": "https://raw.githubusercontent.com/sgossner/VCSL/c1ea7bcc3c7309650ab0da9d15c9cd1fbc4a4c7e/",
        "files": (
            ("clavisynth-c3.wav", "Electrophones/TX81Z/Clavisynth/Clavisynth_C3_vl3.wav", 474_865,
             "57747fa824ce998140fb00f88c1f3badebb6d02f84fd80e505086c323ef47f91"),
            ("fm-piano-c3.wav", "Electrophones/TX81Z/FM%20Piano/FMPiano_C3_vl3.wav", 1_340_008,
             "4e7a7d75bf01af25bcd8b321853e61f159c14432360705b319154d7010ffb1fc"),
            ("brake-drum.wav", "Idiophones/Struck%20Idiophones/Brake%20Drum/BrakeDrum1_Hammer_v3_rr1_Mid.wav", 299_416,
             "a488e7cdca983fc53c4399fa0c492d582510dd48ee35e5336113b0041f8f55b1"),
            ("ocean-drum.wav", "Membranophones/Other%20Membranophones/Ocean%20Drum/OceanDrum_Sus_2_Mid.wav", 3_966_032,
             "6642dbaeef3f4feb94765ecdbd3d0411c623b3a6b3078110201bddd722948d9d"),
        ),
    },
)


def by_id(identifier: str) -> dict | None:
    return next((pack for pack in PACKS if pack["id"] == identifier), None)


def root() -> Path:
    override = os.environ.get("KILIX_TECHNO_SOUNDBANK_DIR")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_absolute():
            return candidate
        return Path.home() / ".local" / "share" / "kilix-techno" / "soundbanks"
    data = os.environ.get("XDG_DATA_HOME")
    candidate = Path(data).expanduser() if data else None
    base = candidate if candidate and candidate.is_absolute() else Path.home() / ".local" / "share"
    return base / "kilix-techno" / "soundbanks"


def output_names(pack: dict) -> tuple[str, ...]:
    key = "outputs" if pack["mode"] == "forge" else "files"
    return tuple(item[0] for item in pack[key])


def directory(pack: dict) -> Path:
    return root() / pack["directory"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ready(pack: dict) -> bool:
    target = directory(pack)
    receipt = target / ".kilix-bank"
    try:
        if target.is_symlink() or not target.is_dir() or \
                receipt.is_symlink() or not receipt.is_file():
            return False
        record = json.loads(receipt.read_text(encoding="utf-8"))
        if record.get("schema") != 1 or record.get("id") != pack["id"]:
            return False
        checksums = record.get("files")
        if not isinstance(checksums, dict):
            return False
        for name in output_names(pack):
            sample = target / name
            if sample.is_symlink() or not sample.is_file() or sample.stat().st_size < 46:
                return False
            if checksums.get(name) != _sha256(sample):
                return False
    except (OSError, ValueError, TypeError):
        return False
    return True


def human_size(size: int) -> str:
    value = float(size)
    for suffix in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or suffix == "GiB":
            return f"{value:.0f} {suffix}" if suffix == "B" else f"{value:.1f} {suffix}"
        value /= 1024.0
    return f"{size} B"


def rows() -> list[dict]:
    result = []
    for pack in PACKS:
        installed = ready(pack)
        result.append({
            "id": pack["id"],
            "label": pack["label"],
            "kind": "soundbank",
            "description": pack["description"],
            "license": pack["license"],
            "source": pack["source"],
            "download_bytes": pack["download_bytes"],
            "installed_bytes": pack["installed_bytes"],
            "size": f"{human_size(pack['download_bytes'])} down / "
                    f"{human_size(pack['installed_bytes'])} disk",
            "installed": installed,
            "path": str(directory(pack)) if installed else "",
        })
    return result
