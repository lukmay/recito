import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Numbered setup step
RowLayout {
    id: st
    default property alias content: stepCol.data
    property int number
    property string title
    Layout.fillWidth: true
    spacing: 16
    Rectangle {
        Layout.alignment: Qt.AlignTop
        implicitWidth: 28; implicitHeight: 28; radius: 14; color: Theme.primary
        Label { anchors.centerIn: parent; text: st.number; color: Theme.colorOnPrimary
                font.pixelSize: 13; font.weight: Font.Bold }
    }
    ColumnLayout {
        id: stepCol
        Layout.fillWidth: true
        spacing: 8
        Label { text: st.title; color: Theme.colorOnSurface; font.pixelSize: 15
                font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.Wrap
                Layout.topMargin: 3 }
    }
}
