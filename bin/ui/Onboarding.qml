// First-run setup assistant: welcome (with the app language), reading
// languages, PC check with an engine suggestion (ElevenLabs set up inline),
// a voice per reading language, speed, shortcuts, optional own voice, done
// with the chosen shortcuts.
// Every step can be skipped; a skipped step leaves its settings untouched.
// Listen plays a cached preview (tts_previews) when there is one, else the
// daemon renders it live and keeps it for next time.
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

Rectangle {
    id: onb
    color: Theme.surface

    // current settings of the window (backend.initial())
    property var initial: ({})
    property bool firstRun: true

    signal finished(var result, bool closeWindow)
    signal skipped()
    signal createVoice(string engineKey)
    signal notify(string text)
    signal playPreview(string url)          // cached sample; "" stops the player

    property var info: null                 // backend.onboardingInfo(), arrives async
    property string engine: ""
    property var voices: ({})              // lang -> voice id
    property var readLangs: ["de", "en"]
    property bool langsTouched: false       // the user changed the languages step
    property string speedKey: "normal"
    property var applied: ({})
    property int step: 0
    property bool goingBack: false
    property var keys: ({})                 // action -> combo chosen here
    property var keyChecks: ({})            // action -> backend.hotkeyIssue() of a recorded combo

    // ElevenLabs, set up inline in the engine step
    readonly property var el: initial.el || ({ models: [], charsPerHour: 54000 })
    property string elState: ""             // "" | busy | ok | perms | error
    property string elError: ""
    property var elVoices: []
    property var elRates: ({})
    property var elPerms: ({})
    property var elSub: ({})
    property string elModel: ""
    property string elDefault: ""
    property string elKeyText: ""
    property bool elShowKey: false
    readonly property bool elReady: elState === "ok"
    readonly property string icShow: ""       // Material Icons "visibility"
    readonly property string icHide: ""       // "visibility_off"

    readonly property var steps: {
        var s = ["welcome", "langs", "engine", "voice", "speed"]
        if (mainHotkeys.length) s.push("hotkeys")
        if (engineInfo(engine).clone) s.push("clone")
        s.push("done")
        return s
    }
    readonly property string stepName: steps[Math.min(step, steps.length - 1)]
    readonly property var hotkeys: initial.hotkeys || []
    readonly property var mainHotkeys: info && info.mainHotkeys ? info.mainHotkeys : []
    readonly property string readCombo: hotkeys.length ? comboOf(hotkeys[0].action) : ""
    readonly property string fastCombo: hotkeys.length > 1 ? comboOf(hotkeys[1].action) : ""
    readonly property var keyIssues: {
        var out = {}
        for (var i = 0; i < mainHotkeys.length; i++) {
            var a = mainHotkeys[i].action, c = comboOf(a)
            out[a] = ""
            if (!c) continue
            for (var j = 0; j < hotkeys.length; j++) {
                var b = hotkeys[j].action
                if (b !== a && comboOf(b).toLowerCase() === c.toLowerCase()) { out[a] = "dup:" + b; break }
            }
            if (!out[a] && keys[a] !== undefined) out[a] = keyChecks[a] || ""
        }
        return out
    }
    readonly property bool keysBlocked: {
        for (var a in keyIssues) if (keyIssues[a]) return true
        return false
    }

    function start() {
        step = 0; goingBack = false; applied = {}; info = null; engine = ""
        keys = {}; keyChecks = {}
        elModel = el.model || "eleven_flash_v2_5"
        elVoices = el.voices || []; elRates = el.rates || {}; elPerms = el.perms || {}
        elSub = {}; elError = ""; elDefault = ""; elKeyText = ""; elShowKey = false
        elState = ""
        // first run: the app language plus English (set when the step opens,
        // so a language picked on the welcome page counts); later: what is saved
        readLangs = ((firstRun ? initial.systemLangs : initial.readLangs) || ["de", "en"]).slice()
        langsTouched = false
        var sp = initial.speedNormal || 1.0
        speedKey = sp >= 1.08 ? "relaxed" : sp <= 0.92 ? "brisk" : "normal"
        stack.replace(null, pageFor("welcome"), {}, StackView.Immediate)
        backend.detectHardware()
    }
    function engineInfo(key) {
        if (key === "elevenlabs") {
            var p = elPicks(), picks = {}
            for (var j = 0; j < readLangs.length; j++) picks[readLangs[j]] = p
            return { key: key, picks: picks, caps: [], langs: readLangs }
        }
        var list = info ? info.engines : []
        for (var i = 0; i < list.length; i++) if (list[i].key === key) return list[i]
        return { key: key, picks: {}, caps: [], langs: [] }
    }
    // --- Reading languages ---
    function speaks(key, lang) { return (engineInfo(key).langs || []).indexOf(lang) >= 0 }
    function spokenLangs(key) { return readLangs.filter(function (l) { return onb.speaks(key, l) }) }
    function coverageText(key) {
        var n = spokenLangs(key).length
        return n === readLangs.length ? "" : Tr.t("lang.coverage", { n: n, total: readLangs.length })
    }
    function langMeta(code) {
        var all = initial.langs || []
        for (var i = 0; i < all.length; i++) if (all[i].code === code) return all[i]
        return { code: code, piper: "", piperMb: 63, edge: "" }
    }
    function piperPicks(lang) { return engineInfo("piper").picks[lang] || [] }
    // Who reads a language the engine cannot speak (same order as the daemon)
    function langRoute(lang) {
        if (piperPicks(lang).length) return "piper"
        if (initial.edgeFallback && engine !== "piper") return "edge"
        return "none"
    }
    function routeVoice(lang, route) {
        if (route === "piper") return piperPicks(lang)[0].name
        if (route === "edge") {
            var id = langMeta(lang).edge, picks = engineInfo("edge").picks[lang] || []
            for (var i = 0; i < picks.length; i++) if (picks[i].id === id) return picks[i].name
            return id
        }
        return ""
    }
    function firstSpoken(key) { var l = spokenLangs(key); return l.length ? l[0] : readLangs[0] }

    // --- Shortcuts ---
    function initialCombo(action) {
        for (var i = 0; i < hotkeys.length; i++) if (hotkeys[i].action === action) return hotkeys[i].combo
        return ""
    }
    function comboOf(action) { return keys[action] !== undefined ? keys[action] : initialCombo(action) }
    function setKey(action, combo) {
        var k = Object.assign({}, keys), c = Object.assign({}, keyChecks)
        if (combo === initialCombo(action)) { delete k[action]; delete c[action] }
        else { k[action] = combo; c[action] = combo ? backend.hotkeyIssue(combo) : "" }
        keyChecks = c; keys = k
    }
    function keyIssueText(issue) {
        if (!issue) return ""
        if (issue.indexOf("dup:") === 0) return Tr.t("onb.keys.dup", { action: Tr.t("hotkey." + issue.slice(4)) })
        return Tr.t("onb.keys." + issue)
    }

    // --- ElevenLabs ---
    function elRate(id) {
        if (elRates[id] !== undefined) return elRates[id]
        for (var i = 0; i < (el.models || []).length; i++) if (el.models[i].id === id) return el.models[i].credits
        return 1
    }
    function elModelName(id) {
        for (var i = 0; i < (el.models || []).length; i++) if (el.models[i].id === id) return el.models[i].name
        return id
    }
    function elVoice(id) {
        for (var i = 0; i < elVoices.length; i++) if (elVoices[i].id === id) return elVoices[i]
        return null
    }
    // Voice cards for the voice step, in a stable order: the saved and the
    // default voice, then voices that are not being retired (up to 8). A voice
    // picked in the engine step that is not among them is added at the end.
    function elPicks() {
        var cur = el.current || {}
        var ids = readLangs.map(function (l) { return cur[l] }).concat([cur.de, cur.en, elDefault])
        var out = [], seen = {}
        var rest = elVoices.filter(function (v) { return !v.retiring }).concat(
                   elVoices.filter(function (v) { return v.retiring }))
        for (var i = 0; i < rest.length; i++) ids.push(rest[i].id)
        function add(id) {
            var v = elVoice(id)
            if (!v || seen[v.id]) return
            seen[v.id] = true
            var desc = [v.desc || "", v.retiring ? Tr.t("el.retiringShort") : ""]
                       .filter(function (x) { return x !== "" }).join(" · ")
            out.push({ id: v.id, name: v.name, desc: desc, gender: "", trait: "" })
        }
        for (var j = 0; j < ids.length && out.length < 8; j++) add(ids[j])
        for (var k = 0; k < readLangs.length; k++) add(voices[readLangs[k]])
        return out
    }
    function verifyEl(key) {
        if (!key) return
        elState = "busy"; elError = ""
        backend.verifyKey(key, true)
    }
    function takeKey(key) {                  // a key copied to the clipboard
        if (engine !== "elevenlabs") pickEngine("elevenlabs")
        elKeyText = key
        verifyEl(key)
    }
    function elSetVoice(id) {
        var v = Object.assign({}, voices)
        for (var i = 0; i < readLangs.length; i++) v[readLangs[i]] = id
        voices = v
    }
    // The saved voice of each reading language, else the first one, else d
    function elVoicesFor(cur, d) {
        var v = {}, first = ""
        for (var i = 0; i < readLangs.length; i++) if (!first && elVoice(cur[readLangs[i]])) first = cur[readLangs[i]]
        for (var j = 0; j < readLangs.length; j++) {
            var l = readLangs[j]
            v[l] = elVoice(cur[l]) ? cur[l] : (first || d)
        }
        return v
    }

    function pickEngine(key) {
        engine = key
        if (key === "elevenlabs") {
            voices = elVoicesFor(el.current || {}, elDefault)
            if (el.hasKey && elState === "") verifyEl(backend.storedKey())
            return
        }
        var e = engineInfo(key), cur = (initial.current || {})[key] || {}, v = {}
        for (var li = 0; li < readLangs.length; li++) {
            var lang = readLangs[li]
            var ids = (e.picks[lang] || []).map(function (p) { return p.id })
            v[lang] = ids.indexOf(cur[lang]) >= 0 ? cur[lang] : (ids[0] || "")
        }
        voices = v
    }
    function setVoice(lang, id) { var v = Object.assign({}, voices); v[lang] = id; voices = v }
    function useVoice(lang, id) { setVoice(lang, id); var a = Object.assign({}, applied); a.voice = true; applied = a }
    function toggleLang(lang) {
        var l = readLangs.slice(), i = l.indexOf(lang)
        if (i >= 0) { if (l.length > 1) l.splice(i, 1); else { notify(Tr.t("lang.keepOne")); return } }
        else l.push(lang)
        readLangs = l
        langsTouched = true
        if (engine !== "") pickEngine(engine)        // voices for the new set of languages
    }
    function speedValue(k) { return (info && info.speeds) ? info.speeds[k] : ({ relaxed: 1.15, normal: 1.0, brisk: 0.85 })[k] }

    function listen(eng, lang, voice, scale) {
        if (eng === "elevenlabs" && !elReady) { notify(Tr.t("test.elNoKey")); return }
        if (!voice) { notify(Tr.t("test.noVoice")); return }
        var req = { engine: eng, lang: lang, voice: voice, scale: scale || speedValue(speedKey) }
        if (eng === "elevenlabs") req.model = elModel
        var url = backend.previewUrl(req)
        if (url) { playPreview(url); return }
        playPreview("")
        var hint = backend.testInfo(req)
        if (!backend.test(req)) { notify(Tr.t("test.daemonStarting")); return }
        if (hint) notify(hint)
    }

    function go(delta, apply) {
        if (apply && stepName !== "welcome" && stepName !== "done") {
            var a = Object.assign({}, applied); a[stepName] = true; applied = a
        }
        goingBack = delta < 0
        step = Math.max(0, Math.min(steps.length - 1, step + delta))
        if (stepName === "langs" && firstRun && !langsTouched) {
            readLangs = backend.systemReadLangs(Tr.language)
            if (engine !== "") pickEngine(engine)
        }
        stack.replace(null, pageFor(stepName))
    }
    function finish(closeWindow) {
        var r = {}
        if (applied.engine || applied.voice) r.engine = engine
        if (applied.voice || (applied.engine && engine === "elevenlabs")) r.voices = voices
        if (applied.langs || r.voices) r.readLangs = readLangs
        if (r.engine === "elevenlabs") r.elModel = elModel
        if (applied.speed) r.speed = speedValue(speedKey)
        if (applied.hotkeys && Object.keys(keys).length) r.hotkeys = keys
        finished(r, closeWindow)
    }
    function pageFor(name) {
        return { welcome: welcomePage, langs: langsPage, engine: enginePage, voice: voicePage, speed: speedPage,
                 hotkeys: hotkeysPage, clone: clonePage, done: donePage }[name]
    }

    Connections {
        target: backend
        function onHardwareReady(r) {
            onb.info = r
            var cur = onb.initial.engine
            var keys = r.engines.map(function (e) { return e.key })
            if (!onb.firstRun && cur === "elevenlabs" && onb.el.hasKey) onb.pickEngine(cur)
            else onb.pickEngine(!onb.firstRun && keys.indexOf(cur) >= 0 ? cur : r.recommended)
        }
        function onElVerified(r) {
            onb.elVoices = r.voices; onb.elRates = r.rates; onb.elPerms = r.perms; onb.elSub = r.sub
            onb.elDefault = r.defaultVoice
            onb.elState = r.perms.tts && r.perms.voices ? "ok" : "perms"
            if (onb.engine === "elevenlabs") onb.voices = onb.elVoicesFor(onb.voices, r.defaultVoice)
        }
        // A Piper voice arrived: refresh the voice cards and use it with Piper
        function onPiperDone(lang, ok, voice) {
            if (!ok || !onb.info) return
            onb.info = backend.onboardingInfo(onb.info.hw)
            if (onb.engine === "piper") onb.setVoice(lang, voice)
        }
        function onElFailed(msg) { onb.elState = "error"; onb.elError = msg }
    }
    Keys.onEscapePressed: if (step > 0 && stepName !== "done") go(-1, false)

    // ================================================================= //
    //  Building blocks
    // ================================================================= //
    component StepPage: ScrollView {
        id: sp
        default property alias content: spCol.data
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            id: spCol
            width: Math.min(sp.availableWidth - 48, 680)
            x: (sp.availableWidth - width) / 2
            spacing: 16
            Item { Layout.preferredHeight: 8 }
        }
    }
    component StepTitle: ColumnLayout {
        property string title
        property string text
        Layout.fillWidth: true
        Layout.bottomMargin: 4
        spacing: 8
        Headline { text: parent.title; font.pixelSize: 30 }
        Body { text: parent.text; visible: text !== ""; font.pixelSize: 15 }
    }
    component ListenButton: M3Button {
        text: Tr.t("common.listen")
        glyph: Theme.ic.play
        outlined: true
        container: Theme.surface
    }
    component HwTile: Rectangle {
        id: tile
        property string glyph
        property string caption
        property string value
        property string detail
        property string help: ""
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        Layout.fillHeight: true
        implicitHeight: tileCol.implicitHeight + 32
        radius: 16
        color: Theme.surfaceContainerLow
        ColumnLayout {
            id: tileCol
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
            spacing: 4
            RowLayout {
                spacing: 8
                Icon { glyph: tile.glyph; color: Theme.primary; font.pixelSize: 20 }
                Small { text: tile.caption; font.pixelSize: 12; font.weight: Font.DemiBold; Layout.fillWidth: true }
                HelpTip { visible: tile.help !== ""; text: tile.help }
            }
            Label { text: tile.value; color: Theme.colorOnSurface; font.pixelSize: 15; font.weight: Font.DemiBold
                    wrapMode: Text.Wrap; Layout.fillWidth: true }
            Small { text: tile.detail; visible: tile.detail !== ""; Layout.fillWidth: true }
        }
    }

    // ================================================================= //
    //  Frame: header with back, progress and skip; pages; footer
    // ================================================================= //
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            TextButton {
                anchors { left: parent.left; leftMargin: 16; verticalCenter: parent.verticalCenter }
                text: Tr.t("common.back"); glyph: Theme.ic.back
                opacity: onb.step > 0 && onb.stepName !== "done" ? 1 : 0
                enabled: opacity > 0
                onClicked: onb.go(-1, false)
            }
            Row {
                anchors.centerIn: parent
                spacing: 6
                Accessible.role: Accessible.ProgressBar
                Accessible.name: Tr.t("onb.progress", { n: onb.step + 1, total: onb.steps.length })
                Repeater {
                    model: onb.steps.length
                    Rectangle {
                        required property int index
                        width: index === onb.step ? 28 : 8
                        height: 8; radius: 4
                        color: index <= onb.step ? Theme.primary : Theme.outlineVariant
                        Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                        Behavior on color { ColorAnimation { duration: 220 } }
                    }
                }
            }
            TextButton {
                anchors { right: parent.right; rightMargin: 16; verticalCenter: parent.verticalCenter }
                text: Tr.t("onb.skipAll")
                opacity: onb.stepName !== "done" ? 1 : 0
                enabled: opacity > 0
                onClicked: onb.skipped()
            }
        }

        StackView {
            id: stack
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            replaceEnter: Transition {
                ParallelAnimation {
                    NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 220 }
                    NumberAnimation { property: "x"; from: onb.goingBack ? -48 : 48; to: 0
                                      duration: 280; easing.type: Easing.OutCubic }
                }
            }
            replaceExit: Transition {
                NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 120 }
            }
        }

        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.outlineVariant
            visible: footer.visible
        }
        RowLayout {
            id: footer
            visible: onb.stepName !== "welcome" && onb.stepName !== "done"
            Layout.fillWidth: true
            Layout.margins: 16
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            spacing: 8
            TextButton { text: Tr.t("onb.skipStep"); onClicked: onb.go(1, false) }
            Item { Layout.fillWidth: true }
            FilledButton {
                id: nextBtn
                text: onb.stepName === "clone" ? Tr.t("onb.clone.later") : Tr.t("common.continue")
                glyph: Theme.ic.forward
                enabled: onb.stepName === "engine" ? onb.info !== null && (onb.engine !== "elevenlabs" || onb.elReady)
                         : onb.stepName === "hotkeys" ? !onb.keysBlocked : true
                onClicked: onb.go(1, onb.stepName !== "clone")
            }
        }
    }

    // ================================================================= //
    //  Steps
    // ================================================================= //
    Component {
        id: welcomePage
        StepPage {
            // the read shortcut can be tried right here (see WelcomeDemo)
            Keys.onPressed: (e) => { if (demo.keyEvent(e, true)) e.accepted = true }
            Keys.onReleased: (e) => { if (demo.keyEvent(e, false)) e.accepted = true }
            Item { Layout.preferredHeight: 8 }
            // smaller on short windows, so the start button stays in view
            Mascot { Layout.alignment: Qt.AlignHCenter; size: onb.height < 720 ? 92 : 128; excited: demo.playing }
            Item { Layout.preferredHeight: onb.height < 720 ? 0 : 8 }
            Headline {
                text: Tr.t("onb.welcome.title"); font.pixelSize: 34
                horizontalAlignment: Text.AlignHCenter
            }
            Body {
                text: Tr.t("onb.welcome.text"); font.pixelSize: 16
                horizontalAlignment: Text.AlignHCenter
                Layout.maximumWidth: 520; Layout.alignment: Qt.AlignHCenter
            }
            WelcomeDemo {
                id: demo
                combo: onb.readCombo
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: 8
            }
            Item { Layout.preferredHeight: 4 }
            FilledButton {
                text: Tr.t("onb.welcome.start"); glyph: Theme.ic.forward
                Layout.alignment: Qt.AlignHCenter
                Component.onCompleted: forceActiveFocus()
                onClicked: onb.go(1, false)
            }
            Small {
                text: Tr.t("onb.welcome.time")
                Layout.alignment: Qt.AlignHCenter
            }
            Item { Layout.preferredHeight: 8 }
            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 8
                Icon { glyph: Theme.ic.language; font.pixelSize: 18 }
                Label { text: Tr.t("settings.language"); color: Theme.colorOnSurfaceVariant }
                LanguageBox { }
            }
        }
    }

    Component {
        id: langsPage
        StepPage {
            StepTitle { title: Tr.t("onb.langs.title"); text: Tr.t("onb.langs.text") }
            Flow {
                objectName: "langChips"
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: onb.initial.langs || []
                    LangChip {
                        required property var modelData
                        lang: modelData.code
                        on: onb.readLangs.indexOf(modelData.code) >= 0
                        onToggled: onb.toggleLang(modelData.code)
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 8
                Icon { glyph: Theme.ic.info; font.pixelSize: 18; Layout.alignment: Qt.AlignTop }
                Small { text: Tr.t("onb.langs.hint"); Layout.fillWidth: true; font.pixelSize: 13 }
            }
            Item { Layout.preferredHeight: 12 }
        }
    }

    Component {
        id: enginePage
        StepPage {
            StepTitle { title: Tr.t("onb.engine.title"); text: Tr.t("onb.engine.text") }

            Banner { visible: onb.info === null; tone: "busy"; text: Tr.t("onb.engine.checking") }

            RowLayout {
                visible: onb.info !== null
                Layout.fillWidth: true
                spacing: 12
                readonly property var hw: onb.info ? onb.info.hw : ({ gpu: {} })
                HwTile {
                    glyph: Theme.ic.computer; caption: Tr.t("onb.hw.system")
                    value: parent.hw.os || ""
                    detail: parent.hw.ramGb ? Tr.t("onb.hw.ram", { gb: parent.hw.ramGb }) : ""
                }
                HwTile {
                    glyph: Theme.ic.board; caption: Tr.t("onb.hw.graphics")
                    help: Tr.t("onb.hw.vramHelp")
                    value: parent.hw.gpu.nvidia ? parent.hw.gpu.name || "NVIDIA"
                           : Tr.t("onb.hw.noNvidia")
                    detail: parent.hw.gpu.nvidia
                            ? Tr.t("onb.hw.vram", { gb: Tr.num(parent.hw.gpu.totalMb / 1024, 0) })
                            : (parent.hw.gpu.name ? parent.hw.gpu.name + ". " : "") + Tr.t("onb.hw.cpuPath")
                }
                HwTile {
                    glyph: Theme.ic.memory; caption: Tr.t("onb.hw.processor")
                    value: parent.hw.cores ? Tr.t("onb.hw.cores", { n: parent.hw.cores }) : ""
                    detail: parent.hw.cpu || ""
                }
            }

            GroupLabel { visible: onb.info !== null; text: Tr.t("onb.engine.suggestion") }
            Repeater {
                model: onb.info ? onb.info.engines : []
                ColumnLayout {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    spacing: 16
                    GroupLabel { visible: index === 1; text: Tr.t("onb.engine.others") }
                    ChoiceCard {
                        glyph: Theme.engineGlyph(modelData.key)
                        title: Tr.engineTitle(modelData.key)
                        supporting: Tr.engineBlurb(modelData.key)
                                    + (modelData.vramMb ? " " + Tr.t("onb.engine.usesVram",
                                       { gb: Tr.num(modelData.vramMb / 1024, 1) }) : "")
                        badge: modelData.key === onb.info.recommended ? Tr.t("onb.engine.recommended") : ""
                        caps: modelData.caps || []
                        note: onb.coverageText(modelData.key)
                        selected: onb.engine === modelData.key
                        onPicked: onb.pickEngine(modelData.key)
                        ListenButton {
                            onClicked: {
                                var lang = onb.firstSpoken(modelData.key)
                                var p = modelData.picks[lang] || []
                                var v = onb.engine === modelData.key ? onb.voices[lang] : (p.length ? p[0].id : "")
                                onb.listen(modelData.key, lang, v, 1.0)
                            }
                        }
                    }
                }
            }

            // ---- Paid online voices: ElevenLabs, configured right here ----
            GroupLabel { visible: onb.info !== null; text: Tr.t("onb.engine.premium") }
            ChoiceCard {
                visible: onb.info !== null
                glyph: Theme.engineGlyph("elevenlabs")
                title: Tr.engineTitle("elevenlabs")
                supporting: Tr.engineBlurb("elevenlabs")
                selected: onb.engine === "elevenlabs"
                onPicked: onb.pickEngine("elevenlabs")
            }
            Rectangle {
                id: elPanel
                visible: onb.info !== null && onb.engine === "elevenlabs"
                Layout.fillWidth: true
                implicitHeight: elCol.implicitHeight + 40
                radius: 16
                color: Theme.surfaceContainerLow
                border.width: 1
                border.color: Theme.outlineVariant
                ColumnLayout {
                    id: elCol
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
                    spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        Icon { glyph: Theme.ic.savings; color: Theme.primary; font.pixelSize: 20
                               Layout.alignment: Qt.AlignTop }
                        Body {
                            Layout.fillWidth: true
                            text: Tr.t("onb.el.cost", {
                                minutes: Tr.num(10000 / (onb.el.charsPerHour / 60 * onb.elRate(onb.elModel)), 0),
                                model: onb.elModelName(onb.elModel) })
                        }
                    }
                    Divider { }
                    Label { text: Tr.t("onb.el.keyTitle"); color: Theme.colorOnSurface; font.pixelSize: 15
                            font.weight: Font.DemiBold }
                    Small { text: Tr.t("onb.el.keyText"); Layout.fillWidth: true; font.pixelSize: 13 }
                    OutlinedButton {
                        text: Tr.t("onb.el.getKey"); glyph: Theme.ic.open
                        onClicked: backend.openKeyPage()
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        TextField {
                            id: elKeyField
                            Layout.fillWidth: true
                            Material.containerStyle: Material.Outlined
                            placeholderText: Tr.t("onb.el.paste")
                            echoMode: onb.elShowKey ? TextInput.Normal : TextInput.Password
                            text: onb.elKeyText
                            onTextEdited: onb.elKeyText = text
                            Accessible.name: Tr.t("el.keyField")
                            onAccepted: onb.verifyEl(text.trim())
                        }
                        M3Button {
                            flat: true; text: ""
                            glyph: onb.elShowKey ? onb.icHide : onb.icShow
                            leftPadding: 12; rightPadding: 12
                            Accessible.name: onb.elShowKey ? Tr.t("onb.el.hide") : Tr.t("onb.el.show")
                            ToolTip.visible: hovered; ToolTip.text: Accessible.name
                            onClicked: onb.elShowKey = !onb.elShowKey
                        }
                        TonalButton {
                            text: Tr.t("onb.el.check"); glyph: Theme.ic.check
                            enabled: onb.elKeyText.trim() !== "" && onb.elState !== "busy"
                            onClicked: onb.verifyEl(onb.elKeyText.trim())
                        }
                    }
                    Small { text: Tr.t("onb.el.permHint"); Layout.fillWidth: true }
                    Banner {
                        visible: onb.elState !== "" || onb.el.hasKey
                        tone: onb.elState === "busy" ? "busy" : onb.elState === "ok" ? "ok"
                              : onb.elState === "" ? "info" : "error"
                        text: {
                            if (onb.elState === "busy") return Tr.t("el.checking")
                            if (onb.elState === "error") return onb.elError
                            if (onb.elState === "perms") return onb.elPerms.tts ? Tr.t("el.noVoices") : Tr.t("el.noTts")
                            if (onb.elState === "ok") {
                                if (onb.elSub && onb.elSub.limit) {
                                    var left = Math.max(0, onb.elSub.limit - onb.elSub.used)
                                    return Tr.t("el.connectedBalance", {
                                        tier: onb.elSub.tier, left: Tr.num(left, 0), limit: Tr.num(onb.elSub.limit, 0),
                                        hours: Tr.num(left / (onb.el.charsPerHour * onb.elRate(onb.elModel)), 1),
                                        model: onb.elModelName(onb.elModel) })
                                }
                                return Tr.t("el.connectedNoBalance")
                            }
                            return Tr.t("onb.el.saved")
                        }
                    }

                    // Model and voice, once the key works
                    ColumnLayout {
                        visible: onb.elReady
                        Layout.fillWidth: true
                        spacing: 8
                        Divider { Layout.topMargin: 4; Layout.bottomMargin: 4 }
                        Label { text: Tr.t("onb.el.choose"); color: Theme.colorOnSurface; font.pixelSize: 15
                                font.weight: Font.DemiBold }
                        ButtonGroup { id: elModelGroup }
                        Repeater {
                            model: onb.el.models || []
                            RowLayout {
                                id: mrow
                                required property var modelData
                                readonly property bool available: Object.keys(onb.elRates).length === 0
                                                                  || onb.elRates[modelData.id] !== undefined
                                Layout.fillWidth: true
                                spacing: 8
                                enabled: available
                                RadioButton {
                                    ButtonGroup.group: elModelGroup
                                    checked: onb.elModel === mrow.modelData.id
                                    onClicked: onb.elModel = mrow.modelData.id
                                    padding: 0
                                    Layout.alignment: Qt.AlignTop
                                    Accessible.name: mrow.modelData.name
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    Layout.topMargin: 2
                                    RowLayout {
                                        Layout.fillWidth: true; spacing: 8
                                        Label { text: mrow.modelData.name; font.pixelSize: 14; font.weight: Font.DemiBold
                                                color: Theme.colorOnSurface }
                                        Chip { text: onb.elRate(mrow.modelData.id) <= 0.5 ? Tr.t("el.halfPrice") : Tr.t("el.fullPrice")
                                               accent: onb.elRate(mrow.modelData.id) > 0.5 }
                                        Item { Layout.fillWidth: true }
                                        Small { text: mrow.available
                                                      ? Tr.t("el.perHour", { credits: Tr.num(onb.el.charsPerHour * onb.elRate(mrow.modelData.id), 0) })
                                                      : Tr.t("el.notAvailable") }
                                    }
                                    Small { text: Tr.t("el.model." + mrow.modelData.id, undefined, mrow.modelData.blurb)
                                            Layout.fillWidth: true }
                                    TapHandler { onTapped: if (mrow.available) onb.elModel = mrow.modelData.id }
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.topMargin: 8
                            spacing: 12
                            Label { text: Tr.t("onb.el.voiceAll"); color: Theme.colorOnSurfaceVariant }
                            ComboBox {
                                id: elVoiceBox
                                Layout.fillWidth: true
                                Material.containerStyle: Material.Outlined
                                textRole: "name"; valueRole: "id"
                                model: onb.elVoices
                                currentIndex: Math.max(0, indexOfValue(onb.voices[onb.readLangs[0]]))
                                onActivated: onb.elSetVoice(currentValue)
                                Accessible.name: Tr.t("onb.el.voiceAll")
                            }
                            ListenButton {
                                onClicked: onb.listen("elevenlabs", onb.readLangs[0], onb.voices[onb.readLangs[0]], 1.0)
                            }
                        }
                    }
                    Small {
                        visible: !onb.elReady
                        text: Tr.t("onb.el.needKey"); Layout.fillWidth: true
                        color: Theme.colorOnSurfaceVariant; font.pixelSize: 13
                    }
                }
            }

            GroupLabel {
                visible: onb.info !== null && onb.info.install.length > 0
                text: Tr.t("onb.install.title")
            }
            Body {
                visible: onb.info !== null && onb.info.install.length > 0
                text: Tr.t("onb.install.text")
            }
            Repeater {
                model: onb.info ? onb.info.install : []
                ChoiceCard {
                    required property var modelData
                    selectable: false
                    glyph: modelData.fits ? Theme.ic.download : Theme.ic.lock
                    opacity: modelData.fits ? 1 : 0.7
                    title: modelData.title
                    badge: modelData.key === "kokoro2" && modelData.fits ? Tr.t("onb.engine.recommended") : ""
                    supporting: {
                        var s = modelData.cpuOk ? Tr.t("onb.install.anyPc")
                                : Tr.t("onb.install.needsNvidia", { gb: Tr.num(modelData.vramMb / 1024, 1) })
                        if (modelData.diskMb) s += " " + Tr.t("onb.install.download", { gb: Tr.num(modelData.diskMb / 1024, 1) })
                        if (!modelData.fits) s += " " + Tr.t("onb.install.notHere")
                        else if (!modelData.command) s += " " + Tr.t("onb.install.noScript")
                        return s
                    }
                    caps: modelData.clone ? ["clone"] : []
                    OutlinedButton {
                        visible: modelData.fits && modelData.command !== ""
                        text: Tr.t("onb.install.copy"); glyph: Theme.ic.copy
                        onClicked: { backend.copyText(modelData.command); onb.notify(Tr.t("onb.install.copied")) }
                    }
                }
            }
            Item { Layout.preferredHeight: 12 }
        }
    }

    Component {
        id: voicePage
        StepPage {
            StepTitle { title: Tr.t("onb.voice.title"); text: Tr.t("onb.voice.text") }
            Repeater {
                model: onb.readLangs
                ColumnLayout {
                    id: langCol
                    required property string modelData
                    readonly property var picks: onb.engineInfo(onb.engine).picks[modelData] || []
                    readonly property bool spoken: onb.speaks(onb.engine, modelData)
                    // Piper without a voice for this language: offer the download
                    readonly property bool piperGap: onb.engine === "piper" && picks.length === 0
                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    spacing: 10
                    GroupLabel { text: Tr.t("onb.voice.for", { lang: Tr.langName(langCol.modelData) }) }
                    LangGap {
                        visible: !langCol.spoken || langCol.piperGap
                        lang: langCol.modelData
                        labelWidth: 0
                        engineTitle: langCol.piperGap ? "" : Tr.engineTitle(onb.engine)
                        route: langCol.piperGap ? "none" : onb.langRoute(langCol.modelData)
                        byVoice: onb.routeVoice(langCol.modelData, route)
                        piperVoice: onb.langMeta(langCol.modelData).piper
                        piperMb: onb.langMeta(langCol.modelData).piperMb
                    }
                    Body { visible: langCol.spoken && !langCol.piperGap && langCol.picks.length === 0
                           text: Tr.t("onb.voice.auto") }
                    GridLayout {
                        visible: langCol.spoken
                        Layout.fillWidth: true
                        columns: width > 560 ? 2 : 1
                        rowSpacing: 10; columnSpacing: 10
                        Repeater {
                            model: langCol.picks
                            ChoiceCard {
                                required property var modelData
                                Layout.preferredWidth: 1
                                avatar: modelData.name.charAt(0).toUpperCase()
                                avatarColor: Theme.avatarColor(modelData.name)
                                title: modelData.name
                                supporting: modelData.desc ? modelData.desc
                                            : [modelData.trait ? Tr.t("trait." + modelData.trait) : "",
                                               modelData.gender ? Tr.t("gender." + modelData.gender) : ""]
                                              .filter(function (x) { return x !== "" }).join(" · ")
                                selected: onb.voices[langCol.modelData] === modelData.id
                                onPicked: onb.setVoice(langCol.modelData, modelData.id)
                                M3Button {
                                    flat: true; glyph: Theme.ic.play; text: ""
                                    leftPadding: 12; rightPadding: 12
                                    Accessible.name: Tr.t("onb.voice.listenTo", { name: modelData.name })
                                    ToolTip.visible: hovered; ToolTip.text: Tr.t("common.listen")
                                    onClicked: onb.listen(onb.engine, langCol.modelData, modelData.id)
                                }
                            }
                        }
                    }
                }
            }
            Item { Layout.preferredHeight: 12 }
        }
    }

    Component {
        id: speedPage
        StepPage {
            StepTitle { title: Tr.t("onb.speed.title"); text: Tr.t("onb.speed.text") }
            Repeater {
                model: [{ k: "relaxed", g: Theme.ic.schedule }, { k: "normal", g: Theme.ic.headphones },
                        { k: "brisk", g: Theme.ic.speed }]
                ChoiceCard {
                    required property var modelData
                    glyph: modelData.g
                    title: Tr.t("onb.speed." + modelData.k)
                    supporting: Tr.t("onb.speed." + modelData.k + ".text")
                    selected: onb.speedKey === modelData.k
                    onPicked: onb.speedKey = modelData.k
                    M3Button {
                        flat: true; glyph: Theme.ic.play; text: ""
                        leftPadding: 12; rightPadding: 12
                        Accessible.name: Tr.t("common.listen")
                        ToolTip.visible: hovered; ToolTip.text: Tr.t("common.listen")
                        onClicked: {
                            onb.speedKey = modelData.k
                            var lang = onb.firstSpoken(onb.engine)
                            onb.listen(onb.engine, lang, onb.voices[lang], onb.speedValue(modelData.k))
                        }
                    }
                }
            }
            RowLayout {
                visible: onb.fastCombo !== ""
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 10
                Icon { glyph: Theme.ic.bolt; color: Theme.tertiary; font.pixelSize: 18 }
                Small { text: Tr.t("onb.speed.fast"); font.pixelSize: 13 }
                Keycaps { combo: onb.fastCombo }
                Item { Layout.fillWidth: true }
            }
        }
    }

    Component {
        id: hotkeysPage
        StepPage {
            StepTitle { title: Tr.t("onb.keys.title"); text: Tr.t("onb.keys.text") }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: keyRows.implicitHeight + 16
                radius: 16
                color: Theme.surfaceContainerLow
                ColumnLayout {
                    id: keyRows
                    anchors { left: parent.left; right: parent.right; top: parent.top
                              topMargin: 8; leftMargin: 20; rightMargin: 16 }
                    spacing: 0
                    Repeater {
                        model: onb.mainHotkeys
                        ColumnLayout {
                            id: krow
                            required property var modelData
                            required property int index
                            readonly property string issue: onb.keyIssues[modelData.action] || ""
                            Layout.fillWidth: true
                            spacing: 0
                            Divider { visible: krow.index > 0 }
                            RowLayout {
                                Layout.fillWidth: true
                                Layout.topMargin: 10; Layout.bottomMargin: 10
                                spacing: 14
                                Icon {
                                    glyph: ({ read: Theme.ic.headphones, fast: Theme.ic.bolt, pause: Theme.ic.pause,
                                              stop: Theme.ic.stop, ocr: Theme.ic.desktop })[krow.modelData.role]
                                           || Theme.ic.keyboard
                                    color: krow.issue ? Theme.error : Theme.primary
                                    font.pixelSize: 22
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Label { text: Tr.t("hotkey." + krow.modelData.action); color: Theme.colorOnSurface
                                            font.pixelSize: 15; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                    Small { visible: krow.issue !== ""; text: onb.keyIssueText(krow.issue)
                                            color: Theme.error; Layout.fillWidth: true }
                                }
                                HotkeyField {
                                    Layout.preferredWidth: 250; Layout.preferredHeight: 48
                                    combo: onb.comboOf(krow.modelData.action)
                                    label: Tr.t("hotkey." + krow.modelData.action)
                                    invalid: krow.issue !== ""
                                    onRecorded: (c) => onb.setKey(krow.modelData.action, c)
                                }
                            }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Icon { glyph: Theme.ic.info; font.pixelSize: 18; Layout.alignment: Qt.AlignTop }
                Small { text: Tr.t("onb.keys.hint"); Layout.fillWidth: true; font.pixelSize: 13 }
                TextButton {
                    visible: Object.keys(onb.keys).length > 0
                    text: Tr.t("onb.keys.reset"); glyph: Theme.ic.replay
                    onClicked: { onb.keys = {}; onb.keyChecks = {} }
                }
            }
            Banner { visible: onb.keysBlocked; tone: "error"; text: Tr.t("onb.keys.fixFirst") }
            Item { Layout.preferredHeight: 12 }
        }
    }

    Component {
        id: clonePage
        StepPage {
            Item { Layout.preferredHeight: 8 }
            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                implicitWidth: 112; implicitHeight: 112; radius: 56
                color: Theme.tertiaryContainer
                Icon { anchors.centerIn: parent; glyph: Theme.ic.mic; font.pixelSize: 52
                       color: Theme.colorOnTertiaryContainer }
            }
            Headline { text: Tr.t("onb.clone.title"); font.pixelSize: 30; horizontalAlignment: Text.AlignHCenter }
            Body {
                text: Tr.t("onb.clone.text", { engine: Tr.engineTitle(onb.engine) })
                font.pixelSize: 15; horizontalAlignment: Text.AlignHCenter
                Layout.maximumWidth: 540; Layout.alignment: Qt.AlignHCenter
            }
            Small {
                text: Tr.t("onb.clone.consent")
                horizontalAlignment: Text.AlignHCenter; font.pixelSize: 13
                Layout.maximumWidth: 540; Layout.alignment: Qt.AlignHCenter; Layout.fillWidth: true
            }
            FilledButton {
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: 8
                text: Tr.t("onb.clone.create"); glyph: Theme.ic.add
                onClicked: onb.createVoice(onb.engine)
            }
        }
    }

    Component {
        id: donePage
        StepPage {
            Item { Layout.preferredHeight: 12 }
            Rectangle {
                id: badge
                Layout.alignment: Qt.AlignHCenter
                implicitWidth: 112; implicitHeight: 112; radius: 56
                color: Theme.okContainer
                Icon { anchors.centerIn: parent; glyph: Theme.ic.taskAlt; font.pixelSize: 60; color: Theme.ok }
                scale: 1
                SequentialAnimation on scale {
                    running: true
                    NumberAnimation { from: 0.4; to: 1.0; duration: 420; easing.type: Easing.OutBack }
                }
            }
            Headline { text: Tr.t("onb.done.title"); font.pixelSize: 34; horizontalAlignment: Text.AlignHCenter }
            Body {
                text: onb.readCombo ? Tr.t("onb.done.text") : Tr.t("onb.done.textNoKey")
                font.pixelSize: 15; horizontalAlignment: Text.AlignHCenter
                Layout.maximumWidth: 520; Layout.alignment: Qt.AlignHCenter
            }
            Keycaps { combo: onb.readCombo; large: true; Layout.alignment: Qt.AlignHCenter
                      visible: onb.readCombo !== "" }
            Rectangle {
                Layout.fillWidth: true
                Layout.topMargin: 8
                radius: 20
                color: Theme.surfaceContainer
                implicitHeight: keysCol.implicitHeight + 28
                visible: onb.hotkeys.length > 1
                ColumnLayout {
                    id: keysCol
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                              leftMargin: 20; rightMargin: 20 }
                    spacing: 10
                    // the shortcuts as chosen in the shortcuts step
                    Repeater {
                        model: onb.hotkeys.slice(1).map(function (h) { return { action: h.action, combo: onb.comboOf(h.action) } })
                                          .filter(function (h) { return h.combo !== "" })
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 12
                            Label { text: Tr.t("hotkey." + modelData.action); color: Theme.colorOnSurface
                                    wrapMode: Text.Wrap; Layout.fillWidth: modelData.action !== "reader" }
                            Chip { visible: modelData.action === "reader"; text: Tr.t("beta.tag"); tip: Tr.t("beta.reader") }
                            Item { visible: modelData.action === "reader"; Layout.fillWidth: true }
                            Keycaps { combo: modelData.combo }
                        }
                    }
                }
            }
            Small {
                text: Tr.t("onb.done.change")
                Layout.alignment: Qt.AlignHCenter; horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true; font.pixelSize: 13
            }
            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: 8
                spacing: 12
                TonalButton { text: Tr.t("onb.done.settings"); glyph: Theme.ic.tune
                              onClicked: onb.finish(false) }
                FilledButton {
                    text: Tr.t("onb.done.start"); glyph: Theme.ic.check
                    Component.onCompleted: forceActiveFocus()
                    onClicked: onb.finish(true)
                }
            }
            Item { Layout.preferredHeight: 16 }
        }
    }
}
