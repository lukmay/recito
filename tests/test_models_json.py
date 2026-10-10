"""Schema of neural/models.json and the files each entry points to."""
import json
from pathlib import Path

import pytest

NEURAL = Path(__file__).resolve().parent.parent / "neural"
RAW = json.loads((NEURAL / "models.json").read_text(encoding="utf-8-sig"))
MODELS = {k: v for k, v in RAW.items() if not k.startswith("_")}

REQUIRED = {"title": str, "dir": str, "port": int, "vram_mb": int}
OPTIONAL_FLAGS = {"cpu_ok", "clone", "instruct", "tags", "markup", "native_speed"}
ALLOWED = set(REQUIRED) | OPTIONAL_FLAGS | {"env"}


def test_has_models():
    assert MODELS


@pytest.mark.parametrize("key", sorted(MODELS))
def test_entry_schema(key):
    spec = MODELS[key]
    assert key.isidentifier() and key == key.lower()
    assert set(spec) <= ALLOWED, f"unknown fields: {set(spec) - ALLOWED}"
    for field, typ in REQUIRED.items():
        assert isinstance(spec.get(field), typ), f"{key}.{field} must be {typ.__name__}"
    assert 1024 < spec["port"] < 65536
    assert spec["vram_mb"] > 0
    for flag in OPTIONAL_FLAGS & set(spec):
        assert isinstance(spec[flag], bool), f"{key}.{flag} must be a boolean"
    env = spec.get("env", {})
    assert isinstance(env, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())


@pytest.mark.parametrize("key", sorted(MODELS))
def test_entry_files(key):
    d = NEURAL / MODELS[key]["dir"]
    server = d / "server.py"
    assert server.is_file()
    assert (d / "setup.ps1").is_file() or (d / "setup.sh").is_file()
    src = server.read_text(encoding="utf-8")
    assert "run(" in src and f'"{key}"' in src, "server.py must call run(backend, key)"


def test_ports_unique():
    ports = [m["port"] for m in MODELS.values()]
    assert len(ports) == len(set(ports))


def test_launcher_registry_matches_file():
    import launcher
    assert launcher.REGISTRY == MODELS
