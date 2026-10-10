pragma Singleton
// Translation lookup for QML. Reads the "i18n" context property (tts_i18n.py),
// so every binding that calls t() updates live when the language changes.
import QtQuick

QtObject {
    readonly property var strings: i18n.strings
    readonly property string language: i18n.language
    readonly property var locale: Qt.locale(i18n.locale)

    // t("key", {name: "x"}) -> text with {name} filled in; fallback when the
    // key is missing everywhere (for texts that come from the backend).
    function t(key, args, fallback) {
        var s = strings[key]
        if (s === undefined) s = fallback !== undefined ? fallback : key
        if (args)
            for (var k in args) s = s.split("{" + k + "}").join(args[k])
        return s
    }
    function has(key) { return strings[key] !== undefined }
    function num(n, digits) { return Number(n).toLocaleString(locale, "f", digits || 0) }
    function langName(code) { return t("lang." + code, undefined, code) }
    // capability keys -> [{text, tip}] for chips
    function caps(keys) {
        return (keys || []).map(function (k) { return { text: t("cap." + k), tip: t("cap." + k + ".tip") } })
    }
    function engineTitle(key, fallback) { return t("engine." + key + ".title", undefined, fallback || key) }
    function engineBlurb(key) { return t("engine." + key + ".blurb", undefined, "") }
}
