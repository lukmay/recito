import QtQuick

// Keyboard focus outline around an item (the parent unless target is set)
Rectangle {
    property Item target: parent
    property real gap: 3
    property real shapeRadius: 12
    property bool shown: false
    x: (target === parent ? 0 : target.x) - gap
    y: (target === parent ? 0 : target.y) - gap
    width: target.width + 2 * gap
    height: target.height + 2 * gap
    radius: shapeRadius + gap
    color: "transparent"
    border.width: 2
    border.color: Theme.primary
    visible: shown
    z: 10
}
