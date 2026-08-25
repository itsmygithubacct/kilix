"""Pinned, redistributable audio subsets offered to Kilix Techno.

The application stays network-free.  These records are consumed only by the
explicit ``kilix install`` path and describe exactly which upstream bytes are
downloaded, which voices survive curation, and their installed footprint.
Legacy packs are normalized to mono WAV; asset packs preserve WAV, SFZ and SF2
so the shared Kilix audio asset layer exercises the native format directly.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path


CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
ONGENET_REVISION = "4ac5ff452134866e923d19f58451aad64804cba1"
ONGENET_RAW = (
    "https://raw.githubusercontent.com/1-chris/Ongenet/"
    f"{ONGENET_REVISION}/"
)
ONGENET_ATTRIBUTION = (
    "https://github.com/1-chris/Ongenet/blob/"
    f"{ONGENET_REVISION}/Content/Core/ATTRIBUTION.md"
)


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
    {
        "id": "techno-ongenet-kit",
        "directory": "ongenet-kit",
        "label": "Kilix Techno: Ongenet Kit",
        "description": "Six compact procedural drums; the curated CC0 default kit",
        "license": "CC0-1.0",
        "license_url": CC0_URL,
        "license_evidence": ONGENET_ATTRIBUTION,
        "source": "https://github.com/1-chris/Ongenet",
        "download_source": "https://github.com/1-chris/Ongenet",
        "revision": ONGENET_REVISION,
        "download_bytes": 146_674,
        "installed_bytes": 146_674,
        "mode": "assets",
        "raw_base": ONGENET_RAW,
        "install_note": "Preserved source WAV files; no conversion.",
        "files": (
            ("kick.wav", "Content/Core/Samples/Drums/OngenetKit/kick_120bpm_punch.wav", 39_734,
             "c758859504a929d06097185df6d00c0cb6670e6513ecfe4e8ce1a9161b6ae5bd"),
            ("snare.wav", "Content/Core/Samples/Drums/OngenetKit/snare_crack.wav", 24_740,
             "4d42e977bfce00186628e7d7f1b2eedaa14b04587c006a5014d10904a3b62083"),
            ("clap.wav", "Content/Core/Samples/Drums/OngenetKit/clap_room.wav", 26_504,
             "8ab73c527ffa4a99e003de22d333575f27c3b1a31027761ea88cfac5b1fda547"),
            ("closed-hat.wav", "Content/Core/Samples/Drums/OngenetKit/hat_closed.wav", 7_100,
             "8027e2ef8eb46217e1f9d4d4c208697c343952cc34bdcdfc1ed1e131f6029046"),
            ("open-hat.wav", "Content/Core/Samples/Drums/OngenetKit/hat_open.wav", 30_912,
             "ce86e3faeb4bbbf6075388336eb6b24fccb6c9a867a1b10c9424c6bd9b1591b3"),
            ("ride.wav", "Content/Core/Samples/Drums/OngenetKit/ride_tick.wav", 17_684,
             "de1a3645cc25df41b034ea24962d4e0998878424663664d7d1abd1d56c1baa53"),
        ),
    },
    {
        "id": "techno-vcsl-acoustic",
        "directory": "vcsl-acoustic",
        "label": "Kilix Techno: VCSL Acoustic SFZ",
        "description": "Four curated SFZ instruments: kick, snare, Strumstick and Kawai piano",
        "license": "CC0-1.0",
        "license_url": CC0_URL,
        "license_evidence": ONGENET_ATTRIBUTION,
        "source": "https://github.com/sgossner/VCSL",
        "download_source": "https://github.com/1-chris/Ongenet",
        "revision": ONGENET_REVISION,
        "download_bytes": 6_624_200,
        "installed_bytes": 6_624_200,
        "mode": "assets",
        "raw_base": ONGENET_RAW,
        "install_note": "Preserved SFZ maps plus the one WAV region selected by Kilix.",
        "files": (
            ("kick.sfz", "Content/Core/Soundfonts/VCSL/Membranophones/Struck%20Membranophones/Bass%20Drum%201.sfz", 1_372,
             "b612aa0afef74ce35ba390042a581c583a78f9328fba90c19ab64fbdb9c4802f"),
            ("Bass Drum 1/BDrumNew_hit_v5_rr1_Sum.wav", "Content/Core/Soundfonts/VCSL/Membranophones/Struck%20Membranophones/Bass%20Drum%201/BDrumNew_hit_v5_rr1_Sum.wav", 588_092,
             "642aaf3375eaff1307973f7de3ccbf74d18e71fb3cf9412e021b08fc3bf1981c"),
            ("snare.sfz", "Content/Core/Soundfonts/VCSL/Membranophones/Struck%20Membranophones/Snare%20Drum%2C%20Modern%201.sfz", 5_097,
             "e731950940683b944bbae86307b3c1ad6693724818b8ea862ff83c032dc4316f"),
            ("Snare Drum, Modern 1/Snare2_HitNS_v5_rr1_Mid.wav", "Content/Core/Soundfonts/VCSL/Membranophones/Struck%20Membranophones/Snare%20Drum%2C%20Modern%201/Snare2_HitNS_v5_rr1_Mid.wav", 168_240,
             "422855e723dea59d316bd71346397833340a20c538c648c1b4369e9a3617b61b"),
            ("strumstick.sfz", "Content/Core/Soundfonts/VCSL/Chordophones/Composite%20Chordophones/Strumstick.sfz", 8_698,
             "450e4ab1a58136191b51dadd2de5c3f0ddb01edacfbcb3fb28e8b619defd3a09"),
            ("Strumstick/Finger/Strumstick_Finger_Str2_Main_B2_vl2_rr1.wav", "Content/Core/Soundfonts/VCSL/Chordophones/Composite%20Chordophones/Strumstick/Finger/Strumstick_Finger_Str2_Main_B2_vl2_rr1.wav", 1_532_882,
             "04b5e2167dadb7c8c4956af63ea5317cbcdc7869a6f103ea82cd541956723c4d"),
            ("piano.sfz", "Content/Core/Soundfonts/VCSL/Piano.sfz", 36_491,
             "f4c5447f8662915545ab1acd598c11e3515811298240f060d1a96297ae59bd86"),
            ("Chordophones/Zithers/Grand Piano, Kawai/Sustains/GPiano_sus_C3_v2_rr1_Player.wav", "Content/Core/Soundfonts/VCSL/Chordophones/Zithers/Grand%20Piano%2C%20Kawai/Sustains/GPiano_sus_C3_v2_rr1_Player.wav", 4_283_328,
             "fce33012fe900309abafd1a4d6d254d142b0cbf33834c7a72c2fd1ada4e746e1"),
        ),
    },
    {
        "id": "techno-vsco2ce",
        "directory": "vsco2ce",
        "label": "Kilix Techno: VSCO 2 CE SFZ",
        "description": "Four compact SFZ orchestra voices for strings, woodwind and brass",
        "license": "CC0-1.0",
        "license_url": CC0_URL,
        "license_evidence": ONGENET_ATTRIBUTION,
        "source": "https://github.com/sgossner/VSCO-2-CE",
        "download_source": "https://github.com/1-chris/Ongenet",
        "revision": ONGENET_REVISION,
        "download_bytes": 1_184_344,
        "installed_bytes": 1_184_344,
        "mode": "assets",
        "raw_base": ONGENET_RAW,
        "install_note": "Preserved SFZ maps plus the one WAV region selected by Kilix.",
        "files": (
            ("violin-pizz.sfz", "Content/Core/Soundfonts/VSCO2CE/ViolinEnsPizz.sfz", 5_069,
             "c94b1129e5a8c7e7f7a8e556f6d7c491dbe88875cc2080a0fd2794eb77f5b733"),
            ("Strings/Violin Section/Pizz/VlnEns_Pizz_B2_v2_rr1.wav", "Content/Core/Soundfonts/VSCO2CE/Strings/Violin%20Section/Pizz/VlnEns_Pizz_B2_v2_rr1.wav", 180_134,
             "71aaf7c111d427e76d91a58efc4cf661880cae85ec91123f8f57d421fe36cd06"),
            ("cello-pizz.sfz", "Content/Core/Soundfonts/VSCO2CE/CelloEnsPizz.sfz", 5_637,
             "84e586f7518b99c11da5efddb321a9ea4b9edc4149b96f3e9621f4c7e9fc3cca"),
            ("Strings/Cello Section/pizzT/pizzT_C3_v2_RR1.wav", "Content/Core/Soundfonts/VSCO2CE/Strings/Cello%20Section/pizzT/pizzT_C3_v2_RR1.wav", 762_122,
             "fbc86686ef092e92e473dce4d1ff5f1e351dcc76ed2d94b0d336c1346f314e59"),
            ("clarinet-stac.sfz", "Content/Core/Soundfonts/VSCO2CE/ClarinetStac.sfz", 7_495,
             "e3657824a241cfc55b9bac03e359da24068498955597cf8dbc2684330b3ddfed"),
            ("Woodwinds/Clarinet/stac/DCClar_stac_D3_v2_rr1_sum.wav", "Content/Core/Soundfonts/VSCO2CE/Woodwinds/Clarinet/stac/DCClar_stac_D3_v2_rr1_sum.wav", 101_040,
             "ffd6122784c9acb0e49890955be3130d9634c25b99fed501d03eed4e6d882d98"),
            ("horn-stac.sfz", "Content/Core/Soundfonts/VSCO2CE/FHornStac.sfz", 6_263,
             "cdb75af40a2fc88f0e4f311e46677e74346004f2946606fd8905323543ca9201"),
            ("Brass/F Horn/stac/MOHorn_stac_C3_v2_rr1.wav", "Content/Core/Soundfonts/VSCO2CE/Brass/F%20Horn/stac/MOHorn_stac_C3_v2_rr1.wav", 116_584,
             "fdaf3d5ad1e732a6e8e466ee232b101fe83b4cc79e79941315fba3037666eedf"),
        ),
    },
    {
        "id": "techno-sf2-chaosbank",
        "directory": "sf2-chaosbank",
        "label": "Kilix Techno: ChaosBank SF2",
        "description": "Compact general-MIDI SoundFont, rendered on demand by Kilix",
        "license": "CC0-1.0 (as declared by distributor)",
        "license_url": CC0_URL,
        "license_evidence": ONGENET_ATTRIBUTION,
        "source": "https://github.com/bratpeki/soundfonts",
        "download_source": "https://github.com/1-chris/Ongenet",
        "revision": ONGENET_REVISION,
        "download_bytes": 12_038_662,
        "installed_bytes": 12_038_662,
        "mode": "assets",
        "raw_base": ONGENET_RAW,
        "install_note": "Preserved source SF2; Ongenet supplies the CC0 declaration.",
        "files": (
            ("ChaosBank.sf2", "Content/Core/Soundfonts/Sf2/GM/ChaosBank/ChaosBank.sf2", 12_038_662,
             "0a107e182fee704ad9b91cbbad2febf97f55cf778ff656ed49415c6a5addd01e"),
        ),
    },
    {
        "id": "techno-sf2-jnsgm2",
        "directory": "sf2-jnsgm2",
        "label": "Kilix Techno: JNS-GM 2 SF2",
        "description": "General-MIDI SoundFont with a stronger synth-bass palette",
        "license": "CC0-1.0 (as declared by distributor)",
        "license_url": CC0_URL,
        "license_evidence": ONGENET_ATTRIBUTION,
        "source": "https://github.com/bratpeki/soundfonts",
        "download_source": "https://github.com/1-chris/Ongenet",
        "revision": ONGENET_REVISION,
        "download_bytes": 33_187_490,
        "installed_bytes": 33_187_490,
        "mode": "assets",
        "raw_base": ONGENET_RAW,
        "install_note": "Preserved source SF2; Ongenet supplies the CC0 declaration.",
        "files": (
            ("Jnsgm2.sf2", "Content/Core/Soundfonts/Sf2/GM/Jnsgm2/Jnsgm2.sf2", 33_187_490,
             "dc48cb5c322cab23fce1b18442066be30ccc49a184603c7a3bf7615003ee137d"),
        ),
    },
    {
        "id": "techno-sf2-masterpiece",
        "directory": "sf2-masterpiece",
        "label": "Kilix Techno: Masterpiece SF2",
        "description": "General-MIDI SoundFont curated for keys and pads",
        "license": "CC0-1.0 (as declared by distributor)",
        "license_url": CC0_URL,
        "license_evidence": ONGENET_ATTRIBUTION,
        "source": "https://github.com/bratpeki/soundfonts",
        "download_source": "https://github.com/1-chris/Ongenet",
        "revision": ONGENET_REVISION,
        "download_bytes": 29_208_234,
        "installed_bytes": 29_208_234,
        "mode": "assets",
        "raw_base": ONGENET_RAW,
        "install_note": "Preserved source SF2; Ongenet supplies the CC0 declaration.",
        "files": (
            ("Masterpiece.sf2", "Content/Core/Soundfonts/Sf2/GM/Masterpiece/Masterpiece.sf2", 29_208_234,
             "52d854c93853ec97380af351595fdcce0571242763b7879fd96caac6f86aaf79"),
        ),
    },
    {
        "id": "techno-sf2-unison",
        "directory": "sf2-unison",
        "label": "Kilix Techno: Unison SF2",
        "description": "General-MIDI SoundFont curated for synth lead textures",
        "license": "CC0-1.0 (as declared by distributor)",
        "license_url": CC0_URL,
        "license_evidence": ONGENET_ATTRIBUTION,
        "source": "https://github.com/bratpeki/soundfonts",
        "download_source": "https://github.com/1-chris/Ongenet",
        "revision": ONGENET_REVISION,
        "download_bytes": 29_258_148,
        "installed_bytes": 29_258_148,
        "mode": "assets",
        "raw_base": ONGENET_RAW,
        "install_note": "Preserved source SF2; Ongenet supplies the CC0 declaration.",
        "files": (
            ("Unison.SF2", "Content/Core/Soundfonts/Sf2/GM/Unison/Unison.SF2", 29_258_148,
             "a9af8184b7afd36dc8fde39992ff67542d01eb486295c353fc54d3f3b693d51c"),
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
