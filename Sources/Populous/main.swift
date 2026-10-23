import AppKit
import MetalKit
import PopulousAssets
import PopulousAudio
import PopulousCore
import PopulousHD
import PopulousRender

/// Shows the game screen and lets you look around a generated world.
/// Left click raises land and right click lowers it where your people are in view;
/// the board's arrows, behaviour, power and magnet icons work; clicking the minimap jumps
/// there. Arrow keys also scroll, [ and ] change world, M and S switch the music
/// and the sound effects on and off.
final class GameView: NSView, MTKViewDelegate {
    let assets: GameAssets
    let renderer: Renderer
    /// Puts frames on the screen with Metal, in classic or HD graphics.
    let screenRenderer: ScreenRenderer
    /// HD graphics (a work in progress) instead of the original's pictures. Tab
    /// switches between them at any moment; the game itself is the same either way.
    var hdGraphics = UserDefaults.standard.bool(forKey: "hdGraphics") {
        didSet {
            UserDefaults.standard.set(hdGraphics, forKey: "hdGraphics")
            needsDisplay = true
        }
    }
    let startTime = Date()
    /// The Metal surface filling the view; this view handles the input.
    let metalView: MetalSurface

    /// Every change that asks for a redraw also redraws the Metal surface. (This
    /// view paints nothing itself, so AppKit drops the flag; pass it on as set.)
    override var needsDisplay: Bool {
        get { super.needsDisplay }
        set {
            super.needsDisplay = newValue
            if newValue { metalView.needsDisplay = true }
        }
    }
    var worldNumber = 0
    var world: World
    /// Nil if the Mac's audio could not be started; the game then runs silent.
    let audio: AudioOutput?
    /// Ticks to wait before the fire crackle may sound again.
    var fireQuiet = 0
    var viewX = 0
    var viewY = 0

    init(assets: GameAssets, screenRenderer: ScreenRenderer) {
        self.assets = assets
        self.screenRenderer = screenRenderer
        renderer = Renderer(assets: assets)
        world = World(random: GameRandom(seed: 0))
        // POPULOUS_SILENT=1 runs without opening the audio output.
        audio = ProcessInfo.processInfo.environment["POPULOUS_SILENT"] == nil ? AudioOutput(music: assets.music, speech: assets.speech) : nil
        metalView = MetalSurface(frame: NSRect(x: 0, y: 0, width: 960, height: 600), device: screenRenderer.device)
        super.init(frame: NSRect(x: 0, y: 0, width: 960, height: 600))
        // Frames are drawn when something changes, not on a timer of the view's own.
        metalView.colorPixelFormat = ScreenRenderer.pixelFormat
        metalView.colorspace = CGColorSpace(name: CGColorSpace.sRGB)
        metalView.enableSetNeedsDisplay = true
        metalView.isPaused = true
        metalView.delegate = self
        metalView.autoresizingMask = [.width, .height]
        addSubview(metalView)
        loadWorld(0)
        // The title waits a few seconds, then runs the demo until a click or key.
        Timer.scheduledTimer(withTimeInterval: TitleDemo.stepSeconds, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                if case .title = self.overlay {
                    self.titleIdle += TitleDemo.stepSeconds
                    if self.titleIdle >= Self.titleWait { self.startDemo() }
                } else if case .demo = self.overlay {
                    self.demoScreen = self.demo?.step()
                    self.needsDisplay = true
                }
            }
        }
        // The lord's speech runs at its own pace: one pass every four 50 Hz frames.
        Timer.scheduledTimer(withTimeInterval: LordSpeech.stepSeconds, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, case .lord(var speech, let lines, let next) = self.overlay else { return }
                if let audio = self.audio {
                    audio.say { speech.step(idle: $0) }
                } else {
                    _ = speech.step(idle: true)
                }
                self.overlay = .lord(speech, lines: lines, next: next)
                self.needsDisplay = true
            }
        }
        // The original's frame rate is not known yet; 8 ticks a second looks about right.
        Timer.scheduledTimer(withTimeInterval: 0.125, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                if case .title = self.overlay { return }
                if case .demo = self.overlay { return }
                if self.overlay != nil, self.world.defeatedSide == nil {
                    // A dialog box is up: the game waits, only the cursor blinks.
                    self.blink += 1
                    self.window?.title = "Populous"
                    self.needsDisplay = true
                    return
                }
                if let loser = self.world.defeatedSide {
                    // The score box, as in the original; the lord's speech that follows
                    // a win there is summed up in the title bar instead.
                    let won = loser == 1
                    if self.overlay == nil {
                        self.overlay = .score(self.world.scoreSummary(won: won), won: won)
                        self.overlayCanClose = false
                        self.needsDisplay = true
                    }
                    self.blink += 1
                    if case .score = self.overlay {} else {
                        self.window?.title = "Populous"
                        self.needsDisplay = true
                        return
                    }
                    let score = self.world.finalScore(won: won)
                    if !won {
                        self.worldAfterThis = self.worldNumber
                        self.window?.title = "Populous - \(self.worldName) is lost - score \(score)"
                    } else if let next = Conquest.world(after: self.worldNumber, score: score) {
                        self.worldAfterThis = next
                        self.window?.title =
                            "Populous - well done, you conquered \(self.worldName) - score \(score) - now battle at \(self.names.name(ofWorld: next))"
                    } else {
                        self.worldAfterThis = 0
                        self.window?.title = "Populous - well done, you have conquered evil - the battle is over"
                    }
                    return
                }
                if self.paused {
                    self.window?.title = "Populous - \(self.worldName) - paused (click zzZ or press P to go on)"
                    return
                }
                self.world.tick(rules: self.rules)
                // The original's shield drawing lets go of a person who has died.
                let followed = self.world.selectedPerson
                if followed != 0, self.world.people[followed - 1].life <= 0 { self.world.selectedPerson = 0 }
                self.world.processOrders()
                self.updateSound()
                if self.noticeTicks > 0 {
                    self.noticeTicks -= 1
                    self.window?.title = "Populous - " + self.notice
                } else if let path = self.world.unportedPath {
                    self.window?.title = "Populous - stopped: \(path) is not ported yet"
                } else {
                    let you = self.world.players[0]
                    let them = self.world.players[1]
                    self.window?.title =
                        "Populous - \(self.worldName)\(self.conquest ? " (battle \(self.worldNumber / 5))" : "") - mana \(you.mana) - people \(you.population) vs \(them.population) - "
                        + (self.placingMagnet ? "place magnet" : self.placingSwamp ? "place swamp" : Self.modeNames[Int(you.mode) & 3])
                        + (self.inspecting ? " - looking" : "")
                        + (self.musicOn ? "" : " - music off") + (self.effectsOn ? "" : " - effects off")
                        + (self.world.armageddon ? " - ARMAGEDDON" : "")
                }
                self.needsDisplay = true
            }
        }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError() }

    /// Passes this tick's events to the sound driver: any effect the world asked
    /// for, clashes and crackling for fights and ruins in view, and the heartbeat.
    func updateSound() {
        var effect = world.pendingSound
        world.pendingSound = 0
        let states = Renderer.visibleStates(world: world, viewX: viewX, viewY: viewY, side: -1)
        if states & Int(Person.State.fighting) != 0 { effect = SoundDriver.Effect.fight + world.phase }
        if states & 0x80 != 0 {
            if fireQuiet == 0 { effect = SoundDriver.Effect.fire }
            fireQuiet = 3
        } else if fireQuiet > 0 {
            fireQuiet -= 1
        }
        let own = Int(world.players[0].population)
        let enemy = Int(world.players[1].population)
        audio?.update { driver in
            if effect != 0 { driver.play(effect: effect) }
            driver.setHeartbeat(own: own, enemy: enemy)
        }
    }

    /// The dialog box in front of the game, if any ($68CA2, $676AC).
    enum Overlay {
        /// The title picture with its three buttons.
        case title
        /// The animated demo the title falls into when left alone.
        case demo
        case score(ScoreSummary, won: Bool)
        /// The lord's speech after a win, on the way to world `next` (nil: all done).
        case lord(LordSpeech, lines: [String], next: Int?)
        /// What awaits on a world, with START GAME and NEW GAME.
        case world(Int)
        /// Typing a world's name.
        case entry(text: String, message: String)
        /// One of the option dialogs. `saved` is what CANCEL goes back to.
        case options(OptionsKind, saved: World.Options, savedConquest: Bool)
    }
    enum OptionsKind {
        case side(Int), rules, setup
    }

    /// False in a custom game (the tutorial is one): no world number, and the
    /// options can be changed.
    var conquest = true
    /// Which of the four landscapes is in use.
    var terrain = 0
    /// The seed the current custom map was made from, for "restart this map".
    var worldSeed: UInt16 = 0

    func optionsBox(_ kind: OptionsKind) -> Renderer.OptionsBox {
        switch kind {
        case .side(let side): return .side(side, world.options)
        case .rules: return .rules(world.ruleFlags)
        case .setup: return .setup(conquest: conquest)
        }
    }

    func openOptions(_ kind: OptionsKind) {
        overlay = .options(kind, saved: world.options, savedConquest: conquest)
        overlayCanClose = false
        needsDisplay = true
    }

    /// A new custom map ($5EBB6 with no world number). A fresh one may move on to
    /// the next landscape; restarting keeps everything.
    func loadCustom(seed: UInt16, options: World.Options, fresh: Bool) {
        let made = World.custom(seed: seed, options: options, startingPeople: options.startingPeople)
        if fresh, made.nextTerrain { terrain = (terrain + 1) & 3 }
        begin(made.world, seed: seed)
    }

    func loadTutorial() {
        terrain = 0
        begin(World.tutorial(), seed: World.Options.tutorialSeed)
        // The original starts the tutorial paused.
        paused = true
    }

    private func begin(_ custom: World, seed: UInt16) {
        conquest = false
        worldSeed = seed
        worldAfterThis = nil
        overlay = nil
        gameBegun = true
        paused = false
        audio?.setMuted(false)
        world = custom
        showLeader()
    }

    func showLeader() {
        let leader = Int(world.players[0].leader)
        let cell = leader > 0 ? Int(world.people[leader - 1].cell) : 0
        centerView(x: cell % World.size, y: cell / World.size)
        needsDisplay = true
    }

    /// "Move to next map" and "restart this map" ($6B958, after the setup box).
    func nextMap() {
        if conquest { loadWorld(worldNumber) } else { loadCustom(seed: world.random.seed, options: world.options, fresh: true) }
    }

    func restartMap() {
        if conquest { loadWorld(worldNumber) } else { loadCustom(seed: worldSeed, options: world.options, fresh: false) }
    }

    /// A click on a line of an option dialog.
    func pressOption(_ kind: OptionsKind, item: Int, value: Int) {
        var options = world.options
        switch kind {
        case .side(let side):
            // As in the original, only a custom game lets these be changed.
            guard !conquest else { return notify("These can only be changed in a custom game") }
            switch item {
            case 1...9: options.abilities[side] ^= 1 << (item - 1)
            case 11: options.skill[side] = 10 - value
            case 13: options.period[side] = 10 - value
            default: break
            }
        case .rules:
            guard !conquest else { return notify("These can only be changed in a custom game") }
            options.ruleFlags ^= 1 << ((item - 1) / 2)
        case .setup:
            switch item {
            case 2: notify("Two-player games are not ported")
            case 4: notify("Painting the map is not ported")
            case 6: notify("Playing as evil is not ported")
            case 8: notify("Computer against computer is not ported")
            case 9:
                // Conquest: choose the world to conquer.
                if !conquest {
                    overlay = .entry(text: "", message: "")
                    overlayCanClose = true
                }
            case 10: conquest = false
            case 11:
                overlay = .options(.rules, saved: options, savedConquest: conquest)
            case 12: saveGame(nil)
            case 13: openGame(nil)
            case 14: nextMap()
            case 15: restartMap()
            case 16:
                overlay = nil
                world.defeatedSide = 0
            default: break
            }
            needsDisplay = true
            return
        }
        world.apply(options)
        needsDisplay = true
    }
    var overlay: Overlay? {
        didSet { if case .title = overlay { titleIdle = 0 } }
    }
    var demo: TitleDemo?
    var demoScreen: IndexedImage?
    var titleIdle = 0.0
    /// How long the title waits before the demo. (The original counts a loop
    /// 350,000 times; a few seconds on an Amiga, by estimate.)
    static let titleWait = 4.0

    func startDemo() {
        var fresh = TitleDemo(assets: assets)
        demoScreen = fresh.step()
        demo = fresh
        overlay = .demo
        audio?.setDemoMusic(DemoMusic(data: assets.demoData, samples: assets.effects))
        needsDisplay = true
    }

    func endDemo() {
        demo = nil
        demoScreen = nil
        audio?.setDemoMusic(nil)
        overlay = .title
        needsDisplay = true
    }
    /// Where the box's buttons were last drawn.
    var overlayButtons: [DialogButton] = []
    /// Escape puts a box away only if the player asked for it during a game.
    var overlayCanClose = false
    var blink = 0

    /// A click on a dialog button: 0 is the left one.
    func pressDialogButton(_ index: Int) {
        guard let overlay else { return }
        switch (overlay, index) {
        case (.title, _): break
        case (.demo, _): endDemo()
        case (.options(let kind, let saved, let savedConquest), _):
            if index == 1 {
                world.apply(saved)
                conquest = savedConquest
            }
            // The rules box is reached from the setup box and returns to it.
            if case .rules = kind {
                self.overlay = .options(.setup, saved: world.options, savedConquest: conquest)
            } else {
                self.overlay = nil
            }
        case (.score, _) where !conquest: nextMap()
        case (.score(let summary, let won), _):
            if won {
                // The lord speaks, then on to the world the score has earned.
                let next = Conquest.world(after: worldNumber, score: summary.score)
                self.overlay = .lord(
                    LordSpeech(nextWorld: next),
                    lines: renderer.lordLines(conquered: worldNumber, next: next, names: names), next: next)
                audio?.setSpeaking(true)
            } else {
                loadWorld(worldNumber)
            }
        case (.lord(_, _, let next), _):
            // After the last world the original starts over from the beginning.
            audio?.setSpeaking(false)
            self.overlay = .world(next ?? 0)
        case (.world(let number), 0): loadWorld(number)
        case (.world, _): self.overlay = .entry(text: "", message: "")
        case (.entry(let text, _), 0):
            guard !text.isEmpty else { return }
            // (A number here starts a custom landscape in the original; not ported.)
            if let number = names.world(named: text), number <= Conquest.lastWorld {
                self.overlay = .world(number)
            } else {
                self.overlay = .entry(text: text, message: assets.dialogs.noSuchWorld)
            }
        case (.entry, _): self.overlay = .entry(text: "", message: "")
        }
        needsDisplay = true
    }

    /// Keys while a dialog box is up. Typing follows the original's name field
    /// ($65F50): capitals, up to fifteen characters, Delete and Return.
    func dialogKey(_ event: NSEvent) {
        if case .demo = overlay { return endDemo() }
        if event.keyCode == 53 {
            if overlayCanClose { overlay = nil }
        } else if event.keyCode == 36 || event.keyCode == 76 {
            pressDialogButton(0)
        } else if case .entry(var text, let message) = overlay {
            if event.keyCode == 51 || event.keyCode == 117 {
                if !text.isEmpty { text.removeLast() }
            } else if let scalar = event.characters?.unicodeScalars.first, (0x20...0x7A).contains(scalar.value),
                text.count < Renderer.nameLimit
            {
                text += String(Character(scalar)).uppercased()
            }
            overlay = .entry(text: text, message: message)
        }
        needsDisplay = true
    }

    @objc func newGame(_ sender: Any?) {
        // From the title picture this is the same as choosing Conquest.
        if case .title = overlay { overlayCanClose = false } else { overlayCanClose = true }
        overlay = .world(worldNumber)
        audio?.setMuted(false)
        needsDisplay = true
    }

    var level: LevelRecord { assets.levels[worldNumber / 25] }
    var rules: LandRules { LandRules(landWords: assets.landSets[terrain].headerWords) }

    var names: Conquest.Names {
        Conquest.Names(start: assets.nameSyllables[0], middle: assets.nameSyllables[1], end: assets.nameSyllables[2])
    }
    var worldName: String { conquest ? names.name(ofWorld: worldNumber) : "CUSTOM GAME" }
    /// Where Return leads once this world is over.
    var worldAfterThis: Int?

    func loadWorld(_ number: Int) {
        worldNumber = min(max(number / 5 * 5, 0), Conquest.lastWorld)
        worldAfterThis = nil
        overlay = nil
        gameBegun = true
        conquest = true
        terrain = level.terrain
        audio?.setMuted(false)
        world = World.conquest(
            seed: World.conquestSeed(worldNumber: worldNumber, levelWord: level.word),
            setup: World.Setup(
                skill: level.skill, period: level.period, computerPowers: level.computerPowers,
                playerPowers: level.playerPowers, ruleFlags: level.ruleFlags,
                startingPeople: (level.startingPeople.top, level.startingPeople.bottom), worldNumber: worldNumber))
        // Open on the first side's leader so there is something to see.
        let leader = Int(world.players[0].leader)
        let cell = leader > 0 ? Int(world.people[leader - 1].cell) : 0
        centerView(x: cell % World.size, y: cell / World.size)
        needsDisplay = true
    }

    func centerView(x: Int, y: Int) {
        viewX = min(max(x - 4, 0), Renderer.maxViewOrigin)
        viewY = min(max(y - 4, 0), Renderer.maxViewOrigin)
        needsDisplay = true
    }

    override var acceptsFirstResponder: Bool { true }
    override var isFlipped: Bool { true }


    /// The 320x200 screen scaled by the largest whole factor that fits, centred.
    var screenRect: NSRect {
        let scale = max(1, min(Int(bounds.width) / 320, Int(bounds.height) / 200))
        let size = NSSize(width: 320 * scale, height: 200 * scale)
        return NSRect(
            x: ((bounds.width - size.width) / 2).rounded(), y: ((bounds.height - size.height) / 2).rounded(),
            width: size.width, height: size.height)
    }

    /// Draws the dialog box, if one is up, and notes where its buttons are.
    func drawOverlay(into image: inout IndexedImage) {
        switch overlay {
        case .score(let summary, let won):
            overlayButtons = renderer.drawScore(summary, won: won, newGame: won || !conquest, into: &image)
        case .options(let kind, _, _):
            overlayButtons = renderer.drawOptions(optionsBox(kind), into: &image)
        case .world(let number):
            let brief = WorldBrief(
                name: names.name(ofWorld: number), worldNumber: number, level: assets.levels[number / 25])
            overlayButtons = renderer.drawWorldBox(brief, into: &image)
        case .entry(let text, let message):
            var brief = WorldBrief(name: "", worldNumber: worldNumber, level: level)
            brief.entry = (text, blink & 4 == 0, message)
            overlayButtons = renderer.drawWorldBox(brief, into: &image)
        case .title, .demo, .lord, nil: overlayButtons = []
        }
    }

    /// What is on the screen now, ready for the GPU.
    func displayFrame() -> DisplayFrame {
        var palette = assets.gamePalette
        var image: IndexedImage
        var closeUp: CloseUp?
        var over: [UInt8]?
        // The dialog box, if any; in HD it goes over the close-up.
        var dialog = IndexedImage(width: 320, height: 200, fill: IndexedImage.transparent)
        drawOverlay(into: &dialog)
        switch overlay {
        case .lord(let speech, let lines, _):
            image = renderer.lordScreen(lines: lines, mouth: speech.mouth, eyes: speech.eyesDrawn)
            palette = assets.lord.palette
        case .title:
            image = assets.title.image
            palette = assets.title.palette
        case .demo where demoScreen != nil:
            image = demoScreen!
        default:
            // Light the icon for what a click in the view will do, the current
            // behaviour, and the sound and pause switches.
            let cursorIcon = inspecting ? (6, 0) : placingMagnet ? (6, 2) : placingSwamp ? (2, 2) : (6, 1)
            let behaviourIcon = [(3, 3), (4, 3), (5, 3), (4, 4)][Int(world.players[0].mode) & 3]
            var sideIcons: [(Int, Int)] = []
            if musicOn { sideIcons.append((0, 3)) }
            if effectsOn { sideIcons.append((0, 4)) }
            if paused { sideIcons.append((1, 3)) }
            image = renderer.frame(
                world: world, terrain: terrain, viewX: viewX, viewY: viewY, phase: world.phase,
                litIcons: [cursorIcon, behaviourIcon], litSideIcons: sideIcons, closeUp: !hdGraphics)
            if hdGraphics {
                closeUp = renderer.closeUp(world: world, viewX: viewX, viewY: viewY, phase: world.phase)
                if overlay != nil { over = dialog.rgba(palette: palette) }
            } else {
                image.draw(dialog, x: 0, y: 0)
            }
        }
        // The pointer is a hardware sprite in the original: it takes its colours
        // from the upper half of the palette, which only the title picture changes.
        if let pointer {
            var layer = over ?? [UInt8](repeating: 0, count: 320 * 200 * 4)
            var colors = assets.gamePalette.colors
            if case .title = overlay, palette.colors.count >= 32 { colors = Array(palette.colors[16...]) }
            let sprite = assets.sprites[pointerSprite]
            for sy in 0..<sprite.height where (0..<200).contains(pointer.y + sy) {
                for sx in 0..<sprite.width where (0..<320).contains(pointer.x + sx) {
                    let p = Int(sprite[sx, sy])
                    guard p < colors.count else { continue }
                    let o = ((pointer.y + sy) * 320 + pointer.x + sx) * 4
                    layer[o] = colors[p].r
                    layer[o + 1] = colors[p].g
                    layer[o + 2] = colors[p].b
                    layer[o + 3] = 255
                }
            }
            over = layer
        }
        var frame = DisplayFrame(
            under: image.rgba(palette: palette), over: over, closeUp: closeUp, terrain: terrain,
            time: Date().timeIntervalSince(startTime))
        frame.underWidth = image.width
        frame.underHeight = image.height
        return frame
    }

    /// The game screen's place in the drawable, in its pixels.
    func screenRectInPixels(drawableWidth: CGFloat) -> CGRect {
        let scale = bounds.width > 0 ? drawableWidth / bounds.width : 1
        let rect = screenRect
        return CGRect(x: rect.minX * scale, y: rect.minY * scale, width: rect.width * scale, height: rect.height * scale)
    }

    func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {}

    func draw(in view: MTKView) {
        guard let drawable = view.currentDrawable else { return }
        screenRenderer.present(
            displayFrame(), to: drawable, target: drawable.texture,
            screen: screenRectInPixels(drawableWidth: CGFloat(drawable.texture.width)))
    }

    /// A picture of the window's contents at full resolution, for screenshots.
    func snapshot() -> NSBitmapImageRep? {
        let scale = window?.backingScaleFactor ?? 2
        let width = Int(bounds.width * scale), height = Int(bounds.height * scale)
        guard width > 0, height > 0,
            let image = screenRenderer.snapshot(
                displayFrame(), width: width, height: height,
                screen: screenRectInPixels(drawableWidth: CGFloat(width)))
        else { return nil }
        return NSBitmapImageRep(cgImage: image)
    }

    @objc func toggleGraphics(_ sender: Any?) {
        hdGraphics.toggle()
        notify(hdGraphics ? "HD graphics (early preview) - Tab switches back" : "Classic graphics - Tab switches to HD")
    }

    @objc func saveGame(_ sender: Any?) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "\(worldName).GAM"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        var save = SaveGame(world: world)
        save.worldNumber = conquest ? worldNumber : -1
        save.conquestWorld = worldNumber
        save.levelRecord = level.bytes
        save.terrain = terrain
        save.worldSeed = conquest ? World.conquestSeed(worldNumber: worldNumber, levelWord: level.word) : worldSeed
        do {
            try Data(save.bytes).write(to: url)
        } catch {
            NSAlert(error: error).runModal()
        }
    }

    @objc func openGame(_ sender: Any?) {
        let panel = NSOpenPanel()
        panel.message = "Choose a saved game"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let save = try SaveGame(bytes: [UInt8](try Data(contentsOf: url)))
            guard save.worldNumber == -1 || (0...Conquest.lastWorld).contains(save.worldNumber),
                (0...3).contains(save.terrain)
            else { throw SaveGame.LoadError.notASaveGame }
            conquest = save.worldNumber != -1
            if conquest { worldNumber = save.worldNumber }
            terrain = save.terrain
            worldSeed = save.worldSeed
            worldAfterThis = nil
            overlay = nil
            gameBegun = true
            audio?.setMuted(false)
            world = save.world
            let leader = Int(world.players[0].leader)
            let cell = leader > 0 ? Int(world.people[leader - 1].cell) : Int(world.players[0].magnetCell)
            centerView(x: (cell & 63) + 1, y: (cell >> 6) + 1)
        } catch {
            let alert = NSAlert()
            alert.messageText = "\(url.lastPathComponent) is not a Populous saved game this version can load."
            alert.runModal()
        }
    }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 48 { return toggleGraphics(nil) }  // Tab, anywhere
        if overlay != nil { return dialogKey(event) }
        switch event.keyCode {
        case 123: centerView(x: viewX + 3, y: viewY + 4)  // left
        case 124: centerView(x: viewX + 5, y: viewY + 4)  // right
        case 126: centerView(x: viewX + 4, y: viewY + 3)  // up
        case 125: centerView(x: viewX + 4, y: viewY + 5)  // down
        case 53:  // Escape: back to the normal raise/lower cursor
            inspecting = false
            placingMagnet = false
            placingSwamp = false
            needsDisplay = true
        case 36:  // Return: move on once the world is decided
            if let next = worldAfterThis { loadWorld(next) }
        default:
            switch event.charactersIgnoringModifiers {
            case "[": loadWorld(worldNumber - 5)
            case "]": loadWorld(worldNumber + 5)
            case "m": pressSideIcon(column: 0, row: 3)
            case "s": pressSideIcon(column: 0, row: 4)
            case "p": pressSideIcon(column: 1, row: 3)
            default: super.keyDown(with: event)
            }
        }
    }

    /// Screen pixel (0..<320, 0..<200) under the mouse, if it is on the game screen.
    func screenPoint(_ event: NSEvent) -> (x: Int, y: Int)? {
        let point = convert(event.locationInWindow, from: nil)
        let rect = screenRect
        guard rect.contains(point) else { return nil }
        return (Int((point.x - rect.minX) * 320 / rect.width), Int((point.y - rect.minY) * 200 / rect.height))
    }

    /// The human plays the first side: left button raises land, right button lowers.
    /// As in the original, this places an order that the next tick carries out, and
    /// it only works while some people are in view.
    func reshape(_ event: NSEvent, raise: Bool) {
        guard let point = screenPoint(event) else { return }
        // Say why when a click does nothing; the original just stays silent.
        if paused {
            notify("The game is paused - click zzZ or press P to go on")
        } else if world.ruleFlags & (raise ? 4 : 12) != 0 {
            notify("This world does not allow \(raise ? "raising" : "lowering") land")
        } else if Renderer.visibleStates(world: world, viewX: viewX, viewY: viewY, side: 0) == 0 {
            notify("You can only reshape land while some of your people are in view")
        } else if world.players[0].mana < 10 {
            notify("Not enough mana to reshape land")
        } else if let corner = Renderer.viewCorner(atX: point.x, y: point.y, world: world, viewX: viewX, viewY: viewY) {
            world.orders[0].action = raise ? 1 : 2
            world.orders[0].x = corner.x
            world.orders[0].y = corner.y
        }
    }

    /// A short message for the title bar, and how many more ticks to show it.
    var notice = ""
    var noticeTicks = 0

    func notify(_ text: String) {
        notice = text
        noticeTicks = 24
        window?.title = "Populous - " + text
    }

    /// Set by the "place papal magnet" icon: the next click in the view sends the
    /// magnet there instead of raising land.
    var placingMagnet = false
    var paused = false
    /// Set by the "?" icon: clicks in the close-up pick a person to follow.
    var inspecting = false
    var musicOn = true
    var effectsOn = true
    /// Where the "next fight" and "next settlement / knight" icons last stopped.
    var lastFight = 0
    var lastOwn = 0
    /// Likewise for the swamp icon.
    var placingSwamp = false

    func order(_ action: Int, x: Int, y: Int) {
        world.orders[0].action = action
        world.orders[0].x = x
        world.orders[0].y = y
    }
    static let modeNames = ["go to magnet", "settle", "gather", "fight"]

    func setMode(_ mode: Int) { order(0xE, x: mode, y: 1) }

    /// The panel icons: view arrows, the four behaviours, the divine powers, the
    /// cursor choices and "go to leader". Each power needs enough mana and must be
    /// allowed in this world, or nothing happens.
    func pressIcon(column: Int, row: Int, rightButton: Bool) {
        switch (column, row) {
        case (3...5, 0...2) where (column, row) != (4, 1):
            viewX = min(max(viewX + (column == 5 ? 1 : 0) - (column == 3 ? 1 : 0), 0), Renderer.maxViewOrigin)
            viewY = min(max(viewY + (row == 2 ? 1 : 0) - (row == 0 ? 1 : 0), 0), Renderer.maxViewOrigin)
        case (3, 3): setMode(0)
        case (4, 3): setMode(1)
        case (5, 3): setMode(2)
        case (4, 4): setMode(3)
        case (0, 0): order(0xE, x: 0, y: 4)  // flood
        case (1, 0): order(0xE, x: 0, y: 3)  // Armageddon
        case (1, 1): order(6, x: viewX, y: viewY)  // volcano under the view
        case (2, 0): order(3, x: viewX, y: viewY)  // earthquake under the view
        case (2, 1): order(0xE, x: 0, y: 5)  // knight
        case (2, 2):
            placingSwamp = true
            placingMagnet = false
        case (6, 0), (6, 1):
            // The "?" looks at people instead of reshaping land; the arrows go back.
            inspecting = column == 6 && row == 0
            placingMagnet = false
            placingSwamp = false
        case (6, 2):
            placingMagnet = true
            placingSwamp = false
        case (4, 1):
            // Centre of the arrows: back to the person being followed.
            if world.selectedPerson != 0 { show(person: world.selectedPerson - 1, select: false) }
        case (7, 0):
            // Left button: to the papal magnet. Right button: to the leader, who is
            // then followed; to the magnet if there is no leader.
            let leader = Int(world.players[0].leader)
            if rightButton, leader != 0 {
                show(person: leader - 1, select: true)
            } else {
                show(cell: Int(world.players[0].magnetCell))
            }
        case (7, 1):
            // The next fight anywhere on the map.
            if let found = nextPerson(after: lastFight, where: { $0.state & Person.State.fighting != 0 }) {
                lastFight = found
                show(person: found, select: true)
            }
        case (8, 0):
            // Left button: the next of your settlements. Right button: your next knight.
            func wanted(_ person: Person) -> Bool {
                guard person.owner == 0 else { return false }
                return rightButton ? person.long14 != 0 : person.state == Person.State.settled
            }
            if let found = nextPerson(after: lastOwn, where: wanted) {
                lastOwn = found
                show(person: found, select: true)
            }
        default: break
        }
        needsDisplay = true
    }

    /// The original puts a looked-at cell three in from the view's top corner.
    func show(cell: Int) {
        centerView(x: (cell & 63) + 1, y: (cell >> 6) + 1)
    }

    func show(person index: Int, select: Bool) {
        show(cell: Int(world.people[index].cell))
        if select { world.selectedPerson = index + 1 }
    }

    /// The next living person after `index` (wrapping round) that passes the test.
    func nextPerson(after index: Int, where test: (Person) -> Bool) -> Int? {
        let count = world.personCount
        guard count > 0 else { return nil }
        for offset in 1...count {
            let candidate = (index + offset) % count
            let person = world.people[candidate]
            if person.life > 0, test(person) { return candidate }
        }
        return nil
    }

    /// The lower-right icons that are ported: music, sound effects and pause.
    func pressSideIcon(column: Int, row: Int, rightButton: Bool = false) {
        switch (column, row) {
        // The scales: the left button shows evil's options, the right button good's.
        case (0, 0): openOptions(.side(rightButton ? 0 : 1))
        // The globe: game setup.
        case (1, 1): openOptions(.setup)
        case (0, 3):
            musicOn.toggle()
            audio?.update { $0.musicOn = self.musicOn }
        case (0, 4):
            effectsOn.toggle()
            audio?.update { $0.effectsOn = self.effectsOn }
        case (1, 3): paused.toggle()
        default: break
        }
    }

    /// Clicking a person in the close-up while looking (not reshaping) follows them.
    func personSprite(atX px: Int, y py: Int) -> Int? {
        for cy in 0..<Renderer.viewSize {
            for cx in 0..<Renderer.viewSize {
                let cell = viewX + cx + (viewY + cy) * World.size
                let who = Int(world.who[cell])
                guard who != 0, who <= World.maxPeople else { continue }
                let x = Renderer.viewOriginX + 8 + 16 * cx - 16 * cy
                let y = Renderer.viewOriginY - 8 + 8 * cx + 8 * cy - Int(world.height[cell]) * 8
                if (x...x + 16).contains(px), (y...y + 16).contains(py) { return who - 1 }
            }
        }
        return nil
    }

    /// Where the mouse is on the game screen, if it is over it.
    var pointer: (x: Int, y: Int)?
    /// False until the first world is begun; the pointer is a plain arrow till then.
    var gameBegun = false

    /// The sprite the original uses as its pointer: a green arrow before the game
    /// begins, then by cursor mode ($73014) a shield for looking, an arrow with an
    /// ankh for placing the magnet, one with reeds for a placed power, and
    /// otherwise the hand. Its top-left pixel is the point that clicks.
    var pointerSprite: Int {
        if case .title = overlay { return 0x81 }
        if case .demo = overlay { return 0x81 }
        if !gameBegun { return 0x81 }
        return inspecting ? 0x44 : placingMagnet ? 0x50 : placingSwamp ? 0x53 : 0x4E
    }

    func trackPointer(_ event: NSEvent) {
        pointer = screenPoint(event)
        needsDisplay = true
    }

    override func mouseMoved(with event: NSEvent) { trackPointer(event) }
    override func rightMouseDragged(with event: NSEvent) { trackPointer(event) }
    override func mouseExited(with event: NSEvent) {
        pointer = nil
        needsDisplay = true
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(
            rect: bounds, options: [.mouseMoved, .mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
            owner: self, userInfo: nil))
    }

    /// The Mac's own arrow is hidden over the game screen.
    override func resetCursorRects() {
        let image = NSImage(size: NSSize(width: 16, height: 16))
        image.lockFocus()
        NSColor.clear.set()
        NSRect(x: 0, y: 0, width: 16, height: 16).fill()
        image.unlockFocus()
        let blank = NSCursor(image: image, hotSpot: .zero)
        addCursorRect(screenRect, cursor: blank)
    }

    override func mouseDown(with event: NSEvent) { click(event, rightButton: false) }

    override func rightMouseDown(with event: NSEvent) { click(event, rightButton: true) }

    func click(_ event: NSEvent, rightButton: Bool) {
        guard let point = screenPoint(event) else { return }
        defer { needsDisplay = true }
        if case .lord = overlay { return pressDialogButton(0) }
        if case .demo = overlay { return endDemo() }
        if case .title = overlay {
            switch Renderer.titleChoice(atX: point.x, y: point.y) {
            case .conquest:
                overlay = .world(0)
                audio?.setMuted(false)
            case .tutorial: loadTutorial()
            case .custom:
                // (The original's first landscape here is whatever its random
                // seed holds at start-up; any seed will do.)
                terrain = 0
                loadCustom(seed: UInt16.random(in: 1...0x7FFF), options: .standard, fresh: false)
            // A click anywhere else starts the demo straight away, as in the original.
            case nil: startDemo()
            }
            return
        }
        if case .options(let kind, _, _) = overlay,
            let hit = renderer.optionsItem(optionsBox(kind), atX: point.x, y: point.y)
        {
            return pressOption(kind, item: hit.item, value: hit.value)
        }
        if overlay != nil {
            if let index = overlayButtons.firstIndex(where: { $0.contains(x: point.x, y: point.y) }) {
                pressDialogButton(index)
            }
            return
        }
        if let cell = Renderer.minimapCell(atX: point.x, y: point.y) {
            centerView(x: cell.x + 1, y: cell.y + 1)
        } else if let icon = Renderer.panelIcon(atX: point.x, y: point.y) {
            pressIcon(column: icon.column, row: icon.row, rightButton: rightButton)
        } else if let icon = Renderer.sidePanelIcon(atX: point.x, y: point.y) {
            pressSideIcon(column: icon.column, row: icon.row, rightButton: rightButton)
        } else if rightButton {
            reshape(event, raise: false)
        } else if inspecting {
            if let person = personSprite(atX: point.x, y: point.y) {
                world.selectedPerson = person + 1
            } else {
                notify("Looking mode - click the up/down arrows icon or press Esc to reshape land again")
            }
        } else if placingMagnet || placingSwamp {
            guard let corner = Renderer.viewCorner(atX: point.x, y: point.y, world: world, viewX: viewX, viewY: viewY)
            else { return }
            // One less than the corner, wrapping as a byte like the original.
            order(placingMagnet ? 5 : 4, x: corner.x - 1, y: corner.y - 1)
            placingMagnet = false
            placingSwamp = false
        } else {
            reshape(event, raise: true)
        }
    }

    override func mouseDragged(with event: NSEvent) {
        trackPointer(event)
        guard overlay == nil else { return }
        guard let point = screenPoint(event), let cell = Renderer.minimapCell(atX: point.x, y: point.y) else { return }
        centerView(x: cell.x + 1, y: cell.y + 1)
    }
}

/// The Metal view inside `GameView`. Clicks and keys pass through it to the game.
final class MetalSurface: MTKView {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}

/// The disk image: first argument, else the one used last time, else an .adf in the
/// current directory or beside the app, else ask. The choice is remembered.
@MainActor func findDisk() -> URL? {
    let files = FileManager.default
    func adf(in folder: URL) -> URL? {
        let found = try? files.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
        return found?.first { $0.pathExtension.lowercased() == "adf" }
    }
    var disk: URL?
    if CommandLine.arguments.count > 1 {
        disk = URL(fileURLWithPath: CommandLine.arguments[1])
    } else if let last = UserDefaults.standard.string(forKey: "diskPath"), files.fileExists(atPath: last) {
        disk = URL(fileURLWithPath: last)
    } else if let here = adf(in: URL(fileURLWithPath: files.currentDirectoryPath)) {
        disk = here
    } else if let beside = adf(in: Bundle.main.bundleURL.deletingLastPathComponent()) {
        disk = beside
    } else {
        let panel = NSOpenPanel()
        panel.message = "Choose your Populous disk image (.adf)"
        panel.allowedContentTypes = [.init(filenameExtension: "adf") ?? .data]
        if panel.runModal() == .OK { disk = panel.url }
    }
    if let disk { UserDefaults.standard.set(disk.path, forKey: "diskPath") }
    return disk
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)

guard let disk = findDisk() else { exit(0) }
let assets: GameAssets
do {
    assets = try GameAssets(volume: ADFVolume(data: [UInt8](try Data(contentsOf: disk))))
} catch {
    let alert = NSAlert()
    alert.messageText = "Could not load \(disk.lastPathComponent)"
    alert.informativeText = "\(error)"
    alert.runModal()
    exit(1)
}

let window = NSWindow(
    contentRect: NSRect(x: 0, y: 0, width: 960, height: 600),
    styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
let screenRenderer: ScreenRenderer
do {
    screenRenderer = try ScreenRenderer(assets: assets)
} catch {
    let alert = NSAlert()
    alert.messageText = "Could not start the graphics"
    alert.informativeText = "\(error)"
    alert.runModal()
    exit(1)
}
let view = GameView(assets: assets, screenRenderer: screenRenderer)
window.contentView = view
window.contentMinSize = NSSize(width: 320, height: 200)
window.center()
window.makeKeyAndOrderFront(nil)
window.makeFirstResponder(view)
// Like the original, begin at the title picture.
if ProcessInfo.processInfo.environment["POPULOUS_SELFTEST"] == nil {
    view.overlay = .title
    view.gameBegun = false
    view.audio?.setMuted(true)
    window.title = "Populous"
}

// POPULOUS_MANUAL_SHOTS=<folder> writes pictures of every screen for the manual.
if let folder = ProcessInfo.processInfo.environment["POPULOUS_MANUAL_SHOTS"] {
    takeManualShots(view: view, window: window, folder: folder)
}

// POPULOUS_SELFTEST=1 feeds the view a few synthetic clicks and reports what they
// did, without waiting for a person at the mouse.
if ProcessInfo.processInfo.environment["POPULOUS_SELFTEST"] != nil {
    func press(_ x: Int, _ y: Int, right: Bool = false) {
        let rect = view.screenRect
        // Window coordinates have their origin at the bottom left.
        let point = NSPoint(
            x: rect.minX + (CGFloat(x) + 0.5) * rect.width / 320,
            y: view.bounds.height - (rect.minY + (CGFloat(y) + 0.5) * rect.height / 200))
        let event = NSEvent.mouseEvent(
            with: right ? .rightMouseDown : .leftMouseDown, location: point, modifierFlags: [], timestamp: 0,
            windowNumber: window.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1)!
        if right { view.rightMouseDown(with: event) } else { view.mouseDown(with: event) }
    }
    func report(_ what: String) {
        let o = view.world.orders[0]
        print("SELFTEST \(what): order \(o.action) at \(o.x),\(o.y); view \(view.viewX),\(view.viewY); inspecting \(view.inspecting) magnet \(view.placingMagnet) paused \(view.paused) selected \(view.world.selectedPerson)")
        view.world.orders[0].action = 0
    }
    report("start")
    press(128, 184); report("left click on go-to-magnet")
    press(192, 130); print("SELFTEST raise with nobody in view: notice '\(view.notice)'"); report("after it")
    press(128, 184, right: true); report("right click on go-to-leader")
    press(192, 130); report("left click in the middle of the view")
    press(192, 130, right: true); report("right click in the middle of the view")
    press(113, 173); report("click on ?")
    press(192, 130); report("left click in view while looking")
    press(97, 184); report("click on the up/down arrows icon")
    press(192, 130); report("left click in view again")
    press(128, 184, right: true); report("right click on go-to-leader")
    press(128, 184); report("left click on go-to-magnet")
    press(240, 192); report("click on FX")
    press(272, 192); report("click on zzZ")
    print("SELFTEST effects on \(view.effectsOn) music on \(view.musicOn)")
    // The dialogs: ask for a new game, type a wrong and a right name, start it.
    func box() -> String {
        var scratch = IndexedImage(width: 320, height: 200)
        view.drawOverlay(into: &scratch)
        return "\(String(describing: view.overlay)) buttons \(view.overlayButtons.map { "\($0.text)@\($0.x),\($0.y)-\($0.right)" })"
    }
    func type(_ text: String) {
        for character in text {
            view.keyDown(with: NSEvent.keyEvent(
                with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
                context: nil, characters: String(character), charactersIgnoringModifiers: String(character),
                isARepeat: false, keyCode: character == "\r" ? 36 : 0)!)
        }
    }
    // With POPULOUS_SHOTS=<folder>, pictures of the view with the pointer in each mode.
    if let folder = ProcessInfo.processInfo.environment["POPULOUS_SHOTS"] {
        func shot(_ name: String) {
            view.pointer = (200, 120)
            guard let rep = view.snapshot() else { return }
            try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: folder + "/" + name + ".png"))
        }
        view.paused = false
        shot("pointer-hand")
        view.inspecting = true; shot("pointer-shield"); view.inspecting = false
        view.placingMagnet = true; shot("pointer-magnet"); view.placingMagnet = false
        view.placingSwamp = true; shot("pointer-swamp"); view.placingSwamp = false
        view.overlay = .title; shot("pointer-title"); view.overlay = nil
        view.pointer = nil
    }
    // The title picture: a click beside the buttons does nothing, Conquest goes on.
    view.overlay = .title
    press(100, 100); print("SELFTEST title, click on the castle: \(String(describing: view.overlay))")
    press(260, 163); print("SELFTEST title, click on Tutorial: \(String(describing: view.overlay))")
    press(260, 177); print("SELFTEST title, click on Conquest: \(String(describing: view.overlay))")
    // The demo: a click beside the title's buttons starts it, another ends it.
    view.overlay = .title
    press(100, 100); print("SELFTEST title, click on the castle: \(String(describing: view.overlay)) demo \(view.demo != nil)")
    press(100, 100); print("SELFTEST click during the demo: \(String(describing: view.overlay))")
    // The tutorial and a custom game, and the option dialogs.
    view.overlay = .title
    press(260, 163)
    print("SELFTEST tutorial: conquest \(view.conquest) paused \(view.paused) mana \(view.world.players[0].mana) people \(view.world.players[0].population) vs \(view.world.players[1].population) abilities \(view.world.options.abilities)")
    view.overlay = .title
    press(260, 190)
    print("SELFTEST custom: conquest \(view.conquest) options \(view.world.options) people \(view.world.personCount)")
    press(304, 164); print("SELFTEST scales, left button: \(box())")
    press(48, 49); press(160, 149); print("SELFTEST unticked 'modify land', aggression 6: \(view.world.options)")
    press(view.overlayButtons[1].x + 4, view.overlayButtons[1].y + 4); print("SELFTEST after CANCEL: \(view.world.options)")
    press(304, 180); print("SELFTEST globe: \(box())")
    let before = view.world.fingerprint
    press(100, 143); print("SELFTEST next map: new world \(view.world.fingerprint != before) terrain \(view.terrain) overlay \(String(describing: view.overlay))")
    let map = view.world.fingerprint
    view.world.tick(rules: view.rules)
    press(304, 180); press(100, 153); print("SELFTEST restart map: same world \(view.world.fingerprint == map)")
    view.paused = false
    // A win: score box, the lord, then the box for the world the score earned.
    view.overlay = .score(view.world.scoreSummary(won: true), won: true)
    view.pressDialogButton(0)
    if case .lord(let speech, let lines, let next) = view.overlay {
        print("SELFTEST lord: rank \(speech.rank) \(lines) next \(String(describing: next))")
    }
    press(160, 100); print("SELFTEST after the lord: \(box())")
    view.newGame(nil); print("SELFTEST new game: \(box())")
    press(view.overlayButtons[1].x + 4, view.overlayButtons[1].y + 4); print("SELFTEST after NEW GAME: \(box())")
    type("nowhere\r"); print("SELFTEST typed a wrong name: \(box())")
    press(view.overlayButtons[1].x + 4, view.overlayButtons[1].y + 4)
    type(view.names.name(ofWorld: 25).lowercased() + "\r"); print("SELFTEST typed world 25's name: \(box())")
    press(view.overlayButtons[0].x + 4, view.overlayButtons[0].y + 4)
    print("SELFTEST after START GAME: world \(view.worldNumber) \(box())")
    exit(0)
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
let delegate = AppDelegate()
app.delegate = delegate

// A minimal menu bar: quit, save and open, graphics, full screen.
let menuBar = NSMenu()
let appMenu = NSMenu()
appMenu.addItem(withTitle: "Quit Populous", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
let fileMenu = NSMenu(title: "Game")
fileMenu.addItem(withTitle: "New Game…", action: #selector(GameView.newGame(_:)), keyEquivalent: "n").target = view
fileMenu.addItem(withTitle: "Open Saved Game…", action: #selector(GameView.openGame(_:)), keyEquivalent: "o").target = view
fileMenu.addItem(withTitle: "Save Game…", action: #selector(GameView.saveGame(_:)), keyEquivalent: "s").target = view
let viewMenu = NSMenu(title: "View")
// Tab does the same; the menu shows which graphics are on.
viewMenu.addItem(withTitle: "HD Graphics", action: #selector(GameView.toggleGraphics(_:)), keyEquivalent: "").target = view
let windowMenu = NSMenu(title: "Window")
windowMenu.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
for menu in [appMenu, fileMenu, viewMenu, windowMenu] {
    let item = NSMenuItem()
    item.submenu = menu
    menuBar.addItem(item)
}
app.mainMenu = menuBar
window.collectionBehavior.insert(.fullScreenPrimary)
app.activate(ignoringOtherApps: true)
app.run()
