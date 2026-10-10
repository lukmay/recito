import QtQuick
import QtQuick.Shapes

// The app's little talking speech bubble: sound bars bounce while it speaks.
// "excited" is the reading moment: the bubble hops, the bars dance and sound
// waves leave the bubble on the right.
Item {
    id: m
    property bool talking: true
    property bool excited: false
    property real size: 120
    implicitWidth: size * 1.25
    implicitHeight: size
    Accessible.ignored: true

    onExcitedChanged: if (excited) hop.restart()

    // soft glow behind the bubble
    Rectangle {
        anchors.centerIn: bubble
        width: bubble.width * 1.25; height: width; radius: width / 2
        color: Theme.dark ? Theme.primary : Theme.tertiaryContainer
        opacity: (Theme.dark ? 0.14 : 0.55) * (m.excited ? 1.5 : 1)
        Behavior on opacity { NumberAnimation { duration: 300 } }
        scale: m.talking ? 1.0 : 0.9
        SequentialAnimation on scale {
            running: m.talking; loops: Animation.Infinite
            NumberAnimation { to: 1.05; duration: 1400; easing.type: Easing.InOutSine }
            NumberAnimation { to: 0.95; duration: 1400; easing.type: Easing.InOutSine }
        }
    }

    // sound waves: three arcs that travel outwards and fade, one after another
    Repeater {
        model: 3
        Shape {
            id: wave
            required property int index
            property real p: 0                 // 0 = at the bubble, 1 = gone
            readonly property real r: m.size * 0.5
            width: r; height: r * 2
            x: bubble.x + bubble.width - r * 0.78 + p * m.size * 0.26
            y: bubble.y + bubble.height / 2 - r
            opacity: p <= 0 || p >= 1 ? 0 : Math.min(1, p * 5) * (1 - p)
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: Theme.primary
                strokeWidth: Math.max(2, m.size * 0.035)
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                PathAngleArc {
                    centerX: 0; centerY: wave.r
                    radiusX: wave.r * (0.62 + wave.p * 0.3); radiusY: radiusX
                    startAngle: -32; sweepAngle: 64
                }
            }
            SequentialAnimation {
                running: m.excited
                loops: Animation.Infinite
                alwaysRunToEnd: true
                PauseAnimation { duration: wave.index * 260 }
                NumberAnimation { target: wave; property: "p"; from: 0; to: 1; duration: 820; easing.type: Easing.OutCubic }
                PropertyAction { target: wave; property: "p"; value: 0 }
                PauseAnimation { duration: (2 - wave.index) * 260 }
            }
        }
    }

    // Bubble body plus tail as one shape, the app mark's outline
    // (assets/brand/BRAND.md, "Construction") on a 224 wide grid.
    Item {
        id: bubble
        width: m.size; height: m.size * 0.73        // body only, the tail hangs below
        anchors.horizontalCenter: parent.horizontalCenter
        y: 0
        transformOrigin: Item.Bottom
        SequentialAnimation {
            id: hop
            NumberAnimation { target: bubble; property: "scale"; to: 1.07; duration: 140; easing.type: Easing.OutQuad }
            NumberAnimation { target: bubble; property: "scale"; to: 1.0; duration: 380; easing.type: Easing.OutBack; easing.overshoot: 2.2 }
        }
        Shape {
            width: 224; height: 204
            transform: Scale { xScale: m.size / 224; yScale: m.size / 224 }
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillColor: Theme.primaryContainer
                strokeColor: "transparent"
                PathSvg {
                    path: "M0 56A56 56 0 0 1 56 0H168A56 56 0 0 1 224 56V108A56 56 0 0 1 168 164"
                          + "H80Q64 164 50.43 172.48L8.48 198.7Q0 204 0 194Z"
                }
            }
        }
        Row {
            anchors.centerIn: parent
            spacing: m.size * 0.063                 // bar centres 0.143 * size apart
            Repeater {
                // rest heights relative to the centre bar (0.50 * size)
                model: [0.44, 0.74, 1.0, 0.58, 0.40]
                Rectangle {
                    id: bar
                    required property real modelData
                    required property int index
                    width: m.size * 0.08
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: Theme.primary
                    property real amp: modelData
                    // extra bounce while excited, eased back to 1 afterwards
                    property real kick: 1
                    height: Math.min(m.size * 0.62, m.size * 0.50 * amp * kick)
                    SequentialAnimation on amp {
                        running: m.talking; loops: Animation.Infinite
                        PauseAnimation { duration: index * 90 }
                        NumberAnimation { to: Math.max(0.25, modelData - 0.35); duration: 260; easing.type: Easing.InOutQuad }
                        NumberAnimation { to: modelData; duration: 300; easing.type: Easing.InOutQuad }
                        NumberAnimation { to: Math.min(1.0, modelData + 0.15); duration: 240; easing.type: Easing.InOutQuad }
                        NumberAnimation { to: modelData; duration: 280; easing.type: Easing.InOutQuad }
                    }
                    SequentialAnimation {
                        running: m.excited
                        loops: Animation.Infinite
                        PauseAnimation { duration: [60, 0, 110, 30, 80][bar.index] }
                        NumberAnimation { target: bar; property: "kick"; to: 1.55 - bar.modelData * 0.3
                                          duration: [130, 150, 120, 160, 140][bar.index]; easing.type: Easing.OutQuad }
                        NumberAnimation { target: bar; property: "kick"; to: 0.75
                                          duration: [150, 130, 170, 120, 160][bar.index]; easing.type: Easing.InOutQuad }
                        onStopped: kickBack.restart()
                    }
                    NumberAnimation { id: kickBack; target: bar; property: "kick"; to: 1; duration: 260; easing.type: Easing.OutCubic }
                }
            }
        }
    }
    // a little sparkle, twinkling faster in the reading moment
    Icon {
        glyph: Theme.ic.sparkle
        color: Theme.tertiary
        font.pixelSize: m.size * 0.22
        x: bubble.x + bubble.width - width * 0.4
        y: -height * 0.25
        scale: m.excited ? 1.2 : 1
        Behavior on scale { NumberAnimation { duration: 260; easing.type: Easing.OutBack } }
        SequentialAnimation on opacity {
            running: m.talking; loops: Animation.Infinite
            NumberAnimation { to: 0.35; duration: m.excited ? 260 : 900 }
            NumberAnimation { to: 1.0; duration: m.excited ? 260 : 900 }
        }
    }
}
