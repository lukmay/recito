import QtQuick

// Quiet line of what an engine can do: icon plus label per capability
// (cap.<key> and cap.<key>.tip), wrapping like text. Display only.
Flow {
    id: cl
    property var caps: []              // capability keys from the backend
    property color ink: Theme.colorOnSurfaceVariant
    property color glyphColor: Theme.primary
    readonly property var glyphs: ({
        clone: "", tags: "", instruct: "", expr: "",
        style: "", markup: "", fast: ""
    })
    spacing: 4
    visible: caps.length > 0
    Repeater {
        model: cl.caps
        Chip {
            required property string modelData
            plain: true
            text: Tr.t("cap." + modelData)
            tip: Tr.t("cap." + modelData + ".tip")
            glyph: cl.glyphs[modelData] || Theme.ic.sparkle
            ink: cl.ink
            glyphColor: cl.glyphColor
        }
    }
}
