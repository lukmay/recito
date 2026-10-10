pragma Singleton
// Material 3 color roles (baseline violet with a rose tertiary), icons and
// type scale shared by the settings window and the onboarding.
// The "on-…" roles are called colorOn…: QML treats properties that start with
// "on" plus a capital letter as signal handlers.
import QtQuick

QtObject {
    id: t

    readonly property string forced: backend.themeOverride()
    readonly property bool dark: forced !== "" ? forced === "dark"
                                 : Qt.styleHints.colorScheme === Qt.ColorScheme.Dark
                                   || (Qt.styleHints.colorScheme === Qt.ColorScheme.Unknown
                                       && backend.systemDark())

    readonly property color primary: dark ? "#D0BCFF" : "#6750A4"
    readonly property color colorOnPrimary: dark ? "#381E72" : "#FFFFFF"
    readonly property color primaryContainer: dark ? "#4F378B" : "#EADDFF"
    readonly property color colorOnPrimaryContainer: dark ? "#EADDFF" : "#21005D"
    readonly property color secondaryContainer: dark ? "#4A4458" : "#E8DEF8"
    readonly property color colorOnSecondaryContainer: dark ? "#E8DEF8" : "#1D192B"
    readonly property color tertiary: dark ? "#EFB8C8" : "#7D5260"
    readonly property color tertiaryContainer: dark ? "#633B48" : "#FFD8E4"
    readonly property color colorOnTertiaryContainer: dark ? "#FFD8E4" : "#31111D"
    readonly property color errorContainer: dark ? "#8C1D18" : "#F9DEDC"
    readonly property color colorOnErrorContainer: dark ? "#F9DEDC" : "#410E0B"
    readonly property color surface: dark ? "#141218" : "#FEF7FF"
    readonly property color surfaceContainerLow: dark ? "#1D1B20" : "#F7F2FA"
    readonly property color surfaceContainer: dark ? "#211F26" : "#F3EDF7"
    readonly property color surfaceContainerHigh: dark ? "#2B2930" : "#ECE6F0"
    readonly property color surfaceContainerHighest: dark ? "#36343B" : "#E6E0E9"
    readonly property color colorOnSurface: dark ? "#E6E0E9" : "#1D1B20"
    readonly property color colorOnSurfaceVariant: dark ? "#CAC4D0" : "#49454F"
    readonly property color outline: dark ? "#938F99" : "#79747E"
    readonly property color outlineVariant: dark ? "#49454F" : "#CAC4D0"
    readonly property color inverseSurface: dark ? "#E6E0E9" : "#322F35"
    readonly property color inverseOnSurface: dark ? "#322F35" : "#F5EFF7"
    readonly property color ok: dark ? "#8BD7A0" : "#1B6C3A"
    readonly property color okContainer: dark ? "#1E4D2E" : "#D3F5DC"
    readonly property color warn: dark ? "#FFB86E" : "#8A5100"
    readonly property color error: dark ? "#F2B8B5" : "#B3261E"

    readonly property string fontFamily: Qt.platform.os === "windows" ? "Segoe UI" : "Noto Sans"
    readonly property string iconFamily: iconFont.name
    property FontLoader iconFont: FontLoader { source: "../fonts/MaterialIconsRound-Regular.otf" }

    // Soft avatar colors for voices (warm pastels in light, muted in dark)
    readonly property var avatarColors: dark ? ["#4F378B", "#633B48", "#3B4F3A", "#4A4458", "#5C4326"]
                                             : ["#EADDFF", "#FFD8E4", "#D3F5DC", "#E8DEF8", "#FFE0C2"]
    function avatarColor(name) {
        var h = 0
        for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 997
        return avatarColors[h % avatarColors.length]
    }
    function engineGlyph(key) {
        return ({ elevenlabs: ic.app, edge: ic.cloud, kokoro2: ic.memory,
                  piper: ic.bolt, sapi: ic.desktop, mssp: ic.voice, qwen3: ic.tune,
                  chatterbox: ic.mic, fish: ic.mic, stepaudio: ic.tune })[key] || ic.voice
    }

    // Material Icons Round codepoints
    readonly property var ic: ({
        engine: "", speed: "", filter: "", keyboard: "",
        play: "", check: "", checkCircle: "", key: "",
        open: "", error: "", info: "", cloud: "",
        app: "", refresh: "", clear: "", savings: "",
        translate: "", fallback: "", cancel: "", warning: "",
        memory: "", bolt: "", desktop: "", voice: "",
        unchecked: "", tune: "", schedule: "", add: "",
        list: "", mic: "", upload: "", stop: "",
        del: "", speaker: "", pause: "", replay: "",
        back: "", forward: "", close: "", help: "",
        language: "", computer: "", board: "", sparkle: "",
        celebrate: "", wave: "", headphones: "", copy: "",
        download: "", rocket: "", settings: "", taskAlt: "",
        lock: "", book: ""
    })
}
