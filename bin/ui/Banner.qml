import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Status banner; tone: ok | error | warn | info | busy
Rectangle {
    id: bn
    property string tone: "info"
    property alias text: bnText.text
    Layout.fillWidth: true
    radius: 12
    implicitHeight: bnRow.implicitHeight + 24
    color: tone === "error" ? Theme.errorContainer
           : tone === "ok" ? Theme.okContainer : Theme.surfaceContainerHighest
    Behavior on color { ColorAnimation { duration: 180 } }
    Accessible.role: Accessible.AlertMessage
    Accessible.name: text
    RowLayout {
        id: bnRow
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                  leftMargin: 16; rightMargin: 16 }
        spacing: 12
        // Own spinner: a BusyIndicator fading in nudged the page scroll
        Icon {
            id: bnIcon
            glyph: bn.tone === "busy" ? Theme.ic.refresh : bn.tone === "ok" ? Theme.ic.checkCircle
                   : bn.tone === "error" ? Theme.ic.error : bn.tone === "warn" ? Theme.ic.warning
                   : Theme.ic.info
            color: bn.tone === "ok" ? Theme.ok : bn.tone === "error" ? Theme.colorOnErrorContainer
                   : bn.tone === "warn" ? Theme.warn : Theme.primary
            Layout.alignment: Qt.AlignTop
            RotationAnimation on rotation {
                running: bn.tone === "busy"; from: 0; to: 360; duration: 900
                loops: Animation.Infinite
                onRunningChanged: if (!running) bnIcon.rotation = 0
            }
        }
        Label {
            id: bnText
            Layout.fillWidth: true; wrapMode: Text.Wrap; lineHeight: 1.2
            color: bn.tone === "error" ? Theme.colorOnErrorContainer : Theme.colorOnSurface
        }
    }
}
