# Deckhand design language

Deckhand shares its look with its sister app, [Arcadia](https://github.com/PossiblyPengu/arcadia). Arcadia's
[`DESIGN.md`](https://github.com/PossiblyPengu/arcadia/blob/main/DESIGN.md) is the reference for colour, type, shape,
focus and motion; this file only says how Deckhand applies it. `deckhand/theme.py` holds the values.

## What is shared

- **Colour:** the same two schemes. **Dusk** (pastels on deep violet) is Deckhand's default, because it is mostly used in Game
  Mode; **Cloud** (light pastels) is Arcadia's. ☰ Menu → Light look or Dark look switches between them. Pink is the one accent, for the focus ring,
  the main button and progress.
- **Type:** Fraunces SemiBold for titles and the wordmark, Nunito for everything else. Both are bundled in
  `deckhand/fonts` (SIL Open Font License).
- **Shape:** rounded cards, pill buttons and chips, a plum (cream in Dusk) pill for the chosen section in the left rail.
- **Words:** sentence case ("Found on this Deck"), short and plain. No capitals for emphasis.
- **Controller first:** everything reachable with the D-pad, A/B, L1/R1, and button hints along the bottom.

## What is Deckhand's own

- The rail has six sections (Start, Install, Stores, Stream, Add-ons, Installed) where Arcadia's settings have a rail of
  setting groups.
- **Status pills** on every card say what is on the Deck ("Installed", "Not installed", "Done").
- Services, stores and add-ons keep their own logos and brand colours; Arcadia's pastels are for Arcadia's consoles.

## Keeping the pair in step

When Arcadia's colours, fonts or shapes change, update `theme.py` to match and note it in both repositories' commit
messages. The two apps point to each other: Arcadia is first in Deckhand's Add-ons, and Arcadia's Setup has a
"Windows games and stores" step that installs or opens Deckhand.
