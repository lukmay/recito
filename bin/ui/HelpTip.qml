import QtQuick
import QtQuick.Controls

// Small "?" that explains a term in plain words, on hover or keyboard focus
Item {
    id: ht
    property string text
    implicitWidth: 24
    implicitHeight: 24
    activeFocusOnTab: true
    Accessible.role: Accessible.StaticText
    Accessible.name: text
    Icon {
        anchors.centerIn: parent
        glyph: Theme.ic.help
        font.pixelSize: 18
        color: hover.hovered || ht.activeFocus ? Theme.primary : Theme.outline
    }
    HoverHandler { id: hover; cursorShape: Qt.WhatsThisCursor }
    TapHandler { onTapped: ht.forceActiveFocus() }
    FocusRing { shapeRadius: 12; gap: 1; shown: ht.activeFocus }
    ToolTip {
        visible: hover.hovered || ht.activeFocus
        delay: hover.hovered ? 250 : 0
        width: Math.min(tipLabel.implicitWidth + leftPadding + rightPadding, 340)
        contentItem: Label {
            id: tipLabel
            text: ht.text; wrapMode: Text.Wrap; color: Theme.inverseOnSurface
            font.pixelSize: 13; lineHeight: 1.2
        }
        background: Rectangle { radius: 8; color: Theme.inverseSurface }
    }
}
