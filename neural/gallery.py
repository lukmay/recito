r"""
Builds <data_dir>/samples/gallery.html from all report.json files: per model
a table with audio player, text, voice, tags or instruction, and latency. Open
the file directly in a browser to listen.

    python neural/gallery.py
"""
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from server_base import data_dir, leave_store_python  # noqa: E402

CSS = """
:root{--bg:#fafaf7;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e2dc;--acc:#2f6f5e;--bad:#b3261e}
@media (prefers-color-scheme: dark){:root{--bg:#161615;--fg:#ecebe6;--mut:#a3a29c;--line:#2e2e2b;--acc:#7cc7ad;--bad:#ff8a80}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1200px;padding:24px 16px}
h1{font-size:26px;margin:0 0 4px}h2{margin:36px 0 6px;font-size:20px}
.mut{color:var(--mut)}table{border-collapse:collapse;width:100%;margin-top:8px}
td,th{border-bottom:1px solid var(--line);padding:8px 6px;vertical-align:top;text-align:left}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
audio{width:240px;height:34px}.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;background:var(--line);border-radius:4px;padding:0 6px;font-size:12px;margin-right:4px}
.grp{font-size:12px;color:var(--acc);font-weight:600}.err{color:var(--bad)}
.sum{display:flex;gap:18px;flex-wrap:wrap;margin:6px 0}.sum b{font-variant-numeric:tabular-nums}
code{font-size:13px}
"""


def build():
    root = data_dir() / "samples"
    root.mkdir(parents=True, exist_ok=True)
    reports = []
    for rp in sorted(root.glob("*/report.json")):
        try:
            reports.append(json.loads(rp.read_text(encoding="utf-8")))
        except Exception:
            pass
    parts = ["<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
             f"<title>TTS Comparison</title><style>{CSS}</style>",
             "<h1>Local TTS models compared</h1>",
             "<p class=mut>TTFA = time to first audio (warm), RTF = compute time / audio duration "
             "(below 1 = faster than real time), WER = word error rate of the Whisper transcription "
             "(hover to see what Whisper understood). Group \"common\" is the same for all models, "
             "\"model\" holds model-specific samples with and without tags.</p>"]
    for rep in reports:
        ok = [r for r in rep["results"] if "error" not in r]
        parts.append(f"<h2 id='{rep['model']}'>{html.escape(rep['title'])} "
                     f"<span class=mut>({rep['model']})</span></h2>")
        if ok:
            ttfas = sorted(r["ttfa_ms"] for r in ok)
            rtfs = sorted(r["rtf"] for r in ok)
            parts.append(
                "<div class=sum>"
                f"<span>TTFA Median <b>{ttfas[len(ttfas) // 2]} ms</b></span>"
                f"<span>RTF Median <b>{rtfs[len(rtfs) // 2]}</b></span>"
                f"<span>Total VRAM in use <b>{rep.get('vram_total_used_mb')} MB</b></span>"
                + "".join(f"<span>WER {k.upper()} <b>{v:.0%}</b></span>"
                          for k, v in (rep.get('wer_mean') or {}).items())
                + f"<span class=mut>As of {rep.get('time', '')}</span></div>")
        tags = rep.get("meta", {}).get("tags") or []
        if tags:
            parts.append("<p class=mut>Tags: " + " ".join(
                f"<span class=tag>{html.escape(t)}</span>" for t in tags[:40]) + "</p>")
        parts.append("<table><tr><th>Sample</th><th>Audio</th><th>Text</th>"
                     "<th>Voice / instruction</th><th>Latency</th></tr>")
        for r in rep["results"]:
            extra = html.escape(r.get("voice", ""))
            if r.get("instruct"):
                extra += f"<br><span class=mut>\"{html.escape(r['instruct'])}\"</span>"
            if r.get("note"):
                extra += f"<br><span class=mut>{html.escape(r['note'])}</span>"
            if "error" in r:
                audio, lat = f"<span class=err>{html.escape(r['error'])}</span>", ""
            else:
                audio = f"<audio controls preload=none src='{rep['model']}/{r['wav']}'></audio>"
                lat = (f"<span class=num>{r['ttfa_ms']} ms</span><br>"
                       f"<span class='num mut'>RTF {r['rtf']} · {r['audio_s']} s</span>")
                if "wer" in r:
                    lat += (f"<br><span class='num {'err' if r['wer'] > .15 else 'mut'}' "
                            f"title='{html.escape(r.get('asr', ''), quote=True)}'>"
                            f"WER {r['wer']:.0%}</span>")
            parts.append(f"<tr><td><span class=grp>{html.escape(r.get('group', ''))}</span><br>"
                         f"<code>{html.escape(r['id'])}</code></td><td>{audio}</td>"
                         f"<td>{html.escape(r['text'])}</td><td>{extra}</td><td>{lat}</td></tr>")
        parts.append("</table>")
    out = root / "gallery.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


if __name__ == "__main__":
    leave_store_python()
    print(build())
