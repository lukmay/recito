import QtQuick
import QtQuick.Controls

// "Ctrl+Alt+P" drawn as keyboard keys; modifier names follow the UI language.
// Keys can be shown pressed (down: {"Ctrl": true}), can invite with a gentle
// press wave, and with interactive set they act as one button (activated).
Item {
    id: kc
    property string combo: ""
    property bool large: false
    property var down: ({})            // part name -> held
    property bool lit: false           // whole combo held: caps glow
    property bool inviting: false      // periodic press wave, left to right
    property bool interactive: false
    signal activated()
    implicitWidth: capRow.implicitWidth
    implicitHeight: capRow.implicitHeight
    readonly property var parts: {
        if (!combo) return []
        var p = combo.split("+").filter(function (x) { return x !== "" })
        if (combo.endsWith("++")) p.push("+")
        return p
    }
    // presses every cap in turn, as if the combo was typed
    function tapAll() { tapWave() }
    signal tapWave()
    signal inviteWave()
    readonly property int tapMs: Math.max(0, parts.length - 1) * 70   // until the last cap is down

    activeFocusOnTab: interactive
    Accessible.role: interactive ? Accessible.Button : Accessible.StaticText
    Accessible.name: combo
    Keys.onPressed: (e) => {
        if (interactive && !e.isAutoRepeat && (e.key === Qt.Key_Space || e.key === Qt.Key_Return
                                                || e.key === Qt.Key_Enter)) {
            kc.activated(); e.accepted = true
        }
    }

    Timer {
        interval: 2600; repeat: true; triggeredOnStart: false
        running: kc.inviting && kc.visible
        onTriggered: kc.inviteWave()
    }

    Row {
        id: capRow
        spacing: kc.large ? 6 : 4
        Repeater {
            model: kc.parts
            // darker base below the key cap gives it depth; a held key sinks into it
            Item {
                id: key
                required property string modelData
                required property int index
                readonly property real depth: kc.large ? 4 : 3
                property bool tapped: false
                property real ghost: 0                 // invite wave, 0..1
                readonly property bool held: kc.down[modelData] === true || tapped
                implicitWidth: Math.max(kc.large ? 40 : 28, keyLabel.implicitWidth + (kc.large ? 24 : 14))
                implicitHeight: kc.large ? 42 : 28

                Rectangle {
                    anchors.fill: parent
                    radius: kc.large ? 10 : 7
                    color: key.held || kc.lit ? Theme.primary : Theme.outlineVariant
                    Behavior on color { ColorAnimation { duration: 90 } }
                }
                Rectangle {
                    id: cap
                    width: parent.width
                    height: parent.height - key.depth
                    radius: kc.large ? 10 : 7
                    y: key.held ? key.depth : key.depth * key.ghost
                    Behavior on y {
                        enabled: !ghostAnim.running
                        NumberAnimation { duration: key.held ? 50 : 170
                                          easing.type: key.held ? Easing.OutQuad : Easing.OutBack }
                    }
                    color: key.held ? Theme.primaryContainer
                           : kc.lit ? Theme.secondaryContainer
                           : Qt.tint(Theme.surfaceContainerLow, Qt.rgba(Theme.primary.r, Theme.primary.g,
                                                                     Theme.primary.b, key.ghost * 0.3))
                    Behavior on color { ColorAnimation { duration: 90 } }
                    border.width: 1
                    border.color: key.held || kc.lit ? Theme.primary
                                  : kc.interactive && hover.hovered || key.ghost > 0.5 ? Theme.outline : Theme.outlineVariant
                    Label {
                        id: keyLabel
                        anchors.centerIn: parent
                        // Meta is the Windows key on Windows and Super elsewhere
                        text: key.modelData === "Meta" && Qt.platform.os !== "windows" ? Tr.t("key.Super")
                              : Tr.t("key." + key.modelData, undefined, key.modelData)
                        font.pixelSize: kc.large ? 16 : 12
                        font.weight: Font.DemiBold
                        color: key.held ? Theme.colorOnPrimaryContainer : Theme.colorOnSurface
                    }
                }
                SequentialAnimation {
                    id: ghostAnim
                    PauseAnimation { duration: key.index * 110 }
                    NumberAnimation { target: key; property: "ghost"; to: 1; duration: 110; easing.type: Easing.OutQuad }
                    NumberAnimation { target: key; property: "ghost"; to: 0; duration: 280; easing.type: Easing.OutBack }
                }
                SequentialAnimation {
                    id: tapAnim
                    PauseAnimation { duration: key.index * 70 }
                    PropertyAction { target: key; property: "tapped"; value: true }
                    PauseAnimation { duration: (kc.parts.length - key.index) * 70 + 200 }
                    PropertyAction { target: key; property: "tapped"; value: false }
                }
                Connections {
                    target: kc
                    function onInviteWave() { if (!key.held) ghostAnim.restart() }
                    function onTapWave() { ghostAnim.stop(); key.ghost = 0; tapAnim.restart() }
                }
            }
        }
    }

    HoverHandler { id: hover; enabled: kc.interactive; cursorShape: Qt.PointingHandCursor }
    TapHandler { enabled: kc.interactive; onTapped: { kc.forceActiveFocus(); kc.activated() } }
    FocusRing { shapeRadius: kc.large ? 10 : 7; gap: 4; shown: kc.activeFocus && kc.interactive }
}
