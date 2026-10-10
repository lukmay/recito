# Translations

Every text that the tray menu, the settings window and the setup assistant show
comes from a catalog in this folder. A catalog is one JSON file per language,
named after the two-letter language code: `en.json` for English, `de.json` for
German, `fr.json`, `es.json` and `it.json` for French, Spanish and Italian.
English is the source catalog. When a key is missing in another
catalog, the app shows the English text for that key.

## Adding a language

1. Copy `en.json` to a file named after the language code, for example `pl.json`.
2. Set `_meta` at the top: `name` is the language name in that language
   (`"Polski"`), `locale` is used for numbers (`"pl_PL"`), and `decimal` is
   the decimal separator (`","`).
3. Translate the values. Keep every key unchanged.
4. Keep placeholders in curly braces exactly as they are, for example `{name}`
   or `{gb}`. The app fills them in at runtime, and their position in the
   sentence may move.
5. Run `python scripts/check_i18n.py`. It lists missing and extra keys,
   placeholders that differ from English, em dashes and values that are still
   the English text. The test suite runs the same check on every catalog.
6. Restart the tray. The language appears in the language menu of the tray and
   in the settings window, and the setting "Same as system" picks it
   automatically when the system runs in that language.

Nothing needs to be built or compiled. A catalog that is only partly
translated works too, with the untranslated keys shown in English.

## Conventions

Keys are grouped by a prefix that names where the text appears: `nav.` for the
navigation, `onb.` for the setup assistant, `tray.` for the tray menu and its
notifications, `engine.<key>.` for the engine cards. Write the way the English
catalog does: address the reader directly, use short sentences and plain words,
and describe what a control does rather than how it was made.

## Using a key in code

In Python, `from tts_i18n import tr` and then `tr("save.failed", error=str(exc))`.
In QML, inside `bin/ui/` or with `import "ui"`, call
`Tr.t("save.failed", {error: msg})`. QML bindings that call `Tr.t` update by
themselves when the language changes. A new key goes into `en.json` first and
then into every other catalog.
