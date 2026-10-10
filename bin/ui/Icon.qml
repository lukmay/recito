import QtQuick

// A Material Icons Round glyph
Text {
    property string glyph
    text: glyph
    font.family: Theme.iconFamily
    font.pixelSize: 20
    color: Theme.colorOnSurfaceVariant
    verticalAlignment: Text.AlignVCenter
    horizontalAlignment: Text.AlignHCenter
    Accessible.ignored: true
}
