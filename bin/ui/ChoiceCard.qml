import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Big selectable card: icon, title, supporting text, optional badge and an
// extra slot on the right (for example a Listen button). Space or Enter selects.
Rectangle {
    id: card
    default property alias extra: extraRow.data
    property string glyph: ""
    property string title: ""
    property string supporting: ""
    property string badge: ""
    property bool selected: false
    property bool selectable: true
    property var caps: []                // capability keys, shown as a quiet line
    property string note: ""             // extra line with a warning mark, e.g. language coverage
    property string avatar: ""           // initial letter instead of an icon
    property color avatarColor: Theme.secondaryContainer
    signal picked()
    Layout.fillWidth: true
    implicitHeight: Math.max(row.implicitHeight + 28, 64)
    radius: 16
    color: selected ? Theme.secondaryContainer : Theme.surfaceContainerLow
    border.width: selected ? 2 : 1
    border.color: selected ? Theme.primary : Theme.outlineVariant
    scale: tap.pressed ? 0.98 : 1.0
    activeFocusOnTab: selectable
    Accessible.role: Accessible.RadioButton
    Accessible.name: title + ". " + supporting + (note !== "" ? ". " + note : "")
    Accessible.description: caps.map(function (k) { return Tr.t("cap." + k) }).join(", ")
    Accessible.checked: selected
    Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
    Behavior on color { ColorAnimation { duration: 160 } }
    Behavior on border.color { ColorAnimation { duration: 160 } }
    Keys.onPressed: (e) => {
        if (selectable && (e.key === Qt.Key_Space || e.key === Qt.Key_Return || e.key === Qt.Key_Enter)) {
            card.picked(); e.accepted = true
        }
    }

    Rectangle {
        anchors.fill: parent; radius: parent.radius; color: Theme.colorOnSurface
        opacity: !card.selectable ? 0 : tap.pressed ? 0.10 : hover.hovered ? 0.05 : 0
    }
    HoverHandler { id: hover; enabled: card.selectable; cursorShape: Qt.PointingHandCursor }
    TapHandler { id: tap; enabled: card.selectable; onTapped: { card.forceActiveFocus(); card.picked() } }
    FocusRing { shapeRadius: card.radius; shown: card.activeFocus }

    RowLayout {
        id: row
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                  leftMargin: 16; rightMargin: 16 }
        spacing: 14
        Rectangle {
            visible: card.glyph !== "" || card.avatar !== ""
            implicitWidth: 40; implicitHeight: 40; radius: 20
            Layout.alignment: Qt.AlignTop
            color: card.selected ? Theme.primary
                   : card.avatar !== "" ? card.avatarColor : Theme.secondaryContainer
            Behavior on color { ColorAnimation { duration: 160 } }
            Icon { anchors.centerIn: parent; glyph: card.glyph; visible: card.avatar === ""
                   color: card.selected ? Theme.colorOnPrimary : Theme.colorOnSecondaryContainer }
            Label { anchors.centerIn: parent; text: card.avatar; visible: card.avatar !== ""
                    font.pixelSize: 17; font.weight: Font.DemiBold
                    color: card.selected ? Theme.colorOnPrimary : Theme.colorOnSurface }
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 3
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    text: card.title; font.pixelSize: 16; font.weight: Font.DemiBold
                    color: card.selected ? Theme.colorOnSecondaryContainer : Theme.colorOnSurface
                    Layout.fillWidth: card.badge === ""
                    wrapMode: Text.Wrap
                }
                Chip { visible: card.badge !== ""; text: card.badge; accent: true; glyph: Theme.ic.sparkle }
                Item { visible: card.badge !== ""; Layout.fillWidth: true }
            }
            Small {
                visible: card.supporting !== ""
                text: card.supporting
                Layout.fillWidth: true
                color: card.selected ? Theme.colorOnSecondaryContainer : Theme.colorOnSurfaceVariant
                font.pixelSize: 13
            }
            RowLayout {
                visible: card.note !== ""
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 6
                Icon { glyph: Theme.ic.translate; font.pixelSize: 16; color: Theme.warn
                       Layout.alignment: Qt.AlignTop }
                Small { text: card.note; Layout.fillWidth: true; font.pixelSize: 12
                        color: card.selected ? Theme.colorOnSecondaryContainer : Theme.colorOnSurfaceVariant }
            }
            CapabilityList {
                caps: card.caps
                Layout.fillWidth: true
                Layout.topMargin: 6
                ink: card.selected ? Theme.colorOnSecondaryContainer : Theme.colorOnSurfaceVariant
            }
        }
        RowLayout { id: extraRow; spacing: 8; Layout.alignment: Qt.AlignVCenter }
        Icon {
            visible: card.selectable
            glyph: card.selected ? Theme.ic.checkCircle : Theme.ic.unchecked
            color: card.selected ? Theme.primary : Theme.outline
            font.pixelSize: 22
            scale: card.selected ? 1 : 0.85
            Behavior on scale { NumberAnimation { duration: 240; easing.type: Easing.OutBack } }
        }
    }
}
