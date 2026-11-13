# Deckhand + Arcadia: audit and plan

Second full audit, 2026-11-13, of both apps as released (**Deckhand 4.6.1**, **Arcadia 0.1.4**) plus the fixes it led to
(Arcadia 0.1.5 on its branch). The first audit (2026-11-12, Deckhand 4.3.1 / Arcadia 0.1.1) is in this file's history.

Aim, unchanged: **simple to use and effective**. A new Steam Deck owner gets from nothing to playing without reading a
manual, and every failure says what to do next.

How the evidence was gathered:
- Deckhand: the full test suite with coverage, flake8, a security read of everything that runs a command, downloads or
  handles a password, and screenshots of every screen at 1280x800 in both looks.
- Arcadia: its setup-tool tests, shellcheck and flake8 on the tools, an **AddressSanitizer build** driven through the
  first-start screen, Settings (every section and its More options), Setup, the Emulators screen with a live install,
  the tool window, the game list and quitting, plus a search of the C++ for the bug class of the crash reported from a
  Deck this week.
- A real crash report from a Steam Deck (a SIGSEGV in Arcadia 0.1.2), traced to source by disassembling the released
  binary.

**Still not tested on a real Deck with a controller** beyond that crash report. That remains the biggest gap.

---

## 1. Where things stand

| | Deckhand 4.6.1 | Arcadia 0.1.4 (0.1.5 on its branch) |
|---|---|---|
| Tests | 346 pass, **86 % coverage** (85 % at the last audit) | setup-tool checks all pass (about 120); **no C++ tests** |
| Lint | flake8: 38 long lines, a few spacing nits (unused imports fixed) | shellcheck: 11 minor warnings; flake8: spacing only |
| Types | mypy can't run: this PyQt6 build's stubs don't parse | n/a |
| Memory safety | n/a | ASan: one use-after-free, **fixed** (below) |
| Releases | automatic when `bin/deckhand.json` changes on `main` | automatic when `VERSION` changes on `main` |

Weakest tested areas in Deckhand: the Add-ons page (53 %), gamepad input (55 %), background threads (59 %), `app.py` (73 %).

## 2. What the audit found

### Fixed during this audit (on the branches, Arcadia as 0.1.5)
| Finding | Effect | Fix |
|---|---|---|
| **Crash from a Deck:** with no games, changing the colours (or theme) reloaded the views, which read a theme that had never been looked up (`sCurrentTheme`), then went to the first console of an empty list | Arcadia closed on a fresh install | released in 0.1.4: no-games reloads do nothing; the theme is looked up when needed |
| More no-games crashes of the same kind: Collections (now in Look and feel's main list), "Group custom collections", the UI mode, `goToStart`, and the save step of every settings card all read the first console of an empty list | the same crash from other settings | settings are saved and nothing navigates when there are no consoles |
| **Use-after-free:** quitting (or Steam closing Arcadia) while a Setup tool ran left the tool's thread writing into the freed tool window | crash or memory corruption on exit | the thread writes only to state it shares with the window |
| **Nobody says there's an update:** the Deck that crashed was still on 0.1.2 | old bugs linger silently | Arcadia checks once a day in the background and says "Arcadia X is available"; a switch in Advanced turns it off |
| Two-line notifications lost their line break when short | hints ran into the message | the popup measures each line |
| Unused imports left from the sidebar removal, an ambiguous name | lint noise | removed |

### Open
**Correctness and robustness**
- Setup and the Emulators screen wait for their background status read when you leave them (it shells out to
  `flatpak info` for each emulator). On a real Deck leaving at once could freeze the screen for a second or two. Use the
  shared-state pattern the tool window now has.
- No C++ tests: the ASan run that found the bugs above was manual. The no-games paths have no automated check.
- New English strings (badges, Emulators screen, update notice, hints) aren't in the translation catalogs; other
  languages show English for them.

**Structural**
- Deckhand still commits its 62 MB binary: the git pack is **183 MB** and grows by about 60 MB a release.
- `deckhand/core.py` is still 2.5k lines mixing prefixes, Steam shortcuts, installing and discovery.
- Arcadia's docs screenshots (`docs/screenshots`) predate the hub, dialogs, Emulators screen and simpler settings.
- No lint in either CI.

**UX (seen on screen)**
- Arcadia's game screen shows four "unknown" chips (year, genre, players, last played) on every game with no info yet.
  The theme places them at fixed positions, so hiding the empty ones needs a layout change.
- Arcadia's welcome screen: the background behind it and the help bar keep the old colours after a colour change.
- Deckhand's Add-ons list shows Arcadia as "Installed" with no version and no "update available".

### Security
- Deckhand: no `shell=True`, `eval`, `pickle` or disabled TLS checks; every URL is HTTPS. The Decky admin password lives
  in a private folder (mode 700, file 600) and is wiped afterwards. Add-on downloads need a published checksum (Nexus
  SHA-512, Arcadia SHA-256 digest, GE-Proton SHA-512); store installers and Windows fixes have none to check (vendors
  don't publish them).
- Arcadia: Update Arcadia checks the AppImage and the extras archive against GitHub's SHA-256 digests; RetroArch cores
  are checked against libretro's published CRC-32 (integrity, not authenticity: HTTPS from libretro is the trust).
  Downloads go only to an allow-list of hosts.

---

## 3. The plan

Each item says **why**, **size** (S under half a day, M a day or two, L a week) and **done when**.

### Now
1. **Release Arcadia 0.1.5** (the crash fixes and the update notice). **S**. Done when it's published and on the Deck.
2. **Prove it on the Deck** with the checklist below, starting from a fresh install with no games. **S**

### Next: keep it from breaking again
1. **No-games smoke test in CI**: start Arcadia with an empty ROM folder under Xvfb (ASan build), change Colours,
   Collections grouping and UI mode, quit. Fails on any sanitizer report. **M**
2. Same harness for the tool window: quit while a tool runs. **S** (after 1)
3. Setup and Emulators screens: don't block when left early (shared state, like the tool window). **S**
4. Translation catalogs: add the new strings (`locale/`), keep English as the fallback. **S**

### Then: cheaper to maintain
1. **Stop committing Deckhand's binary**: publish it only as a release asset; `get.sh` and the updater read the latest
   release (the updater reads `bin/deckhand.json` from `main` today). **M**
2. flake8 and shellcheck in CI, warnings only at first; keep Deckhand's coverage at or above 86 %. **S**
3. Tests for the Add-ons page actions (open, update, uninstall for Arcadia, Nexus, Decky). **M**
4. Regenerate Arcadia's docs screenshots with a script. **M**
5. Split `core.py` into `prefix`, `steam`, `installer` and `discovery`, without changing behaviour. **L**

### Polish
1. Arcadia game screen: show the info chips only when there's information, or one "No info yet · Covers and info" chip.
   **M**
2. Deckhand Add-ons: Arcadia's version and "update available" (Deckhand knows the latest release). **S**
3. Welcome screen: the background and help bar follow a colour change too. **S**

### Features (decide after the Deck test)
Unchanged from the first audit: Proton version per program, installing several programs from a folder, ProtonDB
ratings, SteamGridDB art by default, one Updates screen in Deckhand; Arcadia's cloud-save wizard and per-game emulator
choice; a suite-wide update.

## 4. Deck checklist (one page, every release)
- Fresh Deck, no games: install Arcadia from Deckhand (Add-ons). Open it. Change Colours, then Theme, then
  Collections → Group custom collections. It must not close.
- Setup → Emulators with a few ROMs: X installs what's needed (RetroArch and cores); a game then starts.
- Start a Setup tool and close Arcadia from Steam (STEAM → Exit game) while it runs: no crash report.
- Deckhand in Game Mode, controller only: every screen reachable; every dialog closes with B; notifications readable.
- Suspend and resume during a download.

## 5. Principles
- Every screen readable at 1280x800, controller only.
- Defaults work; options are for the few who want them.
- Every failure names the next action.
- Nothing is called done until it ran on the real program: screenshots for UI, a faked server for downloads, a
  sanitizer build for Arcadia's C++, and a real Deck for the checklist.
- Release by bumping the version on `main`; nothing else to remember.
