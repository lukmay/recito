"""HTTP protocol of neural/server_base.py against a fake backend on an
ephemeral port, plus the client side in bin/tts_neural.stream()."""
import http.client
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import server_base
import tts_neural

SR = 16000


class FakeBackend(server_base.Backend):
    name = "fake"
    sample_rate = SR
    tags = ["[laugh]"]
    instruct = True

    def __init__(self):
        self.calls = []

    def voices(self):
        return [{"id": "anna", "lang": "de", "desc": "test voice"}]

    def default_voices(self):
        return {"de": "anna", "en": "anna"}

    def synth(self, text, voice, lang, speed=1.0, instruct=""):
        self.calls.append((text, voice, lang, speed, instruct))
        if text == "fail":
            raise ValueError("boom")
        if text == "silent":
            return
        yield np.array([0.0, 0.5, -0.5, 2.0], dtype=np.float32)    # 2.0 gets clipped
        yield b""                                                     # skipped
        yield b"\x01\x00\x02\x00"


@pytest.fixture
def server():
    backend = FakeBackend()
    exits = []
    srv = server_base.make_server(backend, 0, "fake", exit_fn=exits.append)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    yield SimpleNamespace(port=srv.server_address[1], backend=backend, exits=exits, srv=srv)
    srv.shutdown()
    srv.server_close()


def request(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=data,
                 headers={"Content-Type": "application/json"} if data else {})
    r = conn.getresponse()
    out = r.status, dict(r.getheaders()), r.read()
    conn.close()
    return out


def test_port_file(server, isolated_dirs):
    pf = isolated_dirs["TTS_NEURAL_DIR"] / "fake" / "port"
    assert int(pf.read_text()) == server.port


def test_health(server):
    status, _, body = request(server.port, "GET", "/health")
    assert status == 200
    assert json.loads(body) == {"ok": True, "model": "fake", "loaded": True}


def test_voices(server):
    status, _, body = request(server.port, "GET", "/voices")
    meta = json.loads(body)
    assert status == 200
    assert meta["voices"][0]["id"] == "anna"
    assert meta["default"] == {"de": "anna", "en": "anna"}
    assert meta["tags"] == ["[laugh]"] and meta["instruct"] is True
    assert meta["sample_rate"] == SR


def test_unknown_paths(server):
    assert request(server.port, "GET", "/nope")[0] == 404
    assert request(server.port, "POST", "/nope", {})[0] == 404


def test_synth_streams_pcm(server):
    status, headers, body = request(server.port, "POST", "/synth",
                                    {"text": " Hallo ", "voice": "anna", "lang": "de",
                                     "speed": 1.25, "instruct": "calm"})
    assert status == 200
    assert headers["X-Sample-Rate"] == str(SR)
    expected = np.array([0, 16383, -16383, 32767], dtype="<i2").tobytes() + b"\x01\x00\x02\x00"
    assert body == expected
    assert server.backend.calls == [("Hallo", "anna", "de", 1.25, "calm")]


def test_synth_defaults(server):
    request(server.port, "POST", "/synth", {"text": "x"})
    assert server.backend.calls == [("x", "", "en", 1.0, "")]


def test_synth_errors(server):
    status, _, body = request(server.port, "POST", "/synth", {"text": "   "})
    assert status == 400 and json.loads(body)["error"] == "empty text"
    status, _, body = request(server.port, "POST", "/synth", {"text": "fail"})
    assert status == 500 and "ValueError: boom" in json.loads(body)["error"]
    status, _, body = request(server.port, "POST", "/synth", {"text": "silent"})
    assert status == 500 and json.loads(body)["error"]

    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("POST", "/synth", body=b"{not json")
    assert conn.getresponse().status == 400
    conn.close()


def test_shutdown_calls_exit(server):
    status, _, body = request(server.port, "POST", "/shutdown", {})
    assert status == 200 and json.loads(body) == {"ok": True}
    deadline = time.time() + 3
    while not server.exits and time.time() < deadline:
        time.sleep(0.05)
    assert server.exits == [0]


def test_idle_exit(server):
    exits = []
    server.srv.state["last"] = time.time() - 120
    server_base.start_idle_exit(server.srv, "fake", idle_min=1, exit_fn=exits.append, poll_s=0.01)
    deadline = time.time() + 3
    while not exits and time.time() < deadline:
        time.sleep(0.02)
    assert exits == [0]


def test_idle_exit_disabled(server):
    exits = []
    server.srv.state["last"] = 0
    server_base.start_idle_exit(server.srv, "fake", idle_min=0, exit_fn=exits.append, poll_s=0.01)
    time.sleep(0.1)
    assert exits == []


def test_to_pcm16_clips_and_flattens():
    pcm = server_base.to_pcm16([[1.5], [-1.5], [0.25]])
    assert np.frombuffer(pcm, dtype="<i2").tolist() == [32767, -32767, 8191]


def test_load_registry_skips_doc_keys(tmp_path):
    p = tmp_path / "models.json"
    p.write_text('{"_doc": "x", "a": {"port": 1}}', encoding="utf-8")
    assert server_base.load_registry(p) == {"a": {"port": 1}}


def test_data_dir_override(isolated_dirs):
    assert server_base.data_dir() == isolated_dirs["TTS_NEURAL_DIR"]
    assert server_base.voices_dir() == isolated_dirs["TTS_NEURAL_DIR"] / "voices"


@pytest.fixture
def neural_client(server, monkeypatch):
    """tts_neural pointed at the fake server."""
    fake = SimpleNamespace(port_of=lambda key: server.port,
                           healthy=lambda port, timeout=1.0: True)
    monkeypatch.setattr(tts_neural, "launcher", fake)
    monkeypatch.setattr(tts_neural, "_broken_until", {})
    return server


def test_client_stream(neural_client):
    parts = list(tts_neural.stream("fake", "Hallo", voice="anna", lang="de", speed=1.1))
    assert all(sr == SR for _, sr in parts)
    pcm = b"".join(p for p, _ in parts)
    assert len(pcm) == 12 and len(pcm) % 2 == 0
    assert neural_client.backend.calls[-1] == ("Hallo", "anna", "de", 1.1, "")


def test_client_error_message(neural_client):
    with pytest.raises(RuntimeError, match="fake: ValueError: boom"):
        list(tts_neural.stream("fake", "fail"))
