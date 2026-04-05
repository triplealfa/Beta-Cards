# Beta Cards

Beta Cards is a standalone desktop app for building decks and playing your solo digital collectible card game.

Use `beta_cards.py` with `PySide6`.

## Desktop app features

- Load cards from a chosen `cards` folder
- Support image-only cards and JSON-defined cards
- Include a Card Maker tab that generates matching JSON files next to card images
- Build decks with duplicate copies allowed
- Require a configurable minimum number of cards to save a valid deck
- Save decks into the app data folder as JSON files
- Draw random cards from a chosen saved deck
- Track a discard pile
- Provide a built-in stopwatch and countdown timer

## Card data format

The recommended format is:

- `my-card.png`
- `my-card.json`

Example:

```json
{
  "id": "my-card",
  "name": "My Card",
  "value": "7",
  "faction": "Crystal",
  "set_name": "Core Set",
  "card_number": "0001",
  "artist_name": "Artist Name",
  "card_author": "Card Author",
  "effect": "Do something useful.",
  "image": "my-card.png"
}
```

The app now treats `value` as the main numeric field. Older JSON files using `cost` are still read as a fallback.
The Card Maker tab supports these metadata fields as well: `faction`, `set_name`, `card_number`, `artist_name`, and `card_author`.

## Options

- The minimum deck size is configurable in the `Options` tab.
- The default minimum deck size is `30`.

## Run it

1. Install Python 3.
2. Install dependencies with `pip install -r requirements.txt`.
3. Run `python beta_cards.py`.

## Build a Windows release

1. Install PyInstaller with `python -m pip install pyinstaller`.
2. Run `powershell -ExecutionPolicy Bypass -File .\build_release.ps1`.
3. The packaged app will be created at `dist\BetaCards\BetaCards.exe`.

## Data locations

The app stores data outside the project folder in an OS-appropriate app data directory:

- Windows: `%APPDATA%/BetaCards`
- macOS: `~/Library/Application Support/BetaCards`
- Linux: `~/.local/share/BetaCards`

Inside that folder:

- `config.json` stores the last chosen `cards` folder
- `decks/*.json` stores saved decks

## Notes

- The default cards folder is `cards/`.
- The app has been run locally during development.

## License

- Beta Cards is licensed under `GPL-3.0-or-later`.
- See `LICENSE.md`.
- See `THIRD_PARTY_NOTICES.md` for dependency notices.
