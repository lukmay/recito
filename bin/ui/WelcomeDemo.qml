import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Try-it card on the welcome page: the read shortcut as keycaps that follow
// the real keyboard, and a short purely visual "reading" moment (a sample
// sentence lit word by word) when the whole combo is pressed or the keys are
// clicked. Nothing is spoken; the page wires keyEvent() and binds the mascot
// to "playing".
Rectangle {
    id: wd
    property string combo: ""
    readonly property bool playing: anim.running && progress < 1
    property real progress: 0             // 0..1 through the sample sentence
    property bool tried: false
    property var held: ({})               // part name -> held on the keyboard

    readonly property var parts: caps.parts
    readonly property var words: Tr.t("onb.welcome.sample").split(" ")
    readonly property int wordIndex: Math.min(words.length - 1, Math.floor(progress * words.length))
    readonly property string keysText: parts.map(function (p) {
        return p === "Meta" && Qt.platform.os !== "windows" ? Tr.t("key.Super") : Tr.t("key." + p, undefined, p)
    }).join(" + ")

    implicitWidth: col.implicitWidth + 64
    implicitHeight: col.implicitHeight + 28
    radius: 20
    color: Theme.surfaceContainer

    function play() {
        accept.restart()
        anim.restart()
    }
    function keyCode(part) {
        var named = { Ctrl: Qt.Key_Control, Alt: Qt.Key_Alt, Shift: Qt.Key_Shift, Meta: Qt.Key_Meta,
                      Space: Qt.Key_Space, Del: Qt.Key_Delete, Esc: Qt.Key_Escape, Ins: Qt.Key_Insert,
                      Home: Qt.Key_Home, End: Qt.Key_End, PgUp: Qt.Key_PageUp, PgDown: Qt.Key_PageDown,
                      Tab: Qt.Key_Tab, Backspace: Qt.Key_Backspace, Return: Qt.Key_Return, Enter: Qt.Key_Enter }
        if (named[part] !== undefined) return named[part]
        if (/^F\d+$/.test(part)) return Qt.Key_F1 + parseInt(part.slice(1)) - 1
        if (part.length === 1) return part.toUpperCase().charCodeAt(0)
        return -1
    }
    // Feed key presses and releases of the page; returns true when the key
    // belongs to the combo. Modifiers are re-read from every event, so a key
    // released outside the window cannot stay stuck.
    function keyEvent(e, pressed) {
        if (!parts.length) return false
        var masks = { Ctrl: Qt.ControlModifier, Alt: Qt.AltModifier, Shift: Qt.ShiftModifier,
                      Meta: Qt.MetaModifier }
        var d = {}, hit = false
        for (var i = 0; i < parts.length; i++) {
            var p = parts[i]
            if (e.key === keyCode(p)) { d[p] = pressed; hit = true }
            else if (masks[p] !== undefined) d[p] = (e.modifiers & masks[p]) !== 0
            else d[p] = held[p] === true
        }
        held = d
        if (hit && pressed && !e.isAutoRepeat
                && parts.every(function (x) { return d[x] === true }))
            play()
        return hit
    }
    readonly property bool appActive: Qt.application.state === Qt.ApplicationActive
    onAppActiveChanged: held = ({})

    SequentialAnimation {
        id: anim
        PropertyAction { target: wd; property: "progress"; value: 0 }
        NumberAnimation { target: wd; property: "progress"; to: 1; duration: 1500 }
        PauseAnimation { duration: 220 }
        PropertyAction { target: wd; property: "tried"; value: true }
    }

    ColumnLayout {
        id: col
        anchors.centerIn: parent
        spacing: 10
        Small {
            text: wd.combo ? Tr.t("onb.welcome.howto") : Tr.t("onb.welcome.howtoNoKey")
            font.pixelSize: 13
            Layout.alignment: Qt.AlignHCenter
            horizontalAlignment: Text.AlignHCenter
            Layout.maximumWidth: 420
        }
        Item {
            visible: wd.combo !== ""
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: caps.implicitWidth
            implicitHeight: caps.implicitHeight
            // ring that pops out of the keys when the combo lands
            Rectangle {
                id: ring
                anchors.centerIn: parent
                width: parent.width + 16; height: parent.height + 16
                radius: 16
                color: "transparent"
                border.width: 2
                border.color: Theme.primary
                opacity: 0
                ParallelAnimation {
                    id: accept
                    NumberAnimation { target: ring; property: "opacity"; from: 0.7; to: 0; duration: 520; easing.type: Easing.OutCubic }
                    NumberAnimation { target: ring; property: "scale"; from: 0.94; to: 1.12; duration: 520; easing.type: Easing.OutCubic }
                }
            }
            Keycaps {
                id: caps
                combo: wd.combo
                large: true
                interactive: true
                down: wd.held
                inviting: !wd.tried && !anim.running
                Accessible.name: Tr.t("onb.welcome.tryKeys", { keys: wd.keysText })
                onActivated: { tapAll(); tapDelay.restart() }
                Timer { id: tapDelay; interval: caps.tapMs + 40; onTriggered: wd.play() }
            }
        }

        // Stage below the keys: hint, then the sentence, then the confirmation.
        // Fixed height, so nothing around it moves.
        Item {
            id: stage
            visible: wd.combo !== ""
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: Math.max(hint.implicitWidth, sentence.implicitWidth, done.implicitWidth)
            implicitHeight: 28

            Row {
                id: hint
                anchors.centerIn: parent
                spacing: 6
                opacity: !wd.tried && !anim.running ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 160 } }
                Icon { glyph: Theme.ic.keyboard; font.pixelSize: 16; color: Theme.primary
                       anchors.verticalCenter: parent.verticalCenter }
                Label {
                    text: Tr.t("onb.welcome.try", { keys: wd.keysText })
                    font.pixelSize: 13; font.weight: Font.Medium
                    color: Theme.primary
                    anchors.verticalCenter: parent.verticalCenter
                }
                // breathes gently until the first try
                SequentialAnimation on scale {
                    running: hint.visible; loops: Animation.Infinite
                    NumberAnimation { to: 1.04; duration: 1300; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1.0; duration: 1300; easing.type: Easing.InOutSine }
                }
            }

            Item {
                id: sentence
                anchors.centerIn: parent
                implicitWidth: wordRow.implicitWidth
                implicitHeight: wordRow.implicitHeight
                width: implicitWidth; height: implicitHeight
                opacity: wd.playing ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: wd.playing ? 120 : 200 } }
                // highlight that glides from word to word
                Rectangle {
                    readonly property Item w: wordRepeater.count > wd.wordIndex ? wordRepeater.itemAt(wd.wordIndex) : null
                    x: w ? w.x - 4 : 0
                    width: w ? w.width + 8 : 0
                    y: -3; height: parent.height + 6
                    radius: 8
                    color: Qt.rgba(Theme.primary.r, Theme.primary.g, Theme.primary.b, Theme.dark ? 0.26 : 0.2)
                    Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                    Behavior on width { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                }
                Row {
                    id: wordRow
                    spacing: 8
                    Repeater {
                        id: wordRepeater
                        model: wd.words
                        Label {
                            required property string modelData
                            required property int index
                            text: modelData
                            font.pixelSize: 15
                            font.weight: Font.Medium
                            color: index === wd.wordIndex ? Theme.primary
                                   : index < wd.wordIndex ? Theme.colorOnSurface : Theme.colorOnSurfaceVariant
                            opacity: index <= wd.wordIndex ? 1 : 0.5
                            Behavior on opacity { NumberAnimation { duration: 120 } }
                        }
                    }
                }
            }

            Row {
                id: done
                anchors.centerIn: parent
                spacing: 8
                readonly property bool shown: wd.tried && !wd.playing
                opacity: shown ? 1 : 0
                scale: shown ? 1 : 0.85
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 180 } }
                Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 2.4 } }
                Rectangle {
                    width: 22; height: 22; radius: 11
                    color: Theme.okContainer
                    anchors.verticalCenter: parent.verticalCenter
                    Icon { anchors.centerIn: parent; glyph: Theme.ic.check; font.pixelSize: 16; color: Theme.ok }
                }
                Label {
                    text: Tr.t("onb.welcome.tried")
                    font.pixelSize: 14; font.weight: Font.DemiBold
                    color: Theme.colorOnSurface
                    anchors.verticalCenter: parent.verticalCenter
                }
                Label {
                    text: Tr.t("onb.welcome.triedMore")
                    font.pixelSize: 13
                    color: Theme.colorOnSurfaceVariant
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
    }
}
