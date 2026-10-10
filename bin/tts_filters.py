# -*- coding: utf-8 -*-
"""
Code for the text clean-up before reading aloud, and the preset rules.

Everything the daemon does to a text before reading it is one ordered list
of rules in rules.json (tts_rules). Most rules are regular expressions; a
few transforms need code and live here as functions text -> text, the code
rules (kind 'builtin'):

  latex        copied LaTeX/Unicode math (e.g. from Readest) -> spoken German
  dollar-math  formulas between $ signs in Markdown -> spoken German

PRESETS are the rules the app ships, in the groups Obsidian, Markdown and
Formulas. In rules.json they are ordinary rules: the user can change, switch
off, delete and restore them (tts_rules).

Advanced: a tts_filters.py in the config folder (tts_platform.piper_dir())
is loaded as well. Functions in its FILTERS dict add code rules or replace
built-in ones of the same name (tts-rules add-code <name>).
"""

import importlib.util
import re
import unicodedata
from pathlib import Path

# --------------------------------------------------------------------------- #
#  LaTeX / Unicode math  ->  spoken German
# --------------------------------------------------------------------------- #
# Text copied from rendered pages (Readest and others) contains every formula
# twice: first as Unicode glyphs (t=y(𝐱,𝐰)+ϵ), then as LaTeX source
# (t = y(\mathbf{x},...)). The Unicode form is lossy (superscripts flattened:
# β−1 instead of β^{-1}), so it is dropped and the full LaTeX copy is
# translated.

_GREEK = {  # LaTeX command -> German pronunciation
    "alpha": "Alpha", "beta": "Beta", "gamma": "Gamma", "delta": "Delta",
    "epsilon": "Epsilon", "varepsilon": "Epsilon", "zeta": "Zeta", "eta": "Eta",
    "theta": "Theta", "vartheta": "Theta", "iota": "Jota", "kappa": "Kappa",
    "lambda": "Lambda", "mu": "Mü", "nu": "Nü", "xi": "Xi", "pi": "Pi",
    "rho": "Rho", "sigma": "Sigma", "tau": "Tau", "phi": "Phi", "varphi": "Phi",
    "chi": "Chi", "psi": "Psi", "omega": "Omega", "Gamma": "Gamma",
    "Delta": "Delta", "Theta": "Theta", "Lambda": "Lambda", "Xi": "Xi",
    "Pi": "Pi", "Sigma": "Sigma", "Phi": "Phi", "Psi": "Psi", "Omega": "Omega",
}
_CMD = {  # other LaTeX commands
    "mid": "gegeben", "propto": "proportional zu", "sim": "verteilt nach",
    "cdot": "mal", "times": "mal", "in": "element von", "infty": "unendlich",
    "sum": "Summe", "int": "Integral", "partial": "partiell", "nabla": "Nabla",
    "approx": "ungefähr gleich", "leq": "kleiner gleich", "geq": "größer gleich",
    "neq": "ungleich", "to": "gegen", "log": "Logarithmus",
    "ln": "natürlicher Logarithmus", "exp": "e hoch", "prod": "Produkt",
    "sqrt": "Wurzel", "pm": "plus minus", "mp": "minus plus", "equiv": "äquivalent",
}
_UGREEK = {  # Unicode Greek -> German (for Unicode-only math without LaTeX)
    "α": "Alpha", "β": "Beta", "γ": "Gamma", "δ": "Delta", "ε": "Epsilon",
    "ϵ": "Epsilon", "ζ": "Zeta", "η": "Eta", "θ": "Theta", "ϑ": "Theta",
    "κ": "Kappa", "λ": "Lambda", "μ": "Mü", "ν": "Nü", "ξ": "Xi", "π": "Pi",
    "ρ": "Rho", "σ": "Sigma", "τ": "Tau", "φ": "Phi", "ϕ": "Phi", "χ": "Chi",
    "ψ": "Psi", "ω": "Omega", "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta",
    "Λ": "Lambda", "Σ": "Sigma", "Φ": "Phi", "Ω": "Omega",
}
_USYM = {  # Unicode operators/relations -> German
    "−": " minus ", "∣": " gegeben ", "∝": " proportional zu ", "⋅": " mal ",
    "×": " mal ", "∈": " element von ", "≈": " ungefähr gleich ",
    "≤": " kleiner gleich ", "≥": " größer gleich ", "≠": " ungleich ",
    "→": " gegen ", "∞": " unendlich ", "∑": " Summe ", "∫": " Integral ",
    "√": " Wurzel ", "∇": " Nabla ",
}
_NUM = {"0": "null", "1": "eins", "2": "zwei", "3": "drei", "4": "vier",
        "5": "fünf", "6": "sechs", "7": "sieben", "8": "acht", "9": "neun"}


def _is_umath(c):
    """Unicode math character? (bold/script alphabets, Greek, operators)."""
    return (0x1D400 <= ord(c) <= 0x1D7FF) or c in _UGREEK or c in _USYM


def _num_de(s):
    return _NUM.get(s, s)


def _brace_group(t, j):
    """Read a balanced {...} group from position j -> (content, end_pos).
    Skips leading blanks. content is None if no '{' follows."""
    while j < len(t) and t[j] in " \t":
        j += 1
    if j >= len(t) or t[j] != "{":
        return None, j
    depth, start = 0, j
    while j < len(t):
        if t[j] == "{":
            depth += 1
        elif t[j] == "}":
            depth -= 1
            if depth == 0:
                return t[start + 1:j], j + 1
        j += 1
    return None, j


def _expand_frac(t):
    """\\frac{A}{B} -> ' A durch B ' with balanced braces. A and B may contain
    braces themselves (e.g. ^{D/2}), which a plain regex cannot match."""
    out, i = [], 0
    while True:
        k = t.find(r"\frac", i)
        if k == -1:
            out.append(t[i:])
            return "".join(out)
        out.append(t[i:k])
        a, j = _brace_group(t, k + 5)
        b, j = _brace_group(t, j)
        if a is None or b is None:
            out.append(t[k:j])
        else:
            out.append(" " + a + " durch " + b + " ")
        i = j


def _conv_latex(t):
    """Translate a LaTeX expression into German words."""
    t = re.sub(r"\\[!,;:>]", " ", t)                       # \, \! \; etc.
    t = re.sub(r"\\(left|right|big|Big|bigg|Bigg|quad|qquad)\b", " ", t)
    t = re.sub(r"\\([{}|])", r"\1", t)                     # \{ \} \| -> { } |
    t = re.sub(r"\\mathbf\{0\}", " Nullvektor ", t)        # must precede the general rule
    t = re.sub(r"\\mathbf\{I\}", " Einheitsmatrix ", t)
    t = re.sub(r"\\mathbf\{([a-z])\}", r" Vektor \1 ", t)
    t = re.sub(r"\\mathbf\{([A-Z])\}", r" Matrix \1 ", t)
    t = re.sub(r"\\mathcal\{N\}", " Normalverteilung ", t)
    t = re.sub(r"\\mathrm\{T\}", "T", t)                   # transpose marker (-> ^T)
    # Bold/vector/accent wrappers: keep only the content (\boldsymbol{\mu} -> \mu)
    t = re.sub(r"\\(boldsymbol|bm|vec|hat|bar|tilde|overline|underline)\{([^}]*)\}",
               r" \2 ", t)
    t = re.sub(r"\\(mathcal|mathrm|mathbb|mathsf|mathit|text|operatorname)\{([^}]*)\}",
               r" \2 ", t)
    t = _expand_frac(t)
    t = re.sub(r"\\([a-zA-Z]+)",
               lambda m: " " + _GREEK.get(m.group(1),
                                          _CMD.get(m.group(1), m.group(1))) + " ", t)
    t = re.sub(r"\^\{?-1\}?", " hoch minus eins ", t)
    t = re.sub(r"\^\{?T\}?", " transponiert ", t)
    t = re.sub(r"\^\{([^}]*)\}", r" hoch \1 ", t)
    t = re.sub(r"\^(\w)", r" hoch \1 ", t)
    t = re.sub(r"_\{([^}]*)\}", r" Index \1 ", t)
    t = re.sub(r"_(\w)", r" Index \1 ", t)
    t = (t.replace("/", " durch ").replace("(", " von ").replace(")", " ")
          .replace("{", " ").replace("}", " ").replace("=", " gleich ")
          .replace("+", " plus ").replace("-", " minus ").replace("|", " gegeben ")
          .replace(",", " Komma "))
    t = re.sub(r"\b(\d+)\b", lambda m: _num_de(m.group(1)), t)
    # Safety net: no leftover formula control characters reach the TTS
    t = t.replace("^", " hoch ").replace("\\", " ").replace("$", " ")
    return re.sub(r"\s+", " ", t).strip()


def _conv_unicode(seg):
    """Translate Unicode-only math (no LaTeX copy) by folding the glyphs."""
    r = []
    for c in seg:
        if c in _UGREEK:
            r.append(" " + _UGREEK[c] + " ")
        elif c in _USYM:
            r.append(_USYM[c])
        elif 0x1D400 <= ord(c) <= 0x1D7FF:
            r.append(unicodedata.normalize("NFKC", c))     # 𝐱->x, 𝒩->N, 𝟎->0
        else:
            r.append(c)
    return _conv_latex("".join(r))


def _is_word(t):
    return len(t) >= 2 and t.isalpha() and not any(_is_umath(c) for c in t)


def _isolate_latex(seg):
    """Extract the LaTeX copy from a duplicated math segment (Unicode + LaTeX).
    The boundary is after the last Unicode math character. Leading ASCII is
    kept only if it is a real LaTeX start (e.g. 't =', 'p('); leftovers such
    as '/2' or '1)' are dropped and the copy starts at the first backslash.
    None = no LaTeX copy."""
    fb = seg.find("\\")
    if fb == -1:
        return None
    ums = [k for k, c in enumerate(seg) if _is_umath(c)]
    if ums and ums[-1] < fb:
        core = seg[ums[-1] + 1:fb].lstrip(" \t)]},/0123456789")
        return (core + seg[fb:]) if re.search(r"[A-Za-z]\s*[=(]", core) else seg[fb:]
    return seg[fb:]


def latex(text):
    """Filter: copied LaTeX/Unicode math -> speakable German.

    Prose stays untouched (fast path when there is no backslash or Unicode
    math). Math segments are detected, the lossy Unicode rendering is dropped
    and the LaTeX source is translated to German."""
    if "\\" not in text and not any(_is_umath(c) for c in text):
        return text                                        # plain prose
    toks = [m.group(0) for m in
            re.finditer(r"\\[a-zA-Z]+|\\[!,;:]|\w+|\s+|[^\w\s]", text, re.UNICODE)]
    out, j = [], 0
    while j < len(toks):
        tk = toks[j]
        if _is_word(tk) or tk in ".!?:;" or tk.isspace():
            out.append(tk)
            j += 1
            continue
        anchor = tk.startswith("\\") or any(_is_umath(c) for c in tk)
        mathish = (anchor or (len(tk) == 1 and tk in "(){}[]^_|,=+-*/<>")
                   or (len(tk) <= 1 and tk.isalnum()) or tk.isdigit())
        if not mathish:
            out.append(tk)
            j += 1
            continue
        buf = [tk]
        j += 1
        while j < len(toks):                               # collect the math region
            t2 = toks[j]
            if _is_word(t2) or t2 in ".!?":
                break
            if t2.startswith("\\") or any(_is_umath(c) for c in t2):
                anchor = True
            buf.append(t2)
            j += 1
        seg = "".join(buf).strip()
        trail = ""
        while seg and seg[-1] in ",.;:":                   # strip sentence punctuation
            trail = seg[-1] + trail
            seg = seg[:-1]
        if not anchor:                                     # not real math
            out.append(seg + trail)
            continue
        latex_src = _isolate_latex(seg)
        body = _conv_latex(latex_src) if latex_src is not None else _conv_unicode(seg)
        out.append(" " + body + " " + trail + " ")
    txt = "".join(out)
    txt = re.sub(r"\s+([.,;:!?])", r"\1", txt)
    return re.sub(r"[ \t]+", " ", txt).strip()




# --------------------------------------------------------------------------- #
#  Formulas between $ signs (Markdown, Obsidian)
# --------------------------------------------------------------------------- #
# $$...$$ becomes its own sentence (periods around it give a pause). Inline
# $...$ follows Pandoc: no space after the opening or before the closing $,
# and no digit right after the closing $, so "$5 and $10" stays prose.
_BLOCK_MATH = re.compile(r"\$\$(.+?)\$\$", re.S)
_INLINE_MATH = re.compile(r"\$(?=[^\s$])([^$\n]+?)(?<=[^\s$])\$(?!\d)")


def dollar_math(text):
    """Code rule: formulas between $ signs -> speakable German."""
    if "$" not in text:
        return text
    text = _BLOCK_MATH.sub(lambda m: ". " + _conv_latex(m.group(1)) + ". ", text)
    return _INLINE_MATH.sub(lambda m: " " + _conv_latex(m.group(1)) + " ", text)


# Code rules by name; a rule {"kind": "builtin", "builtin": name} runs one.
BUILTINS = {
    "latex": latex,
    "dollar-math": dollar_math,
}


def registry(user_dir=None, log=None):
    """BUILTINS plus the FILTERS dict of <user_dir>/tts_filters.py, if any."""
    reg = dict(BUILTINS)
    if user_dir is None:
        import tts_platform
        user_dir = tts_platform.piper_dir()
    user_mod = Path(user_dir) / "tts_filters.py"
    if user_mod.exists():
        try:
            spec = importlib.util.spec_from_file_location("tts_filters_user", str(user_mod))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            reg.update({str(k): v for k, v in getattr(mod, "FILTERS", {}).items() if callable(v)})
        except Exception as exc:
            if log:
                log(f"user tts_filters error ({exc})")
    return reg


# --------------------------------------------------------------------------- #
#  Preset rules
# --------------------------------------------------------------------------- #
# Groups in list order. Obsidian runs before Markdown because its syntax
# builds on Markdown ([[a|b]] would otherwise lose its | to the table rule).
# Formulas run last, on text without Markdown marks. "user" holds the user's
# own rules and is not a preset group.
GROUPS = ("obsidian", "markdown", "formulas")
USER_GROUP = "user"
ALL_GROUPS = GROUPS + (USER_GROUP,)
PRESET_VERSION = 1


def _p(preset_id, group, pattern, replacement, note):
    return {"preset_id": preset_id, "group": group, "kind": "regex", "pattern": pattern,
            "replacement": replacement, "lang": "all", "note": note}


def _code(preset_id, group, note):
    return {"preset_id": preset_id, "group": group, "kind": "builtin", "builtin": preset_id,
            "pattern": "", "replacement": "", "lang": "all", "note": note}


PRESETS = [
    # --- Obsidian --------------------------------------------------------- #
    _p("obs-comment", "obsidian", r"(?s)%%.*?%%", "",
       "Comments between %% are not read"),
    _p("obs-embed", "obsidian", r"!\[\[[^\]]*\]\]", "",
       "Embedded notes and images ![[...]] are left out"),
    _p("obs-callout", "obsidian", r"(?m)^[ \t]*>[ \t]*\[![\w-]+\][+-]?[ \t]*", "",
       "Callouts > [!note] Title keep only their title"),
    _p("obs-link-alias", "obsidian", r"\[\[[^\]|]*\|[ \t]*([^\]]*?)[ \t]*\]\]", r"\1",
       "Links with a display name [[Page|Name]] read the name"),
    _p("obs-link", "obsidian",
       r"(?i)\[\[(?:[^\]|#]*/)?([^\]|#/]*?)(?:\.(?:md|pdf|png|jpe?g|gif|svg|canvas))?"
       r"(?:#[^\]|]*)?\]\]", r"\1",
       "Links [[Folder/Page#Heading]] read the page name"),
    _p("obs-block-id", "obsidian", r"(?m)(?:^|[ \t]+)\^[A-Za-z0-9-]+[ \t]*$", "",
       "Block IDs ^abc123 at the end of a line are left out"),
    _p("obs-highlight", "obsidian", r"==([^=\n]+?)==", r"\1",
       "Highlights ==text== keep their text"),
    _p("obs-tag", "obsidian", r"(?<![\w#&/\\])#([^\W\d][\w/-]*)", r"\1",
       "Tags #idea are read without the # sign"),
    # --- Markdown --------------------------------------------------------- #
    _p("md-front-matter", "markdown",
       r"\A---[ \t]*\n(?:[ \t]*(?:[\w.-][\w .-]*:[^\n]*|-[^\n]*)?\n)*?---[ \t]*(?:\n|\Z)", "",
       "Properties (front matter) at the top are not read"),
    _code("dollar-math", "markdown",
          "Formulas between $ signs are read as words (in German)"),
    _p("md-code-fence", "markdown", r"(?m)^[ \t]*(?:```|~~~)[^\n]*$", "",
       "Code block fences ``` are dropped, the code inside is read"),
    _p("md-html", "markdown", r"(?s)<!--.*?-->|</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>", "",
       "HTML tags and comments are left out"),
    _p("md-image", "markdown", r"!\[[^\]]*\]\([^)]*\)", "",
       "Images ![alt](file) are left out"),
    _p("md-link", "markdown", r"\[([^\]]+?)\]\([^)]*\)", r"\1",
       "Links [text](address) read only the text"),
    _p("md-footnote", "markdown", r"(?m)^\[\^[^\]\s]+\]:[ \t]*|\[\^[^\]\s]+\]", "",
       "Footnote marks [^1] are left out, the footnote text is read"),
    _p("md-url", "markdown",
       r"<?\bhttps?://(?:www\.)?([^\s/?#<>]+)[^\s<>]*?>?(?=[.,;:!?)\]]*(?:\s|$))", r"\1",
       "Web addresses read only the site name"),
    _p("md-heading", "markdown", r"(?m)^[ \t]*#{1,6}[ \t]+", "",
       "Heading marks # are left out"),
    _p("md-quote", "markdown", r"(?m)^[ \t]*>(?:[ \t]?>)*[ \t]?", "",
       "Quote marks > at the start of a line are left out"),
    _p("md-table-rule", "markdown",
       r"(?m)^[ \t]*\|?(?:[ \t]*:?-+:?[ \t]*\|)+(?:[ \t]*:?-+:?[ \t]*)?$", "",
       "Table separator lines |---|---| are left out"),
    _p("md-table-cells", "markdown", r"(?m)[ \t]*\|[ \t]*(?=[^\n]*\|[ \t]*$)", ", ",
       "Table cells: each | inside a row becomes a short pause"),
    _p("md-table-edges", "markdown", r"(?m)^, |[ \t]*\|[ \t]*$", "",
       "Table rows lose their outer | marks"),
    _p("md-rule", "markdown", r"(?m)^[ \t]*([-*_=])(?:[ \t]*\1){2,}[ \t]*$", "",
       "Horizontal lines --- and *** are left out"),
    _p("md-bullet", "markdown", r"(?m)^[ \t]*[-*+][ \t]+(?:\[[ xX/-]\][ \t]+)?", "",
       "List bullets and checkboxes - [ ] are left out"),
    _p("md-number", "markdown", r"(?m)^[ \t]*\d+[.)][ \t]+", "",
       "List numbers 1. at the start of a line are left out"),
    _p("md-bold", "markdown", r"\*\*([^*]+?)\*\*|__([^_]+?)__", r"\1\2",
       "Bold **text** keeps its text"),
    _p("md-italic", "markdown", r"\*([^*\n]+?)\*|(?<![\w\\])_([^_\n]+?)_(?!\w)", r"\1\2",
       "Italic *text* keeps its text"),
    _p("md-strike", "markdown", r"~~([^~]+?)~~", r"\1",
       "Strikethrough ~~text~~ keeps its text"),
    _p("md-code", "markdown", r"`([^`\n]+?)`", r"\1",
       "Inline code `text` keeps its text"),
    _p("md-spaces", "markdown", r"(?m)^[ \t]+|[ \t]+$|[ \t]+(?=[.,;:!?])|(?<=[ \t])[ \t]+", "",
       "Tidies up the spaces the rules above leave behind"),
    _p("md-blank-lines", "markdown", r"\n[ \t]*\n(?:[ \t]*\n)*", r"\n",
       "Empty lines are dropped"),
    # --- Formulas --------------------------------------------------------- #
    _code("latex", "formulas",
          "Copied LaTeX and math symbols are read as words (in German)"),
]
for _r in PRESETS:
    _r["preset_version"] = PRESET_VERSION
PRESETS_BY_ID = {r["preset_id"]: r for r in PRESETS}

# Former filters= names in voices.conf -> the preset groups they stand for
LEGACY_FILTERS = {"obsidian": ("obsidian", "markdown"), "latex": ("formulas",)}
