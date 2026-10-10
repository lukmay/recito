import QtQuick
import QtQuick.Layouts

// Filled card with an optional icon, title, help tip and supporting text
Rectangle {
    id: sec
    default property alias content: col.data
    property alias spacing: col.spacing
    property string title: ""
    property string supporting: ""
    property string glyph: ""
    property string help: ""
    Layout.fillWidth: true
    radius: 20
    color: Theme.surfaceContainer
    implicitHeight: col.implicitHeight + 44
    ColumnLayout {
        id: col
        anchors { left: parent.left; right: parent.right; top: parent.top
                  leftMargin: 24; rightMargin: 24; topMargin: 20 }
        spacing: 14
        RowLayout {
            visible: sec.title !== ""
            spacing: 12
            Icon { glyph: sec.glyph; visible: sec.glyph !== ""; color: Theme.primary; font.pixelSize: 22 }
            Title { text: sec.title; Layout.fillWidth: false; Layout.maximumWidth: col.width - 80 }
            HelpTip { visible: sec.help !== ""; text: sec.help }
            Item { Layout.fillWidth: true }
        }
        // Pull the subtitle towards the title without overlapping at spacing 0
        Body { text: sec.supporting; visible: sec.supporting !== ""
               Layout.topMargin: col.spacing >= 8 ? -6 : 4 }
    }
}
