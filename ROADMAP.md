# Deckhand + Arcadia: audit and plan

Third audit, 2026-11-15, of both apps as they go out as **Deckhand 4.7.0** and **Arcadia 0.1.6**, after a session of
testing on a real Steam Deck (SteamOS 3.9, Desktop Mode; Game Mode only briefly). The earlier audits (2026-11-12 and
2026-11-13) are in this file's history.

Aim, unchanged: **simple to use and effective**. A new Steam Deck owner gets from nothing to playing without reading a
manual, and every failure says what to do next.

How the evidence was gathered: every kind of install, update and uninstall run for real on the Deck (Flathub apps,
stores, streaming, GE-Proton, Arcadia's emulators with a test ROM); response times and frame rates measured on the
Deck; Deckhand's suite (397 tests, 88 % coverage) and Arcadia's setup-tool checks; flake8 and shellcheck; an
AddressSanitizer build of Arcadia driven with no games (`tools/smoke_test.sh`, now in CI).

---

## 1. Where things stand

| | Deckhand 4.7.0 | Arcadia 0.1.6 |
|---|---|---|
| Tests | 397 pass, **88 % coverage** (gate: 86 %) | setup-tool checks all pass; **ASan smoke test with no games in CI** |
| Lint | flake8: only long lines left; shellcheck: clean (both report in CI, warnings only) | shellcheck: notes only (the one real warning fixed); flake8: spacing |
| Releases | change `VERSION` + `RELEASE_NOTES.md`, push to `main`: GitHub tests, builds, publishes | the same |
| Speed on a Deck | window in **0.35–0.47 s** (was 1.6 s); a button press handled in ~5 ms | window in 0.33 s; 90 FPS, ~30 when idle |

Weakest tested areas in Deckhand: `app.py` (75 %); gamepad input is now 84 % (was 55 %).

## 2. Done since the last audit
- **Everything Deckhand installs can be uninstalled from Deckhand** (store and streaming apps, browsers it installed,
  GE-Proton, Deckhand itself; Arcadia's tools go with Arcadia). A Steam shortcut left by an uninstall while Steam was
  open is removed by itself the next time Steam is closed.
- **Games and Tools** replace Stores and Add-ons: each section holds what its name says, under headings.
- **Deckhand starts four times faster**: unpacked once into `~/.cache/deckhand`, not on every start.
- **Update notifications** for Deckhand and its add-ons, in Game Mode too (a systemd user timer).
- Install fixes found on the Deck: GE-Proton's ARM build was installed; Electron apps quit when started from an editor;
  emulators duplicated their runtimes per user (2.4 GB); Windows stores now wait for the Steam Linux Runtime.
- Arcadia: Setup and Emulators never wait when left; the game screen hides facts a game doesn't have; the welcome
  screen follows a colour change; idle frame rate ~30; its own screens are translatable (the extraction had been
  stopping at the first "…": 932 strings now).
- Releases built by GitHub for both apps, the same way; no binary in git any more; CI actions on Node 24.

## 3. Open

**Fixes**
- ~~Cloud saves need a terminal~~ **Done:** Connect gets rclone (checked) and signs in with the browser (Desktop Mode).
- ~~Screens at Desktop Mode's 1.35× scale~~ **Done:** every page and dialog rendered at that size; the Menu overlapped
  and now takes as many columns as it needs.
- ~~Setup's checklist can't be translated~~ **Done:** the tool sends codes, Setup turns them into translated words.
- ~~Uninstalling Deckhand itself while Steam is open leaves its own shortcut~~ **Done:** the app stays (out of the menu)
  until Steam is closed; its timer then removes the shortcut, the app and itself.
- Apps get set up as they install (done): emulators find `~/Emulation/bios` and get starter controls; Protontricks,
  Bottles and itch see the SD card. Not yet: BIOS links for Flycast, xemu, Cemu keys, RPCS3 firmware.

**Not yet tried on a real Deck**
- Game Mode end to end, controller only (both apps), and the update notification as a Steam toast.
- Decky Loader install (needs the admin password); cloud save sync (needs a Google sign-in); the other five Windows
  stores (they use the path the EA app took).

**Maintenance**
- Deckhand is English only (Arcadia has 25 languages).
- `deckhand/core.py` is 2.6k lines mixing prefixes, Steam shortcuts, installing and discovery.
- The git history still holds ~183 MB of old Deckhand binaries (only a history rewrite removes them).
- ~~Arcadia's docs screenshots predate the hub~~ **Done:** `tools/screenshots.sh` takes them (and the Emulators screen).

**Features people will look for**
- Deckhand: ~~Proton version per program~~ (done: Installed → Proton version); several installers at once; ProtonDB ratings before installing; SteamGridDB art
  by default; one Updates screen.
- Arcadia: ~~guided cloud-save setup~~ (done, in Desktop Mode); ~~per-game emulator choice hard to find~~ (done: game
  options → Emulator for this game).
- Both: one update for the pair.

## 4. The plan
Each item: why, size (S under half a day, M a day or two, L a week).

### Now
1. **Game Mode pass** on the Deck with the checklist below, controller only. **S**
2. **Cloud saves without a terminal**: ship rclone with Arcadia's extras and run its Google sign-in on screen (a code to
   enter on a phone). **M**

### Next
1. Deckhand's dialogs and lists at 1.35× scale: screenshots, then fix what overlaps. **S**
2. Arcadia's checklist from C++ (or the tools emit keys the C++ translates), so it can be translated. **S**
3. Tests for gamepad input (55 %). **M**
4. ~~Regenerate Arcadia's docs screenshots with a script~~ (done). **M**

### Then
1. Split `core.py` into `prefix`, `steam`, `installer` and `discovery`, without changing behaviour. **L**
2. Deckhand translations (Qt's own tools; English stays the fallback). **M**
3. Features above, in the order people ask for them; Proton per program first. **M** each

## 5. Deck checklist (one page, every release)
- Fresh Deck, no games: install Arcadia from Deckhand (Games). Open it. Change Colours, then Theme, then
  Collections → Group custom collections. It must not close. (CI runs this with ASan; this is the real-Deck check.)
- Setup → Emulators with a few ROMs: X installs what's needed (RetroArch and cores); a game then starts.
- Start a Setup tool and close Arcadia from Steam (STEAM → Exit game) while it runs: no crash report.
- Deckhand in Game Mode, controller only: every screen reachable; every dialog closes with B; notifications readable.
- Uninstall something from Deckhand in Game Mode; after the next restart of Steam its shortcut is gone.
- Suspend and resume during a download.

## 6. Principles
- Every screen readable at 1280x800, controller only.
- Defaults work; options are for the few who want them.
- Every failure names the next action.
- Everything we install can be uninstalled from the app that installed it.
- Nothing is called done until it ran on the real program: screenshots for UI, a faked server for downloads, a
  sanitizer build for Arcadia's C++, and a real Deck for the checklist.
- Release by bumping the version on `main`; nothing else to remember.
