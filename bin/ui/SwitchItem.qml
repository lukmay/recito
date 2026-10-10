import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// List item with a switch; clicking the text toggles it too
RowLayout {
    id: si
    property alias checked: sw.checked
    property string title
    property string supporting
    Layout.fillWidth: true
    spacing: 16
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 2
        Label { text: si.title; font.pixelSize: 15; color: Theme.colorOnSurface
                Layout.fillWidth: true; wrapMode: Text.Wrap }
        Small { text: si.supporting; Layout.fillWidth: true; visible: text !== "" }
        TapHandler { onTapped: sw.toggle() }
    }
    Switch {
        id: sw
        Layout.alignment: Qt.AlignVCenter
        Accessible.name: si.title
        FocusRing {
            target: sw.indicator
            shapeRadius: sw.indicator ? sw.indicator.height / 2 : 16
            shown: sw.visualFocus
        }
    }
}
