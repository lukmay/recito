"""
tts_reader: create a new ElevenReader document from the selected text and
play it right away, in a window of the tray app (Qt WebEngine).

ElevenReader has no official API. The web app creates documents itself via
POST /v1/reader/reads/add (file upload, response with read_id), authenticated
with the Firebase token of the logged-in user. This window does the same and
then opens the new document; autoplay is allowed here.

Login: Google sign-in does not work in embedded browsers ("This browser or app
may not be secure"), so the session is taken over once from the regular
browser. A bookmarklet ("Mit Recito verbinden"), clicked on elevenreader.io,
copies the page's Firebase user to the clipboard. The tray detects it, stores
it (session.json, readable by the user only), clears the clipboard and puts it
into this window's IndexedDB. The page's Firebase library then signs in by
itself and refreshes the token as in the browser.

The endpoint is unofficial, so every failure is reported through on_error and
the tray reads the text locally instead.
"""

import base64
import json
import os
import time
import webbrowser
from pathlib import Path

import tts_icons
import tts_platform

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow

HOME_URL = "https://elevenreader.io/reader/library"
READ_URL = "https://elevenreader.io/reader/library/{}"
API_ADD = "https://api.elevenlabs.io/v1/reader/reads/add"
SESSION_PREFIX = "tts-elevenreader-session:"


def storage_dir() -> Path:
    if tts_platform.IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return base / "tts-elevenreader"
    return Path.home() / ".local/share/tts-elevenreader"


def make_title(text: str) -> str:
    """Title from the first words, so the library stays readable."""
    words = " ".join(text.split())
    title = words[:60].rsplit(" ", 1)[0] if len(words) > 60 else words
    return (title.rstrip(" .,;:") or "Text") + time.strftime(" (%d.%m. %H:%M)")


# --------------------------------------------------------------------------- #
#  Session takeover from the browser
# --------------------------------------------------------------------------- #
def session_path() -> Path:
    return storage_dir() / "session.json"


def load_session():
    try:
        return json.loads(session_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


DIAG_PREFIX = "tts-elevenreader-diag:"


def _decode(payload):
    return json.loads(base64.b64decode(payload).decode("utf-8"))


def parse_session(text: str):
    """Clipboard content from the bookmarklet -> login entry or None.
    Entry = {'raw': record as stored, 'value': Firebase user,
    'where': location (local | session | idb with db/store)}."""
    text = (text or "").strip()
    if not text.startswith(SESSION_PREFIX):
        return None
    try:
        entry = _decode(text[len(SESSION_PREFIX):])
        ok = entry["value"]["stsTokenManager"]["refreshToken"] and entry["where"]["type"]
        return entry if ok else None
    except (ValueError, KeyError, TypeError):
        return None


def parse_diag(text: str):
    text = (text or "").strip()
    if not text.startswith(DIAG_PREFIX):
        return None
    try:
        return _decode(text[len(DIAG_PREFIX):])
    except (ValueError, TypeError):
        return None


def save_session(entry):
    p = session_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entry), encoding="utf-8")
    if not tts_platform.IS_WINDOWS:
        os.chmod(p, 0o600)


# Finds the Firebase login in every storage of the page: localStorage,
# sessionStorage and each IndexedDB database (which one the web app uses
# depends on the browser). Returns {raw, value, where} or null. Used in the
# bookmarklet and in the window, so it works as a single line and contains no
# "%" (browsers decode javascript: URLs).
_FIND_AUTH_JS = (
    "async function ttsFindAuth(){"
    "const isUser=v=>v&&v.stsTokenManager&&v.stsTokenManager.refreshToken;"
    "const dig=(v,n)=>{if(!v||typeof v!=='object'||n>5)return null;if(isUser(v))return v;"
    "for(const k of Object.keys(v)){let x=v[k];if(typeof x==='string'&&x.charAt(0)==='{'){try{x=JSON.parse(x);}catch(e){}}"
    "const f=dig(x,n+1);if(f)return f;}return null;};"
    "for(const [t,st] of [['local',localStorage],['session',sessionStorage]]){"
    "for(let i=0;i<st.length;i++){const k=st.key(i);if(k&&k.startsWith('firebase:authUser:')){"
    "try{const v=JSON.parse(st.getItem(k));if(isUser(v))return{raw:{key:k},value:v,where:{type:t,key:k}};}catch(e){}}}}"
    "const dbs=indexedDB.databases?await indexedDB.databases():[{name:'firebaseLocalStorageDb'}];"
    "for(const d of dbs){const db=await new Promise(r=>{const q=indexedDB.open(d.name);"
    "q.onsuccess=()=>r(q.result);q.onerror=()=>r(null);});if(!db)continue;"
    "for(const sn of Array.from(db.objectStoreNames)){let os;try{os=db.transaction(sn,'readonly').objectStore(sn);}catch(e){continue;}"
    "const kp=os.keyPath;const all=await new Promise(r=>{const q=os.getAll();q.onsuccess=()=>r(q.result||[]);q.onerror=()=>r([]);});"
    "for(const e of all){const v=dig(e,0);"
    "if(v){db.close();return{raw:e,value:v,where:{type:'idb',db:d.name,store:sn,keyPath:kp}};}}}db.close();}"
    "return null;}"
    "async function ttsDiag(){const dbs=indexedDB.databases?await indexedDB.databases():[];"
    "const out={href:location.href,local:Object.keys(localStorage),session:Object.keys(sessionStorage),"
    "cookies:document.cookie.split(';').map(c=>c.split('=')[0].trim()).filter(x=>x),idb:[]};"
    "for(const d of dbs){const db=await new Promise(r=>{const q=indexedDB.open(d.name);q.onsuccess=()=>r(q.result);q.onerror=()=>r(null);});"
    "if(db){const info={name:d.name,version:db.version,stores:{}};"
    "for(const sn of Array.from(db.objectStoreNames)){try{const os=db.transaction(sn,'readonly').objectStore(sn);"
    "const recs=await new Promise(r=>{const q=os.getAll(null,5);q.onsuccess=()=>r(q.result||[]);q.onerror=()=>r([]);});"
    "const shape=(v,n)=>v&&typeof v==='object'&&n<3?Object.fromEntries(Object.keys(v).slice(0,30).map(k=>[k,shape(v[k],n+1)])):typeof v;"
    "info.stores[sn]={keyPath:os.keyPath,count:recs.length,shape:recs.map(x=>shape(x,0))};}catch(e){info.stores[sn]='?';}}"
    "out.idb.push(info);db.close();}}return out;}"
)

# Bookmarklet for the regular browser. Runs only on elevenreader.io, so it
# never exposes another site's storage. Firefox gives bookmarklets no
# clipboard access; the text then appears in a prompt for copying.
_BOOKMARKLET = (
    "javascript:(async()=>{if(location.hostname!=='elevenreader.io'){alert('Bitte zuerst "
    "elevenreader.io öffnen und dort angemeldet sein.');location.href='" + HOME_URL + "';return;}"
    + _FIND_AUTH_JS +
    "const give=(t,ok)=>navigator.clipboard.writeText(t).then(()=>alert(ok),"
    "()=>prompt('Mit Strg+C kopieren, dann Enter. Recito übernimmt den Rest von selbst.',t));"
    "const a=await ttsFindAuth();"
    "if(a){give('" + SESSION_PREFIX + "'+btoa(unescape(encodeURIComponent(JSON.stringify(a)))),"
    "'Verbunden. Recito übernimmt die Anmeldung jetzt.');return;}"
    "const d=await ttsDiag();give('" + DIAG_PREFIX + "'+btoa(unescape(encodeURIComponent(JSON.stringify(d)))),"
    "'Anmeldung nicht gefunden. Eine Diagnose (nur Namen, keine Inhalte) ist kopiert, Recito speichert sie.');"
    "})();"
)

_CONNECT_HTML = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<title>ElevenReader verbinden</title><style>
body{font:16px/1.5 "Segoe UI",system-ui,sans-serif;background:#FBF8FF;color:#1B1B21;margin:0}
main{max-width:640px;margin:48px auto;padding:0 24px}
h1{font-size:28px;font-weight:400;margin:0 0 8px}p{color:#46464F}
ol{padding:0;list-style:none;counter-reset:s}li{counter-increment:s;display:flex;gap:16px;margin:24px 0}
li:before{content:counter(s);flex:0 0 28px;height:28px;border-radius:14px;background:#4355B9;
color:#fff;font-weight:700;display:grid;place-items:center;font-size:14px}
a.bm{display:inline-block;margin-top:8px;padding:10px 24px;border-radius:20px;background:#4355B9;
color:#fff;text-decoration:none;font-weight:600;cursor:grab}
a{color:#4355B9}small{color:#46464F}
@media (prefers-color-scheme:dark){body{background:#131318;color:#E4E1E9}p,small{color:#C6C5D0}
li:before,a.bm{background:#BAC3FF;color:#08218A}a{color:#BAC3FF}}
</style></head><body><main>
<h1>ElevenReader mit Recito verbinden</h1>
<p>Einmalig nötig. Google erlaubt keine Anmeldung in eingebetteten Fenstern, deshalb übernimmt
die App deine Anmeldung aus diesem Browser.</p>
<ol>
<li><div><b>Diesen Button in die Lesezeichenleiste ziehen</b><br>
<small>Leiste einblenden: Strg+Shift+B</small><br>
<a class="bm" href="%(bm)s">Mit Recito verbinden</a></div></li>
<li><div><b><a href="%(home)s">elevenreader.io öffnen</a></b> und prüfen, dass du angemeldet bist.</div></li>
<li><div><b>Dort auf das Lesezeichen klicken.</b><br>
<small>Recito meldet sich mit „ElevenReader verbunden“. Danach kannst du das Lesezeichen
wieder löschen.</small></div></li>
</ol></main></body></html>"""


def open_connect_page():
    """Open the instructions with the bookmarklet in the default browser."""
    p = storage_dir() / "verbinden.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    bm = _BOOKMARKLET.replace("&", "&amp;").replace('"', "&quot;")
    p.write_text(_CONNECT_HTML % {"bm": bm, "home": HOME_URL}, encoding="utf-8")
    webbrowser.open(p.as_uri())


# --------------------------------------------------------------------------- #
#  Page scripts
# --------------------------------------------------------------------------- #
# Writes the Firebase user into the page's storage, in the format the Firebase
# library uses itself; Python reloads the page afterwards.
_INJECT_JS = r"""
(() => {
  const a = %(entry)s;
  window.__ttsInject = "running";
  const ok = () => { window.__ttsInject = "done"; };
  const fail = () => { window.__ttsInject = "error"; };
  try {
    if (a.where.type === "local") { localStorage.setItem(a.where.key, JSON.stringify(a.value)); return ok(); }
    if (a.where.type === "session") { sessionStorage.setItem(a.where.key, JSON.stringify(a.value)); return ok(); }
  } catch (e) { return fail(); }
  const put = (db) => {
    const tx = db.transaction(a.where.store, "readwrite");
    tx.objectStore(a.where.store).put(a.raw);
    tx.oncomplete = () => { db.close(); ok(); };
    tx.onerror = fail;
  };
  const first = indexedDB.open(a.where.db);
  first.onerror = fail;
  first.onupgradeneeded = () => first.result.createObjectStore(a.where.store, a.where.keyPath ? { keyPath: a.where.keyPath } : undefined);
  first.onsuccess = () => {
    const db = first.result;
    if (db.objectStoreNames.contains(a.where.store)) return put(db);
    const v = db.version + 1; db.close();
    const up = indexedDB.open(a.where.db, v);
    up.onerror = fail;
    up.onupgradeneeded = () => up.result.createObjectStore(a.where.store, a.where.keyPath ? { keyPath: a.where.keyPath } : undefined);
    up.onsuccess = () => put(up.result);
  };
})();
"""

# Gets the Firebase token from the web app's storage (refreshed via
# securetoken when expired), uploads the text and puts the result into
# window.__ttsReader; Python polls it with a timer.
_UPLOAD_JS = r"""
(() => {
  const job = %(job)s;
  window.__ttsReader = { state: "running" };
  const done = (r) => { window.__ttsReader = Object.assign({ state: "done" }, r); };
  %(find)s
  (async () => {
    const found = await ttsFindAuth();
    const user = found && found.value;
    if (!user || !user.stsTokenManager) return done({ ok: false, needLogin: true });
    let token = user.stsTokenManager.accessToken;
    if (!token || user.stsTokenManager.expirationTime < Date.now() + 60000) {
      const r = await fetch("https://securetoken.googleapis.com/v1/token?key=" + user.apiKey, {
        method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "grant_type=refresh_token&refresh_token=" + encodeURIComponent(user.stsTokenManager.refreshToken) });
      if (!r.ok) return done({ ok: false, needLogin: true, expired: true });
      token = (await r.json()).id_token;
    }
    const fd = new FormData();
    fd.append("from_document", new File([job.text], job.title + ".txt", { type: "text/plain" }));
    const r = await fetch(job.api, { method: "POST", headers: { Authorization: "Bearer " + token }, body: fd });
    const body = await r.text();
    let data = null; try { data = JSON.parse(body); } catch (e) {}
    if (!r.ok) return done({ ok: false, status: r.status, error: (data && data.detail && (data.detail.message || data.detail)) || body.slice(0, 300) });
    const id = data && (data.read_id || (data.read && data.read.read_id));
    if (!id) return done({ ok: false, error: "Antwort ohne read_id: " + body.slice(0, 200) });
    done({ ok: true, readId: id });
  })().catch(err => done({ ok: false, error: String(err) }));
})();
"""

# Starts playback on the document page: finds the play button by its label
# (de/en). Returns "1" once it has clicked.
_PLAY_JS = r"""
(() => {
  // Click only once the new document's page is open: the library has its own
  // "Play" buttons that would start a different document.
  if (!decodeURIComponent(location.pathname).endsWith(%(read)s)) return "";
  const words = ["play", "abspielen", "wiedergabe", "listen", "anhören"];
  const buttons = Array.from(document.querySelectorAll("button, [role=button]")).filter(b => b.offsetParent !== null);
  const label = b => ((b.getAttribute("aria-label") || "") + " " + (b.title || "") + " " +
                      (b.dataset.testid || "") + " " + (b.innerText || "")).toLowerCase();
  // The document page's player has aria-label="Play"; otherwise match by words.
  const hit = buttons.find(b => b.getAttribute("aria-label") === "Play") ||
              buttons.find(b => words.some(w => label(b).includes(w)) && !label(b).includes("pause"));
  if (hit) { hit.click(); return "1"; }
  return "";
})();
"""


# Shown in the window while no login has been taken over, instead of the
# sign-up page, where Google login does not work in this window.
_HINT_HTML = """<!doctype html><html lang="de"><head><meta charset="utf-8"><style>
body{font:16px/1.5 "Segoe UI","Noto Sans",system-ui,sans-serif;background:#FBF8FF;color:#1B1B21;
margin:0;display:grid;place-items:center;height:100vh}
main{max-width:520px;padding:0 24px}h1{font-size:28px;font-weight:400;margin:0 0 12px}
p{color:#46464F}a{display:inline-block;margin-top:16px;padding:10px 24px;border-radius:20px;
background:#4355B9;color:#fff;text-decoration:none;font-weight:600}
@media (prefers-color-scheme:dark){body{background:#131318;color:#E4E1E9}p{color:#C6C5D0}
a{background:#BAC3FF;color:#08218A}}
</style></head><body><main>
<h1>ElevenReader verbinden</h1>
<p>Google erlaubt keine Anmeldung in eingebetteten Fenstern. Deshalb übernimmt die App einmalig
deine Anmeldung aus deinem Browser. Die Anleitung dazu hat sich dort geöffnet.</p>
<p>%(pending)s</p>
<a href="tts://connect">Anleitung erneut öffnen</a>
</main></body></html>"""


class _Page(QWebEnginePage):
    """Intercepts tts:// links on the hint page."""

    def __init__(self, profile, parent, on_link):
        super().__init__(profile, parent)
        self.on_link = on_link

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == "tts":
            self.on_link(url.host())
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


# Small status toast over the page while ElevenReader processes the text
# server-side.
_TOAST_JS = r"""
(() => {
  let t = document.getElementById("tts-toast");
  if (!%(show)s) { if (t) t.remove(); return; }
  if (!t) {
    t = document.createElement("div"); t.id = "tts-toast";
    t.style.cssText = "position:fixed;left:50%%;bottom:32px;transform:translateX(-50%%);z-index:2147483647;" +
      "background:#E4E1E9;color:#303036;font:500 15px system-ui,sans-serif;padding:14px 20px;border-radius:12px;" +
      "box-shadow:0 6px 24px rgba(0,0,0,.35);display:flex;gap:12px;align-items:center";
    const dot = document.createElement("span");
    dot.style.cssText = "width:16px;height:16px;border-radius:50%%;border:2px solid #4355B9;border-top-color:transparent;" +
      "animation:ttsspin .8s linear infinite";
    const st = document.createElement("style"); st.textContent = "@keyframes ttsspin{to{transform:rotate(360deg)}}";
    t.append(st, dot, document.createElement("span"));
    document.body.append(t);
  }
  t.lastChild.textContent = %(text)s;
})();
"""


# --------------------------------------------------------------------------- #
#  Window
# --------------------------------------------------------------------------- #
class ReaderWindow(QMainWindow):
    """ElevenReader window. send(text) creates a document and plays it."""

    POLL_MS = 250

    def __init__(self, on_status=None, on_error=None):
        super().__init__()
        self.on_status = on_status or (lambda msg: None)
        self.on_error = on_error or (lambda msg, text: None)
        self.setWindowTitle("ElevenReader")
        self.setWindowIcon(tts_icons.app_icon())
        self.resize(1100, 820)

        store = storage_dir()
        store.mkdir(parents=True, exist_ok=True)
        # Named profile: cookies and IndexedDB persist across restarts.
        self.profile = QWebEngineProfile("tts-elevenreader", self)
        self.profile.setPersistentStoragePath(str(store / "storage"))
        self.profile.setCachePath(str(store / "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        self.profile.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)

        self.view = QWebEngineView(self)
        self.page = _Page(self.profile, self.view,
                          lambda what: self.on_status("connect") if what == "connect" else None)
        self.view.setPage(self.page)
        self.setCentralWidget(self.view)
        self.page.loadFinished.connect(self._loaded)

        self._ready = False
        self._pending = None            # (text, title) waiting for page/login
        self._injected = False          # session already injected in this run
        self._poll = QTimer(self, interval=self.POLL_MS, timeout=self._check_result)
        self._play_tries = 0
        self._play_timer = QTimer(self, interval=200, timeout=self._try_play)
        self._started = 0.0
        self._attempts = 0
        self._read_id = ""
        if load_session():
            self.view.load(QUrl(HOME_URL))
        else:
            self._show_hint()

    # --- public ---------------------------------------------------------- #
    def open_window(self):
        self.show()
        self.raise_()
        self.activateWindow()
        if self.isMinimized():
            self.showNormal()

    def send(self, text: str):
        text = text.strip()
        if not text:
            return
        self._pending = (text, make_title(text))
        self._attempts = 0
        self.open_window()
        if not load_session():                 # connect first, text waits
            self._show_hint()
            self.on_status("connect")
            return
        if self._ready and not self._poll.isActive():
            self._upload()
        elif "elevenreader.io" not in self.page.url().toString():
            self.view.load(QUrl(HOME_URL))     # e.g. hint page after connecting

    def session_updated(self):
        """New session from the browser: reload the page and continue."""
        self._injected = False
        self.view.load(QUrl(HOME_URL))

    # --- internal -------------------------------------------------------- #
    def _show_hint(self):
        self._ready = False
        pending = ("Dein markierter Text wartet und wird nach dem Verbinden automatisch "
                   "hochgeladen." if self._pending else "")
        self.view.setHtml(_HINT_HTML % {"pending": pending}, QUrl("about:blank"))

    def _loaded(self, ok):
        self._ready = ok and "elevenreader.io" in self.page.url().toString()
        if not self._ready:
            return
        # The login persists in the profile; it is injected only when the
        # upload reports "not logged in" (see _on_result).
        if self._pending and not self._poll.isActive():
            self._upload()

    def _inject_and_reload(self):
        entry = load_session()
        if not entry:
            return False
        self._injected = True
        self.page.runJavaScript("window.__ttsInject = null")
        self.page.runJavaScript(_INJECT_JS % {"entry": json.dumps(entry)})
        started = time.time()

        def wait():
            self.page.runJavaScript("window.__ttsInject || null", 0, check)

        def check(state):
            if state == "done":
                self.view.load(QUrl(HOME_URL))      # Firebase reads the session on load
            elif state == "error" or time.time() - started > 10:
                self._fail("Die ElevenReader-Anmeldung ließ sich nicht übernehmen.")
            else:
                QTimer.singleShot(150, wait)
        QTimer.singleShot(100, wait)
        return True

    def _upload(self):
        if not self._pending:
            return
        text, title = self._pending
        self.on_status("Lege das Dokument in ElevenReader an …")
        self._toast("Wird in ElevenReader angelegt …")
        job = {"text": text, "title": title, "api": API_ADD}
        self.page.runJavaScript(_UPLOAD_JS % {"job": json.dumps(job), "find": _FIND_AUTH_JS})
        self._started = time.time()
        self._poll.start()

    def _check_result(self):
        # Fetched as JSON text: PySide6 passes JavaScript objects from
        # runJavaScript through as an empty string.
        self.page.runJavaScript("JSON.stringify(window.__ttsReader || null)", 0,
                                lambda raw: self._on_result(json.loads(raw) if raw else None))

    def _on_result(self, r):
        if r is None and time.time() - self._started > 1.5:
            # Job lost (page reloaded): start again instead of waiting
            self._poll.stop()
            self._attempts += 1
            if self._attempts <= 3:
                QTimer.singleShot(800, lambda: self._ready and self._upload())
            else:
                self._fail("ElevenReader hat den Auftrag mehrfach verworfen.")
            return
        if not r or r.get("state") != "done":
            if time.time() - self._started > 45:
                self._poll.stop()
                self._fail("ElevenReader hat nicht geantwortet.")
            return
        self._poll.stop()
        self.page.runJavaScript("window.__ttsReader = null")
        if r.get("needLogin"):
            # The profile has no login yet: inject the stored one (once per
            # run; reloads, then uploads). If that one is no longer valid
            # (e.g. account or password change): reconnect, text waits.
            if not r.get("expired") and not self._injected and self._inject_and_reload():
                return
            self._show_hint()
            self.on_status("connect")
            return
        if not r.get("ok"):
            status = r.get("status")
            detail = r.get("error") or "unbekannter Fehler"
            self._fail(f"Hochladen fehlgeschlagen ({status}): {detail}" if status else detail)
            return
        self._pending = None
        # Navigate through the web app's router instead of reloading; the
        # player shows up sooner.
        self._toast("Wiedergabe startet …")
        self._read_id = r["readId"]
        path = "/reader/library/" + r["readId"]
        self.page.runJavaScript(
            "window.next && window.next.router && window.next.router.push ? "
            "window.next.router.push(%s) : location.assign(%s)"
            % (json.dumps(path), json.dumps(READ_URL.format(r["readId"]))))
        self._play_tries = 0
        self._play_timer.start()
        self.on_status("In ElevenReader angelegt, Wiedergabe startet.")

    def _toast(self, text):
        self.page.runJavaScript(_TOAST_JS % {"show": "true" if text else "false",
                                             "text": json.dumps(text or "")})

    def _try_play(self):
        self._play_tries += 1
        if self._play_tries > 25:              # give up, the user clicks play
            self._play_timer.stop()
            return
        def done(hit):
            if hit == "1":
                self._play_timer.stop()
                self._toast(None)
        self.page.runJavaScript(_PLAY_JS % {"read": json.dumps(self._read_id)}, 0, done)

    def _fail(self, msg):
        self._toast(None)
        text = self._pending[0] if self._pending else ""
        self._pending = None
        self.on_error(msg, text)

    def closeEvent(self, ev):
        # Close = hide and go back to the library: this stops playback but
        # keeps the page loaded, so the next hotkey uploads without load time.
        ev.ignore()
        self._play_timer.stop()
        self.hide()
        self.view.load(QUrl(HOME_URL))
