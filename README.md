# Royal Ripper

A Windows GUI for yt-dlp for Australian free-to-air catch-up: ABC iView, 10, SBS On Demand, 7plus and 9Now.
Successor to the Tas Rip Pack (formerly TasRip, and before that MediaRipper). WEB-DL only, no DRM.

**Get it:** download the zip from [Releases](../../releases), unzip anywhere, double-click `RoyalRipper.bat`.
The first start downloads every tool it needs (yt-dlp, ffmpeg, N_m3u8DL-RE, aria2c, Streamlink, mkvmerge, SubtitleEdit).

**Update:** Royal Ripper checks this page for a new release once a day and offers to install it (or Tools > Check for a Royal Ripper update). By hand: unzip the new version over your folder, then run `Update RoyalRipper.bat`.

**Something not working?** Press "Report a problem" in the app - it makes a report zip and opens a new [issue](../../issues) here for it.

**Coming from TasRip?** This is the same app, renamed. See [New in 1.0.8](#new-in-108) below.

**Contents:**
[New in 1.1.0](#new-in-110) ·
[New in 1.0.8](#new-in-108) ·
[First run](#first-run) ·
[Updating](#updating-to-a-new-version) ·
[Search shows](#search-shows) ·
[Downloading](#downloading) ·
[The options](#the-options) ·
[Signing in](#signing-in-sbs-and-10-need-it) ·
[Services](#services) ·
[Australian sites](#australian-sites-au-site-recipes-on-by-default) ·
[Record live TV](#record-live-tv) ·
[Watch list](#watch-list) ·
[History](#history) ·
[Dark mode](#dark-mode) ·
[Settings](#settings) ·
[Site engines](#site-engines) ·
[Plugins](#plugins-tab) ·
[Test channels](#test-channels) ·
[Problems](#problems)

## New in 1.1.0

- **.nfo files are now off by default** for new installs. If you already use them, nothing changes - your setting is kept. To turn them on, pick *Scene (text)* or *Kodi / Jellyfin (XML)* next to **.nfo** on the main screen.
- **Less to download, less on disk:** tools Royal Ripper never actually used are gone.
  - **wget** - only there for old Tas Rip Pack .bat scripts, and the site it came from (eternallybored.org) was being flagged by Malwarebytes.
  - **jq** - also only for old Tas Rip Pack .bat scripts.
  - **mkvextract, mkvinfo and mkvpropedit** - came along with mkvmerge but were never used.

  Copies of these left in `bin` by older versions are deleted automatically when you update. If you still run an old Tas Rip Pack .bat that needs wget or jq, put `wget.exe` / `jq.exe` somewhere on your PATH instead.

## New in 1.0.8

- **TasRip is now Royal Ripper.** The files are renamed too: `RoyalRipper.bat`,
  `Update RoyalRipper.bat`, `royalripper.py` and `RoyalRipper.exe`. Coming from TasRip,
  either way works:
  - **Fresh install (cleanest):** unzip Royal Ripper into a new folder next to your TasRip
    folder and double-click `RoyalRipper.bat`. On the first start it copies your settings,
    archive, watch list, history, queue, saved 10 sign-in, site engines and tools across
    from the TasRip folder. Once you're happy, delete the TasRip folder.
  - **Or just overwrite:** close TasRip, unzip over your TasRip folder, then double-click
    `Update RoyalRipper.bat` (not the old `Update TasRip.bat`). It removes `TasRip.bat`,
    `Update TasRip.bat`, `tasrip.py` and `TasRip.exe`, and rebuilds the exe as
    `RoyalRipper.exe` if you used one.

  Either way, point any shortcuts at `RoyalRipper.bat` instead of `TasRip.bat`.
- **Royal Ripper updates itself from GitHub:** once a day it checks the
  [releases](../../releases) for a newer version. When there is one it shows what's new, and
  Update now downloads it, closes Royal Ripper, puts the new files in the folder (settings,
  archive, watch list, history and downloads are kept), runs the updater and starts it again.
  Or open the release page, choose Later, or Skip this version.
  Tools > Check for a Royal Ripper update checks straight away; Settings > General turns the
  daily check off.
- **Reports go to GitHub:** after "Report a problem" (or a failed row's report), Royal Ripper
  offers to open a new [GitHub issue](../../issues) with the version, Windows version and the
  failed link filled in, and opens the folder with the report zip so you can drag it in.
  You need a free GitHub account, and issues are public - the zip has passwords, email and
  your user name removed.
- **At once** (main options row): download 1, 2 or 3 episodes at the same time.
- **The queue is remembered:** close Royal Ripper with downloads waiting or failed and they're back
  next time - press Start to carry on.
- A failed row now says "double-click for a report". Double-click it (or right-click >
  Make a report for this item) for a small zip with everything logged for that one
  download - passwords and your user name removed. Send that instead of the whole log.
- **Tools keep themselves up to date:** yt-dlp daily, the others weekly (ffmpeg monthly). Each
  new version is tested before it replaces the old one; if it doesn't run, the old one is
  put back. Settings > Tools to turn it off; Tools > Update all the other tools now.
- **Antivirus helper** (Tools menu, and pops up by itself when a tool vanishes or Windows says
  a file "contains a virus"): one button asks Windows Defender to trust the Royal Ripper folder,
  another re-downloads what was removed. Notes for Avast/Norton/etc. too.
- **Mac leftovers** (`._` files, `.DS_Store`, `__MACOSX`) are cleaned out on every start, so a zip
  passed through a Mac can't break the engines again.
- **Tools > Sign-in status:** one line per channel showing whether Royal Ripper can see your sign-in
  (and the 7plus login cookie). Double-click a channel to sign in.
- First start shows a window with a progress bar for each tool being downloaded.
- **Subtitle check:** after a download, the subtitles are checked against the episode length.
  If they stop early, have a big gap or are very thin, the row says "subtitles look
  incomplete" and the log says why (it's usually the site's own file).
- **Leaving soon:** when a site says when an episode expires, the log shows "Available until
  ...". If one fails and leaves within a week, you get a notification and the row shows
  the date. Most sites don't publish this, so you'll only see it sometimes.
- **Speed schedule** (Settings > General): e.g. limit to 2M between 07:00 and 23:00, full
  speed overnight.
- **Tools > Export my setup / Import a setup:** moves settings, watch list, history, archive and
  site engines to another PC in one zip. Your 10 password is never included.

## First run

1. Unzip anywhere (a USB stick is fine).
2. Double-click `RoyalRipper.bat`. That's it - it sets itself up:
   - no usable Python on the PC? it downloads a portable one into `bin\python`
     (nothing is installed on the PC, so it travels with the folder)
   - Royal Ripper then downloads every tool it uses into `bin`: yt-dlp, ffmpeg, N_m3u8DL-RE,
     aria2c and Streamlink, plus the Tas Rip Pack's other tools in the background:
     mkvmerge (MKVToolNix), SubtitleEdit (needs .NET Framework 4.8 - built into
     Windows 10/11) and 7zr (7-Zip's unpacker, used for MKVToolNix)

   The first start takes a few minutes; later starts are instant.

Everything comes from the official sources; nothing large is bundled.

**Coming from TasRip or MediaRipper?** Unzip Royal Ripper next to your old TasRip or MediaRipper
folder and its settings, archive.txt, site engines and tools are copied across on the first
start. Or unzip Royal Ripper over the old folder and run `Update RoyalRipper.bat`.

**Want a single RoyalRipper.exe?** Run `build_exe.bat` once. `RoyalRipper.bat` then opens the exe instead.
After an update, `RoyalRipper.bat` notices the exe is older than `royalripper.py` and rebuilds it by
itself (once, a few minutes) - so nobody gets stuck on an old version.

**Old Tas Rip Pack scripts:** `RoyalRipper.bat` makes an `Extractors` folder that points at `bin`, and
the tools sit where the scripts expect them (yt-dlp.exe, aria2c.exe, ffmpeg.exe,
mkvmerge.exe, N_m3u8DL-RE.exe, `SubEdit\SubtitleEdit.exe`). Drop the old .bat files
into the Royal Ripper folder and they run as before - including the iView and 10 bats that use
yt-dlp-iView-beta.exe and yt-dlp-modified.exe.

## Updating to a new version

Royal Ripper checks GitHub once a day and offers to update itself (see New in 1.0.8). To do it by hand:

1. Close Royal Ripper.
2. Unzip the new version over your Royal Ripper folder (say Yes to replacing files).
3. Double-click `Update RoyalRipper.bat`. It keeps your settings, archive, watch list, history
   and site engines, tidies away old files, gets any missing tools, updates yt-dlp,
   rebuilds RoyalRipper.exe if you use one, then starts Royal Ripper.

`Update RoyalRipper.bat full` also re-downloads ffmpeg, N_m3u8DL-RE, aria2c and Streamlink.

## Search shows

*Top of the Downloads tab.*

Type a show name, pick "All 5 channels" or one channel, press Search. The results window
lists matching shows from iView, 10, SBS, 7plus and 9Now. Double-click one (or press Pick
episodes) to choose episodes, or Add whole show to queue, or Add to watch list.
10, SBS, 7plus and 9Now use each channel's own search; iView (which has no public search)
uses a web search for iView show pages, which is also the backup if a channel's search fails.

## Downloading

1. Paste links (one per line) - episodes, seasons, show pages, playlists, podcasts.
2. Pick options, then "Add to queue". Each item keeps the options it was added with.
3. Start. The Status column says what's happening in plain English. When a row says
   "Done ✓", double-click it to see the file. If one fails, it says why and what to do.

Right-click a row: play the file, show it in its folder, retry, remove, copy link.

**Pick episodes ...** - paste a season, show, playlist, podcast or iView channel page and press
this instead of Add to queue: tick just the episodes (or shows) you want.

## The options

*Main screen, as in 0.7.5.*

- **Mode / Quality / Container / Audio format / Browser cookies**
- **Prefer H.264/AAC** - streams that play on everything (never re-encodes)
- **Subtitles** - embedded, plus .srt files
- **Embed metadata / thumbnail, Whole playlist**
- **Save thumbnail (.jpg)** - the episode picture saved next to the video (Plex/Jellyfin/Kodi use it)
- **.nfo** (Off by default)
  - *Scene (text):* a scene-style info file - release name, show/episode,
    air date, video/audio/subtitle details, size, runtime, plot.
    Only written for untouched downloads (WEB-DL): files you have
    Royal Ripper re-encode don't get one.
  - *Kodi / Jellyfin (XML):* the metadata file media centres read.
  - *Off:* none.
- **Skip already downloaded** - remembers finished items in archive.txt
- **File naming** - includes "Plex / Jellyfin": `TV Shows\Show\Season 01\Show - S01E01 - Title`,
  `Movies\Title (Year)\Title (Year)`, `Audio\Show\Title`
- **Speed limit, Fragments at once** (4 default; 8-16 faster), **Use aria2c, AU site recipes**
- **Ask if below** (720p default) - before an AU-channel download starts, the log says what
  you'll get ("About to download: ... | 720p | 7plus signed-in stream"). If
  it's below this, a box asks: Download anyway or Skip (tick "same for the
  rest of this run" to stop it asking). Skipped items show why and can be
  retried later. Watch-list checks never wait on the box - they skip.
- **Save to, Extra yt-dlp args**

## Signing in (SBS and 10 need it)

Press "Sign in to a site" and pick the site.

- **10:** 10 can't use a browser sign-in. Type your 10 email and password and press "Sign in to
  10" - it checks them with 10 straight away. Tick "Remember on this PC" so you only do
  it once (the password is encrypted so only your Windows account can read it, and it's
  never put in problem reports). "Forget" removes it.
- **Others:** follow the three steps (open the site in Firefox, sign in and close Firefox, Check
  my sign-in). Royal Ripper only reads Firefox's sign-in; it never sees that password.
  Once Firefox is signed in to 7plus, SBS, 9Now or iView, Royal Ripper uses that sign-in for
  those sites automatically (unless you picked another browser under Browser cookies).
  7plus only gives 720p to signed-in accounts (otherwise 504p/540p). It needs 7plus's
  own login cookie in Firefox - "Check my sign-in" checks for exactly that one. If it
  says the cookie is missing, sign out of 7plus in Firefox, sign in again, close Firefox.
  Chrome / Edge / Brave mostly refuse to share their sign-in now - use Firefox.

## Services

| Service | Notes |
| --- | --- |
| ABC iView, 10, SBS On Demand, 7plus, 9Now | AU site recipes (below) |
| ABC Kids / ABC Entertains etc. | any show page works; for a channel page use Pick episodes |
| Tubi | movies and whole series |
| ABC Listen, SBS Audio, podcasts | saved as audio from the show's podcast feed - paste the program page; Royal Ripper finds the feed by itself |
| Pluto TV | yt-dlp's Pluto support is currently broken (and Pluto now wants an account) - if a Pluto plugin appears, install it from the Plugins tab |
| NZ: TVNZ+ / ThreeNow | TVNZ+ is DRM-only; NZ services need an NZ connection |

Plus anything yt-dlp handles (YouTube, Vimeo, news sites ...). DRM-protected titles can't
be downloaded.

## Australian sites ("AU site recipes", on by default)

iView, 10, SBS, 7plus and 9Now are handled the way the Tas Rip Pack did it:

- iView and 10 try the 1080p stream first, falling back to the standard one
- subtitles are fetched separately and muxed in, tagged English; SBS forced subtitles
  get their own track, flagged forced; none are switched on by default
- 7plus uses N_m3u8DL-RE
- season / series URLs and 10 show pages are split into one row per episode

These downloads are always saved as MKV (only MKV keeps the subtitle flags), built with
mkvmerge like the pack did (ffmpeg if mkvmerge isn't there; switch in Tools & engines).
Subtitles ffmpeg can't convert (DFXP/TTML) are converted with SubtitleEdit.

## Record live TV

*Record live ... button, or Tools.*

Paste a channel's live page or a stream address (.m3u8), how many minutes, and start now or
at a set time. Royal Ripper finds the stream with yt-dlp, and falls back to Streamlink if yt-dlp
can't. Saved in the Live folder. Keep Royal Ripper open until it's done. Right-click to stop.

## Watch list

*Tab.*

Add a show, season, channel or podcast. Royal Ripper checks it once a day (or press Check all
now) and downloads only episodes that aren't in archive.txt. "Only titles containing"
narrows it down. Tick "Skip the episodes already out" to only get new ones from now on.

## History

*Tab.*

Everything downloaded, searchable: play, show in folder, copy link, download again.

## Dark mode

Click "Dark mode" on the menu bar (or Settings > General). It's remembered.

## Settings

*Tools > Settings, or the Settings button.*

- **General** - dark mode, notification + sound when the queue finishes, pause the queue when
  the save drive is low on space (5 GB default), daily watch list check,
  Windows certificate checks
- **Encoding** - re-encode after downloading (off by default): H.264 / H.265 / AV1, quality,
  speed, resize, audio. "Upscale to 1080p" turns 720p into 1080p (Lanczos +
  light sharpening) - cleaner on a big TV, but it can't add real detail and
  files get bigger. CPU unless you tick "Use the graphics card"; "Check my
  hardware and recommend" tests this PC and suggests the best option
- **Tools & engines** - yt-dlp Nightly (the default - newest site fixes) / Stable, daily update on/off, each tool's version with
  Reinstall, site engines, AU recipe on/off per site

## Site engines

*The pack's custom yt-dlp builds - included.*

`bin\engines` comes with two of the Tas Rip Pack's builds, used automatically:

| Engine | Used for | |
| --- | --- | --- |
| yt-dlp-iView-beta.exe | ABC iView | reads iView's current API; plain yt-dlp still uses the old one |
| yt-dlp-modified.exe | 10 | the pack's 10 build; signs in with your 10 email + password like the 10playV4 bat |

If an engine won't start (blocked or damaged by antivirus, or a "._" file left by a Mac),
Royal Ripper says so in the log and uses the normal yt-dlp for that download instead.
Everything else uses the normal, always-updated yt-dlp. Settings > Tools & engines >
Site engines shows and changes this (pick "standard yt-dlp" to stop using one). You can drop
other builds into `bin\engines` too - only add ones you trust. Engines don't load plugins.

## Plugins tab

Lists every yt-dlp plugin on GitHub. Install, update and remove from there.
The SBS fix (`bin\yt-dlp-plugins\sbsfix2026`) is included.

## Test channels

*Button bottom right, or Tools > Test all channels.*

Checks all 5 channels in one go without downloading anything: whether you're signed in,
finds a current episode, and shows the best quality you'd get (including whether iView/10
have 1080p and whether 7plus gives you the signed-in 720p stream). "Copy results" puts it
on the clipboard to paste to whoever is helping you.

## Problems

- Press "Report a problem" (bottom right). It saves a zip in the Royal Ripper folder with your
  settings, tool versions and recent logs - email, passwords, tokens and your Windows user
  name are removed. Then it offers to open a new [GitHub issue](../../issues) for it: drag the
  zip into the issue, say what happened, and press Create. Or send the zip to whoever is
  helping you.
- "SSL: CERTIFICATE_VERIFY_FAILED": usually antivirus HTTPS scanning or a filtered network.
  Royal Ripper switches to Windows' own certificate checks by itself and retries. If it says
  "expired" or "not yet valid", fix the PC's clock.
- Downloads suddenly failing: Tools > Update yt-dlp now (Royal Ripper uses yt-dlp's nightly
  builds by default, which get site fixes first).
- 10 "Not available from your location": if it won't play in the browser either, an
  Australian VPN server is the only known fix.
- Tools > Diagnose a URL lists what a site offers (qualities, DRM, subtitles).
