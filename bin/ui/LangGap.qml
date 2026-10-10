import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

// A reading language without a voice of its own: who reads it instead
// (route), and the download of the curated Piper voice with progress.
// engineTitle "" means the row stands for Piper itself; then route "engine"
// says the active engine (activeTitle) reads the language anyway.
ColumnLayout {
    id: gap
    property string lang
    property string engineTitle: ""
    property string route: "none"         // engine | piper | edge | none (tts_langs.speak_route)
    property string activeTitle: ""
    property string byVoice: ""           // display name of the voice that steps in
    property string piperVoice: ""        // curated Piper voice to download
    property int piperMb: 63
    property real labelWidth: 84
    property string phase: "idle"        // idle | busy | error
    property real frac: 0
    property string error: ""
    signal installed(string voice)

    readonly property string piperName: {
        var p = piperVoice.split("-")
        var n = p.length > 1 ? p[1].replace(/_/g, " ") : piperVoice
        return n.charAt(0).toUpperCase() + n.slice(1)
    }
    readonly property bool offerDownload: route !== "piper" && piperVoice !== ""
    // the download row lines up with the text after the icon
    readonly property real indent: (labelWidth > 0 ? labelWidth + 12 : 0) + 30
    Layout.fillWidth: true
    spacing: 6

    Connections {
        target: backend
        function onPiperProgress(lang, done, total) {
            if (lang !== gap.lang) return
            gap.phase = "busy"
            gap.frac = total > 0 ? done / total : 0
        }
        function onPiperDone(lang, ok, msg) {
            if (lang !== gap.lang) return
            gap.phase = ok || msg === "" ? "idle" : "error"
            gap.error = ok ? "" : msg
            if (ok) gap.installed(msg)
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 12
        Label {
            visible: gap.labelWidth > 0
            text: Tr.langName(gap.lang)
            Layout.preferredWidth: gap.labelWidth
            Layout.alignment: Qt.AlignTop
            color: Theme.colorOnSurfaceVariant; elide: Text.ElideRight
        }
        Icon {
            glyph: gap.route === "none" ? Theme.ic.warning : Theme.ic.info
            color: gap.route === "none" ? Theme.warn : Theme.primary
            font.pixelSize: 18
            Layout.alignment: Qt.AlignTop
        }
        Small {
            Layout.fillWidth: true
            font.pixelSize: 13
            color: Theme.colorOnSurface
            text: {
                var head = gap.engineTitle !== ""
                         ? Tr.t("lang.gap.engine", { engine: gap.engineTitle, lang: Tr.langName(gap.lang) })
                         : Tr.t("lang.gap.piperNone", { lang: Tr.langName(gap.lang) })
                var tail = gap.route === "engine" ? Tr.t("lang.gap.byEngine", { engine: gap.activeTitle })
                         : gap.route === "piper" ? Tr.t("lang.gap.byPiper", { voice: gap.byVoice })
                         : gap.route === "edge" ? Tr.t("lang.gap.byEdge", { voice: gap.byVoice })
                         : Tr.t("lang.gap.none")
                return head + " " + tail
            }
        }
    }
    RowLayout {
        visible: gap.offerDownload
        Layout.fillWidth: true
        Layout.leftMargin: gap.indent
        spacing: 12
        TonalButton {
            visible: gap.phase !== "busy"
            text: Tr.t("piper.download", { name: gap.piperName, mb: gap.piperMb })
            glyph: Theme.ic.download
            onClicked: {
                gap.error = ""
                if (backend.downloadPiper(gap.lang)) { gap.phase = "busy"; gap.frac = 0 }
            }
        }
        ProgressBar {
            visible: gap.phase === "busy"
            Layout.fillWidth: true
            from: 0; to: 1; value: gap.frac
            indeterminate: gap.frac <= 0
            Accessible.name: Tr.t("piper.downloading", { name: gap.piperName })
        }
        Label {
            visible: gap.phase === "busy"
            text: Tr.num(gap.frac * 100, 0) + " %"
            color: Theme.colorOnSurfaceVariant; font.pixelSize: 12
        }
        TextButton {
            visible: gap.phase === "busy"
            text: Tr.t("common.cancel")
            onClicked: backend.cancelPiper(gap.lang)
        }
        Item { Layout.fillWidth: gap.phase !== "busy" }
    }
    Small {
        visible: gap.phase === "error"
        Layout.fillWidth: true
        Layout.leftMargin: gap.indent
        color: Theme.error
        text: Tr.t("piper.failed", { error: gap.error })
    }
}
