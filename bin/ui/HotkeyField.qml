import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Shortcut recorder styled like an M3 outlined field: click, press a
// combination. Delete, Backspace or Esc clear it; Tab moves on.
// Uses backend.keyCombo() to turn a key press into Qt portable text.
Rectangle {
    id: rec
    property string combo: ""
    property string label: ""            // accessible name, e.g. the action
    property bool invalid: false         // error outline (conflict, unusable combo)
    signal recorded(string combo)
    implicitWidth: 260
    implicitHeight: 48
    radius: 8
    color: rec.activeFocus ? Theme.surfaceContainerHigh : "transparent"
    border.width: rec.activeFocus || rec.invalid ? 2 : 1
    border.color: rec.invalid ? Theme.error : rec.activeFocus ? Theme.primary : Theme.outline
    activeFocusOnTab: true
    Accessible.role: Accessible.EditableText
    Accessible.name: rec.label
    Accessible.description: rec.combo || Tr.t("hotkeys.unset")
    RowLayout {
        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 12
        spacing: 8
        Keycaps { combo: rec.combo; visible: rec.combo !== "" }
        Label {
            visible: rec.combo === "" || rec.activeFocus
            Layout.fillWidth: true
            Layout.leftMargin: rec.combo === "" ? 4 : 0
            text: rec.activeFocus ? (rec.combo ? "" : Tr.t("hotkeys.press")) : Tr.t("hotkeys.unset")
            color: Theme.colorOnSurfaceVariant
            elide: Text.ElideRight
        }
        Item { Layout.fillWidth: true; visible: rec.combo !== "" && !rec.activeFocus }
        Icon { glyph: rec.invalid ? Theme.ic.error : Theme.ic.keyboard; font.pixelSize: 18
               color: rec.invalid ? Theme.error : rec.activeFocus ? Theme.primary : Theme.colorOnSurfaceVariant }
    }
    TapHandler { onTapped: rec.forceActiveFocus() }
    HoverHandler { cursorShape: Qt.IBeamCursor }
    Keys.onPressed: (event) => {
        if (event.modifiers === Qt.NoModifier &&
                (event.key === Qt.Key_Escape || event.key === Qt.Key_Delete
                 || event.key === Qt.Key_Backspace)) {
            rec.recorded(""); event.accepted = true; return
        }
        if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) return
        var s = backend.keyCombo(event.key, event.modifiers)
        if (s) rec.recorded(s)
        event.accepted = true
    }
}
