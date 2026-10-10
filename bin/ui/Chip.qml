import QtQuick
import QtQuick.Controls

// Display-only tag with an optional icon and a plain-words tooltip.
// Three looks, none of them button-like: a soft tonal tag (default), an
// accent tag for badges, and "plain" (icon plus text, no container) for
// capability lists. With a tip it can take keyboard focus to show the tip.
Rectangle {
    id: chip
    property alias text: lbl.text
    property bool accent: false
    property bool plain: false
    property string glyph: ""
    property string tip: ""
    // ink follows the card behind the tag (selected or not)
    property color ink: accent ? Theme.colorOnTertiaryContainer : Theme.colorOnSurfaceVariant
    property color glyphColor: plain && !accent ? Theme.primary : ink
    readonly property bool explaining: hover.hovered || chip.activeFocus

    implicitHeight: plain ? 20 : 22
    // plain tags keep a gap on the right instead of a container padding
    implicitWidth: row.implicitWidth + (plain ? 12 : 16)
    radius: 6
    color: plain ? "transparent"
           : accent ? Theme.tertiaryContainer
           : Qt.rgba(ink.r, ink.g, ink.b, Theme.dark ? 0.12 : 0.08)
    activeFocusOnTab: tip !== ""
    Accessible.role: Accessible.StaticText
    Accessible.name: text
    Accessible.description: tip

    Row {
        id: row
        x: chip.plain ? 0 : (parent.width - width) / 2
        anchors.verticalCenter: parent.verticalCenter
        spacing: chip.plain ? 5 : 4
        Icon {
            visible: chip.glyph !== ""; glyph: chip.glyph
            font.pixelSize: chip.plain ? 15 : 14
            color: chip.glyphColor
            anchors.verticalCenter: parent.verticalCenter
        }
        Label {
            id: lbl
            font.pixelSize: 12
            font.weight: chip.plain ? Font.Normal : Font.Medium
            color: chip.ink
            anchors.verticalCenter: parent.verticalCenter
            // a dotted underline hints that hovering explains the term
            Row {
                visible: chip.plain && chip.tip !== ""
                anchors { left: parent.left; top: parent.baseline; topMargin: 3 }
                width: parent.width
                clip: true
                spacing: 2
                opacity: chip.explaining ? 0.8 : 0.35
                Behavior on opacity { NumberAnimation { duration: 120 } }
                Repeater {
                    model: Math.ceil(lbl.width / 4)
                    Rectangle { width: 2; height: 1; color: chip.ink }
                }
            }
        }
    }
    HoverHandler { id: hover; enabled: chip.tip !== ""; cursorShape: Qt.WhatsThisCursor }
    FocusRing { shapeRadius: chip.radius; gap: 2; shown: chip.activeFocus }
    ToolTip {
        visible: chip.explaining && chip.tip !== ""
        delay: hover.hovered ? 300 : 0
        width: Math.min(tipLabel.implicitWidth + leftPadding + rightPadding, 320)
        contentItem: Label { id: tipLabel; text: chip.tip; wrapMode: Text.Wrap
                             color: Theme.inverseOnSurface; font.pixelSize: 13; lineHeight: 1.2 }
        background: Rectangle { radius: 8; color: Theme.inverseSurface }
    }
}
