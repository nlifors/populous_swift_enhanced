import AppKit
import PopulousAssets
import PopulousCore
import PopulousRender

/// POPULOUS_MANUAL_SHOTS=<folder> writes a picture of every screen and pointer
/// mode into the folder, for the manual, and quits. Nothing is shown or heard.
@MainActor func takeManualShots(view: GameView, window: NSWindow, folder: String) {
    func shot(_ name: String, pointer: (Int, Int)? = (200, 120)) {
        view.pointer = pointer
        guard let rep = view.snapshot() else { return }
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: folder + "/" + name + ".png"))
        print("shot \(name)")
    }
    func run(_ ticks: Int) {
        for _ in 0..<ticks {
            view.world.tick(rules: view.rules)
            let followed = view.world.selectedPerson
            if followed != 0, view.world.people[followed - 1].life <= 0 { view.world.selectedPerson = 0 }
            view.world.processOrders()
        }
    }
    func press(_ x: Int, _ y: Int, right: Bool = false) {
        let rect = view.screenRect
        let point = NSPoint(
            x: rect.minX + (CGFloat(x) + 0.5) * rect.width / 320,
            y: view.bounds.height - (rect.minY + (CGFloat(y) + 0.5) * rect.height / 200))
        let event = NSEvent.mouseEvent(
            with: right ? .rightMouseDown : .leftMouseDown, location: point, modifierFlags: [], timestamp: 0,
            windowNumber: window.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1)!
        if right { view.rightMouseDown(with: event) } else { view.mouseDown(with: event) }
    }

    // Title, demo.
    view.overlay = .title
    view.gameBegun = false
    shot("title", pointer: (240, 180))
    view.startDemo()
    for _ in 0..<200 { view.demoScreen = view.demo?.step() }
    shot("demo", pointer: nil)
    view.endDemo()

    // The world box for Genesis, and typing a name.
    view.overlay = .world(0)
    shot("world-box")
    view.overlay = .entry(text: "GENESIS", message: "")
    shot("name-entry")
    view.overlay = .entry(text: "NOWHERE", message: view.assets.dialogs.noSuchWorld)
    shot("name-entry-unknown")

    // Genesis at the start, then after a while.
    view.loadWorld(0)
    view.gameBegun = true
    shot("game-start")
    run(400)
    view.showLeader()
    shot("game-later")
    // Each behaviour lit.
    for (mode, name) in [(1, "settle"), (0, "magnet"), (2, "gather"), (3, "fight")] {
        view.setMode(mode)
        run(1)
        shot("behaviour-\(name)")
    }
    view.setMode(1)
    run(1)
    // Pointer modes.
    shot("pointer-hand")
    view.inspecting = true; shot("pointer-shield"); view.inspecting = false
    view.placingMagnet = true; shot("pointer-magnet"); view.placingMagnet = false
    view.placingSwamp = true; shot("pointer-swamp"); view.placingSwamp = false
    // Following someone: the shield.
    let leader = Int(view.world.players[0].leader)
    if leader != 0 { view.show(person: leader - 1, select: true) }
    shot("shield-leader")
    // A settlement of ours, by the "next settlement" icon.
    view.pressIcon(column: 8, row: 0, rightButton: false)
    shot("shield-settlement")
    // A fight, if one can be found by the icon.
    run(300)
    view.pressIcon(column: 7, row: 1, rightButton: false)
    shot("shield-fight")
    view.world.selectedPerson = 0
    // Paused, sound off.
    view.paused = true
    view.musicOn = false
    view.effectsOn = false
    shot("paused")
    view.paused = false
    view.musicOn = true
    view.effectsOn = true
    // Lots of mana: the pointer far down the rail.
    let savedMana = view.world.players[0].mana
    view.world.players[0].mana = 9000
    shot("mana-high")
    view.world.players[0].mana = 85000
    shot("mana-full")
    view.world.players[0].mana = savedMana
    // Armageddon.
    view.world.players[0].mana = 90000
    view.pressIcon(column: 1, row: 0, rightButton: false)
    run(40)
    shot("armageddon")

    // Score box, won and lost, the lord, and the box for the next world.
    view.loadWorld(0)
    run(200)
    view.overlay = .score(view.world.scoreSummary(won: true), won: true)
    shot("score-won")
    view.overlay = .score(view.world.scoreSummary(won: false), won: false)
    shot("score-lost")
    let next = Conquest.world(after: 0, score: 12000)
    view.overlay = .lord(LordSpeech(nextWorld: next), lines: view.renderer.lordLines(conquered: 0, next: next, names: view.names), next: next)
    shot("lord", pointer: nil)
    view.overlay = .world(next ?? 0)
    shot("world-box-next")

    // Tutorial and custom game, with the option boxes.
    view.loadTutorial()
    shot("tutorial")
    view.paused = false
    view.terrain = 0
    view.loadCustom(seed: 0x1234, options: .standard, fresh: false)
    run(100)
    view.showLeader()
    shot("custom")
    view.openOptions(.side(1)); shot("options-evil")
    view.openOptions(.side(0)); shot("options-good")
    view.openOptions(.rules); shot("options-rules")
    view.openOptions(.setup); shot("options-setup")
    view.overlay = nil
    // Each landscape, from the conquest worlds that use them.
    for number in [0, 25, 50, 75, 100, 125] {
        view.loadWorld(number)
        run(150)
        view.showLeader()
        shot("world-\(number)-terrain\(view.terrain)")
    }
    exit(0)
}
