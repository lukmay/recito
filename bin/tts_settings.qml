// Settings window of the tray app (Material 3 via Qt Quick Controls).
// Python side: tts_settings.py (context properties "backend" and "i18n").
// The form keeps its state here; save() hands everything over in one object.
// Shared building blocks, colors and translations live in ui/.

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Dialogs
import QtMultimedia
import "ui"

ApplicationWindow {
    id: win
    width: 1000
    height: 800
    minimumWidth: 860
    minimumHeight: 600
    title: onboardingOpen ? Tr.t("app.welcomeTitle") : Tr.t("app.settingsTitle")
    color: Theme.surface

    Material.theme: Theme.dark ? Material.Dark : Material.Light
    Material.accent: Theme.primary
    Material.primary: Theme.primary
    Material.background: Theme.surface
    Material.foreground: Theme.colorOnSurface
    font.family: Theme.fontFamily
    font.pixelSize: 14

    readonly property var ic: Theme.ic

    // --- State ------------------------------------------------------------ //
    property var init: backend.initial()
    property string engine: init.engine
    property var voices: JSON.parse(JSON.stringify(init.current))   // engine -> {lang: voice}
    property var instructs: JSON.parse(JSON.stringify(init.instructs || {}))
    property var lists: init.lists
    property var labels: init.labels || ({})      // voice id -> display name
    property var readLangs: (init.readLangs || ["de", "en"]).slice()   // in the user's order
    property string saveState: "idle"             // idle | busy | done | error
    property bool saveSilent: false
    property bool onboardingOpen: init.onboarding
    property bool closeAfterSave: false
    function voiceLabel(id) { return labels[id] || id }
    // --- Reading languages ---
    function engineInfo(key) {
        for (var i = 0; i < init.engines.length; i++) if (init.engines[i].key === key) return init.engines[i]
        return { langs: [] }
    }
    function speaks(key, lang) {
        if (key === "piper") return piperHas(lang)         // follows downloads at once
        return (engineInfo(key).langs || []).indexOf(lang) >= 0
    }
    // "Speaks 2 of your 3 languages", "" when it speaks all of them
    function coverageText(key) {
        var n = readLangs.filter(function (l) { return win.speaks(key, l) }).length
        return n === readLangs.length ? "" : Tr.t("lang.coverage", { n: n, total: readLangs.length })
    }
    function langMeta(code) {
        for (var i = 0; i < (init.langs || []).length; i++) if (init.langs[i].code === code) return init.langs[i]
        return { code: code, piper: "", piperMb: 63, edge: "" }
    }
    function piperHas(lang) { return ((lists.piper || {})[lang] || []).length > 0 }
    // Who reads a language the engine cannot speak (same order as the daemon)
    function langRoute(lang) {
        if (piperHas(lang)) return "piper"
        if (init.edgeFallback && engine !== "piper") return "edge"
        return "none"
    }
    function routeVoice(lang, route) {
        if (route === "piper") return voiceLabel(voices.piper && voices.piper[lang] || lists.piper[lang][0])
        if (route === "edge") return voiceLabel((voices.edge || {})[lang] || langMeta(lang).edge)
        return ""
    }
    function toggleReadLang(code) {
        var l = readLangs.slice(), i = l.indexOf(code)
        if (i >= 0) { if (l.length > 1) l.splice(i, 1); else { snackbar(Tr.t("lang.keepOne")); return } }
        else l.push(code)
        readLangs = l
    }
    // A Piper voice finished downloading: list it and use it for its language
    function piperInstalled(lang, voice) {
        var r = backend.piperLists()
        var l = JSON.parse(JSON.stringify(lists)); l.piper = r.lists; lists = l
        var lb = Object.assign({}, labels, r.labels); labels = lb
        var all = JSON.parse(JSON.stringify(voices))
        if (!all.piper) all.piper = {}
        if (!all.piper[lang]) all.piper[lang] = voice
        voices = all
        snackbar(Tr.t("piper.done", { name: voiceLabel(voice), lang: Tr.langName(lang) }))
    }
    function rulesLangName(l) {
        if (l === "all") return Tr.t("rules.lang.all")
        return Tr.has("rules.lang." + l) ? Tr.t("rules.lang." + l) : Tr.t("rules.lang.only", { lang: Tr.langName(l) })
    }
    function capsOf(key) {
        for (var i = 0; i < init.engines.length; i++)
            if (init.engines[i].key === key) return init.engines[i].caps || []
        return []
    }
    property var elVoices: init.el.voices
    property var elRates: init.el.rates
    property var elPerms: init.el.perms            // empty = never checked
    property var elSub: ({})
    property string elModel: init.el.model
    property var elCurrent: JSON.parse(JSON.stringify(init.el.current))   // lang -> voice id
    property bool elHasKey: init.el.hasKey
    property bool elSetupOpen: !init.el.hasKey
    property bool elBusy: false
    property string elError: ""
    property bool elListening: elSetupOpen && engine === "elevenlabs"
    property var elCache: init.el.cacheStats

    readonly property bool permsKnown: elPerms.tts !== undefined
    readonly property bool requiredMissing: permsKnown && (!elPerms.tts || !elPerms.voices)
    readonly property bool optionalMissing: permsKnown && (!elPerms.models || !elPerms.user)

    // Snackbar; optional action button (label, function), for example Undo
    function snackbar(text, action, fn) {
        snack.text = text
        snack.actionText = action || ""
        snack.action = fn || null
        snack.open()
    }

    // --- Text rules (tts_rules through the backend) ---------------------- //
    // Material Icons Round codepoints used on the text clean-up page
    readonly property var ric: ({ findReplace: "", agent: "", undo: "",
                                  science: "", edit: "", up: "", down: "",
                                  review: "", expand: "", collapse: "",
                                  restore: "", code: "" })
    readonly property string monoFont: Qt.platform.os === "windows" ? "Consolas" : "monospace"
    property var rulesInfo: backend.rulesState()          // {rules, groups, error, canRestore, canUndo, toReview, path}
    property var tryResult: ({ before: [], after: [], fired: [] })
    // Take the state a backend change returns; false (and a message) on errors
    function rulesApply(r, okText) {
        rulesInfo = r
        if (!r.ok) snackbar(r.error)
        else if (okText) snackbar(okText)
        runTry()
        return r.ok
    }
    function removeRule(id) {
        if (rulesApply(backend.ruleRemove(id)))
            snackbar(Tr.t("rules.deleted"), Tr.t("rules.undo"),
                     function () { win.rulesApply(backend.rulesUndo(), Tr.t("rules.undone")) })
    }
    function restorePresets(group) {
        var r = backend.rulesRestore(group || "")
        if (!rulesApply(r)) return
        if (!group && r.added + r.reset === 0) { snackbar(Tr.t("rules.restoredNone")); return }
        snackbar(group ? Tr.t("rules.groupRestored", { group: Tr.t("rules.group." + group) })
                       : Tr.t("rules.restored", { added: r.added, reset: r.reset }),
                 Tr.t("rules.undo"),
                 function () { win.rulesApply(backend.rulesUndo(), Tr.t("rules.undone")) })
    }
    // Groups: open or closed (preset groups start closed), and their state
    property var openGroups: ({ user: true })
    function isOpen(g) { return openGroups[g] === true }
    function toggleOpen(g) { var o = Object.assign({}, openGroups); o[g] = !isOpen(g); openGroups = o }
    function groupInfo(g) {
        var gs = rulesInfo.groups || []
        for (var i = 0; i < gs.length; i++) if (gs[i].key === g) return gs[i]
        return { key: g, on: true, count: 0, onCount: 0, preset: false, missing: 0, changed: 0 }
    }
    // The list as stretches of one group: [{group, items: [{r, index, first, last}]}].
    // A group without rules still gets a stretch, so it can be restored.
    readonly property var ruleSections: {
        var rules = rulesInfo.rules || [], out = [], cur = null, seen = {}
        for (var i = 0; i < rules.length; i++) {
            var g = rules[i].group || "user"
            if (!cur || cur.group !== g) { cur = { group: g, items: [] }; out.push(cur); seen[g] = true }
            cur.items.push({ r: rules[i], index: i })
        }
        var order = ["obsidian", "markdown", "formulas", "user"]
        for (var k = 0; k < order.length; k++) {
            if (seen[order[k]]) continue
            var at = out.length
            for (var j = 0; j < out.length; j++)
                if (order.indexOf(out[j].group) > k) { at = j; break }
            out.splice(at, 0, { group: order[k], items: [] })
        }
        for (var n = 0; n < out.length; n++)
            for (var m = 0; m < out[n].items.length; m++) {
                out[n].items[m].first = m === 0
                out[n].items[m].last = m === out[n].items.length - 1
            }
        return out
    }
    function groupSummary(info) {
        var parts = []
        if (info.count === 0) parts.push(Tr.t(info.preset ? "rules.countNone" : "rules.emptyTitle"))
        else if (!info.on) parts.push(Tr.t(info.count === 1 ? "rules.groupOffOne" : "rules.groupOff",
                                           { n: info.count }))
        else if (info.count === 1) parts.push(Tr.t(info.onCount ? "rules.countOneOn" : "rules.countOneOff"))
        else parts.push(Tr.t("rules.count", { n: info.count, on: info.onCount }))
        if (info.changed > 0) parts.push(Tr.t("rules.changedCount", { n: info.changed }))
        return parts.join("  ·  ")
    }
    function tryLang() { return readLangs[tryLangBox.currentIndex] || readLangs[0] }
    function runTry() { tryResult = backend.rulesTry(tryInput.text, tryLang()) }
    function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") }
    // Runs [{text, rule}] -> rich text with the parts a rule touched highlighted
    function runsHtml(runs, bg, fg) {
        var h = ""
        for (var i = 0; i < (runs || []).length; i++) {
            var t = esc(runs[i].text)
            h += runs[i].rule ? "<span style=\"background-color:" + bg + "; color:" + fg + "\">" + t + "</span>" : t
        }
        return "<div style=\"white-space:pre-wrap\">" + (h || "&nbsp;") + "</div>"
    }
    function ruleLabel(r) { return "“" + r.pattern + "” → “" + r.replacement + "”" }
    // Name of a rule in short lists: the preset's title, else find -> replace
    function ruleName(r) {
        if (r.preset_id) return Tr.t("preset." + r.preset_id, undefined, r.note || r.preset_id)
        if (r.kind === "builtin") return Tr.t("rules.ownCode", { name: r.builtin })
        return ruleLabel(r)
    }
    Timer { id: tryTimer; interval: 150; onTriggered: win.runTry() }
    Connections {
        target: backend
        function onRulesChanged() { win.rulesInfo = backend.rulesState(); win.runTry() }
    }
    function fmt(n) { return Tr.num(n, 0) }
    function fmt1(n) { return Tr.num(n, 1) }
    // Pace factor (1.25 = a quarter faster) <-> stored length scale (0.80)
    function paceOf(scale) { return scale > 0 ? 1 / scale : 1 }
    function scaleOf(pace) { return Math.round(100 / pace) / 100 }
    function paceText(pace) { return Tr.t("speed.times", { x: Tr.num(pace, 2) }) }

    function rate(id) {
        if (elRates[id] !== undefined) return elRates[id]
        for (var i = 0; i < init.el.models.length; i++)
            if (init.el.models[i].id === id) return init.el.models[i].credits
        return 1
    }
    function modelName(id) {
        for (var i = 0; i < init.el.models.length; i++)
            if (init.el.models[i].id === id) return init.el.models[i].name
        return id
    }
    function voiceById(id) {
        for (var i = 0; i < elVoices.length; i++) if (elVoices[i].id === id) return elVoices[i]
        return null
    }

    function voiceOf(eng, lang) {
        if (eng === "elevenlabs") {          // multilingual: another language's voice works too
            if (elCurrent[lang]) return elCurrent[lang]
            for (var i = 0; i < readLangs.length; i++) if (elCurrent[readLangs[i]]) return elCurrent[readLangs[i]]
            return elCurrent.de || elCurrent.en || ""
        }
        return (voices[eng] || {})[lang] || ""
    }

    function test(eng, lang, extra) {
        var req = { engine: eng, lang: lang, voice: voiceOf(eng, lang),
                    scale: speedNormal.value }
        if (eng === "elevenlabs") req.model = elModel
        if (instructs[eng] !== undefined) req.instruct = instructs[eng]
        for (var k in extra) req[k] = extra[k]
        if (!req.voice) {
            snackbar(eng === "elevenlabs" ? Tr.t("test.elNoKey") : Tr.t("test.noVoice"))
            return ""
        }
        // A cached recording plays at once; otherwise the daemon renders and keeps it
        var url = backend.previewUrl(req)
        if (url) { playPreview(url); return "play" }
        stopPlayer()
        var info = backend.testInfo(req)
        if (!backend.test(req)) {
            snackbar(Tr.t("test.daemonStarting"))
            return ""
        }
        if (info) snackbar(info)
        return info ? "load" : "play"
    }

    function save(silent) {
        var keys = {}
        for (var j = 0; j < init.hotkeys.length; j++)
            keys[init.hotkeys[j].action] = hotkeyRepeater.itemAt(j).combo
        saveSilent = silent === true
        saveState = "busy"
        backend.saveAsync({
            engine: engine, langDetect: perSentence.checked ? "sentence" : "text",
            speedNormal: speedNormal.value, speedFast: speedFast.value,
            pause: pauseSlider.value, voices: voices, instructs: instructs, hotkeys: keys,
            readLangs: readLangs, follow: followSwitch.checked,
            followOpacity: followOpacity.value,
            el: Object.assign({}, elCurrent, { model: elModel, cache: elCacheSwitch.checked })
        })
    }
    Timer { id: saveDoneTimer; interval: 1800; onTriggered: win.saveState = "idle" }

    function verify(key, quiet) {
        elBusy = true
        elError = ""
        backend.verifyKey(key, quiet)
    }

    function openOnboarding() {
        onboarding.firstRun = false
        // the assistant starts from what the form shows now
        var cur = Object.assign({}, win.init)
        cur.hotkeys = win.init.hotkeys.map(function (h, j) {
            return { action: h.action, combo: hotkeyRepeater.itemAt(j).combo } })
        cur.el = Object.assign({}, win.init.el, { model: elModel, hasKey: elHasKey, voices: elVoices,
                                                  rates: elRates, perms: elPerms,
                                                  current: Object.assign({}, elCurrent) })
        cur.readLangs = readLangs.slice()
        cur.current = voices
        onboarding.initial = cur
        onboardingOpen = true
        onboarding.start()
    }
    // Take over what the assistant chose, so the form shows it right away
    function applyOnboarding(r) {
        if (r.engine) engine = r.engine
        if (r.readLangs) readLangs = r.readLangs.slice()
        if (r.voices && r.engine === "elevenlabs") {
            var ec = Object.assign({}, elCurrent)
            for (var l in r.voices) if (r.voices[l]) ec[l] = r.voices[l]
            elCurrent = ec
        } else if (r.voices) {
            var all = JSON.parse(JSON.stringify(voices))
            if (!all[r.engine]) all[r.engine] = {}
            for (var lang in r.voices) if (r.voices[lang]) all[r.engine][lang] = r.voices[lang]
            voices = all
        }
        if (r.elModel) elModel = r.elModel
        if (r.speed) { speedNormal.setValue(r.speed); speedFast.setValue(r.speed * 0.8) }
        if (r.hotkeys)
            for (var j = 0; j < init.hotkeys.length; j++)
                if (r.hotkeys[init.hotkeys[j].action] !== undefined)
                    hotkeyRepeater.itemAt(j).combo = r.hotkeys[init.hotkeys[j].action]
    }

    onVisibleChanged: backend.windowVisible(visible)
    onActiveChanged: if (active) backend.checkClipboard()
    // A key in the clipboard only counts while ElevenLabs is chosen and its
    // key is being set up; otherwise an old key would switch things on its own.
    onElListeningChanged: backend.setSetupOpen(elListening)
    Component.onCompleted: {
        backend.setSetupOpen(elListening)
        if (elHasKey) verify(backend.storedKey(), true)
        if (onboardingOpen) { onboarding.initial = init; onboarding.start() }
    }

    Connections {
        target: backend
        function onSaved(ok, msg) {
            win.saveState = ok ? "done" : "error"
            saveDoneTimer.restart()
            if (!ok || !win.saveSilent) snackbar(msg)
            if (ok && win.closeAfterSave) { win.closeAfterSave = false; win.close() }
        }
        function onSelectEngine(key) { nav.currentIndex = 0; win.engine = key }
        function onOpenOnboarding() { win.openOnboarding() }
        function onKeyDetected(key) {
            if (win.onboardingOpen) {            // the assistant checks it quietly, saves nothing else
                onboarding.takeKey(key)
                win.raise(); win.requestActivate()
                return
            }
            keyField.text = key
            nav.currentIndex = 0
            win.verify(key, false)
            win.raise(); win.requestActivate()
        }
        function onElVerified(r) {
            elBusy = false
            elVoices = r.voices
            elRates = r.rates
            elPerms = r.perms
            elSub = r.sub
            elHasKey = true
            keyField.text = ""
            var cur = Object.assign({}, elCurrent)
            for (var i = 0; i < readLangs.length; i++)
                if (!voiceById(cur[readLangs[i]])) cur[readLangs[i]] = r.defaultVoice
            elCurrent = cur
            elSetupOpen = false
            if (r.first && !r.quiet && r.perms.tts && cur[readLangs[0]]) {
                win.engine = "elevenlabs"
                save(true)
                snackbar(Tr.t("el.activated"))
                probeTimer.start()
            }
        }
        function onElFailed(msg) {
            elBusy = false
            elError = msg
            elSetupOpen = true
        }
        function onPiperDone(lang, ok, voice) { if (ok) win.piperInstalled(lang, voice) }
        function onEdgeLoaded(lists2) {
            var l = JSON.parse(JSON.stringify(win.lists)); l.edge = lists2; win.lists = l
            snackbar(Tr.t("edge.refreshed"))
        }
    }
    Timer { id: probeTimer; interval: 1500; onTriggered: test("elevenlabs", win.readLangs[0], {}) }

    // ===================================================================== //
    //  Window-specific building blocks
    // ===================================================================== //

    // Row: language | picker | listen (local engines)
    component VoiceRow: RowLayout {
        id: vr
        property string engineKey
        property string lang
        property var model: []
        Layout.fillWidth: true
        spacing: 12
        Label {
            text: Tr.langName(vr.lang)
            Layout.preferredWidth: 84; color: Theme.colorOnSurfaceVariant; elide: Text.ElideRight
        }
        ComboBox {
            id: cb
            Layout.fillWidth: true
            Material.containerStyle: Material.Outlined
            editable: vr.model.length === 0
            Accessible.name: Tr.t("voice.pickerFor", { lang: Tr.langName(vr.lang) })
            model: {
                var cur = win.voiceOf(vr.engineKey, vr.lang)
                var m = vr.model.slice()
                if (cur && m.indexOf(cur) < 0) m.unshift(cur)
                return m
            }
            currentIndex: Math.max(0, model.indexOf(win.voiceOf(vr.engineKey, vr.lang)))
            displayText: editable ? currentText : win.voiceLabel(currentText)
            delegate: ItemDelegate {
                required property var modelData
                required property int index
                width: cb.width
                text: win.voiceLabel(modelData)
                highlighted: cb.highlightedIndex === index
            }
            onActivated: store(currentText)
            onEditTextChanged: if (editable) store(editText)
            Component.onCompleted: if (editable) editText = win.voiceOf(vr.engineKey, vr.lang)
            function store(v) {
                var all = JSON.parse(JSON.stringify(win.voices))
                if (!all[vr.engineKey]) all[vr.engineKey] = {}
                all[vr.engineKey][vr.lang] = v
                win.voices = all
            }
        }
        TonalButton {
            id: hearBtn
            property string fb: ""                 // "" | "play" | "load"
            text: fb === "load" ? Tr.t("common.loading") : fb === "play" ? Tr.t("common.playing")
                                                        : Tr.t("common.listen")
            glyph: fb === "" ? ic.play : ic.voice
            onClicked: {
                fb = win.test(vr.engineKey, vr.lang, {})
                fbTimer.interval = fb === "load" ? 20000 : 2500
                fbTimer.restart()
            }
            Timer { id: fbTimer; onTriggered: hearBtn.fb = "" }
        }
    }

    // ElevenLabs voice: field shows the name, list shows two lines with description
    component ElVoiceRow: ColumnLayout {
        id: evr
        property string lang
        readonly property var current: win.voiceById(win.elCurrent[lang])
        Layout.fillWidth: true
        spacing: 4
        RowLayout {
            Layout.fillWidth: true; spacing: 12
            Label { text: Tr.langName(evr.lang)
                    Layout.preferredWidth: 84; color: Theme.colorOnSurfaceVariant; elide: Text.ElideRight }
            ComboBox {
                id: ecb
                Layout.fillWidth: true
                Material.containerStyle: Material.Outlined
                enabled: win.elVoices.length > 0
                textRole: "name"; valueRole: "id"
                Accessible.name: Tr.t("voice.pickerFor", { lang: Tr.langName(evr.lang) })
                model: win.elVoices.length ? win.elVoices
                       : [{ id: "", name: Tr.t("el.voicesEmpty"), desc: "" }]
                currentIndex: Math.max(0, indexOfValue(win.elCurrent[evr.lang]))
                onActivated: { var cur = Object.assign({}, win.elCurrent)
                               cur[evr.lang] = currentValue; win.elCurrent = cur }
                delegate: ItemDelegate {
                    required property var modelData
                    required property int index
                    width: ecb.popup.width
                    highlighted: ecb.highlightedIndex === index
                    contentItem: ColumnLayout {
                        spacing: 2
                        Label { text: modelData.name; color: Theme.colorOnSurface; font.pixelSize: 14
                                font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                        Label { text: (modelData.desc || "") + (modelData.retiring ? "  ·  " + Tr.t("el.retiringShort") : "")
                                visible: text !== ""; color: modelData.retiring ? Theme.warn : Theme.colorOnSurfaceVariant
                                font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                }
            }
            TonalButton { text: Tr.t("common.listen"); glyph: ic.play; enabled: win.elVoices.length > 0
                          onClicked: win.test("elevenlabs", evr.lang, {}) }
        }
        Small {
            visible: text !== ""
            Layout.fillWidth: true; Layout.leftMargin: 96
            text: evr.current ? evr.current.desc + (evr.current.retiring ? ". " + Tr.t("el.retiringLong") : "") : ""
            color: evr.current && evr.current.retiring ? Theme.warn : Theme.colorOnSurfaceVariant
        }
    }

    // Selectable engine card: icon and choice mark on top, name with its
    // online/cost line, a short description, and what it can do pinned to the
    // bottom so cards in one grid row line up.
    component EngineCard: Rectangle {
        id: card
        property var info
        property bool selected: win.engine === info.key
        readonly property var caps: info.caps || []
        readonly property color ink: selected ? Theme.colorOnSecondaryContainer : Theme.colorOnSurfaceVariant
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredWidth: 1
        implicitHeight: cardCol.implicitHeight + 32
        radius: 16
        color: selected ? Theme.secondaryContainer : Theme.surfaceContainerLow
        border.width: selected ? 2 : 1
        border.color: selected ? Theme.primary : Theme.outlineVariant
        scale: tap.pressed ? 0.98 : 1.0
        activeFocusOnTab: true
        Accessible.role: Accessible.RadioButton
        Accessible.name: Tr.engineTitle(info.key, info.title)
        Accessible.description: [statusLabel.text, win.coverageText(info.key), Tr.engineBlurb(info.key)]
                                .concat(caps.map(function (k) { return Tr.t("cap." + k) })).join(". ")
        Accessible.checked: selected
        Keys.onPressed: (e) => {
            if (e.key === Qt.Key_Space || e.key === Qt.Key_Return || e.key === Qt.Key_Enter) {
                win.engine = card.info.key; e.accepted = true
            }
        }
        Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        Behavior on color { ColorAnimation { duration: 180 } }
        Behavior on border.color { ColorAnimation { duration: 180 } }

        Rectangle {                       // state layer (hover/press)
            anchors.fill: parent; radius: parent.radius
            color: Theme.colorOnSurface
            opacity: tap.pressed ? 0.10 : hover.hovered ? 0.05 : 0
        }
        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
        TapHandler { id: tap; onTapped: { card.forceActiveFocus(); win.engine = card.info.key } }
        FocusRing { shapeRadius: card.radius; shown: card.activeFocus }
        ColumnLayout {
            id: cardCol
            anchors { fill: parent; margins: 16 }
            spacing: 0
            RowLayout {
                Layout.fillWidth: true; spacing: 12
                Rectangle {
                    implicitWidth: 40; implicitHeight: 40; radius: 20
                    color: card.selected ? Theme.primary : Theme.secondaryContainer
                    Behavior on color { ColorAnimation { duration: 180 } }
                    Icon { anchors.centerIn: parent; glyph: Theme.engineGlyph(card.info.key)
                           color: card.selected ? Theme.colorOnPrimary : Theme.colorOnSecondaryContainer }
                }
                Item { Layout.fillWidth: true }
                Icon {                    // radio mark, filled when chosen
                    Layout.alignment: Qt.AlignTop
                    glyph: card.selected ? ic.checkCircle : ic.unchecked
                    color: card.selected ? Theme.primary : Theme.outline
                    font.pixelSize: 22
                    opacity: card.selected || hover.hovered || card.activeFocus ? 1 : 0.6
                    scale: card.selected ? 1 : 0.85
                    Behavior on scale { NumberAnimation { duration: 260; easing.type: Easing.OutBack } }
                    Behavior on opacity { NumberAnimation { duration: 140 } }
                }
            }
            Label {
                Layout.topMargin: 12
                Layout.fillWidth: true
                text: Tr.engineTitle(card.info.key, card.info.title)
                font.pixelSize: 16; font.weight: Font.DemiBold
                color: card.selected ? Theme.colorOnSecondaryContainer : Theme.colorOnSurface
                wrapMode: Text.Wrap
            }
            RowLayout {                   // online or offline, and what it costs
                Layout.topMargin: 2
                Layout.fillWidth: true
                spacing: 4
                Icon {
                    glyph: card.info.paid ? "" : card.info.online ? ic.cloud : ""
                    font.pixelSize: 14
                    color: statusLabel.color
                    Layout.alignment: Qt.AlignTop
                    Layout.topMargin: 1
                }
                Label {
                    id: statusLabel
                    Layout.fillWidth: true
                    text: card.info.online ? Tr.t("card.online") + " · "
                                             + (card.info.paid ? Tr.t("card.paid") : Tr.t("card.free"))
                                           : Tr.t("card.offline")
                    font.pixelSize: 12; wrapMode: Text.Wrap
                    color: card.info.paid ? Theme.warn : card.ink
                }
            }
            RowLayout {                   // reading languages it cannot speak
                visible: covLabel.text !== ""
                Layout.topMargin: 4
                Layout.fillWidth: true
                spacing: 4
                Icon { glyph: ic.translate; font.pixelSize: 14; color: Theme.warn
                       Layout.alignment: Qt.AlignTop; Layout.topMargin: 1 }
                Label { id: covLabel; Layout.fillWidth: true; wrapMode: Text.Wrap
                        text: win.coverageText(card.info.key)
                        font.pixelSize: 12; color: card.selected ? card.ink : Theme.warn }
            }
            Small {
                Layout.topMargin: 10
                Layout.fillWidth: true
                text: Tr.engineBlurb(card.info.key)
                color: card.ink
            }
            Item { Layout.fillHeight: true; Layout.minimumHeight: card.caps.length ? 12 : 0 }
            CapabilityList {
                Layout.fillWidth: true
                caps: card.caps
                ink: card.ink
            }
        }
    }

    // Slider row with value and listen button. `toUi`/`fromUi` map between the
    // stored value and the slider position; `minText`/`maxText` label the ends.
    component SliderRow: RowLayout {
        id: sr
        property real from: 0
        property real to: 1
        property real step: 0.05
        property real initial: 0
        property var toUi: function (v) { return v }
        property var fromUi: function (u) { return u }
        readonly property real value: sr.fromUi(sl.value)
        property string label
        property string minText: ""
        property string maxText: ""
        property var format: function (u) { return Tr.num(u, 2) }
        property bool probeable: true
        function setValue(v) { sl.value = sr.toUi(v) }
        signal probe()
        Layout.fillWidth: true
        spacing: 12
        Label { text: sr.label; Layout.preferredWidth: 110; color: Theme.colorOnSurfaceVariant
                wrapMode: Text.Wrap }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            Slider { id: sl; Layout.fillWidth: true
                     from: sr.from; to: sr.to; stepSize: sr.step; value: sr.toUi(sr.initial)
                     Accessible.name: sr.label }
            RowLayout {
                visible: sr.minText !== "" || sr.maxText !== ""
                Layout.fillWidth: true
                Layout.leftMargin: 6; Layout.rightMargin: 6
                Small { text: sr.minText; color: Theme.colorOnSurfaceVariant }
                Item { Layout.fillWidth: true }
                Small { text: sr.maxText; color: Theme.colorOnSurfaceVariant }
            }
        }
        Label {
            text: sr.format(sl.value); Layout.preferredWidth: 60
            horizontalAlignment: Text.AlignRight; color: Theme.colorOnSurface
        }
        TonalButton { visible: sr.probeable
                      text: Tr.t("common.listen"); glyph: ic.play; onClicked: sr.probe() }
    }

    // One row of the permission checklist
    component PermRow: RowLayout {
        id: pr
        property var perm
        readonly property var granted: win.elPerms[perm.key]
        Layout.fillWidth: true
        spacing: 12
        Icon {
            glyph: pr.granted === undefined ? ic.unchecked : pr.granted ? ic.checkCircle
                   : pr.perm.required ? ic.cancel : ic.info
            color: pr.granted === undefined ? Theme.outline : pr.granted ? Theme.ok
                   : pr.perm.required ? Theme.error : Theme.colorOnSurfaceVariant
        }
        Label {
            Layout.fillWidth: true; wrapMode: Text.Wrap
            textFormat: Text.StyledText
            color: Theme.colorOnSurface
            text: Tr.t("el.permLine", { area: "<b>" + pr.perm.area + "</b>", level: "<b>" + pr.perm.level + "</b>" })
                  + "<font color='" + Theme.colorOnSurfaceVariant + "'>  ·  "
                  + Tr.t("el.perm." + pr.perm.key, undefined, pr.perm.why)
                  + (pr.perm.required ? "" : " " + Tr.t("el.optional")) + "</font>"
        }
    }

    // Page with scroll area and limited reading width
    component Page: ScrollView {
        id: pg
        default property alias content: pageCol.data
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            id: pageCol
            width: Math.min(pg.availableWidth - 80, 800)
            x: 40
            spacing: 16
            Item { Layout.preferredHeight: 16 }
        }
    }

    // ===================================================================== //
    //  Layout: navigation | pages, footer
    // ===================================================================== //
    RowLayout {
        anchors.fill: parent
        spacing: 0

        // --- Navigation drawer (M3 standard) ---
        Rectangle {
            id: drawer
            Layout.fillHeight: true
            Layout.preferredWidth: 256
            color: Theme.surfaceContainerLow
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 0
                RowLayout {
                    Layout.leftMargin: 12; Layout.topMargin: 16; Layout.bottomMargin: 20
                    spacing: 12
                    // App mark (assets/brand/logo-mark.svg), no tile: the bubble is its own shape
                    Item {
                        implicitWidth: 40; implicitHeight: 40
                        Image {
                            id: brandMark
                            objectName: "brandMark"
                            anchors.fill: parent
                            source: typeof brandLogo === "string" ? brandLogo : ""
                            sourceSize: Qt.size(width * Screen.devicePixelRatio,
                                                height * Screen.devicePixelRatio)
                            fillMode: Image.PreserveAspectFit
                            smooth: true; mipmap: true
                            visible: status === Image.Ready
                        }
                        Rectangle {
                            anchors.fill: parent; radius: 14; color: Theme.primaryContainer
                            visible: !brandMark.visible
                            Icon { anchors.centerIn: parent; glyph: ic.app; color: Theme.colorOnPrimaryContainer }
                        }
                    }
                    Label { text: Tr.t("app.name"); font.pixelSize: 18; font.weight: Font.DemiBold
                            color: Theme.colorOnSurface; Layout.fillWidth: true; elide: Text.ElideRight }
                }
                ListView {
                    id: nav
                    objectName: "nav"
                    Layout.fillWidth: true
                    Layout.preferredHeight: contentHeight
                    interactive: false
                    spacing: 4
                    keyNavigationEnabled: true
                    activeFocusOnTab: true
                    Accessible.role: Accessible.PageTabList
                    model: [
                        { key: "nav.voice", glyph: win.ic.engine },
                        { key: "nav.speed", glyph: win.ic.speed },
                        { key: "nav.filters", glyph: win.ic.filter },
                        { key: "nav.hotkeys", glyph: win.ic.keyboard }
                    ]
                    delegate: ItemDelegate {
                        id: navItem
                        width: nav.width
                        height: 56
                        required property int index
                        required property var modelData
                        readonly property bool current: nav.currentIndex === index
                        focusPolicy: Qt.NoFocus
                        onClicked: nav.currentIndex = index
                        Accessible.role: Accessible.PageTab
                        Accessible.name: Tr.t(modelData.key)
                        background: Rectangle {
                            radius: 28
                            color: navItem.current ? Theme.secondaryContainer : "transparent"
                            Rectangle { anchors.fill: parent; radius: parent.radius; color: Theme.colorOnSurface
                                        opacity: navItem.pressed ? 0.10 : navItem.hovered ? 0.06 : 0 }
                            FocusRing { shapeRadius: 28; gap: 1; shown: navItem.current && nav.activeFocus }
                        }
                        contentItem: RowLayout {
                            spacing: 12
                            Icon { glyph: navItem.modelData.glyph; Layout.leftMargin: 4
                                   color: navItem.current ? Theme.colorOnSecondaryContainer : Theme.colorOnSurfaceVariant }
                            Label {
                                text: Tr.t(navItem.modelData.key); Layout.fillWidth: true
                                font.pixelSize: 14; font.weight: navItem.current ? Font.Bold : Font.DemiBold
                                color: navItem.current ? Theme.colorOnSecondaryContainer : Theme.colorOnSurfaceVariant
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
                ItemDelegate {
                    id: setupItem
                    Layout.fillWidth: true
                    height: 48
                    onClicked: win.openOnboarding()
                    Accessible.name: Tr.t("nav.setup")
                    background: Rectangle {
                        radius: 24; color: Theme.colorOnSurface
                        opacity: setupItem.pressed ? 0.10 : setupItem.hovered ? 0.06 : 0
                    }
                    FocusRing { shapeRadius: 24; gap: 1; shown: setupItem.visualFocus }
                    contentItem: RowLayout {
                        spacing: 12
                        Icon { glyph: ic.sparkle; color: Theme.tertiary; Layout.leftMargin: 4 }
                        Label { text: Tr.t("nav.setup"); color: Theme.colorOnSurfaceVariant
                                font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                }
                Label {
                    text: Tr.t("settings.language")
                    color: Theme.colorOnSurfaceVariant; font.pixelSize: 12
                    Layout.leftMargin: 16; Layout.topMargin: 12; Layout.bottomMargin: 2
                }
                LanguageBox { Layout.fillWidth: true; Layout.leftMargin: 4; Layout.rightMargin: 4 }
            }
        }

        // --- Content and footer ---
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: nav.currentIndex

                // ===== Page 1: voice and engine ============================= //
                Page {
                    id: enginePage
                    Headline { text: Tr.t("page.voice.title") }
                    Body { text: Tr.t("page.voice.text") }

                    Section {
                        objectName: "readLangsSection"
                        title: Tr.t("lang.title"); glyph: ic.translate
                        supporting: Tr.t("lang.text")
                        Flow {
                            Layout.fillWidth: true
                            spacing: 8
                            Repeater {
                                model: win.init.langs || []
                                LangChip {
                                    required property var modelData
                                    lang: modelData.code
                                    on: win.readLangs.indexOf(modelData.code) >= 0
                                    onToggled: win.toggleReadLang(modelData.code)
                                }
                            }
                        }
                    }

                    GroupLabel { text: Tr.t("page.voice.online") }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2; rowSpacing: 12; columnSpacing: 12
                        Repeater {
                            model: win.init.engines.filter(function (e) { return e.online })
                            EngineCard { required property var modelData; info: modelData }
                        }
                    }
                    GroupLabel { text: Tr.t("page.voice.offline") }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: enginePage.availableWidth > 700 ? 3 : 2
                        rowSpacing: 12; columnSpacing: 12
                        Repeater {
                            model: win.init.engines.filter(function (e) { return !e.online })
                            EngineCard { required property var modelData; info: modelData }
                        }
                    }
                    Item { Layout.preferredHeight: 4 }

                    // ---------------- ElevenLabs ---------------- //
                    Section {
                        visible: win.engine === "elevenlabs"
                        title: Tr.t("el.account")
                        glyph: ic.key
                        help: Tr.t("el.keyHelp")

                        Banner {
                            tone: win.elBusy ? "busy"
                                  : win.elError !== "" ? "error"
                                  : !win.elHasKey ? "info"
                                  : win.requiredMissing ? "error" : "ok"
                            text: {
                                if (win.elBusy) return Tr.t("el.checking")
                                if (win.elError !== "") return win.elError
                                if (!win.elHasKey) return Tr.t("el.noKey")
                                if (!win.permsKnown) return Tr.t("el.keySaved")
                                if (!win.elPerms.tts) return Tr.t("el.noTts")
                                if (!win.elPerms.voices) return Tr.t("el.noVoices")
                                if (win.elSub.limit) {
                                    var left = Math.max(0, win.elSub.limit - win.elSub.used)
                                    var h = left / (win.init.el.charsPerHour * win.rate(win.elModel))
                                    return Tr.t("el.connectedBalance", {
                                        tier: win.elSub.tier, left: win.fmt(left), limit: win.fmt(win.elSub.limit),
                                        hours: win.fmt1(h), model: win.modelName(win.elModel) })
                                }
                                return Tr.t("el.connectedNoBalance")
                            }
                        }

                        // Permission checklist with instructions, once something is missing
                        ColumnLayout {
                            visible: win.elHasKey && win.permsKnown && !win.elSetupOpen && !win.elBusy
                                     && (win.requiredMissing || win.optionalMissing)
                            Layout.fillWidth: true; spacing: 10
                            Body { text: win.requiredMissing ? Tr.t("el.permsFix") : Tr.t("el.permsOptional") }
                            Repeater { model: win.init.el.permList; PermRow { required property var modelData; perm: modelData } }
                            RowLayout {
                                spacing: 8
                                OutlinedButton { text: Tr.t("el.openKeyPage"); glyph: ic.open; onClicked: backend.openUrl() }
                                TonalButton { text: Tr.t("el.checkAgain"); glyph: ic.refresh; enabled: !win.elBusy
                                              onClicked: win.verify(backend.storedKey(), true) }
                            }
                        }

                        // Setup in three steps
                        ColumnLayout {
                            visible: win.elSetupOpen
                            Layout.fillWidth: true; spacing: 18
                            Step {
                                number: 1; title: Tr.t("el.step1")
                                FilledButton { text: Tr.t("el.getKey"); glyph: ic.open
                                               onClicked: backend.openKeyPage() }
                            }
                            Step {
                                number: 2; title: Tr.t("el.step2")
                                Repeater { model: win.init.el.permList; PermRow { required property var modelData; perm: modelData } }
                                Small { Layout.fillWidth: true; text: Tr.t("el.step2Note") }
                            }
                            Step {
                                number: 3; title: Tr.t("el.step3")
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 12
                                    TextField {
                                        id: keyField
                                        Layout.fillWidth: true
                                        Material.containerStyle: Material.Outlined
                                        placeholderText: Tr.t("el.pasteHere")
                                        echoMode: TextInput.Password
                                        Accessible.name: Tr.t("el.keyField")
                                        onAccepted: if (text.trim()) win.verify(text.trim(), false)
                                    }
                                    TonalButton { text: Tr.t("el.check"); glyph: ic.check
                                                  enabled: keyField.text.length > 0 && !win.elBusy
                                                  onClicked: win.verify(keyField.text.trim(), false) }
                                }
                            }
                            TextButton { visible: win.elHasKey; text: Tr.t("common.cancel"); Layout.leftMargin: 32
                                         onClicked: { win.elSetupOpen = false; win.elError = "" } }
                        }
                        TextButton {
                            visible: win.elHasKey && !win.elSetupOpen
                            text: Tr.t("el.otherKey"); glyph: ic.key; Layout.leftMargin: -12
                            onClicked: win.elSetupOpen = true
                        }
                    }

                    Section {
                        visible: win.engine === "elevenlabs"
                        title: Tr.t("el.voice"); glyph: ic.voice
                        supporting: Tr.t("el.voiceText")
                        Repeater {
                            model: win.readLangs
                            ElVoiceRow { required property string modelData; lang: modelData }
                        }
                        TextButton {
                            text: Tr.t("el.reloadVoices"); glyph: ic.refresh; Layout.leftMargin: -12
                            enabled: win.elHasKey && !win.elBusy
                            onClicked: win.verify(backend.storedKey(), true)
                        }
                    }

                    Section {
                        visible: win.engine === "elevenlabs"
                        title: Tr.t("el.model"); glyph: ic.tune
                        supporting: Tr.t("el.modelText", { chars: win.fmt(win.init.el.charsPerHour) })
                        help: Tr.t("el.creditsHelp")
                        spacing: 0
                        ButtonGroup { id: modelGroup }
                        Item { Layout.preferredHeight: 10 }
                        Repeater {
                            model: win.init.el.models
                            ColumnLayout {
                                id: mrow
                                required property var modelData
                                required property int index
                                readonly property bool available: Object.keys(win.elRates).length === 0
                                                                  || win.elRates[modelData.id] !== undefined
                                Layout.fillWidth: true
                                spacing: 0
                                Divider { visible: mrow.index > 0; Layout.topMargin: 4; Layout.bottomMargin: 4 }
                                ItemDelegate {
                                    id: mdel
                                    Layout.fillWidth: true
                                    Layout.leftMargin: -12; Layout.rightMargin: -12
                                    enabled: mrow.available
                                    onClicked: win.elModel = mrow.modelData.id
                                    background: Rectangle {
                                        radius: 12; color: Theme.colorOnSurface
                                        opacity: mdel.pressed ? 0.10 : mdel.hovered ? 0.05 : 0
                                    }
                                    contentItem: RowLayout {
                                        spacing: 12
                                        RadioButton {
                                            ButtonGroup.group: modelGroup
                                            checked: win.elModel === mrow.modelData.id
                                            onClicked: win.elModel = mrow.modelData.id
                                            padding: 0
                                            Layout.alignment: Qt.AlignTop
                                            Accessible.name: mrow.modelData.name
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true; spacing: 4
                                            RowLayout {
                                                Layout.fillWidth: true; spacing: 8
                                                Label { text: mrow.modelData.name; font.pixelSize: 15
                                                        font.weight: Font.DemiBold; color: Theme.colorOnSurface }
                                                Chip { text: win.rate(mrow.modelData.id) <= 0.5 ? Tr.t("el.halfPrice") : Tr.t("el.fullPrice")
                                                       accent: win.rate(mrow.modelData.id) > 0.5 }
                                                Chip { text: Tr.t("el.speed." + mrow.modelData.id, undefined, mrow.modelData.speed_label) }
                                                Item { Layout.fillWidth: true }
                                                Label {
                                                    font.pixelSize: 12; color: Theme.colorOnSurfaceVariant
                                                    text: mrow.available
                                                          ? Tr.t("el.perHour", { credits: win.fmt(win.init.el.charsPerHour * win.rate(mrow.modelData.id)) })
                                                          : Tr.t("el.notAvailable")
                                                }
                                            }
                                            Small { text: Tr.t("el.model." + mrow.modelData.id, undefined, mrow.modelData.blurb)
                                                    Layout.fillWidth: true }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Section {
                        visible: win.engine === "elevenlabs"
                        title: Tr.t("el.save"); glyph: ic.savings
                        SwitchItem {
                            id: elCacheSwitch
                            checked: win.init.el.cache
                            title: Tr.t("el.cacheTitle")
                            supporting: Tr.t("el.cacheText")
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Small { text: Tr.t("el.cacheInfo", { count: win.fmt(win.elCache.count),
                                                                  mb: win.elCache.mb, max: win.elCache.maxMb })
                                    Layout.fillWidth: true }
                            TextButton { text: Tr.t("el.cacheClear"); glyph: ic.clear
                                         onClicked: win.elCache = backend.clearCache() }
                        }
                        Small { Layout.fillWidth: true; text: Tr.t("el.speedNote") }
                    }

                    // ---------------- other engines ---------------- //
                    Repeater {
                        model: [{ key: "edge" }, { key: "sapi" }, { key: "mssp" }]
                               .concat(win.init.neural || [])
                        Section {
                            id: esec
                            required property var modelData
                            visible: win.engine === modelData.key && win.lists[modelData.key] !== undefined
                            title: Tr.t("voices.of", { engine: Tr.engineTitle(modelData.key) })
                            glyph: ic.voice
                            CapabilityList { Layout.fillWidth: true; caps: win.capsOf(modelData.key) }
                            Small {
                                visible: modelData.clone === true
                                Layout.fillWidth: true
                                text: Tr.t("voices.cloneHint")
                            }
                            Repeater {
                                model: win.readLangs
                                ColumnLayout {
                                    id: lrow
                                    required property string modelData
                                    readonly property bool spoken: win.speaks(esec.modelData.key, modelData)
                                    Layout.fillWidth: true
                                    spacing: 0
                                    VoiceRow {
                                        visible: lrow.spoken
                                        engineKey: esec.modelData.key; lang: lrow.modelData
                                        model: (win.lists[esec.modelData.key] || {})[lrow.modelData] || []
                                    }
                                    LangGap {
                                        visible: !lrow.spoken
                                        lang: lrow.modelData
                                        engineTitle: Tr.engineTitle(esec.modelData.key)
                                        route: win.langRoute(lrow.modelData)
                                        byVoice: win.routeVoice(lrow.modelData, route)
                                        piperVoice: win.langMeta(lrow.modelData).piper
                                        piperMb: win.langMeta(lrow.modelData).piperMb
                                    }
                                }
                            }
                            RowLayout {
                                visible: modelData.instruct === true
                                Layout.fillWidth: true
                                spacing: 8
                                TextField {
                                    Layout.fillWidth: true
                                    Material.containerStyle: Material.Outlined
                                    placeholderText: Tr.t("voices.instruct")
                                    Accessible.name: Tr.t("voices.instruct")
                                    text: win.instructs[modelData.key] || ""
                                    onTextEdited: {
                                        var all = JSON.parse(JSON.stringify(win.instructs))
                                        all[modelData.key] = text
                                        win.instructs = all
                                    }
                                }
                                HelpTip { text: Tr.t("voices.instructHelp") }
                            }
                            Small {
                                visible: (modelData.tags || []).length > 0
                                Layout.fillWidth: true
                                text: Tr.t("voices.tags", { tags: (modelData.tags || []).join("  ") })
                            }
                            TextButton {
                                visible: modelData.key === "edge"; Layout.leftMargin: -12
                                text: Tr.t("edge.refresh"); glyph: ic.refresh
                                onClicked: backend.refreshEdge()
                            }
                            // Cloning models: create and manage own voices
                            RowLayout {
                                visible: modelData.clone === true
                                spacing: 8
                                TonalButton { text: Tr.t("voices.new"); glyph: ic.add
                                              onClicked: newVoiceDlg.openFor(modelData.key) }
                                TextButton { text: Tr.t("voices.manage"); glyph: ic.list
                                             onClicked: manageDlg.openFor(modelData.key) }
                            }
                        }
                    }

                    Section {
                        title: win.engine === "piper" ? Tr.t("voices.of", { engine: "Piper" }) : Tr.t("voices.fallback")
                        glyph: win.engine === "piper" ? ic.voice : ic.fallback
                        supporting: win.engine === "piper" ? "" : Tr.t("voices.fallbackText")
                        Repeater {
                            model: win.readLangs
                            ColumnLayout {
                                id: prow
                                required property string modelData
                                readonly property bool has: win.piperHas(modelData)
                                Layout.fillWidth: true
                                spacing: 0
                                VoiceRow { visible: prow.has; engineKey: "piper"; lang: prow.modelData
                                           model: win.lists.piper[prow.modelData] || [] }
                                LangGap {
                                    visible: !prow.has
                                    lang: prow.modelData
                                    route: win.engine !== "piper" && win.speaks(win.engine, prow.modelData)
                                           ? "engine" : win.langRoute(prow.modelData)
                                    activeTitle: Tr.engineTitle(win.engine)
                                    byVoice: win.routeVoice(prow.modelData, route)
                                    piperVoice: win.langMeta(prow.modelData).piper
                                    piperMb: win.langMeta(prow.modelData).piperMb
                                }
                            }
                        }
                    }

                    Section {
                        title: Tr.t("detect.title"); glyph: ic.translate
                        supporting: Tr.t("detect.text")
                        ColumnLayout {
                            spacing: 0; Layout.leftMargin: -8
                            RadioButton { text: Tr.t("detect.text1")
                                          checked: win.init.langDetect !== "sentence" }
                            RadioButton { id: perSentence; text: Tr.t("detect.sentence")
                                          checked: win.init.langDetect === "sentence" }
                        }
                    }
                    Item { Layout.preferredHeight: 16 }
                }

                // ===== Page 2: speed and pauses ============================ //
                Page {
                    Headline { text: Tr.t("page.speed.title") }
                    Body { text: Tr.t("page.speed.text") }
                    Section {
                        title: Tr.t("speed.title"); glyph: ic.speed
                        help: Tr.t("speed.help")
                        // The config stores a length scale (smaller = faster); the
                        // sliders show the pace factor, so further right reads faster.
                        SliderRow {
                            id: speedNormal
                            label: Tr.t("speed.normal"); from: 0.5; to: 2.0; step: 0.05
                            toUi: win.paceOf; fromUi: win.scaleOf; format: win.paceText
                            minText: Tr.t("speed.slower"); maxText: Tr.t("speed.faster")
                            initial: win.init.speedNormal
                            onProbe: win.test(win.engine, win.readLangs[0], { scale: value })
                        }
                        SliderRow {
                            id: speedFast
                            label: Tr.t("speed.fast"); from: 0.5; to: 2.0; step: 0.05
                            toUi: win.paceOf; fromUi: win.scaleOf; format: win.paceText
                            minText: Tr.t("speed.slower"); maxText: Tr.t("speed.faster")
                            initial: win.init.speedFast
                            onProbe: win.test(win.engine, win.readLangs[0], { scale: value })
                        }
                    }
                    Section {
                        title: Tr.t("pause.title"); glyph: ic.schedule
                        supporting: Tr.t("pause.text")
                        SliderRow {
                            id: pauseSlider
                            label: Tr.t("pause.label"); from: 0; to: 800; step: 50
                            format: function (v) { return Tr.t("pause.ms", { ms: Math.round(v) }) }
                            initial: win.init.pause
                            onProbe: win.test(win.engine, win.readLangs[0],
                                              { text: backend.pauseSample(win.readLangs[0]), pauseMs: value })
                        }
                    }
                    Section {
                        title: Tr.t("follow.title"); glyph: ic.book
                        supporting: Tr.t("follow.setting")
                        SwitchItem {
                            id: followSwitch
                            checked: win.init.follow
                            title: Tr.t("tray.follow")
                        }
                        SliderRow {
                            id: followOpacity
                            enabled: followSwitch.checked
                            label: Tr.t("follow.opacity"); from: 20; to: 100; step: 5
                            format: function (v) { return Tr.t("follow.percent", { value: Math.round(v) }) }
                            initial: win.init.followOpacity
                            probeable: false
                        }
                    }
                }

                // ===== Page 3: text clean-up =============================== //
                Page {
                    id: filtersPage
                    Headline { text: Tr.t("page.filters.title") }
                    Body { text: Tr.t("page.rules.text") }

                    // --- One list of rules, shown by group ---
                    Section {
                        objectName: "rulesSection"
                        title: Tr.t("rules.title"); glyph: win.ric.findReplace
                        supporting: Tr.t("rules.text")
                        spacing: 12
                        Flow {
                            Layout.fillWidth: true
                            Layout.topMargin: 4
                            spacing: 8
                            TonalButton { text: Tr.t("rules.add"); glyph: ic.add; onClicked: ruleDlg.openNew() }
                            OutlinedButton { text: Tr.t("rules.copyAi"); glyph: win.ric.agent
                                             onClicked: aiDlg.openPrompt() }
                            TextButton { text: Tr.t("rules.restoreAll"); glyph: win.ric.restore
                                         visible: win.rulesInfo.canRestore === true
                                         onClicked: win.restorePresets("") }
                            TextButton { text: Tr.t("rules.undoLast"); glyph: win.ric.undo
                                         visible: win.rulesInfo.canUndo
                                         onClicked: win.rulesApply(backend.rulesUndo(), Tr.t("rules.undone")) }
                        }
                        Banner { visible: win.rulesInfo.error !== ""; tone: "error"; text: win.rulesInfo.error }
                        Banner {
                            visible: win.rulesInfo.toReview > 0
                            tone: "info"
                            text: win.rulesInfo.toReview === 1 ? Tr.t("rules.toReviewOne")
                                                               : Tr.t("rules.toReview", { n: win.rulesInfo.toReview })
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.bottomMargin: 8
                            spacing: 0
                            Repeater {
                                id: groupRepeater
                                model: win.ruleSections
                                RuleGroup {}
                            }
                        }
                    }

                    // --- Try it: before and after, matches highlighted ---
                    Section {
                        objectName: "trySection"
                        title: Tr.t("rules.tryTitle"); glyph: win.ric.science
                        supporting: Tr.t("rules.tryText")
                        TextArea {
                            id: tryInput
                            objectName: "tryInput"
                            Layout.fillWidth: true
                            Layout.minimumHeight: 72
                            wrapMode: TextEdit.Wrap
                            Material.containerStyle: Material.Outlined
                            text: Tr.t("rules.trySample")
                            Accessible.name: Tr.t("rules.tryTitle")
                            onTextChanged: tryTimer.restart()
                            Component.onCompleted: tryTimer.restart()
                        }
                        RowLayout {
                            spacing: 12
                            Label { text: Tr.t("rules.tryLang"); color: Theme.colorOnSurfaceVariant }
                            ComboBox {
                                id: tryLangBox
                                model: win.readLangs.map(function (l) { return Tr.langName(l) })
                                // the sample text is in the app language
                                currentIndex: Math.max(0, win.readLangs.indexOf(Tr.language))
                                Material.containerStyle: Material.Outlined
                                implicitWidth: 170
                                Accessible.name: Tr.t("rules.tryLang")
                                onActivated: tryTimer.restart()
                            }
                        }
                        GroupLabel { text: Tr.t("rules.before") }
                        Label {
                            Layout.fillWidth: true
                            textFormat: Text.RichText
                            wrapMode: Text.Wrap
                            color: Theme.colorOnSurface
                            font.pixelSize: 15
                            text: win.runsHtml(win.tryResult.before, Theme.tertiaryContainer,
                                               Theme.colorOnTertiaryContainer)
                        }
                        GroupLabel { text: Tr.t("rules.after") }
                        Label {
                            objectName: "tryAfter"
                            Layout.fillWidth: true
                            textFormat: Text.RichText
                            wrapMode: Text.Wrap
                            color: Theme.colorOnSurface
                            font.pixelSize: 15
                            text: win.runsHtml(win.tryResult.after, Theme.primaryContainer,
                                               Theme.colorOnPrimaryContainer)
                        }
                        Small {
                            Layout.fillWidth: true
                            Layout.bottomMargin: 8
                            text: (win.tryResult.fired || []).length === 0 ? Tr.t("rules.firedNone")
                                  : Tr.t("rules.fired", { list: (win.tryResult.fired || []).map(function (f) {
                                        return win.ruleName(f) + (f.count > 1 && f.kind !== "builtin"
                                                                  ? " (" + f.count + "×)" : "")
                                    }).join(", ") })
                        }
                    }
                    Item { Layout.preferredHeight: 16 }
                }

                // ===== Page 4: hotkeys ===================================== //
                Page {
                    Headline { text: Tr.t("page.hotkeys.title") }
                    Body {
                        text: win.init.inprocHotkeys ? Tr.t("page.hotkeys.textWin") : Tr.t("page.hotkeys.textLinux")
                    }
                    Section {
                        title: Tr.t("hotkeys.title"); glyph: ic.keyboard
                        spacing: 0
                        Item { Layout.preferredHeight: 6 }
                        Repeater {
                            id: hotkeyRepeater
                            model: win.init.hotkeys
                            ColumnLayout {
                                id: hk
                                required property var modelData
                                required property int index
                                property string combo: modelData.combo
                                Layout.fillWidth: true; spacing: 0
                                Divider { visible: hk.index > 0 }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 16
                                    Layout.topMargin: 10; Layout.bottomMargin: 10
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 4
                                        RowLayout {
                                            Layout.fillWidth: true; spacing: 8
                                            Label { text: Tr.t("hotkey." + hk.modelData.action)
                                                    color: Theme.colorOnSurface; font.pixelSize: 15
                                                    wrapMode: Text.Wrap; Layout.fillWidth: !betaChip.visible }
                                            Chip { id: betaChip; visible: hk.modelData.action === "reader"
                                                   text: Tr.t("beta.tag"); tip: Tr.t("beta.reader") }
                                            Item { visible: betaChip.visible; Layout.fillWidth: true }
                                        }
                                        Small { visible: betaChip.visible; text: Tr.t("beta.reader")
                                                Layout.fillWidth: true }
                                    }
                                    HotkeyField {
                                        Layout.preferredWidth: 260; Layout.preferredHeight: 48
                                        combo: hk.combo
                                        label: Tr.t("hotkey." + hk.modelData.action)
                                        onRecorded: (c) => hk.combo = c
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // --- Footer with divider ---
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.outlineVariant }
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: 16
                Layout.leftMargin: 40; Layout.rightMargin: 24
                spacing: 8
                Small {
                    visible: !!win.init.version
                    text: "Recito TTS " + win.init.version
                    opacity: 0.7
                    wrapMode: Text.NoWrap
                }
                Item { Layout.fillWidth: true }
                TextButton { text: Tr.t("common.close"); onClicked: win.close() }
                FilledButton {
                    id: saveBtn
                    enabled: win.saveState !== "busy"
                    text: win.saveState === "busy" ? Tr.t("save.busy")
                          : win.saveState === "done" ? Tr.t("save.saved")
                          : win.saveState === "error" ? Tr.t("save.notSaved") : Tr.t("save.button")
                    glyph: win.saveState === "busy" ? ic.refresh
                           : win.saveState === "error" ? ic.error : ic.check
                    container: win.saveState === "done" ? Theme.ok : Theme.primary
                    onClicked: win.save(false)
                    // the spinner turns while the daemon restarts
                    RotationAnimation {
                        target: saveBtn.glyphItem
                        running: win.saveState === "busy"; loops: Animation.Infinite
                        from: 0; to: 360; duration: 900
                        onRunningChanged: if (!running) saveBtn.glyphItem.rotation = 0
                    }
                }
            }
        }
    }

    // ===================================================================== //
    //  Setup assistant (covers the whole window while open)
    // ===================================================================== //
    Onboarding {
        id: onboarding
        objectName: "onboarding"
        anchors.fill: parent
        visible: opacity > 0
        opacity: win.onboardingOpen ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 200 } }
        z: 50
        onNotify: (text) => win.snackbar(text)
        onPlayPreview: (url) => url ? win.playPreview(url) : win.stopPlayer()
        onSkipped: {
            backend.finishOnboarding({})
            win.saveSilent = true
            win.onboardingOpen = false
        }
        onFinished: (r, closeWindow) => {
            win.applyOnboarding(r)
            win.saveSilent = !closeWindow
            win.closeAfterSave = closeWindow
            win.saveState = "busy"
            backend.finishOnboarding(r)
            win.onboardingOpen = false
            if (!closeWindow) snackbar(Tr.t("onb.welcomeToSettings"))
        }
        onCreateVoice: (key) => newVoiceDlg.openFor(key)
    }

    // ===================================================================== //
    //  Own voices: create (file, microphone, PC audio) and manage
    // ===================================================================== //
    // One player for the whole window: never two playbacks at once.
    MediaPlayer {
        objectName: "player"
        id: player
        audioOutput: AudioOutput {}
        onErrorOccurred: (err, msg) => snackbar(Tr.t("player.failed", { error: msg }))
    }
    function isCurrent(url) { return url !== "" && player.source.toString() === url }
    function playToggle(url) {
        if (isCurrent(url)) {
            if (player.playbackState === MediaPlayer.PlayingState) player.pause()
            else player.play()
            return
        }
        player.stop()
        player.source = url
        player.play()
    }
    function stopPlayer() { player.stop(); player.source = "" }
    // Cached voice sample: stops a live preview of the daemon, plays from the start
    function playPreview(url) {
        backend.stopSpeech()
        player.stop()
        player.source = url
        player.play()
    }
    function mmss(ms) {
        var s = Math.max(0, Math.round(ms / 1000))
        return Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60)
    }

    // Play/pause, stop, seek and time for one file (url)
    component PlayerBar: RowLayout {
        id: pb
        property string url: ""
        property bool showToggle: true
        readonly property bool current: win.isCurrent(url)
        readonly property bool playing: current && player.playbackState === MediaPlayer.PlayingState
        spacing: 4
        TonalButton {
            visible: pb.showToggle
            text: pb.playing ? Tr.t("player.pause") : (pb.current && player.position > 0 ? Tr.t("player.resume")
                                                                                         : Tr.t("player.play"))
            glyph: pb.playing ? ic.pause : ic.play
            onClicked: win.playToggle(pb.url)
        }
        TextButton {
            glyph: ic.stop; text: Tr.t("player.stop")
            enabled: pb.current && player.playbackState !== MediaPlayer.StoppedState
            onClicked: player.stop()
        }
        Slider {
            Layout.fillWidth: true
            from: 0
            to: pb.current && player.duration > 0 ? player.duration : 1
            value: pb.current ? player.position : 0
            enabled: pb.current && player.duration > 0
            onMoved: player.position = value
            Accessible.name: Tr.t("player.position")
        }
        Label {
            text: pb.current ? win.mmss(player.position) + " / " + win.mmss(player.duration) : ""
            color: Theme.colorOnSurfaceVariant
            font.pixelSize: 12
            Layout.preferredWidth: 72
        }
    }

    function applyLists(m) {
        var l = JSON.parse(JSON.stringify(win.lists))
        for (var k in m) l[k] = m[k]
        win.lists = l
        win.labels = backend.voiceLabels()
    }

    component DialogFrame: Popup {
        id: df
        default property alias content: dfCol.data
        property string heading: ""
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(620, win.width - 48)
        height: Math.min(implicitHeight, win.height - 48)
        modal: true
        padding: 0
        closePolicy: Popup.CloseOnEscape
        // Popups live in the overlay and do not inherit the window's Material scheme
        Material.theme: Theme.dark ? Material.Dark : Material.Light
        Material.accent: Theme.primary
        Material.primary: Theme.primary
        font.family: Theme.fontFamily
        background: Rectangle { radius: 28; color: Theme.surfaceContainerHigh }
        contentItem: ScrollView {
            id: dfScroll
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                id: dfCol
                width: dfScroll.availableWidth - 48
                x: 24
                spacing: 14
                Item { Layout.preferredHeight: 10 }
                Headline { text: df.heading; font.pixelSize: 24 }
            }
        }
    }

    FileDialog {
        id: fileDlg
        title: Tr.t("voice.fromFile")
        nameFilters: [Tr.t("voice.audioFiles") + " (*.wav *.mp3 *.m4a *.flac *.ogg *.opus *.webm *.mp4 *.aac *.wma)",
                      Tr.t("voice.allFiles") + " (*)"]
        onAccepted: newVoiceDlg.setResult(backend.importFile(selectedFile.toString()))
    }

    DialogFrame {
        id: newVoiceDlg
        objectName: "newVoiceDlg"
        heading: Tr.t("voice.newTitle")
        property string engineKey: ""
        property string mode: ""              // "" | "mic" | "pc"
        property bool recording: false
        property real recSeconds: 0
        property real recLevel: 0
        property var result: null            // {ok, notes, seconds, source}
        property var devs: ({ mic: [], pc: [] })
        readonly property string lang: win.readLangs[langBox.currentIndex] || win.readLangs[0]

        property bool saved: false
        function openFor(key) {
            stopPlayer(); backend.discardPending()
            engineKey = key; mode = ""; result = null; recording = false; saved = false
            nameField.text = ""; useBox.checked = true
            langBox.currentIndex = Math.max(0, win.readLangs.indexOf(Tr.language))
            devs = backend.voiceDevices()
            open()
            nameField.forceActiveFocus()
        }
        function setResult(r) { stopPlayer(); result = r }
        function discard() { stopPlayer(); backend.discardPending(); result = null }
        function startRec() {
            discard()
            var err = backend.recordStart(mode, devBox.currentText)
            if (err) { snackbar(err); return }
            recSeconds = 0; recLevel = 0; recording = true
        }
        function stopRec() { recording = false; result = backend.recordStop() }
        function redo() {
            if (mode === "") { discard(); fileDlg.open() }
            else startRec()
        }
        onClosed: {
            stopPlayer()
            if (recording) { recording = false; backend.recordCancel() }
            if (!saved) backend.discardPending()
            result = null
        }

        Connections {
            target: backend
            function onRecTick(s, lvl) { newVoiceDlg.recSeconds = s; newVoiceDlg.recLevel = lvl }
            function onRecAutoStopped() { if (newVoiceDlg.recording) newVoiceDlg.stopRec() }
        }

        Body { text: Tr.t("voice.newText") }
        TextField {
            id: nameField
            Layout.fillWidth: true
            Material.containerStyle: Material.Outlined
            placeholderText: Tr.t("voice.namePlaceholder")
            Accessible.name: Tr.t("voice.name")
        }
        RowLayout {
            spacing: 12
            Label { text: Tr.t("voice.recLang"); color: Theme.colorOnSurfaceVariant }
            ComboBox {
                id: langBox
                model: win.readLangs.map(function (l) { return Tr.langName(l) })
                Material.containerStyle: Material.Outlined
                implicitWidth: 170
                Accessible.name: Tr.t("voice.recLang")
            }
        }
        GroupLabel { text: Tr.t("voice.source") }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            OutlinedButton { text: Tr.t("voice.srcFileButton"); glyph: ic.upload
                             enabled: !newVoiceDlg.recording
                             onClicked: { newVoiceDlg.discard(); newVoiceDlg.mode = ""; fileDlg.open() } }
            OutlinedButton { text: Tr.t("voice.srcMic"); glyph: ic.mic
                             enabled: !newVoiceDlg.recording
                             onClicked: { newVoiceDlg.discard(); newVoiceDlg.mode = "mic" } }
            OutlinedButton { text: Tr.t("voice.srcPc"); glyph: ic.speaker
                             enabled: !newVoiceDlg.recording
                             onClicked: { newVoiceDlg.discard(); newVoiceDlg.mode = "pc" } }
        }

        // Recording (microphone or PC audio)
        ColumnLayout {
            visible: newVoiceDlg.mode !== ""
            Layout.fillWidth: true
            spacing: 10
            Small {
                Layout.fillWidth: true
                text: newVoiceDlg.mode === "pc" ? Tr.t("voice.pcHint") : Tr.t("voice.micHint")
            }
            ComboBox {
                id: devBox
                Layout.fillWidth: true
                Material.containerStyle: Material.Outlined
                enabled: !newVoiceDlg.recording
                model: newVoiceDlg.mode === "pc" ? newVoiceDlg.devs.pc : newVoiceDlg.devs.mic
                Accessible.name: Tr.t("voice.device")
            }
            Small {
                visible: devBox.count === 0
                Layout.fillWidth: true
                color: Theme.warn
                text: Tr.t("voice.noDevice")
            }
            RowLayout {
                spacing: 16
                FilledButton {
                    visible: newVoiceDlg.result === null
                    enabled: devBox.count > 0 || newVoiceDlg.recording
                    text: newVoiceDlg.recording ? Tr.t("voice.recStop") : Tr.t("voice.recStart")
                    glyph: newVoiceDlg.recording ? ic.stop : ic.mic
                    onClicked: newVoiceDlg.recording ? newVoiceDlg.stopRec() : newVoiceDlg.startRec()
                }
                TextButton {
                    visible: newVoiceDlg.recording
                    text: Tr.t("common.cancel"); glyph: ic.cancel
                    onClicked: { newVoiceDlg.recording = false; backend.recordCancel() }
                }
                Label {
                    text: Tr.t("voice.seconds", { s: fmt1(newVoiceDlg.recSeconds) })
                    visible: newVoiceDlg.recording
                    font.pixelSize: 16; font.weight: Font.DemiBold
                    color: newVoiceDlg.recSeconds >= 10 ? Theme.ok : Theme.colorOnSurface
                }
                ProgressBar {
                    visible: newVoiceDlg.recording
                    Layout.fillWidth: true
                    from: 0; to: 1; value: newVoiceDlg.recLevel
                    Accessible.name: Tr.t("voice.level")
                }
            }
        }

        // Result of the clean-up
        Banner {
            visible: newVoiceDlg.result !== null
            tone: !newVoiceDlg.result ? "info" : !newVoiceDlg.result.ok ? "error"
                  : (newVoiceDlg.result.notes.length ? "warn" : "ok")
            text: {
                var r = newVoiceDlg.result
                if (!r) return ""
                var head = r.ok ? Tr.t("voice.resultOk", { s: fmt1(r.seconds), source: r.source })
                                : Tr.t("voice.resultBad")
                return [head].concat(r.notes).join("\n")
            }
        }
        // Listen (with pause and seek), discard or redo
        PlayerBar {
            Layout.fillWidth: true
            visible: newVoiceDlg.result !== null && (newVoiceDlg.result.url || "") !== ""
            url: newVoiceDlg.result ? (newVoiceDlg.result.url || "") : ""
        }
        RowLayout {
            visible: newVoiceDlg.result !== null
            spacing: 8
            TextButton { text: Tr.t("voice.discard"); glyph: ic.del; ink: Theme.error
                         onClicked: newVoiceDlg.discard() }
            TonalButton { text: newVoiceDlg.mode === "" ? Tr.t("voice.otherFile") : Tr.t("voice.redo")
                          glyph: ic.replay
                          onClicked: newVoiceDlg.redo() }
        }
        CheckBox {
            id: useBox
            visible: newVoiceDlg.result !== null && newVoiceDlg.result.ok
            text: Tr.t("voice.useFor", { lang: Tr.langName(newVoiceDlg.lang) })
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.bottomMargin: 18
            Item { Layout.fillWidth: true }
            TextButton { text: Tr.t("common.cancel"); onClicked: newVoiceDlg.close() }
            FilledButton {
                text: Tr.t("voice.save"); glyph: ic.check
                enabled: newVoiceDlg.result !== null && newVoiceDlg.result.ok
                         && nameField.text.trim() !== "" && !newVoiceDlg.recording
                onClicked: {
                    stopPlayer()
                    var name = nameField.text.trim()
                    var r = backend.savePending(name, newVoiceDlg.lang)
                    if (r.error) { snackbar(r.error); return }
                    newVoiceDlg.saved = true
                    applyLists(r.lists)
                    if (useBox.checked) {
                        var all = JSON.parse(JSON.stringify(win.voices))
                        if (!all[newVoiceDlg.engineKey]) all[newVoiceDlg.engineKey] = {}
                        all[newVoiceDlg.engineKey][newVoiceDlg.lang] = "ref:" + r.id
                        win.voices = all
                        if (win.onboardingOpen) onboarding.useVoice(newVoiceDlg.lang, "ref:" + r.id)
                        else save(true)
                        snackbar(Tr.t("voice.savedUsed", { name: name }))
                    } else {
                        snackbar(Tr.t("voice.savedListed", { name: name }))
                    }
                    newVoiceDlg.close()
                }
            }
        }
    }

    DialogFrame {
        id: manageDlg
        objectName: "manageDlg"
        heading: Tr.t("voices.manage")
        property string engineKey: ""
        property var items: []
        property string confirmId: ""
        function openFor(key) { engineKey = key; confirmId = ""; items = backend.voiceList(); open() }
        onClosed: stopPlayer()

        Body { text: Tr.t("manage.text") }
        // Empty state with the next step
        ColumnLayout {
            visible: manageDlg.items.length === 0
            Layout.fillWidth: true
            Layout.topMargin: 8
            spacing: 8
            Icon { glyph: ic.mic; font.pixelSize: 40; color: Theme.primary; Layout.alignment: Qt.AlignHCenter }
            Title { text: Tr.t("manage.emptyTitle"); horizontalAlignment: Text.AlignHCenter }
            Body { text: Tr.t("manage.emptyText"); horizontalAlignment: Text.AlignHCenter }
        }
        Repeater {
            model: manageDlg.items
            ColumnLayout {
                id: vrow
                required property var modelData
                readonly property bool current: win.isCurrent(modelData.url)
                readonly property bool playing: current && player.playbackState === MediaPlayer.PlayingState
                Layout.fillWidth: true
                spacing: 2
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Label { text: vrow.modelData.name; color: Theme.colorOnSurface; font.pixelSize: 15
                                elide: Text.ElideRight; Layout.fillWidth: true }
                        Small {
                            Layout.fillWidth: true
                            text: [Tr.langName(vrow.modelData.lang),
                                   Tr.t("voice.seconds", { s: fmt1(vrow.modelData.seconds) }),
                                   vrow.modelData.source || "", vrow.modelData.created || ""]
                                  .filter(function (x) { return x !== "" }).join(" · ")
                        }
                    }
                    TonalButton {
                        text: vrow.playing ? Tr.t("player.pause")
                              : (vrow.current && player.position > 0 ? Tr.t("player.resume") : Tr.t("player.play"))
                        glyph: vrow.playing ? ic.pause : ic.play
                        onClicked: win.playToggle(vrow.modelData.url)
                    }
                    TextButton {
                        text: manageDlg.confirmId === vrow.modelData.id ? Tr.t("manage.confirmDelete") : Tr.t("manage.delete")
                        glyph: ic.del
                        ink: Theme.error
                        onClicked: {
                            var m = vrow.modelData
                            if (manageDlg.confirmId !== m.id) { manageDlg.confirmId = m.id; return }
                            if (vrow.current) stopPlayer()
                            var r = backend.deleteVoice(m.id)
                            applyLists(r.lists)
                            var all = JSON.parse(JSON.stringify(win.voices))
                            for (var e in all) for (var lg in all[e])
                                if (all[e][lg] === "ref:" + m.id) all[e][lg] = ""
                            win.voices = all
                            if (r.reset.length) save(true)
                            snackbar(Tr.t("manage.deleted", { name: m.name }))
                            manageDlg.confirmId = ""
                            manageDlg.items = backend.voiceList()
                        }
                    }
                }
                // progress only for the voice that is playing
                PlayerBar {
                    Layout.fillWidth: true
                    showToggle: false
                    visible: vrow.current
                    url: vrow.modelData.url
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.bottomMargin: 18
            TonalButton { text: Tr.t("voices.new"); glyph: ic.add
                          onClicked: { manageDlg.close(); newVoiceDlg.openFor(manageDlg.engineKey) } }
            Item { Layout.fillWidth: true }
            TextButton { text: Tr.t("common.close"); onClicked: manageDlg.close() }
        }
    }

    // ===================================================================== //
    //  Text rules: list row, rule dialog, instructions for AI assistants
    // ===================================================================== //
    // Icon-only button with a tooltip
    component IconButton: M3Button {
        property string tip: ""
        text: ""
        ink: Theme.colorOnSurfaceVariant
        Accessible.name: tip
        ToolTip.visible: hovered && tip !== ""
        ToolTip.text: tip
        ToolTip.delay: 400
    }

    // One group: a header that opens and closes it, with the rule count and
    // the group switch; inside, its rules and a way to add deleted presets back
    component RuleGroup: ColumnLayout {
        id: rg
        required property var modelData
        required property int index
        readonly property string group: modelData.group
        readonly property var info: win.groupInfo(group)
        readonly property bool open: win.isOpen(group)
        readonly property string name: Tr.t("rules.group." + group, undefined, group)
        Layout.fillWidth: true
        spacing: 0
        Divider { visible: rg.index > 0 }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            AbstractButton {
                id: head
                objectName: "groupHead_" + rg.group
                Layout.fillWidth: true
                implicitHeight: headRow.implicitHeight + 24
                hoverEnabled: true
                onClicked: win.toggleOpen(rg.group)
                Accessible.role: Accessible.Button
                Accessible.name: Tr.t(rg.open ? "rules.collapse" : "rules.expand", { group: rg.name })
                background: Rectangle {
                    radius: 12; color: Theme.colorOnSurface
                    opacity: head.pressed ? 0.10 : head.hovered ? 0.05 : 0
                    FocusRing { shapeRadius: 12; gap: 1; shown: head.visualFocus }
                }
                contentItem: RowLayout {
                    id: headRow
                    spacing: 12
                    Icon { glyph: rg.open ? win.ric.collapse : win.ric.expand; Layout.leftMargin: 4 }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Label { text: rg.name; font.pixelSize: 16; font.weight: Font.DemiBold
                                color: Theme.colorOnSurface; Layout.fillWidth: true; elide: Text.ElideRight }
                        Small { text: win.groupSummary(rg.info); Layout.fillWidth: true }
                    }
                }
            }
            TextButton {
                visible: rg.info.preset && rg.info.count === 0
                glyph: win.ric.restore
                text: Tr.t("rules.addGroup." + rg.group, undefined, rg.group)
                onClicked: win.restorePresets(rg.group)
            }
            Switch {
                visible: rg.info.count > 0
                checked: rg.info.on
                Accessible.name: Tr.t("rules.groupOn", { group: rg.name })
                onToggled: win.rulesApply(backend.ruleGroupToggle(rg.group, checked))
            }
        }
        ColumnLayout {
            visible: rg.open
            Layout.fillWidth: true
            Layout.leftMargin: 40
            Layout.bottomMargin: 8
            spacing: 0
            Small { text: Tr.t("rules.group." + rg.group + ".desc", undefined, "")
                    visible: text !== ""; Layout.fillWidth: true; Layout.bottomMargin: 4 }
            Repeater {
                model: rg.modelData.items
                RuleRow { groupOn: rg.info.on }
            }
            // Deleted presets of this group (a group without rules has the button in its header)
            RowLayout {
                visible: rg.info.missing > 0 && rg.info.count > 0
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 8
                Small {
                    Layout.fillWidth: true
                    text: rg.info.missing === 1 ? Tr.t("rules.missingOne")
                          : Tr.t("rules.missing", { n: rg.info.missing })
                }
                TextButton {
                    glyph: win.ric.restore
                    text: rg.info.missing === 1 ? Tr.t("rules.addBackOne") : Tr.t("rules.addBack")
                    onClicked: win.restorePresets(rg.group)
                }
            }
            // Your rules, while there are none: what a rule is for, with an example
            RowLayout {
                visible: rg.group === "user" && rg.info.count === 0 && win.rulesInfo.error === ""
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 16
                Rectangle {
                    implicitWidth: 40; implicitHeight: 40; radius: 14
                    color: Theme.secondaryContainer
                    Layout.alignment: Qt.AlignTop
                    Icon { anchors.centerIn: parent; glyph: win.ric.findReplace
                           color: Theme.colorOnSecondaryContainer }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Label { text: Tr.t("rules.emptyTitle"); font.pixelSize: 15; font.weight: Font.DemiBold
                            color: Theme.colorOnSurface; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    Small { text: Tr.t("rules.emptyText"); Layout.fillWidth: true }
                }
            }
        }
    }

    // One rule: switch, what it does, details, badges and actions. Presets
    // show their title and pattern; code rules have nothing to edit.
    component RuleRow: ColumnLayout {
        id: rr
        required property var modelData            // {r, index, first, last}
        property bool groupOn: true
        readonly property var r: modelData.r
        readonly property int index: modelData.index
        readonly property bool preset: (r.preset_id || "") !== ""
        readonly property bool code: r.kind === "builtin"
        readonly property bool changed: (r.changed || []).length > 0
        readonly property bool agent: r.created_by === "agent"
        readonly property color ink: r.enabled && groupOn ? Theme.colorOnSurface : Theme.colorOnSurfaceVariant
        Layout.fillWidth: true
        spacing: 0
        Divider { visible: !rr.modelData.first }
        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 8; Layout.bottomMargin: 8
            spacing: 8
            opacity: rr.groupOn ? 1 : 0.6               // the whole group is switched off
            Switch {
                checked: rr.r.enabled
                Layout.alignment: Qt.AlignTop
                Accessible.name: Tr.t("rules.on", { rule: win.ruleName(rr.r) })
                onToggled: win.rulesApply(backend.ruleToggle(rr.r.id, checked))
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.topMargin: 6
                spacing: 4
                Label {
                    Layout.fillWidth: true
                    textFormat: Text.StyledText
                    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                    font.pixelSize: 15
                    color: rr.ink
                    text: rr.preset || rr.code ? win.esc(win.ruleName(rr.r))
                          : "<b>" + win.esc(rr.r.pattern) + "</b>&nbsp;&nbsp;→&nbsp;&nbsp;"
                            + (rr.r.replacement === ""
                               ? "<font color='" + Theme.colorOnSurfaceVariant + "'>" + win.esc(Tr.t("rules.nothing")) + "</font>"
                               : win.esc(rr.r.replacement))
                }
                // The pattern of a preset, as a starting point for own rules
                Label {
                    visible: rr.preset && !rr.code
                    Layout.fillWidth: true
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    font.family: win.monoFont
                    font.pixelSize: 12
                    color: Theme.colorOnSurfaceVariant
                    text: rr.r.pattern + "   →   " + (rr.r.replacement === "" ? Tr.t("rules.nothing") : rr.r.replacement)
                }
                Small {
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: (rr.code ? [Tr.t("preset." + rr.r.preset_id + ".desc", undefined, rr.r.note)]
                           : rr.preset ? [rr.r.lang === "all" ? "" : win.rulesLangName(rr.r.lang)]
                           : [Tr.t("rules.kind." + rr.r.kind, undefined, rr.r.kind),
                              win.rulesLangName(rr.r.lang), rr.r.note])
                          .concat([rr.r.enabled ? "" : Tr.t("rules.isOff")])
                          .filter(function (x) { return x !== "" }).join("  ·  ")
                }
                Small {
                    visible: rr.r.problem !== ""
                    Layout.fillWidth: true
                    color: Theme.error
                    text: Tr.t("rules.problem", { error: rr.r.problem })
                }
                Flow {
                    visible: rr.agent || rr.changed || rr.code
                    Layout.fillWidth: true
                    spacing: 8
                    Chip { visible: rr.code; text: Tr.t("rules.codeRule"); glyph: win.ric.code
                           tip: Tr.t("rules.codeTip") }
                    Chip { visible: rr.agent; text: Tr.t("rules.byAgent"); glyph: win.ric.agent
                           accent: !rr.r.reviewed; tip: Tr.t("rules.byAgentTip") }
                    TextButton { visible: rr.agent && !rr.r.reviewed; text: Tr.t("rules.review")
                                 glyph: win.ric.review; onClicked: ruleDlg.openEdit(rr.r, true) }
                    Chip { visible: rr.changed; text: Tr.t("rules.changed"); accent: true
                           tip: Tr.t("rules.changedTip") }
                    TextButton { visible: rr.changed; text: Tr.t("rules.reset"); glyph: win.ric.restore
                                 onClicked: win.rulesApply(backend.ruleReset(rr.r.id), Tr.t("rules.resetDone")) }
                }
            }
            IconButton { glyph: win.ric.up; tip: Tr.t("rules.moveUp"); enabled: !rr.modelData.first
                         Layout.alignment: Qt.AlignTop
                         onClicked: win.rulesApply(backend.ruleMove(rr.r.id, rr.index)) }
            IconButton { glyph: win.ric.down; tip: Tr.t("rules.moveDown"); enabled: !rr.modelData.last
                         Layout.alignment: Qt.AlignTop
                         onClicked: win.rulesApply(backend.ruleMove(rr.r.id, rr.index + 2)) }
            IconButton { glyph: win.ric.edit; tip: Tr.t("rules.edit"); Layout.alignment: Qt.AlignTop
                         visible: !rr.code; onClicked: ruleDlg.openEdit(rr.r, false) }
            // keeps the delete buttons in one column when a row has no edit button
            Item { visible: rr.code; implicitWidth: 40; implicitHeight: 1 }
            IconButton { glyph: ic.del; tip: Tr.t("rules.delete"); ink: Theme.error; Layout.alignment: Qt.AlignTop
                         onClicked: win.removeRule(rr.r.id) }
        }
    }

    DialogFrame {
        id: ruleDlg
        objectName: "ruleDlg"
        property string ruleId: ""
        property string presetId: ""
        property bool presetChanged: false
        property bool reviewMode: false
        property string kind: "word"
        property var preview: ({ output: "", fired: [], before: [], after: [] })
        readonly property var kinds: ["word", "literal", "regex"]
        property string editLang: "all"      // scope of the rule being edited, kept if not a reading language
        readonly property var langs: {
            var l = ["all"].concat(win.readLangs)
            if (l.indexOf(editLang) < 0) l.push(editLang)
            return l
        }
        heading: reviewMode ? Tr.t("rules.dlgReview") : ruleId !== "" ? Tr.t("rules.dlgEdit") : Tr.t("rules.dlgNew")
        function fields() {
            return { kind: kind, pattern: patField.text, replacement: repField.text,
                     lang: langs[ruleLangBox.currentIndex], note: noteField.text.trim() }
        }
        readonly property string error: patField.text === "" ? "" : backend.ruleCheck(fields())
        function openNew() {
            ruleId = ""; presetId = ""; presetChanged = false; reviewMode = false; kind = "word"; editLang = "all"
            patField.text = ""; repField.text = ""; noteField.text = ""; ruleLangBox.currentIndex = 0
            open()
            patField.forceActiveFocus()
        }
        function openEdit(r, review) {
            ruleId = r.id; reviewMode = review === true; kind = r.kind; editLang = r.lang || "all"
            presetId = r.preset_id || ""; presetChanged = (r.changed || []).length > 0
            patField.text = r.pattern; repField.text = r.replacement; noteField.text = r.note || ""
            ruleLangBox.currentIndex = Math.max(0, langs.indexOf(r.lang))
            open()
        }
        function refreshPreview() { preview = backend.ruleTryOne(fields(), tryInput.text, win.tryLang()) }
        onOpened: refreshPreview()
        onKindChanged: if (opened) refreshPreview()

        Banner { visible: ruleDlg.reviewMode; tone: "info"; text: Tr.t("rules.reviewText") }
        Banner { visible: ruleDlg.presetId !== "" && !ruleDlg.reviewMode; tone: "info"
                 text: Tr.t("rules.presetText", { name: Tr.t("preset." + ruleDlg.presetId, undefined, ruleDlg.presetId) }) }
        TextField {
            id: patField
            objectName: "patField"
            Layout.fillWidth: true
            Material.containerStyle: Material.Outlined
            placeholderText: Tr.t("rules.pattern")
            Accessible.name: Tr.t("rules.pattern")
            onTextChanged: if (ruleDlg.opened) ruleDlg.refreshPreview()
        }
        TextField {
            id: repField
            Layout.fillWidth: true
            Material.containerStyle: Material.Outlined
            placeholderText: Tr.t("rules.replacement")
            Accessible.name: Tr.t("rules.replacement")
            onTextChanged: if (ruleDlg.opened) ruleDlg.refreshPreview()
        }
        Small { Layout.fillWidth: true; Layout.topMargin: -8; text: Tr.t("rules.replacementHint") }
        GroupLabel { text: Tr.t("rules.kindLabel"); Layout.topMargin: 0 }
        Flow {
            Layout.fillWidth: true
            spacing: 4
            Repeater {
                model: ruleDlg.kinds
                RadioButton {
                    required property var modelData
                    text: Tr.t("rules.kind." + modelData)
                    checked: ruleDlg.kind === modelData
                    onClicked: ruleDlg.kind = modelData
                }
            }
        }
        Small { Layout.fillWidth: true; Layout.topMargin: -8; text: Tr.t("rules.kind." + ruleDlg.kind + ".desc") }
        RowLayout {
            spacing: 12
            Label { text: Tr.t("rules.langLabel"); color: Theme.colorOnSurfaceVariant }
            ComboBox {
                id: ruleLangBox
                model: ruleDlg.langs.map(function (l) { return win.rulesLangName(l) })
                Material.containerStyle: Material.Outlined
                implicitWidth: 220
                Accessible.name: Tr.t("rules.langLabel")
                onActivated: ruleDlg.refreshPreview()
            }
        }
        TextField {
            id: noteField
            Layout.fillWidth: true
            Material.containerStyle: Material.Outlined
            placeholderText: Tr.t("rules.note")
            Accessible.name: Tr.t("rules.note")
        }
        Banner { visible: ruleDlg.error !== ""; tone: "error"; text: ruleDlg.error }
        // What this rule does to the text from "Try it"
        ColumnLayout {
            visible: ruleDlg.error === "" && patField.text !== ""
            Layout.fillWidth: true
            spacing: 4
            GroupLabel { text: Tr.t("rules.previewLabel"); Layout.topMargin: 0 }
            Label {
                visible: ruleDlg.preview.fired.length > 0
                Layout.fillWidth: true
                textFormat: Text.RichText
                wrapMode: Text.Wrap
                color: Theme.colorOnSurfaceVariant
                text: win.runsHtml(ruleDlg.preview.before, Theme.tertiaryContainer, Theme.colorOnTertiaryContainer)
            }
            Label {
                visible: ruleDlg.preview.fired.length > 0
                Layout.fillWidth: true
                textFormat: Text.RichText
                wrapMode: Text.Wrap
                color: Theme.colorOnSurface
                text: win.runsHtml(ruleDlg.preview.after, Theme.primaryContainer, Theme.colorOnPrimaryContainer)
            }
            Small { visible: ruleDlg.preview.fired.length === 0; Layout.fillWidth: true
                    text: Tr.t("rules.previewNone") }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.bottomMargin: 18
            spacing: 8
            TextButton {
                visible: ruleDlg.ruleId !== ""
                text: Tr.t("rules.delete"); glyph: ic.del; ink: Theme.error
                onClicked: { var id = ruleDlg.ruleId; ruleDlg.close(); win.removeRule(id) }
            }
            TextButton {
                visible: ruleDlg.presetChanged
                text: Tr.t("rules.reset"); glyph: win.ric.restore
                onClicked: {
                    if (win.rulesApply(backend.ruleReset(ruleDlg.ruleId), Tr.t("rules.resetDone")))
                        ruleDlg.close()
                }
            }
            Item { Layout.fillWidth: true }
            TextButton { text: Tr.t("common.cancel"); onClicked: ruleDlg.close() }
            FilledButton {
                text: ruleDlg.reviewMode ? Tr.t("rules.keep") : Tr.t("rules.save"); glyph: ic.check
                enabled: patField.text.trim() !== "" && ruleDlg.error === ""
                onClicked: {
                    var f = ruleDlg.fields()
                    if (ruleDlg.ruleId !== "") f.id = ruleDlg.ruleId
                    if (win.rulesApply(backend.ruleSave(f), Tr.t(ruleDlg.reviewMode ? "rules.kept" : "rules.saved")))
                        ruleDlg.close()
                }
            }
        }
    }

    DialogFrame {
        id: aiDlg
        objectName: "aiDlg"
        heading: Tr.t("ai.title")
        width: Math.min(720, win.width - 48)
        property string prompt: ""
        function openPrompt() { prompt = backend.agentPrompt(); open() }

        Body { text: Tr.t("ai.text") }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(360, win.height - 330)
            radius: 12
            color: Theme.surfaceContainer
            border.width: 1
            border.color: Theme.outlineVariant
            ScrollView {
                id: aiScroll
                anchors.fill: parent
                anchors.margins: 4
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                TextArea {
                    objectName: "aiPrompt"
                    text: aiDlg.prompt
                    textFormat: TextEdit.PlainText      // <command> must stay text
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    font.family: win.monoFont
                    font.pixelSize: 12
                    color: Theme.colorOnSurface
                    background: null
                    Accessible.name: Tr.t("ai.title")
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.bottomMargin: 18
            spacing: 8
            Item { Layout.fillWidth: true }
            TextButton { text: Tr.t("common.close"); onClicked: aiDlg.close() }
            FilledButton {
                text: Tr.t("ai.copy"); glyph: ic.copy
                onClicked: { backend.copyText(aiDlg.prompt); aiDlg.close(); snackbar(Tr.t("ai.copied")) }
            }
        }
    }

    // --- Snackbar (M3) ---
    Popup {
        id: snack
        objectName: "snack"
        property alias text: snackLabel.text
        property string actionText: ""
        property var action: null
        readonly property real left: win.onboardingOpen ? 24 : drawer.width + 40
        x: win.onboardingOpen ? (win.width - width) / 2 : left
        y: win.height - height - 92
        width: Math.min(600, win.width - left - 40)
        padding: 0
        closePolicy: Popup.NoAutoClose
        background: Rectangle { radius: 8; color: Theme.inverseSurface }
        contentItem: RowLayout {
            spacing: 0
            Label {
                id: snackLabel
                Layout.fillWidth: true
                padding: 14; leftPadding: 16
                color: Theme.inverseOnSurface; wrapMode: Text.Wrap
                Accessible.role: Accessible.AlertMessage
            }
            M3Button {
                visible: snack.actionText !== ""
                flat: true; text: snack.actionText; ink: Theme.primaryContainer
                onClicked: { var fn = snack.action; snack.close(); if (fn) fn() }
            }
            M3Button {
                flat: true; text: ""; glyph: ic.close; ink: Theme.inverseOnSurface
                leftPadding: 12; rightPadding: 12
                Accessible.name: Tr.t("common.close")
                onClicked: snack.close()
            }
        }
        enter: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 160 }
            NumberAnimation { property: "scale"; from: 0.92; to: 1; duration: 200
                              easing.type: Easing.OutCubic }
        }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 150 } }
        onOpened: snackTimer.restart()
        Timer { id: snackTimer; interval: 6000; onTriggered: snack.close() }
    }
}
