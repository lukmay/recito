# -*- coding: utf-8 -*-
"""Realistic Markdown and Obsidian notes for the golden tests of the preset
rules (test_presets.py). SAME: the former obsidian filter and the presets
read them alike. DIFFERENT: intended changes, with what each reads now."""

SAME = {
    "plain prose": "Das ist ein ganz normaler Satz. Er hat keine Formatierung, nur Punkte und Kommas.",
    "lecture note": """# Vorlesung 3: Gradientenabstieg

Der **Gradientenabstieg** minimiert eine Funktion $f(x)$ schrittweise.
Die Lernrate $\\eta$ bestimmt die Schrittweite, siehe [[Lernrate]].

## Update-Regel

$$w_{t+1} = w_t - \\eta \\nabla f(w_t)$$

- Ist $\\eta$ zu groß, divergiert das Verfahren.
- Ist sie zu *klein*, dauert es lange.
- Mehr in [[Optimierung/Konvexe Funktionen|konvexen Funktionen]].
""",
    "todo list": """## Heute

1. Folien lesen
2. Blatt 3 abgeben, siehe [Moodle](https://moodle.rwth-aachen.de/course/view.php?id=1)
3. ~~Mensa~~ Kochen

* Erster Punkt
+ Zweiter Punkt
""",
    "code and emphasis": """Nutze `git rebase` statt `git merge`, wenn die Historie __linear__ bleiben soll.

```bash
git fetch origin
git rebase origin/main
```

Das ist _wichtig_ für saubere Commits, aber snake_case_namen bleiben.
""",
    "quote and links": """> Ein Zitat aus dem Buch, mit **Betonung**.
> Zweite Zeile des Zitats.

Siehe auch [[Notizen/Bücher/Atomic Habits.md]] und [[Projektplan#Meilensteine]].
Ein Bild: ![Diagramm](diagram.png) und danach Text.
""",
    "nested lists": """- Oberpunkt
    - Unterpunkt mit **fett**
    - Unterpunkt mit [Link](http://example.com)
        1. Tief verschachtelt
""",
    "separator": """Erster Abschnitt.

---

Zweiter Abschnitt.

***

Dritter Abschnitt.
""",
    "readest copy": "Die Verteilung t=y(𝐱,𝐰)+ϵ t = y(\\mathbf{x},\\mathbf{w}) + \\epsilon ist normal.",
    "math in markdown": "Für alle $x \\in \\mathbb{R}$ gilt $x^2 \\geq 0$, und $\\frac{a}{b}$ ist ein Bruch.",
    "headings levels": "# H1\n## H2\n### H3 mit `code`\n#### H4\n##### H5\n###### H6\nText.",
    "emphasis mix": "Ein ***sehr*** wichtiger und **fett *kursiv* gemischter** Satz mit ~~Fehler~~.",
    "punctuation spaces": "Text  mit   vielen    Leerzeichen , und Komma .",
}

# name: (input, what the former obsidian+latex filters read, what the presets read)
DIFFERENT = {
    "embed is dropped": (
        "Vorher ![[Diagramm.png]] nachher.",
        "Vorher ! Diagramm nachher.",
        "Vorher nachher.",
    ),
    "callout keeps its title": (
        "> [!note] Merke\n> Der Inhalt.",
        "[!note] Merke\nDer Inhalt.",
        "Merke\nDer Inhalt.",
    ),
    "tags lose the # sign": (
        "Projekt #uni/master und #idee, aber nicht C# oder #1.",
        "Projekt #uni/master und #idee, aber nicht C# oder #1.",
        "Projekt uni/master und idee, aber nicht C# oder #1.",
    ),
    "block ids are dropped": (
        "Ein wichtiger Absatz. ^abc123\nNächster Absatz.",
        "Ein wichtiger Absatz. ^abc123\nNächster Absatz.",
        "Ein wichtiger Absatz.\nNächster Absatz.",
    ),
    "comments are dropped": (
        "Sichtbar %%unsichtbar, auch über\nmehrere Zeilen%% weiter.",
        "Sichtbar %%unsichtbar, auch über\nmehrere Zeilen%% weiter.",
        "Sichtbar weiter.",
    ),
    "highlights keep their text": (
        "Das ist ==sehr wichtig== hier.",
        "Das ist ==sehr wichtig== hier.",
        "Das ist sehr wichtig hier.",
    ),
    "front matter is dropped": (
        "---\ntags: [uni]\naliases:\n  - GD\n---\n# Titel\nText.",
        "tags: [uni]\naliases:\nGD\nTitel\nText.",
        "Titel\nText.",
    ),
    "tables read as pauses": (
        "| Name | Note |\n|------|:----:|\n| Anna | 1,3 |\n| Ben | 2,0 |",
        "| Name | Note |\n|------|:----:|\n| Anna | 1,3 |\n| Ben | 2,0 |",
        "Name, Note\nAnna, 1,3\nBen, 2,0",
    ),
    "bare web addresses read the site": (
        "Mehr unter https://www.example.com/docs/page?id=3 und <https://rwth.de>.",
        "Mehr unter https://www.example.com/docs/page?id=3 und <https://rwth.de>.",
        "Mehr unter example.com und rwth.de.",
    ),
    "html tags are dropped": (
        "Zeile<br>mit <span style=\"color:red\">Farbe</span> <!-- Kommentar -->Ende.",
        "Zeile<br>mit <span style=\"color:red\">Farbe</span> <!-- Kommentar -->Ende.",
        "Zeilemit Farbe Ende.",
    ),
    "footnotes": (
        "Ein Satz mit Fußnote[^1].\n\n[^1]: Die Quelle.",
        "Ein Satz mit Fußnote[^1].\n[^1]: Die Quelle.",
        "Ein Satz mit Fußnote.\nDie Quelle.",
    ),
    "task boxes and nested quotes": (
        "- [ ] offen\n- [x] erledigt\n> > tief zitiert",
        "[ ] offen\n[x] erledigt\n> tief zitiert",
        "offen\nerledigt\ntief zitiert",
    ),
    "dollar amounts stay prose": (
        "Das kostet $5 und $10 im Monat.",
        "Das kostet fünf und 10 im Monat.",
        "Das kostet $5 und $10 im Monat.",
    ),
    "numbered with parenthesis": (
        "1) Erstens\n2) Zweitens",
        "1) Erstens\n2) Zweitens",
        "Erstens\nZweitens",
    ),
}
