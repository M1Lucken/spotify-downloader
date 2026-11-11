# Local setup

This is the path that downloads a large playlist on a new machine. Commands match this
checkout. You do not need a separate shell script to start the PO-token server or to pass a
JavaScript runtime: `spotdl download` and `spotdl easy` do that.

You are responsible for what you download. Do not commit cookies, Spotify client secrets, or
`.spotipy` token caches.

## 1. Requirements

- Python 3.11 or newer (this project allows up to 3.15)
- Node.js 20 or newer, on `PATH` as `node` (`node -v`)
- ffmpeg (`ffmpeg -version`, or `spotdl --download-ffmpeg`)
- git, the first time spotdl downloads the PO-token server

Deno is used only when `node` is missing. Install it with `spotdl --download-deno` or from
[Deno's install guide](https://docs.deno.com/runtime/getting_started/installation/).

## 2. Create a Spotify app

The old shared Spotify client id is rate-limited immediately (`Retry will occur after: 86400`).
spotdl refuses to start a download while that id is still in the config.

1. Open <https://developer.spotify.com/dashboard> and create an app that uses the Web API.
2. Add this redirect URI exactly: `http://127.0.0.1:9900/`
3. Copy the client id and client secret. Keep them on this computer only.

Generate a config file and edit it:

```bash
spotdl --generate-config
```

The command prints the config path. On macOS and Windows that is `~/.spotdl/config.json`. On
Linux it may be `~/.config/spotdl/config.json`. Set:

```json
"client_id": "your client id",
"client_secret": "your client secret"
```

`spotipy` 2.26 or newer is already required by this project. After you change credentials, delete
the cached token or spotdl keeps using the old one:

```bash
rm -f ~/.spotdl/.spotipy ~/.config/spotdl/.spotipy
```

Some playlists return 403 or 429 until you sign in with your own Spotify account:

```bash
spotdl download --user-auth "https://open.spotify.com/playlist/..."
```

`--user-auth` opens `http://127.0.0.1:9900/`. That URI has to be on the app you just created.

## 3. Install this checkout

From the repository root, with the same interpreter you will use to run spotdl:

```bash
python -m pip install -e '.[default]'
```

That installs spotdl, `yt-dlp[default]` (which brings `yt-dlp-ejs` for YouTube's JavaScript
challenges), and `bgutil-ytdlp-pot-provider` 1.3.1 into that interpreter's plugin path.

With uv:

```bash
uv sync --extra default --group dev
uv run spotdl --help
```

## 4. PO-token server

YouTube binds a PO token to each video, so a single pasted token cannot cover a playlist. The
bgutil HTTP server creates tokens on `127.0.0.1:4416`. When that port is closed, `spotdl
download` and `spotdl easy`:

1. Use `SPOTDL_POT_SERVER` if you set it (repo root or `server/` directory).
2. Otherwise use `~/.spotdl/bgutil-ytdlp-pot-provider` when it is already there.
3. Otherwise clone tag `1.3.1` of
   <https://github.com/Brainicism/bgutil-ytdlp-pot-provider> into the spotdl config folder.
4. Run `npm ci` and `npx tsc` in `server/` if `build/main.js` is missing.
5. Run `node build/main.js` and wait until port 4416 accepts connections.

The server keeps running after spotdl exits. The next download sees the open port and does not
start a second copy. Output goes to `pot-server.log` in the spotdl config folder.

To build and start it yourself instead:

```bash
git clone --single-branch --branch 1.3.1 \
  https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git
cd bgutil-ytdlp-pot-provider/server
npm ci && npx tsc && node build/main.js
```

Or use Docker, and leave spotdl's auto-start off so the two do not fight over the port:

```bash
docker run --name bgutil-provider -d --init -p 4416:4416 \
  brainicism/bgutil-ytdlp-pot-provider
SPOTDL_NO_POT_SERVER=1 spotdl download "https://open.spotify.com/track/..."
```

`SPOTDL_NO_POT_SERVER=1` skips auto-start. If both the HTTP server and the plugin's script
provider exist, yt-dlp prefers HTTP.

## 5. YouTube cookies

Export a Netscape `cookies.txt` from a YouTube session:

1. Open a private window and sign in to YouTube. A throwaway Google account is safer. A personal
   account can be restricted.
2. In that same window, open only <https://www.youtube.com/robots.txt>.
3. Export `youtube.com` cookies in Netscape format.

The steps are documented at
<https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies> and
<https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp>.

Save the file outside this repository, for example `~/youtube-cookies.txt`. Pass it with
`--cookie-file`. Never commit it.

## 6. Try one track, then a playlist

Confirm Node is visible (`node -v` shows 20 or newer). Then one YouTube Music URL, which checks
cookies, the PO-token server, and signature solving:

```bash
spotdl download --threads 1 --bitrate 320k \
  --cookie-file ~/youtube-cookies.txt \
  --output "/tmp/spotdl-test/{artists} - {title}.{output-ext}" \
  "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
```

Then one Spotify track (replace `TRACK_ID`):

```bash
spotdl download --threads 1 --bitrate 320k \
  --cookie-file ~/youtube-cookies.txt \
  --output "/tmp/spotdl-test/{artists} - {title}.{output-ext}" \
  "https://open.spotify.com/track/TRACK_ID"
```

Then the playlist. `--threads 1` is the stable setting for a long job. `--overwrite skip` is
already the default, so a second run leaves finished files alone and continues with the rest.
On macOS, spotdl runs `caffeinate -i` so sleep does not kill the process.

```bash
spotdl download --threads 1 --bitrate 320k \
  --cookie-file ~/youtube-cookies.txt \
  "https://open.spotify.com/playlist/PLAYLIST_ID"
```

If a run stops early, the log lists **failed** tracks separately from **skipped** ones. Songs
with `No results found` are misses, not a crash. They are listed at the end and are not retried.
Other failures are retried for a few rounds. Running the same command again is safe.

spotdl passes `--js-runtimes node` unless you already set `--yt-dlp-args` to choose a runtime
(`--js-runtimes` or `--no-js-runtimes`).

### Without the terminal

```bash
spotdl easy
```

This opens <http://127.0.0.1:8810/> on this computer only. The form asks for a folder (the
current directory), a link, bitrate (320k), threads (1), and the cookie file. Start uses the same
checks as the CLI. If the Spotify app or the cookie file is missing, the page explains what to
do instead of showing a stack trace.

## 7. When something fails

**Spotify says 429 or `Retry will occur after: 86400`.** The shared client id is in the config,
or an old token is cached. Create your own app (step 2), edit `client_id` and `client_secret`,
delete `.spotipy`, and retry. For a playlist that still returns 403 or 429, add `--user-auth`.

**"You are blocked by YouTube Music" or the preflight search is empty.** That check searches
YouTube Music for `"a"` and used to abort the whole run. It is often a false block. spotdl logs
a warning and continues, using `youtube` when `youtube-music` was the only provider. You can also
pass `--audio youtube`.

**yt-dlp says signature solving failed, or `AudioProviderError: YT-DLP download error` with an
empty cause.** Node is missing or older than 20, so YouTube only offered storyboard images. The
error text includes the yt-dlp cause and, when no runtime exists, tells you to install Node.js
20+ or Deno. `pip install 'yt-dlp[default]'` (already part of this project) supplies
`yt-dlp-ejs`. See <https://github.com/yt-dlp/yt-dlp/wiki/EJS> and
<https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide>.

**The PO-token server did not start.** Read `pot-server.log` in the spotdl config folder. Node
20+, git, and a successful `npm ci` are required the first time. If you already run the Docker
image, set `SPOTDL_NO_POT_SERVER=1`.
