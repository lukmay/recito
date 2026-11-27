import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Shortcut recorder styled like an M3 outlined field: click, press a
// combination. Delete, Backspace or Esc clear it; Tab moves on.
// On Windows the keys come from a keyboard hook (backend.keyRecordStart,
// signal keyRecorded), which sees the physical key even when the layout
// turns Ctrl+Alt+E into "€" or another program holds the combination.
// Elsewhere Qt key events go through backend.keyCombo().
Rectangle {
    id: rec
    property string combo: ""
    property string label: ""            // accessible name, e.g. the action
    property bool invalid: false         // error outline (conflict, unusable combo)
    property int hook: 0                 // token of the running key recorder, 0 = none
    property string pending: ""          // modifiers held while recording, "Ctrl+Alt"
    property bool rejected: false        // last key cannot be a shortcut
    readonly property bool recording: activeFocus
    signal recorded(string combo)
    implicitWidth: 260
    implicitHeight: 48
    radius: 8
    color: rec.activeFocus ? Theme.surfaceContainerHigh : "transparent"
    border.width: rec.activeFocus || rec.invalid ? 2 : 1
    border.color: rec.invalid || rec.rejected ? Theme.error : rec.activeFocus ? Theme.primary : Theme.outline
    activeFocusOnTab: true
    Accessible.role: Accessible.EditableText
    Accessible.name: rec.label
    Accessible.description: rec.combo || Tr.t("hotkeys.unset")

    function finish(c) {
        rec.pending = ""
        rec.rejected = false
        rec.recorded(c)
        rec.focus = false                // done: show the result, stop recording
    }
    onActiveFocusChanged: {
        pending = ""
        rejected = false
        if (activeFocus) {
            hook = backend.keyRecordStart()
        } else if (hook) {
            backend.keyRecordStop(hook)
            hook = 0
        }
    }
    Component.onDestruction: if (hook) backend.keyRecordStop(hook)
    Connections {
        target: backend
        enabled: rec.hook !== 0 && rec.activeFocus
        function onKeyRecorded(kind, value) {
            if (kind === "mods") { rec.pending = value; if (value) rec.rejected = false }
            else if (kind === "combo") rec.finish(value)
            else if (kind === "clear") rec.finish("")
            else if (kind === "reject") { rec.rejected = true; rejectTimer.restart() }
            else if (kind === "tab") {
                var next = rec.nextItemInFocusChain(value !== "back")
                if (next) next.forceActiveFocus(value === "back" ? Qt.BacktabFocusReason : Qt.TabFocusReason)
            }
        }
    }
    Timer { id: rejectTimer; interval: 2500; onTriggered: rec.rejected = false }

    RowLayout {
        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 12
        spacing: 8
        Keycaps {
            combo: rec.recording ? rec.pending : rec.combo
            visible: combo !== ""
        }
        Label {
            visible: rec.recording || rec.combo === ""
            Layout.fillWidth: true
            Layout.leftMargin: rec.recording && rec.pending ? 0 : 4
            text: !rec.recording ? Tr.t("hotkeys.unset")
                  : rec.rejected ? Tr.t("hotkeys.reject")
                  : rec.pending ? "…" : Tr.t("hotkeys.press")
            color: rec.rejected ? Theme.error : Theme.colorOnSurfaceVariant
            elide: Text.ElideRight
        }
        Item { Layout.fillWidth: true; visible: rec.combo !== "" && !rec.recording }
        Icon { glyph: rec.invalid || rec.rejected ? Theme.ic.error : Theme.ic.keyboard; font.pixelSize: 18
               color: rec.invalid || rec.rejected ? Theme.error
                      : rec.activeFocus ? Theme.primary : Theme.colorOnSurfaceVariant }
    }
    TapHandler { onTapped: rec.forceActiveFocus(Qt.MouseFocusReason) }
    HoverHandler { cursorShape: Qt.IBeamCursor }
    // Without the hook (Linux, or the hook failed): Qt key events
    Keys.onPressed: (event) => {
        if (rec.hook) { event.accepted = true; return }
        if (event.modifiers === Qt.NoModifier &&
                (event.key === Qt.Key_Escape || event.key === Qt.Key_Delete
                 || event.key === Qt.Key_Backspace)) {
            rec.finish(""); event.accepted = true; return
        }
        if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) return
        var s = backend.keyCombo(event.key, event.modifiers, event.nativeScanCode)
        if (s) rec.finish(s)
        event.accepted = true
    }
}
