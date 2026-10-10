import QtQuick
import QtQuick.Controls

// Toggle chip for a reading language: check mark and filled when chosen.
// Space or a click toggles; the owner decides (for example keeps one chosen).
Rectangle {
    id: lc
    property string lang
    property bool on: false
    signal toggled()
    implicitWidth: lcRow.implicitWidth + 28
    implicitHeight: 36
    radius: 18
    color: on ? Theme.secondaryContainer : "transparent"
    border.width: on ? 0 : 1
    border.color: Theme.outline
    activeFocusOnTab: true
    Accessible.role: Accessible.CheckBox
    Accessible.name: Tr.langName(lang)
    Accessible.checked: on
    Behavior on color { ColorAnimation { duration: 140 } }
    Keys.onSpacePressed: lc.toggled()
    TapHandler { onTapped: { lc.forceActiveFocus(); lc.toggled() } }
    HoverHandler { id: hov; cursorShape: Qt.PointingHandCursor }
    Rectangle { anchors.fill: parent; radius: parent.radius; color: Theme.colorOnSurface
                opacity: hov.hovered ? 0.06 : 0 }
    FocusRing { shapeRadius: lc.radius; shown: lc.activeFocus }
    Row {
        id: lcRow
        anchors.centerIn: parent
        spacing: 6
        Icon { glyph: Theme.ic.check; visible: lc.on; font.pixelSize: 18
               color: Theme.colorOnSecondaryContainer; anchors.verticalCenter: parent.verticalCenter }
        Label { text: Tr.langName(lc.lang); font.pixelSize: 14; font.weight: Font.Medium
                color: lc.on ? Theme.colorOnSecondaryContainer : Theme.colorOnSurfaceVariant
                anchors.verticalCenter: parent.verticalCenter }
    }
}
