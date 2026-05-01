# Bouncer

**Kodi service addon** that intercepts video playback and requires a PIN to continue if the content's rating is in your blocked list.

Designed for **Kodi 21 Omega on Android TV** with streamed content from addons like Umbrella and TMDbHelper, but works with any Kodi video source.

---

## Features

- Blocks playback by MPAA movie rating (G, PG, PG-13, R, NC-17, NR)
- Blocks playback by US TV rating (TV-Y, TV-Y7, TV-G, TV-PG, TV-14, TV-MA, TV-NR)
- PIN prompt uses a D-pad-navigable number pad — fully usable with a standard Android TV remote
- Post-PIN access menu with `Allow one time`, optional timed unlock, and whitelist actions
- Hierarchical whitelist for movies, shows, seasons, and episodes
- Whitelist management in addon settings, including per-entry removal and full clear
- Three-method rating detection chain:
  1. `VideoPlayer.MPAA` infolabel (works with Umbrella, TMDbHelper, library content)
  2. JSON-RPC `Player.GetItem` fallback
  3. TMDb API fallback (optional, requires free API key)
- Configurable behaviour when rating cannot be determined (Allow or Block)
- Verbose debug logging toggle

---

## Installation

### Via dotJustin's Kodi Repository (recommended)

Installs cleanly and receives automatic updates.

1. **Enable unknown sources** — Settings → System → Add-ons → Unknown sources → On
2. **Install the repository addon** — download [`repository.dotjustin-1.0.0.zip`](https://github.com/dot-Justin/kodi-repo/releases/latest) and install via Settings → Add-ons → Install from zip file
3. **Install Bouncer** — Settings → Add-ons → Install from repository → dotJustin's Kodi Repository → Services → Bouncer → Install
4. Configure via **Settings → Add-ons → Bouncer**

### Manual (zip sideload)

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
| Whitelist | _(empty)_ | Manage or clear whitelisted movies, shows, seasons, and episodes |
| If rating cannot be determined | Block | Allow or Block unrated/unknown content |
| TMDb API Key | _(empty)_ | Optional. Improves detection for streamed content |
| Debug logging | Off | Verbose log output to Kodi log |

**Default blocked ratings:** R, NC-17, NR, TV-MA, TV-NR

---

## How It Works

Bouncer registers an `xbmc.Player` subclass that listens for `onAVStarted` — the event that fires when actual audio/video data is flowing. It waits 1500 ms for Kodi's infolabels to populate (required for streaming addons), then evaluates access in this order:

1. One-time allow for the exact current item
2. Whitelist match
3. Blocked rating check
4. Unrated fallback policy

If playback is blocked and the correct PIN is entered, Bouncer shows a follow-up action menu so you can allow the current item once, unlock for the configured session duration, or whitelist the current movie, episode, season, or show.

The PIN dialog uses `INPUT_NUMERIC`, which renders as an on-screen number pad on Android TV. Any other dialog type requires a physical keyboard.

---

## Compatibility

- Kodi 21 Omega (Python 3)
- Android TV / Fire TV
- Works with: Umbrella, TMDbHelper, local library, and any addon that passes `mpaa` via `ListItem.setInfo()`

---

## License

MIT
