# Deckhand + Arcadia: audit and plan

Audit date 2026-11-12, covering both apps as released (Deckhand 4.3.1, Arcadia 0.1.1) plus the Arcadia 0.1.2 branch.
Aim: **simple to use and effective**: a new Steam Deck owner gets from nothing to playing without reading a manual, and every
failure says what to do next.

How the evidence was gathered: static checks (flake8, mypy, shellcheck), test coverage, a security read of everything that
downloads or runs something, and screenshots of both programs at 1280x800 (Deckhand rendered through its test harness; Arcadia
built here and run under a virtual display with fake ROMs). **Nothing was tested on a real Deck or with a real controller.**
That is the biggest gap and is Phase 1.

---

## 1. Where things stand

| | Deckhand | Arcadia |
|---|---|---|
| What it is | PyQt6 installer for Windows programs, stores, streaming, add-ons | ES-DE fork (C++) launcher for retro games, plus shell/Python setup tools |
| Size | 9.5k lines Python, `core.py` alone 2.5k | ES-DE fork; our C++ is mostly the Setup screen (345 lines) and the menu components; 700 lines of setup tools |
| Tests | 235 pass; **85% coverage** | 66 setup-tool checks pass; **no C++ tests** (CI only builds) |
| Lint | flake8: 16 style nits. mypy: 42 warnings, mostly Qt "may be None" | shellcheck: 7 cosmetic warnings |
| Releases | v4.3.1, automatic when the version file changes on `main` | v0.1.1 (0.1.2 on a branch), automatic the same way |
| Look | Dusk and Cloud, shared design language | Cloud and Dusk, the reference for it |

Weakest tested areas in Deckhand: Add-ons page 48%, gamepad input 53%, background threads 59%, `app.py` 74%.

## 2. What the audit found

### Security and trust (fix first)
No shell injection patterns anywhere (`shell=True`, `eval`, `pickle` are absent). But downloads are checked unevenly:

| Download | Checked how | Gap |
|---|---|---|
| Deckhand self-update | SHA-256 from the same GitHub release | Fine for corruption; a hijacked repo defeats it (normal) |
| GE-Proton | SHA-512 file from the release | Good |
| Nexus Launcher | SHA-512 file **if present** | If the file is missing or can't be read, it silently falls back to "looks like an ELF program" |
| Arcadia add-on | GitHub's SHA-256 digest **if present** | Same silent fallback |
| Store installers, VC++/.NET fixes | HTTPS to the vendor | Vendors publish no checksums; accepted |
| Decky installer script | Header contains "Decky" | Runs as-is (by design) |
| **Arcadia self-update** | **Host allow-list and file size only** | **No checksum, and it then runs `install.sh` from the downloaded archive** |

### Bugs and rough edges seen on screen
- Arcadia's sorter files `.md` (Mega Drive) into a new `genesis/` folder even when the library already uses `megadrive/`, which splits a collection.
- Arcadia's docs screenshots are stale: `setup.png` still shows 6 steps, the old "Sections" hint and clipped labels (confirmed by viewing it).
- Body text in both apps looks bold because only Nunito SemiBold and heavier are bundled.

### Fixed during this audit (already done)
Rail navigation in both apps (no L1/R1 on a vertical menu), clipped Setup labels and rail footer, Start/Back buttons hanging off the
Covers card, truncated colour-scheme names, carousels repeating a game when a console has few, empty Installed page with no way
forward, unreadable chosen-section icon, release build bug (`VERSION` variable renamed the AppImage).

### Structural
- Deckhand commits its 62 MB binary to git: **183 MB repo after 10 versions**, and GitHub already warns on every push.
- `core.py` (2.5k lines) mixes prefixes, Steam shortcuts, installing and discovery.
- Deckhand's binary bundles Qt, which needs glibc 2.34 or newer (measured on Qt's core library; Python itself needs only 2.17). Fine for current SteamOS; worth stating in the README.
- No automated check that screens still fit (only the rail has one).

---

## 3. The plan

Each item says **why**, **size** (S under half a day, M a day or two, L a week) and **done when**.

### Phase 0: ship what is finished (today)
1. Merge Arcadia `claude/pensive-hamilton-urh2dr` (releases 0.1.2) and cut Deckhand 4.3.2 (rail footer fix). **S**
2. Put both releases on a real Deck and run the checklist in Phase 1. **S**

### Phase 1: prove it on real hardware (before adding anything)
Nothing so far ran on a Deck. Do this once, write down what breaks.
- Fresh-Deck path: `get.sh` → Deckhand opens in Game Mode with a controller → Start checklist → install one real `.exe` → it appears in Steam and launches.
- Arcadia path: install the AppImage from Deckhand → first start → Setup → sort a real ROM → launch it in an emulator.
- Controller only, no touch, no keyboard: every screen reachable, every dialog dismissible with B.
- Suspend and resume during a download and during an install.
- Write `docs/DECK-TEST.md` (one page, tick boxes) so every release can be checked the same way. **M**, done when the checklist exists and its first run is recorded.

### Phase 2: trust (security fixes above) — mostly done
1. ~~**One verified-download helper**~~ Done for the add-ons: a published checksum is required (unreadable or not listing the file = stop), a release with none is said out loud, GE-Proton and Arcadia refuse to proceed without one. Left: the store installers and Windows fixes (vendors publish no checksums) and Decky's script. **M**
2. ~~**Arcadia self-update verifies SHA-256**~~ Done in Arcadia 0.1.3 (merge pending), with tests that tamper with the AppImage and the extras archive. **S**
3. ~~Print the version of everything downloaded into the install log.~~ Done for the add-ons: `logs/downloads.log`. **S**
Done when: a corrupted or swapped download in tests is refused for every download type.

### Phase 3: correctness
1. ~~Sorter: reuse an existing alias folder~~ Done in Arcadia 0.1.3 (merge pending). **S**
2. Regenerate all Arcadia docs screenshots from the built program with a script (`scripts/screenshots.sh`, the recipe used in this audit) so they never go stale. **M**
3. Fix the 16 flake8 nits and the few real mypy findings (a few places that call uninstall with a value mypy thinks may be `None`, probably false alarms but cheap to make explicit; one wrong type in the updater's source list). **S**

### Phase 4: make it cheap to keep right
1. **Stop committing the binary.** Publish only as release assets; `get.sh` and the updater read the latest release. Shrinks the repo and removes the push warning. Needs care: the updater currently reads `bin/deckhand.json` from `main`. **M**
2. CI: add flake8 and shellcheck (warn only at first); keep coverage at or above 85%. **S**
3. Add Deckhand UI tests for the Add-ons actions (Arcadia and Nexus open/update/uninstall), the weakest area. **M**
4. A screen-fit test for each Deckhand page at 1280x800 (like the rail test) so clipped text fails CI. **M**
5. Split `core.py` into `prefix`, `steam`, `installer`, `discovery` modules without changing behaviour; tests already cover most of it. **L**
6. Arcadia: C++ is only built, never tested. Add the screenshot script to CI as an artifact so a visual regression is at least visible. **M**

### Phase 5: polish (simple and readable)
1. Bundle Nunito Regular so body text is lighter (check the licence and size first). **S**
2. One shared **look setting**: if Arcadia is on Dusk, Deckhand follows (and the reverse) via a tiny shared file. **S**
3. A `--doctor` command in each app that prints what the app can see (Proton, runtime, Steam state, free space, versions) for bug reports. **S**
4. Every error dialog ends with a next step ("Try again", "Open the log", "Use the normal install"). Audit the error strings. **M**
5. Start checklist: show estimated time and size per step. **S**

### Phase 6: features, in order of value (decide after Phase 1)
Deckhand
1. **Pick the Proton version per program** (and remember it); the top fix when one game won't run. **M**
2. **Install several programs in one go** from a folder. **M**
3. Show a game's ProtonDB rating before installing. **M**
4. Offer SteamGridDB art by default once a key is set. **S**
5. Update the add-ons (Arcadia, Nexus, GE-Proton) from one "Updates" screen. **M**

Arcadia
1. **Cloud-save setup wizard** (rclone and Google Drive): the biggest friction in Setup today. **L**
2. Per-game emulator choice in the game menu. **M**
3. Scraper defaults that work without an account, and plain guidance for the account. **M**

Suite
1. Deckhand's Start checklist offers Arcadia's Setup status, and Arcadia's Setup shows Deckhand's. **S**
2. One "Suite update": Deckhand updates itself and Arcadia in one step. **M**

## 4. Principles (how we keep it simple)
- Every screen readable at 1280x800, controller only. A screen that needs touch or a keyboard is a bug.
- Defaults work; options are for the few who want them. If a step can be automatic and safe, it is.
- Every failure names the next action.
- Nothing is called done until it ran on the real program: screenshots for UI, a faked server for downloads, and a real Deck for Phase 1.
- Release by bumping the version on `main`; nothing else to remember.

## 5. Recommended order
Phase 0 → **Phase 1** → Phase 2 → Phase 3 → Phase 4 (items 1, 2, 3) → Phase 5 → Phase 6.
If time is short: Phase 0, Phase 1, Phase 2 items 1 and 2, Phase 3 item 1. Those cover the real risks.
