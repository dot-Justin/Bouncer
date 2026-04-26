# Bouncer

**Kodi service addon** that intercepts video playback and requires a PIN to continue if the content's rating is in your blocked list.

Designed for **Kodi 21 Omega on Android TV** with streamed content from addons like Umbrella and TMDbHelper, but works with any Kodi video source.

---

## Features

- Blocks playback by MPAA movie rating (G, PG, PG-13, R, NC-17, NR)
- Blocks playback by US TV rating (TV-Y, TV-Y7, TV-G, TV-PG, TV-14, TV-MA, TV-NR)
- PIN prompt uses a D-pad-navigable number pad — fully usable with a standard Android TV remote
- Optional session unlock: one correct PIN unlocks for N minutes (configurable 5–60, default off)
- Three-method rating detection chain:
  1. `VideoPlayer.MPAA` infolabel (works with Umbrella, TMDbHelper, library content)
  2. JSON-RPC `Player.GetItem` fallback
  3. TMDb API fallback (optional, requires free API key)
- Configurable behaviour when rating cannot be determined (Allow or Block)
- Verbose debug logging toggle

---

## Installation

1. Download the latest `service.bouncer-x.x.x.zip` from [Releases](../../releases)
2. In Kodi: **Settings → Add-ons → Install from zip file**
3. Select the downloaded zip
4. Configure via **Settings → Add-ons → Bouncer**

---

## Configuration

| Setting | Default | Description |
|---|---|---|
| Enable Bouncer | On | Master switch |
| PIN Code | `1234` | Hidden numeric PIN |
| Minutes unlocked after correct PIN | `0` | `0` = require PIN every title |
| Movie/TV rating toggles | See below | Per-rating block switches |
| If rating cannot be determined | Block | Allow or Block unrated/unknown content |
| TMDb API Key | _(empty)_ | Optional. Improves detection for streamed content |
| Debug logging | Off | Verbose log output to Kodi log |

**Default blocked ratings:** R, NC-17, NR, TV-MA, TV-NR

---

## How It Works

Bouncer registers an `xbmc.Player` subclass that listens for `onAVStarted` — the event that fires when actual audio/video data is flowing. It waits 1500 ms for Kodi's infolabels to populate (required for streaming addons), then reads the content rating and pauses playback if the rating is blocked.

The PIN dialog uses `INPUT_NUMERIC`, which renders as an on-screen number pad on Android TV. Any other dialog type requires a physical keyboard.

---

## Compatibility

- Kodi 21 Omega (Python 3)
- Android TV / Fire TV
- Works with: Umbrella, TMDbHelper, local library, and any addon that passes `mpaa` via `ListItem.setInfo()`

---

## License

MIT
