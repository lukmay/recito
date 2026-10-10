import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material

// Interface language picker; "auto" follows the system. Applies live.
ComboBox {
    id: lb
    Material.containerStyle: Material.Outlined
    implicitWidth: 260
    textRole: "name"
    valueRole: "code"
    model: [{ code: "auto", name: Tr.t("lang.auto", { lang: i18n.systemName }) }]
           .concat(i18n.languages.map(function (l) { return { code: l.code, name: l.name } }))
    currentIndex: Math.max(0, indexOfValue(i18n.choice))
    onActivated: i18n.setLanguage(currentValue)
    Accessible.name: Tr.t("settings.language")
}
