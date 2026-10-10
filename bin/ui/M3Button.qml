import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

// Pill button drawn by hand (container, outline, state layer), so it looks the
// same with every graphics backend. Variants set container/ink/outlined.
Button {
    id: mb
    property string glyph: ""
    property color ink: Theme.primary
    property color container: "transparent"
    property bool outlined: false
    property alias glyphItem: icon
    readonly property bool filled: container.a > 0
    flat: true
    topInset: 4
    bottomInset: 4
    implicitHeight: 48
    scale: pressed ? 0.96 : 1.0
    Behavior on scale { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }
    leftPadding: glyph && text ? 16 : text ? 24 : 12
    rightPadding: text ? 24 : 12
    Accessible.name: text
    hoverEnabled: true

    background: Rectangle {
        implicitWidth: 40
        implicitHeight: 40
        radius: height / 2
        color: !mb.filled ? "transparent"
               : mb.enabled ? mb.container : Qt.rgba(Theme.colorOnSurface.r, Theme.colorOnSurface.g,
                                                     Theme.colorOnSurface.b, 0.12)
        border.width: mb.outlined ? 1 : 0
        border.color: mb.enabled ? Theme.outline : Theme.outlineVariant
        Behavior on color { ColorAnimation { duration: 180 } }
        Rectangle {                 // state layer
            anchors.fill: parent
            radius: parent.radius
            color: mb.ink
            opacity: !mb.enabled ? 0 : mb.pressed ? 0.14 : mb.hovered ? 0.08 : 0
            Behavior on opacity { NumberAnimation { duration: 90 } }
        }
    }
    contentItem: RowLayout {
        spacing: 8
        Icon {
            id: icon
            glyph: mb.glyph; visible: mb.glyph !== ""; font.pixelSize: 18
            color: mb.enabled ? mb.ink : Theme.outline
            Layout.alignment: Qt.AlignVCenter
        }
        Label {
            visible: mb.text !== ""
            text: mb.text; font.pixelSize: 14; font.weight: Font.DemiBold
            color: mb.enabled ? mb.ink : Theme.outline
            Layout.alignment: Qt.AlignVCenter
        }
    }
    FocusRing {
        target: mb.background
        shapeRadius: mb.background ? mb.background.height / 2 : 20
        shown: mb.visualFocus
    }
}
