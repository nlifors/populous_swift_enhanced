```
 ____   ___  ____  _   _ _     ___  _   _ ____
|  _ \ / _ \|  _ \| | | | |   / _ \| | | / ___|
| |_) | | | | |_) | | | | |  | | | | | | \___ \
|  __/| |_| |  __/| |_| | |__| |_| | |_| |___) |
|_|    \___/|_|     \___/|_____\___/ \___/|____/

        a native Mac port of the 1989 Amiga game
```

# Populous for Mac

A port of Bullfrog's *Populous* (Amiga, 1989) to macOS, written in Swift.

It is a **faithful port, not a remake**: the game rules were recovered by
disassembling the original program and rewritten in Swift, then checked against the
original code running in an emulator. The graphics, sounds, worlds and text are read
from your own Amiga disk image when the game starts.

**No game data is included.** You need your own Populous disk image (`.adf`).

## What works

- The whole simulation: landscape generation, walkers, settlements, fights, knights,
  the papal magnet and the computer opponent. These match the original tick for tick
  in 14 recorded scenarios.
- Divine powers: raise and lower land, earthquake, swamp, knight, volcano, flood and
  Armageddon.
- Conquest: all 495 worlds, by name, with the original scoring and progression.
- The tutorial, and custom games with the original's option dialogs: each side's
  abilities, the computer's aggression and rate, and the rules of the land.
- The original screens: title picture, "world to conquer" box, score box, the lord's
  speech, the shield with details of the person you follow, and the game's own mouse
  pointer.
- Music, sound effects and the lord's voice.
- The animated demo with its own tune, which the title falls into if you wait.
- Saving and loading games in the original file layout.

## What is missing

- In the game setup box: two players, painting the map, playing as evil, and
  computer against computer.
- Two-player games over a serial cable.
- The message icon at the lower right of the board.
- The flying creatures that cross the map.

## What is only approximate

- **Speed.** The original's tick rate has not been measured; the port runs at 8 ticks
  a second.
- **Things not checked against the original.** The simulation was compared with the
  original under emulation. Drawing, sound, scoring, the dialogs and save files were
  ported from the disassembly but not compared in the same way.

## Running it

You need a Mac with macOS 13 or later and the Swift toolchain (Xcode, or the Command
Line Tools: `xcode-select --install`).

Build the app:

```sh
./make-app.sh
open Populous.app
```

The app's Dock icon is original art drawn by `tools/make-icon.py` (needs Pillow);
`Populous.icns` is checked in, so you only run that script to change the icon.

The first time, the game asks for your disk image. To skip the question, put the
`.adf` file next to `Populous.app`. The choice is remembered.

To run without building an app bundle:

```sh
swift run Populous "path/to/Populous.adf"
```

To run the tests:

```sh
swift test
```

The tests that compare against the original are skipped unless the reference data has
been generated (see *How the port was made*).

## How to play

You are a god. Your followers (blue) share a world with the followers of a rival god
(red). You cannot command your people directly. You shape the land for them, tell
them in general what to do, and strike the enemy with disasters. You win a world when
the other side has nobody left.

### Getting started

1. On the title picture, click **Conquest**. (**Tutorial** starts a paused practice
   world with a head start and a harmless opponent; press P to begin. **Custom**
   starts a random world whose options you can change.)
2. The "world to conquer" box describes the first world, Genesis. Click
   **START GAME**.
3. The view opens on your leader. The book at the top left is the map of the whole
   world; the square in the middle is the close-up where you act.

### The basic idea

- **Flat land is everything.** Your people build on flat land, and the more flat land
  around a settlement, the bigger it grows, from tent to castle.
- **Left-click raises land, right-click lowers it.** Use this to flatten the ground
  around your people. It only works where some of your people are in view.
- **Settlements make people and mana.** A full settlement sends out a new walker, who
  looks for more flat land. Bigger settlements also give their people stronger
  weapons.
- **Mana pays for everything.** It grows with your population. The pointer on the
  rail at the top right shows how much you have and which powers you can afford.

A good opening: flatten the land around your first walkers, let them settle, and keep
widening the flat area as new walkers appear.

### Telling your people what to do

Four icons at the bottom left set what all your walkers do:

| Behaviour | What walkers do |
|---|---|
| Settle | Look for flat land and build. Use this most of the time. |
| Go to magnet | Walk to your papal magnet (the ankh). The first to arrive becomes your leader. |
| Gather | Join together into fewer, stronger walkers. |
| Fight | Seek out the enemy. |

### Divine powers

Each needs enough mana, and some worlds do not allow all of them.

| Power | Mana | Effect |
|---|---|---|
| Raise or lower land | 10 | One step at the point you click. |
| Place papal magnet | 200 | Moves your ankh. Needs a leader. |
| Earthquake | 2,500 | Shakes the land under the close-up. |
| Swamp | 5,000 | Scatters swamp near the point you click. Walkers who step in are lost. |
| Knight | 7,500 | Turns your leader into a knight who hunts the enemy and burns their homes. |
| Volcano | 10,000 | Raises a mountain under the close-up. |
| Flood | 40,000 | Lowers the whole world one step. Anyone on the shore drowns. |
| Armageddon | 80,000 | Everyone marches to the middle of the world for a final battle. |

Earthquake and volcano strike wherever the close-up is looking, so move the view over
the enemy first.

### Looking around

- Click the map to move the close-up there, or use the arrow keys.
- Click **?** and then a person to follow them. The shield at the top right then shows
  their side, weapon and strength.
- The go-to icons jump to your magnet or leader, the next fight, or your next
  settlement or knight.

### Winning and moving on

When a world is decided, a score box appears. After a win the lord congratulates you
and you move on. A good score skips several worlds at once. Every world has a name,
and you can go straight to any of them: in the world box click **NEW GAME** and type
the name.

### Custom games

Two icons at the lower right of the board open the option boxes:

- **The scales**: left-click for evil's options, right-click for your own. Tick what
  that side may do, and set the computer's aggression and rate.
- **The globe**: game setup. From here you can switch between conquest and custom
  game, set the rules of the land (game options), save or load, move to the next
  map, restart the map, or surrender.

The options can be looked at in any game but only changed in a custom game.

### Keys

| Key | Action |
|---|---|
| Arrow keys | Scroll the close-up |
| Esc | Back to the normal raise/lower pointer |
| P | Pause |
| M / S | Music / sound effects on and off |
| Return | Press the left button of a dialog box |
| ⌘N | New game (the world box) |
| ⌘S / ⌘O | Save / open a saved game |
| ⌘F | Full screen |
| [ and ] | Previous / next world (not in the original) |
| Tab | Switch between the original graphics and the HD graphics (not in the original; HD is an early preview) |

## How the port was made

The disk holds the game as a memory image of the running program. It was disassembled
with Ghidra, and each routine was rewritten in Swift. For the simulation, the original
routines were then run inside Ghidra's emulator, their results recorded, and the Swift
code tested against those recordings.

| Folder | Contents |
|---|---|
| `Sources/PopulousAssets` | Reads the disk image and decodes graphics, sounds, levels and text |
| `Sources/PopulousCore` | The game rules: world, people, powers, computer opponent, conquest, saves |
| `Sources/PopulousRender` | Draws the game screen, shield and dialogs; works out what the close-up shows (`CloseUp`) |
| `Sources/PopulousHD` | Puts frames on the screen with Metal, and draws the close-up in 3D for the HD graphics |
| `Sources/PopulousAudio` | The original's music and effects sequencer |
| `Sources/Populous` | The Mac app |
| `Sources/popassets` | A command-line tool to list, extract and render the disk's contents |
| `re/` | Ghidra scripts, recovered symbol names and the analysis script |

To regenerate the disassembly and the reference recordings (needs
`brew install ghidra`, and takes tens of minutes):

```sh
re/analyze.sh "path/to/Populous.adf"
```

## Legal

*Populous* is the work of Bullfrog Productions, published by Electronic Arts. This
project contains none of the original game's code or data, and is not affiliated with
or endorsed by either company. You need your own copy of the game to use it.
