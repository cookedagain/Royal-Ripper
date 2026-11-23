#!/usr/bin/env python3
"""TasRip (formerly MediaRipper) - a Windows GUI for yt-dlp, successor to the Tas Rip Pack.

Everything lives next to this file (or the built app):
    bin/            yt-dlp, ffmpeg, ffprobe, aria2c, N_m3u8DL-RE (fetched on demand)
    downloads/      default output folder
    settings.json   remembered options
    archive.txt     list of already-downloaded IDs (optional)
    bin/yt-dlp-plugins/   extractor plugins installed from the Plugins tab
    bin/engines/    your own custom yt-dlp builds, used for the sites you assign (engines.json)
"""
import glob
import html
import json
import os
import platform
import queue
import re
import shlex
import shutil
import signal
import ssl
import subprocess
import stat
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except ImportError:  # lets the core logic be imported/tested without a display
    tk = None

APP_NAME = "TasRip"
VERSION = "1.0.8"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
OS_NAME = "Windows" if IS_WIN else "macOS" if IS_MAC else "Linux"
ARM = platform.machine().lower() in ("arm64", "aarch64", "armv8l")
EXE = ".exe" if IS_WIN else ""
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW: no console flashing up on Windows


def _base_dir():
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    d = os.path.dirname(os.path.abspath(sys.executable))
    marker = ".app" + os.sep + "Contents" + os.sep + "MacOS"
    if IS_MAC and marker in d:  # inside TasRip.app: use the folder the app sits in
        d = os.path.dirname(d[:d.index(marker) + 4])
    return d


BASE_DIR = _base_dir()
BIN_DIR = os.path.join(BASE_DIR, "bin")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
ARCHIVE_FILE = os.path.join(BASE_DIR, "archive.txt")
LOG_DIR = os.path.join(BASE_DIR, "logs")  # one file per day, emails/tokens/usernames removed
# The standalone yt-dlp auto-loads <its folder>/yt-dlp-plugins/<package>/yt_dlp_plugins/...
PLUGIN_DIR = os.path.join(BIN_DIR, "yt-dlp-plugins")
ENGINE_DIR = os.path.join(BIN_DIR, "engines")
ENGINES_FILE = os.path.join(BASE_DIR, "engines.json")
# Suggested site for a custom build, from a word in its file name.
ENGINE_HINTS = (("9now", "9now.com.au"), ("iview", "abc.net.au"), ("abc", "abc.net.au"),
                ("modified", "10.com.au"),  # the Tas Rip Pack's yt-dlp-modified.exe was its 10 Play build
                ("7plus", "7plus.com.au"), ("10play", "10.com.au"), ("tenplay", "10.com.au"),
                ("sbs", "sbs.com.au"), ("tubi", "tubitv.com"))
FRAGMENTS = ["1", "4", "8", "16"]
GITHUB_API = "https://api.github.com"
PLUGIN_CACHE = os.path.join(BASE_DIR, "plugins_cache.json")
PLUGIN_QUERIES = ("topic:yt-dlp-plugins", "topic:yt-dlp-plugin", "topic:ytdlp-plugin",
                  "yt-dlp plugin in:name", "ytdlp plugin in:name")
# The catalogue leaves out DRM-decryption tooling.
PLUGIN_SKIP = ("drm", "decrypt", "widevine", "playready", "cdm")
CACHE_MAX_AGE = 24 * 3600

_GH = "https://github.com/yt-dlp/"
if IS_WIN:
    YTDLP_URL = _GH + "yt-dlp/releases/latest/download/" + ("yt-dlp_arm64.exe" if ARM else "yt-dlp.exe")
    FFMPEG_URLS = [_GH + "FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-%s-gpl.zip"
                   % ("winarm64" if ARM else "win64")]
    # Backup source if GitHub fails: gyan.dev, the Windows builds linked from ffmpeg.org.
    FFMPEG_BACKUP = [] if ARM else ["https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"]
elif IS_MAC:
    YTDLP_URL = _GH + "yt-dlp/releases/latest/download/yt-dlp_macos"
    _MR = "https://ffmpeg.martin-riedl.de/redirect/latest/macos/%s/release/" % ("arm64" if ARM else "amd64")
    FFMPEG_URLS = [_MR + "ffmpeg.zip", _MR + "ffprobe.zip"]
    FFMPEG_BACKUP = []
else:
    YTDLP_URL = _GH + "yt-dlp/releases/latest/download/" + ("yt-dlp_linux_aarch64" if ARM else "yt-dlp_linux")
    FFMPEG_URLS = [_GH + "FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-%s-gpl.tar.xz"
                   % ("linuxarm64" if ARM else "linux64")]
    FFMPEG_BACKUP = []
# yt-dlp's official nightly builds get site fixes days or weeks before a stable release (default).
USE_NIGHTLY = True


def ytdlp_url():
    if USE_NIGHTLY:
        return YTDLP_URL.replace("/yt-dlp/yt-dlp/releases/", "/yt-dlp/yt-dlp-nightly-builds/releases/")
    return YTDLP_URL
ARIA2_WIN_FALLBACK = ("https://github.com/aria2/aria2/releases/download/release-1.37.0/"
                      "aria2-1.37.0-win-64bit-build1.zip")
NM3U8_ASSET = ("win-arm64" if ARM else "win-x64") if IS_WIN else \
              ("osx-arm64" if ARM else "osx-x64") if IS_MAC else ("linux-arm64" if ARM else "linux-x64")
# How to get a tool that has no official download for this OS.
SYSTEM_INSTALL = {
    "aria2c": "brew install aria2" if IS_MAC else "sudo apt install aria2  (or your distro's package manager)",
    "ffmpeg": "brew install ffmpeg" if IS_MAC else "sudo apt install ffmpeg",
}

PROG_TAG = "MRPROG"
PROG_TEMPLATE = ("download:" + PROG_TAG + "|%(progress._percent_str)s|%(progress._speed_str)s"
                 "|%(progress._eta_str)s|%(info.resolution)s|%(info.title)s")

QUALITIES = ["Best", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
CONTAINERS = ["mp4", "mkv", "webm"]
AUDIO_FORMATS = ["mp3", "m4a", "opus", "flac", "wav"]
BROWSERS = ["None", "firefox", "chrome", "chromium", "edge", "brave", "opera", "vivaldi"] + \
    (["safari"] if IS_MAC else [])

_SHOW = "%(series,playlist_title,uploader|Unknown)s"
PLEX = "Plex / Jellyfin  (TV Shows + Movies folders)"
TEMPLATES = {
    "Title": "%(title)s [%(id)s].%(ext)s",
    PLEX: "%(title)s [%(id)s].%(ext)s",  # downloaded like this, then moved into the Plex layout
    "Channel / Title": "%(uploader|Unknown)s/%(title)s [%(id)s].%(ext)s",
    "Playlist / 01 - Title": "%(playlist_title|Singles)s/%(playlist_index|00)02d - %(title)s.%(ext)s",
    "TV: Show / Season 01 / Show - S01E01 - Title": (
        _SHOW + "/Season %(season_number|01)02d/" + _SHOW +
        " - S%(season_number|01)02dE%(episode_number,playlist_index|00)02d - %(episode,title)s.%(ext)s"),
}

DEFAULTS = {
    "outdir": os.path.join(BASE_DIR, "downloads"),
    "mode": "Video", "quality": "1080p", "container": "mp4", "audio_fmt": "mp3",
    "template": "Title", "compat": True, "subs": True, "meta": True, "thumb": False,
    "playlist": True, "archive": False, "cookies": "None", "rate": "", "extra": "", "frags": "4",
    "aria2": False, "recipes": True, "syscerts": False, "nightly": True,
    "advanced": False, "autoupdate": True, "recipes_off": "",
    "encode": False, "enc_codec": "H.264", "enc_quality": "Balanced", "enc_speed": "Fast",
    "enc_resize": "Keep", "enc_audio": "Copy", "enc_gpu": False, "enc_vendor": "",
    "dark": False, "notify": True, "sound": True, "min_free_gb": "5", "watch_daily": True,
    "nfo": "Scene (text)", "thumbfile": True, "search_in": "All 5 channels", "cfg_ver": "2",
    "use_mkvmerge": True, "min_quality": "720p", "parallel": "1", "tools_autoupdate": True,
    "sched_on": False, "sched_rate": "2M", "sched_from": "07:00", "sched_to": "23:00",
}
HISTORY_FILE = os.path.join(BASE_DIR, "history.jsonl")
WATCH_FILE = os.path.join(BASE_DIR, "watchlist.json")
LAST_WATCH_FILE = os.path.join(BASE_DIR, "bin", ".last-watch-check")
STREAMLINK_DIR = os.path.join(BIN_DIR, "streamlink")
LAST_UPDATE_FILE = os.path.join(BASE_DIR, "bin", ".last-ytdlp-update")
AUTO_UPDATE_EVERY = 24 * 3600


# ----------------------------------------------------------------- core logic

def tool_path(name):
    """Prefer the copy in bin/, fall back to the system PATH (brew/apt installs). None if missing."""
    local = os.path.join(BIN_DIR, name + EXE)
    if name == "SubtitleEdit":  # bin\SubEdit, the same place the Tas Rip Pack kept it
        found = glob.glob(os.path.join(BIN_DIR, "SubEdit", "SubtitleEdit" + EXE)) + \
            glob.glob(os.path.join(BIN_DIR, "SubEdit", "**", "SubtitleEdit" + EXE), recursive=True)
        found = [f for f in found if not os.path.basename(f).startswith(".")]
        return found[0] if found else None
    if name == "streamlink" and IS_WIN:  # the portable build keeps its own Python beside it
        found = glob.glob(os.path.join(STREAMLINK_DIR, "*", "bin", "streamlink.exe")) + \
            glob.glob(os.path.join(STREAMLINK_DIR, "bin", "streamlink.exe"))
        if found:
            return found[0]
    return local if os.path.isfile(local) else shutil.which(name)


def make_executable(path):
    if not IS_WIN and os.path.isfile(path):
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def open_path(path):
    """Open a folder or file in the system's file manager / default app. False if that failed."""
    try:
        if IS_WIN:
            os.startfile(path)
        else:
            subprocess.Popen(["open" if IS_MAC else "xdg-open", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def split_extra(text):
    """Split a free-text argument string, keeping Windows backslashes intact."""
    out = []
    for tok in shlex.split(text, posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def build_args(ytdlp, url, o, login=None):
    """Build the full yt-dlp command line for one URL from an options dict.

    login is an optional (username, password) pair for sites that need an account.
    """
    a = [ytdlp, "--newline", "--no-colors", "--encoding", "utf-8", "--ignore-config",
         "--progress-template", PROG_TEMPLATE,
         "-P", o["outdir"], "-o", TEMPLATES.get(o["template"], TEMPLATES["Title"]),
         "--windows-filenames", "-N", o.get("frags") if o.get("frags") in FRAGMENTS else "4",
         "--yes-playlist" if o["playlist"] else "--no-playlist"]
    if os.path.isfile(os.path.join(BIN_DIR, "ffmpeg" + EXE)):
        a += ["--ffmpeg-location", BIN_DIR]
    a += aria2_args(o) + tls_args() + plugin_args(ytdlp)

    if o["mode"] == "Audio":
        a += ["-f", "ba/b", "-x", "--audio-format", o["audio_fmt"], "--audio-quality", "0"]
    else:
        sort = []
        if o["quality"] != "Best":
            sort.append("res:" + o["quality"].rstrip("p"))
        if o["compat"]:  # prefer streams that play everywhere; never forces a re-encode
            sort += ["vcodec:h264", "acodec:aac"]
        a += ["-f", "bv*+ba/b", "--merge-output-format", o["container"]]
        if sort:
            a += ["-S", ",".join(sort)]
        if o["subs"]:
            # SBS labels its tracks inconsistently, so take every track it offers there.
            langs = "all,-live_chat" if "sbs.com.au" in url.lower() else "en.*"
            # Convert to SRT first (embeds far more reliably than raw WebVTT); WebM can only hold
            # WebVTT, so use that there. Because --write-subs is given, the files are also kept.
            conv = "vtt" if o["container"] == "webm" else "srt"
            a += ["--write-subs", "--sub-langs", langs, "--convert-subs", conv, "--embed-subs"]

    if o["meta"]:
        a += ["--embed-metadata"]
    if o["thumb"] and thumb_supported(o):
        a += ["--embed-thumbnail"]
    if o.get("thumbfile"):
        a += ["--write-thumbnail", "--convert-thumbnails", "jpg", "--no-write-playlist-metafiles"]
    if o["archive"]:
        a += ["--download-archive", ARCHIVE_FILE]
    if o["cookies"] != "None":
        a += ["--cookies-from-browser", o["cookies"]]
    if o["rate"].strip():
        a += ["-r", o["rate"].strip()]
    if o["extra"].strip():
        a += split_extra(o["extra"])
    if o.get("items"):
        a += ["--playlist-items", str(o["items"])]
    if o.get("match"):
        a += ["--match-filters", "title~='(?i)%s'" % re.escape(o["match"]).replace("'", "\\'")]
    if login and login[0]:
        a += ["--username", login[0], "--password", login[1]]
    return a + ["--", url]


AUDIO_WORK = ".tasrip-audio-tmp"
# What each finished download reports back (its final path, and the details Plex naming needs).
SAVED_FIELDS = ("%(.{filepath,id,title,series,season_number,episode_number,episode,release_year,"
                "release_date,upload_date,playlist_title,uploader,channel,description,duration,extractor_key,"
                "webpage_url,genres,categories,age_limit,timestamp})j")
PARTIAL_EXTS = (".part", ".ytdl", ".temp", ".frag")


def move_finished(src, dest, log):
    """Move completed files from an audio job's working folder into the save folder.

    Partial downloads stay behind so a retry can resume them. Returns the number moved.
    """
    moved = 0
    for folder, _, files in os.walk(src):
        for f in files:
            if f.lower().endswith(PARTIAL_EXTS) or ".part-" in f.lower():
                continue
            rel = os.path.relpath(os.path.join(folder, f), src)
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                os.replace(os.path.join(folder, f), target)
                moved += 1
            except OSError as e:
                log("Couldn't move %s into the save folder: %s" % (rel, e))
    for folder, _, _ in sorted(os.walk(src), key=lambda w: -len(w[0])):  # tidy empty folders
        try:
            os.rmdir(folder)
        except OSError:
            pass
    return moved


def load_engines():
    """{site domain: exe file name in bin/engines}"""
    try:
        with open(ENGINES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return {str(k).lower(): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_engines(engines):
    with open(ENGINES_FILE, "w", encoding="utf-8") as f:
        json.dump(engines, f, indent=2, sort_keys=True)


# Sites that changed address: old name -> current name. 10 Play became 10 (10.com.au) in mid-2025.
SITE_ALIASES = {"10play.com.au": "10.com.au"}


def canonical_site(name):
    name = (name or "").lower()
    for old, new in SITE_ALIASES.items():
        if name == old or name.endswith("." + old):
            return name[:-len(old)] + new
    return name


def host_matches(host, site):
    host, site = canonical_site(host), canonical_site(site)
    return host == site or host.endswith("." + site)


def url_host(url):
    try:
        return (urllib.parse.urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return ""


def engine_file_ok(path):
    """True if path looks like a real program, not a macOS "._" stub, an empty file or a
    half-deleted antivirus leftover (those make Windows say "not compatible"/"16-bit")."""
    name = os.path.basename(path)
    if name.startswith((".", "_")) or not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) < 1024 * 1024:  # real yt-dlp builds are 15 MB+
            return False
        if path.lower().endswith(".exe"):
            with open(path, "rb") as f:
                return f.read(2) == b"MZ"
        return True
    except OSError:
        return False


BAD_ENGINES = set()  # engines that wouldn't start this session


def engine_for(url, engines=None):
    """(site, exe path) of the custom yt-dlp build assigned to this URL's site, or None."""
    host = url_host(url)
    engines = load_engines() if engines is None else engines
    for site in sorted(engines, key=len, reverse=True):  # most specific site wins
        if host_matches(host, site):
            path = os.path.join(ENGINE_DIR, os.path.basename(engines[site]))
            if not IS_WIN and path.lower().endswith(".exe"):
                continue  # a Windows build can't run here; standard yt-dlp handles the site
            if engine_file_ok(path) and path not in BAD_ENGINES:
                return site, path
    return None


def suggest_site(exe_name):
    low = exe_name.lower()
    for word, site in ENGINE_HINTS:
        if word in low:
            return site
    return ""


def aria2_args(o):
    """Hand stream fragments to aria2c, as the old Tas Rip Pack did.

    Only builds of yt-dlp from 2025 and earlier still do this (e.g. the custom site engines);
    current yt-dlp quietly keeps using its own fragment downloader, so it's always safe to pass.
    """
    path = tool_path("aria2c") if o.get("aria2") else None
    return ["--downloader", "m3u8:" + path, "--downloader", "dash:" + path] if path else []


# ------------------------------------------------------------- site recipes
# Ported from the Tas Rip Pack scripts. A recipe site is downloaded as: read the page with
# yt-dlp, try a higher-quality stream address first (falling back to the standard one), fetch
# subtitles separately, then mux everything into one file with tidy track languages/flags.
RECIPES = {
    "abc.net.au": {"name": "ABC iView", "swaps": [("720.m3u8", "1080.m3u8")]},
    "10.com.au": {"name": "10", "swaps": [("300,150,75,55", "500,300,150,75,55")]},  # formerly 10 Play
    "sbs.com.au": {"name": "SBS On Demand"},
    "7plus.com.au": {"name": "7plus", "nm3u8": True},
    "9now.com.au": {"name": "9Now"},
}
WORK_DIR = ".tasrip-work"
LANG3 = {"en": "eng", "fr": "fre", "de": "ger", "es": "spa", "it": "ita", "ja": "jpn", "ko": "kor",
         "zh": "chi", "ar": "ara", "hi": "hin", "vi": "vie", "el": "gre", "pt": "por", "ru": "rus",
         "nl": "dut", "tr": "tur", "th": "tha", "id": "ind", "pl": "pol", "sv": "swe", "da": "dan",
         "no": "nor", "fi": "fin", "he": "heb", "fa": "per", "tl": "tgl", "pa": "pan", "ta": "tam"}
LANG_NAMES = {"en": "English", "fr": "French", "de": "German", "es": "Spanish", "it": "Italian",
              "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "hi": "Hindi",
              "vi": "Vietnamese", "el": "Greek", "pt": "Portuguese", "ru": "Russian"}
MEDIA_EXTS = (".mkv", ".mp4", ".ts", ".webm", ".m4v", ".m4a", ".aac", ".mka", ".mp3", ".opus")
AUDIO_EXTS = (".m4a", ".aac", ".mka", ".mp3", ".opus")


class RecipeFallback(Exception):
    """The recipe couldn't run for this URL; download it the normal way instead."""


def recipe_for(url):
    """(site, recipe) for a URL from a recipe site, or None."""
    host = url_host(url)
    for site in sorted(RECIPES, key=len, reverse=True):
        if host_matches(host, site):
            return site, RECIPES[site]
    return None


# 10 show pages (10.com.au/<show>) aren't supported by yt-dlp, which only takes season pages
# (10.com.au/<show>/episodes/<season>) and episode pages. These expand a show into its seasons.
TEN_NOT_SHOWS = {"live", "shows", "search", "account", "login", "signin", "signup", "register",
                 "categories", "category", "channels", "channel", "news", "sport", "sports", "tv-guide",
                 "guide", "help", "faq", "privacy", "terms", "about", "contact", "watchlist", "my-list"}


def ten_show_slug(url):
    """The show name if this is a bare 10 show page like https://10.com.au/sam-pang-tonight, else None."""
    if not host_matches(url_host(url), "10.com.au"):
        return None
    parts = [p for p in urllib.parse.urlsplit(url.strip()).path.split("/") if p]
    if len(parts) == 1 and parts[0].lower() not in TEN_NOT_SHOWS and re.fullmatch(r"[a-z0-9-]+", parts[0].lower()):
        return parts[0].lower()
    return None


def _http_text(url, timeout=30):
    return fetch_text(url, {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/130.0 Safari/537.36", "Accept": "application/json, text/html;q=0.9, */*;q=0.8"},
        timeout=timeout)


def ten_show_seasons(show, log=lambda t: None, base="https://10.com.au"):
    """Season page URLs for a 10 show - or, for movies and one-off shows that have no seasons,
    their episode/video page URLs (ending in a tpv... id). From 10's show data or the show page."""
    host = r"(?:https?:)?(?://(?:www\.)?10(?:play)?\.com\.au)?"
    season_rx = re.compile(host + "/" + re.escape(show)
                           + r"/episodes/([a-z0-9][a-z0-9-]*)(?=[\"'?#/\\\s<]|$)", re.I)
    episode_rx = re.compile(host + r"(/" + re.escape(show) + r"(?:/[a-z0-9-]+)*/tpv\d{6}[a-z]{5})", re.I)
    seasons, episodes = [], []
    for src in ("%s/api/shows/%s" % (base, show), "%s/%s" % (base, show), "%s/%s/episodes" % (base, show)):
        try:
            text = _http_text(src).replace("\\/", "/")
        except Exception as e:
            log("Couldn't read %s (%s)" % (src, e))
            continue
        for slug in season_rx.findall(text):
            u = "https://10.com.au/%s/episodes/%s" % (show, slug.lower())
            if u not in seasons:
                seasons.append(u)
        for path in episode_rx.findall(text):
            u = "https://10.com.au" + path.lower()
            if u not in episodes:
                episodes.append(u)
        log("  %s: %d KB, %d season link(s), %d episode link(s)"
            % (src.split("//", 1)[-1], len(text) // 1024, len(seasons), len(episodes)))
        if seasons:
            break
    return seasons or episodes


def auth_args(o, login):
    a = []
    if o.get("cookies", "None") != "None":
        a += ["--cookies-from-browser", o["cookies"]]
    if login and login[0]:
        a += ["--username", login[0], "--password", login[1]]
    return a


def swapped_urls(manifest, recipe):
    """The higher-quality stream addresses a recipe knows how to ask for (may be empty)."""
    out = []
    for old, new in recipe.get("swaps", []):
        if manifest and old in manifest and new not in manifest:
            out.append(manifest.replace(old, new))
    return out


def manifests_of(info):
    """Every stream-list address yt-dlp found, best first (manifest_of's pick leads)."""
    first = manifest_of(info)
    out = [first] if first else []
    for f in info.get("formats") or []:
        mu = f.get("manifest_url")
        if mu and mu not in out:
            out.append(mu)
    return out


def swap_candidates(info, recipe):
    """The higher-quality addresses to try, from every stream list (iView's 720.m3u8 -> 1080.m3u8 etc.)."""
    out = []
    for mu in manifests_of(info):
        for u in swapped_urls(mu, recipe):
            if u not in out:
                out.append(u)
    return out


def seven_stream_state(info):
    """'signed' if 7plus handed out its signed-in stream (member markers in the address),
    'anonymous' if it says so, None if it can't be told."""
    text = " ".join(str(f.get("manifest_url") or f.get("url") or "") for f in info.get("formats") or [])
    if re.search(r"[?&]mstatus=true\b", text, re.I) or re.search(r"[?&]ppId=[^&\s]{6,}", text):
        return "signed"
    if re.search(r"[?&]mstatus=false\b", text, re.I):
        return "anonymous"
    return None


def manifest_of(info):
    """The stream manifest address yt-dlp found (what the pack pulled out with jq).

    Some sites (7plus) offer several stream lists for one episode, and the first isn't always
    the best one - so pick the list whose best picture is the biggest (HLS preferred)."""
    fmts = info.get("formats") or []
    best = {}
    for f in fmts:
        mu = f.get("manifest_url")
        if mu:
            score = (1 if "m3u8" in (f.get("protocol") or "") else 0, f.get("height") or 0, f.get("tbr") or 0)
            best[mu] = max(best.get(mu, (0, 0, 0)), score)
    if best:
        return max(best, key=lambda mu: best[mu])
    for f in fmts + [info]:  # a single-quality stream has no master manifest, just its own address
        if "m3u8" in (f.get("protocol") or "") and f.get("url"):
            return f["url"]
    return None


def stream_heights(info):
    """The picture sizes on offer, best first - e.g. [1080, 720, 405]."""
    return sorted({int(f["height"]) for f in info.get("formats") or [] if str(f.get("height") or "").isdigit()
                   and f.get("vcodec") != "none"}, reverse=True)


def video_height(path):
    probe = tool_path("ffprobe")
    if not probe:
        return 0
    try:
        out = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                              "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60,
                             creationflags=NO_WINDOW).stdout.strip()
        return int(out.split()[0]) if out else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def safe_name(text):
    return re.sub(r"[^\w.-]+", "_", str(text))[:80] or "item"


def nice_name(text, limit=120):
    """A readable file name: keeps spaces and punctuation, drops only what Windows refuses."""
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(text)).replace("\u2019", "'")
    t = re.sub(r"\s+", " ", t).strip().rstrip(". ")[:limit].rstrip(". ")
    return "_" + t if t.upper().split(".")[0] in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1") else t


def sub_info(path):
    """(language code, is_forced) from a subtitle file name like subs.en-forced.srt / video.en.srt."""
    name = os.path.basename(path)[:-4]
    lang = name.split(".", 1)[1] if "." in name else "und"
    forced = "forced" in lang.lower() or lang.lower().endswith("_fe")
    base = re.split(r"[-_.]", lang)[0].lower() or "und"
    return base, forced


def pick_subs(files):
    """Subtitle files to mux: forced first, then English, then the rest; one per language/kind."""
    srts = [f for f in files if f.lower().endswith(".srt")]
    preferred = [f for f in srts if os.path.basename(f).startswith("subs.")]
    if preferred:  # yt-dlp's subtitles win over any that N_m3u8DL-RE also saved
        srts = preferred
    seen, out = set(), []
    for f in sorted(srts, key=lambda f: (not sub_info(f)[1], sub_info(f)[0] != "en", f)):
        key = sub_info(f)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def mux_pack(ffmpeg, media, subs, out, meta):
    """Mux like the Tas Rip Pack: video + audio (English), subtitles tagged and titled, forced
    subtitles flagged forced, none switched on by default. Streams are copied, never re-encoded.
    media[0] is the main file; any further media files are extra audio tracks."""
    mp4 = out.lower().endswith(".mp4")
    tmp = out[:-4] + ".mrtmp" + out[-4:]
    a = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for f in list(media) + list(subs):
        a += ["-i", f]
    a += ["-map", "0:v?", "-map", "0:a?"]
    for i in range(1, len(media)):
        a += ["-map", "%d:a?" % i]
    for i in range(len(subs)):
        a += ["-map", "%d:0" % (len(media) + i)]
    a += ["-c", "copy", "-c:s", "mov_text" if mp4 else "srt", "-metadata:s:a", "language=eng"]
    for i, f in enumerate(subs):
        base, forced = sub_info(f)
        name = LANG_NAMES.get(base, base.upper())
        a += ["-metadata:s:s:%d" % i, "language=" + LANG3.get(base, base if len(base) == 3 else "und"),
              "-metadata:s:s:%d" % i, "title=" + ("Forced " if forced else "") + name,
              "-disposition:s:%d" % i, "forced" if forced else "0"]
    for k, v in (meta or {}).items():
        if v not in (None, ""):
            a += ["-metadata", "%s=%s" % (k, v)]
    if mp4:
        a += ["-movflags", "+faststart"]
    a.append(tmp)
    p = subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW)
    if p.returncode != 0 or not os.path.isfile(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError("ffmpeg couldn't mux the file: " + (p.stderr.strip().splitlines() or ["?"])[-1])
    os.replace(tmp, out)


def run_capture(args, log, set_proc):
    """Run a command; stream its stderr to the log and return (exit code, stdout text)."""
    p = popen(args, stderr=subprocess.PIPE)
    set_proc(p)

    def pump():
        for line in p.stderr:
            line = line.rstrip()
            if line and not line.startswith("[debug]"):
                log(line)
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    out = p.stdout.read()
    code = p.wait()
    t.join(5)
    return code, out


_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def run_nm3u8(args, on_text, on_pct, set_proc):
    """Run N_m3u8DL-RE. It crashes when its output goes to a pipe (a bug in its progress
    display), so it gets a real terminal: a pseudo-terminal on macOS/Linux, its own minimised
    console window on Windows. Returns the exit code."""
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7  # SW_SHOWMINNOACTIVE: minimised, doesn't steal focus
        p = subprocess.Popen(args, creationflags=0x00000010, startupinfo=si)  # CREATE_NEW_CONSOLE
        set_proc(p)
        on_text("N_m3u8DL-RE is running in its own (minimised) window ...")
        return p.wait()
    import pty
    import select
    master, slave = pty.openpty()
    try:
        import fcntl
        import struct
        import termios
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 160, 0, 0))
    except Exception:
        pass
    env = dict(os.environ, TERM=os.environ.get("TERM") or "xterm-256color")
    p = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave, env=env,
                         start_new_session=True, close_fds=True)
    os.close(slave)
    set_proc(p)
    pending, last = "", None
    try:
        while True:
            r, _, _ = select.select([master], [], [], 0.5)
            if r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:  # the program closed its terminal
                    break
                if not chunk:
                    break
                text = _ANSI.sub("", chunk.decode("utf-8", "replace"))
                for m in re.findall(r"(\d{1,3}\.\d+)%", text):
                    on_pct(float(m))
                pending += text
                *lines, pending = re.split(r"[\r\n]+", pending)
                for line in lines:
                    line = line.strip()
                    if re.search(r"\b(INFO|WARN|ERROR)\s*:", line) and line != last:
                        last = line
                        on_text(line)
            elif p.poll() is not None:
                break
    finally:
        os.close(master)
    return p.wait()


def archive_id(info):
    ek, vid = info.get("extractor_key") or info.get("extractor"), info.get("id")
    return ("%s %s" % (ek.lower(), vid)) if ek and vid else None


def in_archive(aid):
    try:
        with open(ARCHIVE_FILE, encoding="utf-8") as f:
            return aid in {l.strip() for l in f}
    except OSError:
        return False


def auto_assign_engines(log=lambda t: None):
    """Map custom builds dropped into bin/engines to their sites by file name (once each)."""
    if not os.path.isdir(ENGINE_DIR):
        return
    engines = load_engines()
    changed = False
    for site, name in list(engines.items()):  # repair: assigned to a stub or a broken copy
        if name and not engine_file_ok(os.path.join(ENGINE_DIR, os.path.basename(name))):
            log("Engine for %s ignored - %s isn't a working program (a macOS '._' file or a damaged copy)."
                % (site, name))
            del engines[site]
            changed = True
    used = set(engines.values())
    for name in sorted(os.listdir(ENGINE_DIR)):
        site = suggest_site(name)
        if name in used or not site or site in engines or not engine_file_ok(os.path.join(ENGINE_DIR, name)):
            continue
        engines[site] = name
        used.add(name)
        changed = True
        log("Engine auto-assigned: %s -> %s" % (site, name))
    if changed:
        try:
            save_engines(engines)
        except OSError:
            pass


# ------------------------------------------------------------- friendly errors
# yt-dlp's error text turned into one plain-English line for the Status column and the log.
# Checked in order; the first match wins.
FRIENDLY_ERRORS = (
    (r"invalid username/password", "10 didn't accept that email/password - check it in 'Sign in to a site' > 10"),
    (r"\[10play\].*(?:login required|log in)|login required to access this video",
     "10 needs your 10 email and password - press 'Sign in to a site' and choose 10"),
    (r"only available for registered users|login required|account credentials|needs a signed-in|"
     r"you need to log in|please log in|sign in to",
     "Needs you to be signed in - use 'Sign in to a site', then Retry"),
    (r"\bdrm\b|widevine|playready", "This one is DRM-protected, so it can't be downloaded"),
    (r"tvnz\.co\.nz|threenow|nz connection", "NZ services only work from an NZ connection (and TVNZ+ uses DRM)"),
    (r"not available from your location|geo.?restrict", "The site thinks you're overseas - see the log"),
    (r"http error 410", "The site has switched that address off (SBS: the SBS fix isn't loaded)"),
    (r"plutotv", "Pluto TV support in yt-dlp is broken right now - check the Plugins tab for a Pluto plugin"),
    (r"known to use drm", "This site uses DRM on everything, so it can't be downloaded"),
    (r"unsupported url", "That link isn't a downloadable page - paste an episode or season link"),
    (r"requested format is not available", "That quality isn't available - try Quality: Best"),
    (r"ffmpeg not found|ffprobe and ffmpeg not found|ffmpeg is not installed|ffmpeg is needed",
     "ffmpeg is missing - Settings > Tools & engines > Reinstall"),
    (r"could not find .*cookies|failed to decrypt with dpapi|could not copy chrome cookie|cookies database|"
     r"failed to load cookies",
     "Couldn't read the browser sign-in - close the browser, or use 'Sign in to a site'"),
    (r"certificate_verify_failed|certificate verify failed",
     "A security check was blocked (antivirus?) - see the log"),
    (r"no space left|errno 28|disk full|not enough space|low on space", "The save drive is full"),
    (r"permission denied|access is denied|errno 13", "Can't save there - pick another folder in Settings"),
    (r"http error 404|not found", "Not found - the episode may have expired or been removed"),
    (r"http error 429|too many requests", "The site is limiting you - wait a while, then Retry"),
    (r"http error 403|forbidden", "The site refused it - try Update yt-dlp, or sign in"),
    (r"timed out|timeout|connection reset|name resolution|getaddrinfo failed|network is unreachable|"
     r"connection refused|remote end closed",
     "Network problem - check the internet connection, then Retry"),
    (r"encoding failed|couldn't encode", "Re-encoding failed - see the log (the original was kept)"),
)


def friendly_error(lines):
    """Plain-English explanation of the first recognisable problem in these log lines, or None."""
    low = "\n".join(l for l in lines if l).lower()
    for pattern, message in FRIENDLY_ERRORS:
        if re.search(pattern, low):
            return message
    return None


# ------------------------------------------------------------ Firefox sign-in
SIGNIN_SITES = (("SBS On Demand", "https://www.sbs.com.au/ondemand", "sbs.com.au"),
                ("10", "https://10.com.au", "10.com.au"),
                ("9Now", "https://www.9now.com.au", "9now.com.au"),
                ("7plus", "https://7plus.com.au", "7plus.com.au"),
                ("ABC iView", "https://iview.abc.net.au", "abc.net.au"))
AUTH_COOKIE = re.compile(r"auth|token|sess|login|refresh|access|jwt|sso|user|account|member", re.I)
# Sites where yt-dlp needs one particular cookie: 7plus signs in only with its "glt_<key>" login
# cookie on 7plus.com.au itself - without it yt-dlp quietly downloads anonymously (max 504p).
SIGNIN_COOKIE = {"7plus.com.au": re.compile(r"^glt_", re.I)}


def firefox_exe():
    """Path to Firefox, or None."""
    if IS_WIN:
        try:
            import winreg
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe") as k:
                        path = (winreg.QueryValue(k, None) or "").strip('"')
                        if os.path.isfile(path):
                            return path
                except OSError:
                    pass
        except ImportError:
            pass
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            path = os.path.join(base, "Mozilla Firefox", "firefox.exe") if base else ""
            if path and os.path.isfile(path):
                return path
        return None
    return shutil.which("firefox")


def firefox_root():
    if IS_WIN:
        return os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox")
    if IS_MAC:
        return os.path.expanduser("~/Library/Application Support/Firefox")
    return os.path.expanduser("~/.mozilla/firefox")


def firefox_profiles(root=None):
    """Firefox profile folders that hold cookies, most recently used first."""
    import configparser
    root = root or firefox_root()
    found = []
    cp = configparser.RawConfigParser()
    try:
        cp.read(os.path.join(root, "profiles.ini"), encoding="utf-8")
    except (configparser.Error, OSError):
        return []
    for sec in cp.sections():
        low = sec.lower()
        if low.startswith("profile"):
            path, rel = cp.get(sec, "Path", fallback=None), cp.get(sec, "IsRelative", fallback="1") == "1"
        elif low.startswith("install"):
            path, rel = cp.get(sec, "Default", fallback=None), True
        else:
            continue
        if not path:
            continue
        full = os.path.normpath(os.path.join(root, path) if rel else path)
        if os.path.isfile(os.path.join(full, "cookies.sqlite")) and full not in found:
            found.append(full)
    return sorted(found, key=lambda d: os.path.getmtime(os.path.join(d, "cookies.sqlite")), reverse=True)


def _profile_cookies(profile, domain, with_wal, exact=False):
    """Cookie names Firefox has for a site, read from a copy of its cookie database
    (exact: only cookies for the site itself, not its subdomains - what yt-dlp sends)."""
    import sqlite3
    tmp = tempfile.mkdtemp(prefix="tasrip_ff_")
    try:
        for suffix in ("", "-wal", "-shm") if with_wal else ("",):
            src = os.path.join(profile, "cookies.sqlite" + suffix)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(tmp, "cookies.sqlite" + suffix))
        con = sqlite3.connect(os.path.join(tmp, "cookies.sqlite"))
        try:
            if exact:
                return [r[0] for r in con.execute(
                    "SELECT name FROM moz_cookies WHERE host = ? OR host = ?", (domain, "." + domain))]
            return [r[0] for r in con.execute(
                "SELECT name FROM moz_cookies WHERE host = ? OR host = ? OR host LIKE ?",
                (domain, "." + domain, "%." + domain))]
        finally:
            con.close()
    except Exception:
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def firefox_signin(domain, root=None):
    """How Firefox stands with a site:
    'ready'         signed in, and TasRip/yt-dlp can read it now
    'close_firefox' signed in, but Firefox is still holding the new sign-in - close Firefox
    'visited'       Firefox has been to the site but isn't signed in
    'no'            nothing for that site     'noprofile'  no Firefox profile found"""
    profiles = firefox_profiles(root)
    if not profiles:
        return "noprofile"
    need = SIGNIN_COOKIE.get(domain)
    signed = (lambda names: any(need.match(n) for n in names)) if need else \
        (lambda names: any(AUTH_COOKIE.search(n) for n in names))
    best = "no"
    for prof in profiles:
        # yt-dlp reads Firefox's main cookie file; a sign-in made while Firefox is still open can
        # sit in the side file (-wal) until Firefox closes.
        main = _profile_cookies(prof, domain, with_wal=False, exact=bool(need))
        if signed(main):
            return "ready"
        full = _profile_cookies(prof, domain, with_wal=True, exact=bool(need))
        if signed(full):
            best = "close_firefox"
        elif (main or full) and best == "no":
            best = "visited"
    return best


# ------------------------------------------------------------ problem report
def tool_version(name):
    path = tool_path(name)
    if not path:
        return "not installed"
    if name == "SubtitleEdit":  # a window program: asking it for --version would open it
        try:
            ver = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-Item '%s').VersionInfo.ProductVersion" % path.replace("'", "''")],
                                 capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            ver = ""
        return scrub("SubtitleEdit %s   (%s)" % (ver or "installed", path))
    flag = "-version" if name.startswith("ffmpeg") else "" if name == "7zr" else "--version"
    try:
        out = subprocess.run([path] + ([flag] if flag else []), capture_output=True, text=True,
                             timeout=90 if name == "streamlink" else 30,
                             encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        first = ((out.stdout or out.stderr or "").strip().splitlines() or ["?"])[0]
        return scrub("%s   (%s)" % (first[:80], path))
    except (OSError, subprocess.SubprocessError) as e:
        return "error: %s" % e


def make_problem_report(queue_lines=(), item=None):
    """One zip with recent logs, the latest diagnostic and system details, in the TasRip
    folder. Emails, tokens and the Windows user name are removed. Returns the zip's path.
    item: a failed queue item - its own full log goes in first, as failed-item.txt."""
    path = os.path.join(BASE_DIR, "TasRip-problem-report-%s%s.zip" % (
        "item-" if item else "", time.strftime("%Y%m%d-%H%M%S")))
    info = ["%s v%s problem report - %s" % (APP_NAME, VERSION, time.strftime("%Y-%m-%d %H:%M")),
            "System: %s" % platform.platform(),
            "Python: %s%s" % (platform.python_version(), " (built exe)" if getattr(sys, "frozen", False) else ""),
            "Folder: %s" % scrub(BASE_DIR), "", "Tools:"]
    for name in ("yt-dlp", "ffmpeg", "N_m3u8DL-RE", "aria2c", "streamlink", "mkvmerge", "SubtitleEdit",
                 "jq", "wget", "7zr"):
        info.append("  %-12s %s" % (name, tool_version(name)))
    settings = load_settings()
    settings["outdir"] = scrub(settings["outdir"])
    settings["extra"] = scrub(settings.get("extra", ""))
    info += ["", "Settings:"] + ["  %s = %s" % kv for kv in sorted(settings.items())]
    info += ["", "Site engines: %s" % (load_engines() or "none"),
             "Plugins: %s" % ([n for n, _, _ in installed_plugins()] or "none"),
             "", "Queue:"] + ["  " + scrub(l) for l in queue_lines]
    logs = sorted(glob.glob(os.path.join(LOG_DIR, "tasrip-*.txt")), key=os.path.getmtime)[-3:]
    diags = sorted(glob.glob(os.path.join(LOG_DIR, "diagnostic-*.txt")), key=os.path.getmtime)[-1:]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if item:
            o = {k: v for k, v in (item.get("opts") or {}).items() if k not in ("extra",)}
            head = ["Failed item - %s v%s" % (APP_NAME, VERSION), "URL: %s" % item.get("url"),
                    "Status: %s" % item.get("shown", item.get("status")), "Title: %s" % item.get("title", ""),
                    "Options: %s" % json.dumps(o, sort_keys=True), "", "Everything logged for this item:", ""]
            z.writestr("failed-item.txt", scrub("\n".join(head + list(item.get("log") or ["(nothing logged)"])) + "\n"))
        z.writestr("system.txt", "\n".join(info) + "\n")
        for f in logs + diags:
            with open(f, encoding="utf-8", errors="replace") as fh:
                z.writestr(os.path.basename(f), scrub(fh.read()))
    return path


def show_in_folder(path):
    """Open the folder holding a file, with the file selected (Windows), else just the folder."""
    if IS_WIN and os.path.exists(path):
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return True
        except OSError:
            pass
    return open_path(os.path.dirname(path) if os.path.isfile(path) else path)


# ------------------------------------------------------------ re-encoding
# Off by default: streams are copied as they are (fastest, no quality loss). The CPU encoders
# work on every PC; the graphics-card ones only where the card and its driver support them,
# which detect_hardware() checks by actually test-encoding.
ENC_CODECS = ["H.264", "H.265 (HEVC)", "AV1"]
ENC_QUALITY = ["High", "Balanced", "Small"]
ENC_SPEED = ["Fast", "Balanced", "Smallest files"]
ENC_RESIZE = ["Keep", "1080p", "720p", "576p", "Upscale to 1080p"]
NFO_STYLES = ["Off", "Scene (text)", "Kodi / Jellyfin (XML)"]
MIN_QUALITIES = ["Off", "540p", "720p", "1080p"]
PARALLEL = ["1", "2", "3"]
ENC_AUDIO = ["Copy", "AAC 192k", "AAC 128k", "Opus 128k"]
GPU_VENDORS = ["NVIDIA", "AMD", "Intel"]
ENCODERS = {"CPU": ("libx264", "libx265", "libsvtav1"),
            "NVIDIA": ("h264_nvenc", "hevc_nvenc", "av1_nvenc"),
            "AMD": ("h264_amf", "hevc_amf", "av1_amf"),
            "Intel": ("h264_qsv", "hevc_qsv", "av1_qsv")}
QUALITY_VALUES = {  # High / Balanced / Small, in each encoder's own quality scale
    "libx264": (20, 23, 26), "libx265": (22, 26, 29), "libsvtav1": (28, 32, 36),
    "h264_nvenc": (21, 24, 28), "hevc_nvenc": (23, 27, 31), "av1_nvenc": (27, 31, 35),
    "h264_amf": (20, 23, 27), "hevc_amf": (22, 26, 30), "av1_amf": (26, 30, 34),
    "h264_qsv": (20, 24, 28), "hevc_qsv": (22, 26, 30), "av1_qsv": (26, 30, 34)}
VIDEO_EXTS = (".mkv", ".mp4", ".webm", ".m4v", ".mov", ".ts")


def _pick(options, value, default=0):
    return options.index(value) if value in options else default


def encoder_name(o, force_cpu=False):
    vendor = o.get("enc_vendor") if (o.get("enc_gpu") and not force_cpu) else "CPU"
    return ENCODERS.get(vendor, ENCODERS["CPU"])[_pick(ENC_CODECS, o.get("enc_codec"))]


def video_encode_args(enc, o):
    q = QUALITY_VALUES[enc][_pick(ENC_QUALITY, o.get("enc_quality"), 1)]
    sp = _pick(ENC_SPEED, o.get("enc_speed"))
    if enc in ("libx264", "libx265"):
        a = ["-c:v", enc, "-crf", str(q), "-preset", ("veryfast", "medium", "slow")[sp]]
        return a + (["-x265-params", "log-level=error"] if enc == "libx265" else [])
    if enc == "libsvtav1":
        return ["-c:v", enc, "-crf", str(q), "-preset", ("10", "8", "6")[sp]]
    if enc.endswith("_nvenc"):
        return ["-c:v", enc, "-rc", "vbr", "-cq", str(q), "-b:v", "0", "-preset", ("p2", "p4", "p6")[sp]]
    if enc.endswith("_amf"):
        return ["-c:v", enc, "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q),
                "-quality", ("speed", "balanced", "quality")[sp]]
    return ["-c:v", enc, "-global_quality", str(q), "-preset", ("veryfast", "medium", "slow")[sp]]


def audio_encode_args(o):
    return {"AAC 192k": ["-c:a", "aac", "-b:a", "192k"], "AAC 128k": ["-c:a", "aac", "-b:a", "128k"],
            "Opus 128k": ["-c:a", "libopus", "-b:a", "128k"]}.get(o.get("enc_audio"), ["-c:a", "copy"])


def resize_args(o):
    if o.get("enc_resize") == "Upscale to 1080p":
        # Lanczos upscale of anything under 1080 lines, plus light sharpening. It can't invent detail
        # that isn't in the 720p stream, but it looks cleaner on a 1080p/4K TV than the TV's own scaler.
        return ["-vf", "scale=-2:'max(1080,ih)':flags=lanczos,unsharp=5:5:0.5:5:5:0"]
    h = {"1080p": 1080, "720p": 720, "576p": 576}.get(o.get("enc_resize"))
    return ["-vf", "scale=-2:'min(%d,ih)'" % h] if h else []  # never upscale


def media_duration(path):
    probe = tool_path("ffprobe")
    if not probe:
        return None
    try:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW).stdout
        return float(out.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def encode_file(path, o, on_pct, set_proc, log, stopped=lambda: False):
    """Re-encode a downloaded video in place (the original is only replaced once the new file is
    complete). Falls back to the CPU if the graphics card refuses. Returns the encoder used."""
    ffmpeg = tool_path("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is needed for re-encoding")
    base, ext = os.path.splitext(path)
    tmp = base + ".mrenc" + ext
    dur = media_duration(path)

    def run(enc):
        a = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
             "-i", path, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?", "-map_metadata", "0",
             "-c", "copy"] + video_encode_args(enc, o) + resize_args(o) + audio_encode_args(o)
        if ext.lower() in (".mp4", ".m4v", ".mov"):
            a += ["-c:s", "mov_text", "-movflags", "+faststart"]
        a.append(tmp)
        p = popen(a)
        set_proc(p)
        tail = []
        for line in p.stdout:
            line = line.strip()
            if line.startswith(("out_time_us=", "out_time_ms=")) and dur:
                try:
                    on_pct(min(99.9, int(line.split("=", 1)[1]) / 1e6 / dur * 100))
                except ValueError:
                    pass
            elif line and "=" not in line:
                tail = (tail + [line])[-5:]
        return p.wait(), tail

    enc = encoder_name(o)
    code, tail = run(enc)
    if code != 0 and not stopped() and enc not in ENCODERS["CPU"]:
        log("The graphics card couldn't encode this one - using the CPU instead.")
        if tail:
            log("  (ffmpeg said: %s)" % tail[-1])
        enc = encoder_name(o, force_cpu=True)
        code, tail = run(enc)
    if code != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        if stopped():
            raise RuntimeError("stopped")
        raise RuntimeError("encoding failed: %s" % (tail[-1] if tail else "ffmpeg exit code %s" % code))
    os.replace(tmp, path)
    return enc


def test_encoder(ffmpeg, enc):
    """True if this encoder really works on this PC (hardware + driver), by encoding 5 frames."""
    try:
        p = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=256x256:d=0.5", "-frames:v", "5", "-c:v", enc, "-f", "null", "-"],
                           capture_output=True, timeout=60, creationflags=NO_WINDOW)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect_hardware():
    """The CPU, graphics cards, and which encoders actually work here."""
    hw = {"cpu": "", "threads": os.cpu_count() or 1, "gpus": [], "works": {}}
    if IS_WIN:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "'CPU:' + (Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name); "
                 "Get-CimInstance Win32_VideoController | ForEach-Object { 'GPU:' + $_.Name }"],
                capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW).stdout
            for line in out.splitlines():
                if line.startswith("CPU:"):
                    hw["cpu"] = line[4:].strip()
                elif line.startswith("GPU:") and line[4:].strip():
                    hw["gpus"].append(line[4:].strip())
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            with open("/proc/cpuinfo") as f:
                hw["cpu"] = next((l.split(":", 1)[1].strip() for l in f if l.startswith("model name")), "")
        except OSError:
            pass
    hw["cpu"] = hw["cpu"] or platform.processor() or "unknown CPU"
    ffmpeg = tool_path("ffmpeg")
    for encs in ENCODERS.values():
        for enc in encs:
            hw["works"][enc] = bool(ffmpeg) and test_encoder(ffmpeg, enc)
    return hw


def recommend_encoder(hw):
    """(vendor or 'CPU', codec, explanation) - the best set-up that actually worked in the test."""
    w = hw["works"]
    for vendor, words in (("NVIDIA", ("nvidia", "geforce", "rtx", "quadro")), ("Intel", ("intel", "arc")),
                          ("AMD", ("amd", "radeon"))):
        h264, hevc, av1 = ENCODERS[vendor]
        card = next((g for g in hw["gpus"] if any(x in g.lower() for x in words)), vendor + " graphics")
        av1_note = (" It can also do AV1 (smaller still), but older TVs and some Plex apps can't play AV1."
                    if w.get(av1) else "")
        if w.get(hevc):
            return vendor, "H.265 (HEVC)", (
                "Recommended: your %s, H.265. Encoding on the card is many times faster than the CPU, and "
                "H.265 files are roughly half the size of H.264 at the same quality.%s Tick 'Use the graphics "
                "card' to use it." % (card, av1_note))
        if w.get(h264):
            return vendor, "H.264", ("Recommended: your %s, H.264 - fast, and plays on everything. Tick 'Use "
                                     "the graphics card' to use it." % card)
    if hw["threads"] >= 12 and w.get("libx265"):
        return "CPU", "H.265 (HEVC)", (
            "No graphics card encoder worked here, but your %d-thread CPU can manage H.265 at a reasonable "
            "speed, for files about half the size of H.264." % hw["threads"])
    return "CPU", "H.264", ("No graphics card encoder worked here. H.264 on the CPU at the Fast setting is "
                            "the quickest option that plays on everything.")



# ------------------------------------------------------------ saved site logins
# 10 signs in with email + password only (yt-dlp can't use browser cookies for it). If the user
# ticks "remember", the password is stored encrypted with Windows' own per-user encryption (DPAPI),
# so the file is useless on another PC or Windows account. It is never put in problem reports.
LOGINS_FILE = os.path.join(BASE_DIR, "logins.dat")
PASSWORD_SITES = {"10.com.au": "10"}


def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):  # 0x1: no UI
        raise OSError("Windows couldn't %s the password" % ("encrypt" if protect else "decrypt"))
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def load_logins():
    try:
        with open(LOGINS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def saved_login(site):
    """(email, password) saved for a site, or None."""
    e = load_logins().get(site)
    if not (IS_WIN and isinstance(e, dict) and e.get("user") and e.get("pw")):
        return None
    try:
        import base64
        return e["user"], _dpapi(base64.b64decode(e["pw"]), False).decode("utf-8")
    except Exception:
        return None


def save_login(site, user, password):
    if not IS_WIN:
        raise OSError("remembering passwords needs Windows")
    import base64
    d = load_logins()
    d[site] = {"user": user, "pw": base64.b64encode(_dpapi(password.encode("utf-8"), True)).decode("ascii"),
               "saved": time.strftime("%Y-%m-%d")}
    with open(LOGINS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def forget_login(site):
    d = load_logins()
    if d.pop(site, None) is not None:
        with open(LOGINS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)


def login_site(url):
    """Which password-login site a URL belongs to ("10.com.au"), or None."""
    host = canonical_site(url_host(url))
    return next((s for s in PASSWORD_SITES if host_matches(host, s)), None)


def check_ten_login(user, password):
    """Try the email + password with 10 (the same request yt-dlp makes). -> "ok", "bad" or an error."""
    import base64
    import datetime
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    req = urllib.request.Request("https://10.com.au/api/user/auth", method="POST",
                                 data=json.dumps({"email": user, "password": password}).encode(),
                                 headers={"Content-Type": "application/json", "Referer": "https://10.com.au/",
                                          "X-Network-Ten-Auth": base64.b64encode(stamp.encode()).decode(),
                                          "User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return "ok" if (d.get("jwt") or {}).get("accessToken") else "unexpected reply from 10"
    except urllib.error.HTTPError as e:
        return "bad" if e.code in (400, 401, 403) else "10 said HTTP %d" % e.code
    except Exception as e:
        return str(e)


# ------------------------------------------------------------ show search (the 5 channels)
SEARCH_CHANNELS = ["ABC iView", "10", "SBS On Demand", "7plus", "9Now"]
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
# Show-page addresses for each channel (used to tidy and filter web search results)
SHOW_LINKS = {
    "ABC iView": (r"https?://iview\.abc\.net\.au/show/[a-z0-9-]+", "iview.abc.net.au/show"),
    "10": (r"https?://(?:www\.)?10(?:play)?\.com\.au/[a-z0-9-]+", "10.com.au"),
    "SBS On Demand": (r"https?://www\.sbs\.com\.au/ondemand/(?:tv-series|movie|tv-program)/[a-z0-9-]+",
                      "sbs.com.au/ondemand"),
    "7plus": (r"https?://7plus\.com\.au/[a-z0-9-]+", "7plus.com.au"),
    "9Now": (r"https?://www\.9now\.com\.au/[a-z0-9-]+", "9now.com.au"),
}
NOT_SHOWS = TEN_NOT_SHOWS | {"live-tv", "shows-a-z", "tv-guide", "categories", "my-list", "watchlist", "show",
                             "ondemand", "sport", "news", "movies", "genre", "channel", "channels", "help", "faq",
                             "privacy", "terms", "about", "contact", "kids", "collection", "collections"}


def _api_json(url, headers=None, timeout=15):
    h = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
    h.update(headers or {})
    return json.loads(fetch_text(url, headers=h, timeout=timeout))


def _walk(obj):
    """Every dict inside a JSON document."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def search_7plus(q):
    params = {"searchTerm": q, "market-id": "4", "api-version": "4.4", "platform-id": "androidtv",
              "platform-version": "5.25.0.0"}
    data = _api_json("https://searchapi.swm.digital/3.0/api/Search?" + urllib.parse.urlencode(params),
                     headers={"x-swm-apikey": "kGcrNnuPClrkynfnKwG8IA/NhVG6ut5nPEdWF2jscvE="})
    out = []
    for d in _walk(data):
        link = d.get("contentLink")
        path = link.get("url") if isinstance(link, dict) else link if isinstance(link, str) else None
        if not path:
            continue
        slug = path.split("?")[0].strip("/").split("/")[0]
        title = d.get("title") or (d.get("image") or {}).get("altTag") or (d.get("cardData") or {}).get("title")
        if slug and slug not in NOT_SHOWS and title:
            out.append((str(title).strip(), "https://7plus.com.au/" + slug))
    return out


def search_9now(q):
    params = {"device": "web", "q": q, "region": "nsw"}
    data = _api_json("https://tv-api.9now.com.au/v2/pages/search?" + urllib.parse.urlencode(params))
    out = []
    for d in _walk(data.get("results", data)):
        if d.get("type") == "tv-series" and d.get("slug") and str(d.get("name", "")).strip():
            out.append((d["name"].strip(), "https://www.9now.com.au/" + d["slug"]))
    return out


TEN_SIG_KEY = "b918ff793563080c5821c89ee6c415c363cb36d369db1020369ac4b405a0211d"


def _ten_get(url):
    import hmac
    import hashlib
    import binascii
    ts = int(time.time())
    sig = hmac.new(binascii.unhexlify(TEN_SIG_KEY), ("%d:%s" % (ts, url)).encode(), hashlib.sha256).hexdigest()
    return _api_json(url, headers={"tp-acceptfeature": "v1/fw;v1/drm;v2/live", "tp-platform": "UAP",
                                   "X-N10-SIG": "%d_%s" % (ts, sig)})


def search_10(q):
    cfg = _ten_get("https://10.com.au/api/v1/config?SystemName=android&manufacturer=nvidia")
    data = _ten_get(cfg["searchApiEndpoint"] + urllib.parse.quote(q))
    out = []
    for d in _walk(data):
        if d.get("type") != "Show" or not d.get("title"):
            continue
        slug = None
        for key in ("showUrl", "url", "webUrl", "pageUrl", "link", "slug", "showSlug"):
            v = str(d.get(key) or "")
            m = re.search(r"(?:10(?:play)?\.com\.au)?/?([a-z0-9][a-z0-9-]+)/?$", v.split("?")[0])
            if m and m.group(1) not in NOT_SHOWS:
                slug = m.group(1)
                break
        out.append((d["title"].strip(), "https://10.com.au/" + (slug or _slugify(d["title"]))))
    return out


def search_sbs(q):
    data = _api_json("https://content-search.pr.sbsod.com/catalogue?" + urllib.parse.urlencode({"q": q, "limit": 30}),
                     headers={"x-api-key": "ce3f270a-31fb-486e-855e-4f608a62640e", "accept-language": "en"})
    kinds = {"TV_SERIES": "tv-series", "SPORTS_SERIES": "tv-series", "NEWS_SERIES": "tv-series",
             "MOVIE": "movie", "TV_PROGRAM": "tv-program", "NEWS_PROGRAM": "tv-program", "SPORTS_PROGRAM": "tv-program"}
    out = []
    for d in data.get("items", []):
        kind = kinds.get(d.get("entityType"))
        if kind and d.get("slug") and d.get("title"):
            out.append((d["title"].strip(), "https://www.sbs.com.au/ondemand/%s/%s" % (kind, d["slug"])))
    return out


def web_search(q, channel):
    """Search the web for a channel's show pages (iView has no public search; also the fallback
    when a channel's own search stops answering)."""
    pattern, domain = SHOW_LINKS[channel]
    found = []

    def keep(title, url):
        m = re.match(pattern, urllib.parse.unquote(url), re.I)
        if not m:
            return
        link = m.group(0).lower().replace("10play.com.au", "10.com.au")
        if link.rstrip("/").rsplit("/", 1)[-1] in NOT_SHOWS or any(link == f[1] for f in found):
            return
        title = html.unescape(re.sub(r"<[^>]+>", "", title))
        title = re.split(r"\s+[|–—-]\s+|\s*:\s*Watch\b", title)[0].strip() or link.rsplit("/", 1)[-1]
        found.append((title, link))
    query = "site:%s %s" % (domain, q)
    try:
        rss = fetch_text("https://www.bing.com/search?format=rss&count=30&q=" + urllib.parse.quote(query),
                         headers={"User-Agent": BROWSER_UA}, timeout=15)
        for item in re.findall(r"<item>(.*?)</item>", rss, re.S):
            t = re.search(r"<title>(.*?)</title>", item, re.S)
            l = re.search(r"<link>(.*?)</link>", item, re.S)
            if l:
                keep(t.group(1) if t else "", html.unescape(l.group(1).strip()))
    except Exception:
        pass
    if not found:
        try:
            page = fetch_text("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query),
                              headers={"User-Agent": BROWSER_UA}, timeout=15)
            for href, title in re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
                u = urllib.parse.parse_qs(urllib.parse.urlsplit(html.unescape(href)).query).get("uddg", [href])[0]
                keep(title, u)
        except Exception:
            pass
    return found


SITE_SEARCH = {"10": search_10, "SBS On Demand": search_sbs, "7plus": search_7plus, "9Now": search_9now}


def search_channel(q, channel):
    """[(title, show page URL)] and where they came from ("site" or "web")."""
    fn = SITE_SEARCH.get(channel)
    if fn:
        try:
            rows = fn(q)
            seen, out = set(), []
            for t, u in rows:
                if u not in seen:
                    seen.add(u)
                    out.append((t, u))
            if out:
                return out, "site"
        except Exception:
            pass
    return web_search(q, channel), "web"


# ------------------------------------------------------------ .nfo files and thumbnails
SOURCE_TAGS = {"abc.net.au": ("ABC iView", "iVIEW"), "10.com.au": ("10", "TEN"), "sbs.com.au": ("SBS On Demand", "SBS"),
               "7plus.com.au": ("7plus", "7PLUS"), "9now.com.au": ("9Now", "9NOW"), "tubitv.com": ("Tubi", "TUBI"),
               "youtube.com": ("YouTube", "YT"), "youtu.be": ("YouTube", "YT")}


def probe_media(path):
    ffprobe = tool_path("ffprobe")
    if not ffprobe:
        return {}
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                           capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW)
        return json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _source_of(info):
    host = canonical_site(url_host(info.get("webpage_url") or ""))
    for site, names in SOURCE_TAGS.items():
        if host_matches(host, site):
            return names
    ek = info.get("extractor_key") or ""
    return (ek or "Web"), re.sub(r"[^A-Za-z0-9]", "", ek).upper()[:8] or "WEB"


def _codec_name(c):
    return {"h264": "H.264", "hevc": "H.265", "av1": "AV1", "vp9": "VP9", "mpeg2video": "MPEG-2", "aac": "AAC",
            "ac3": "DD", "eac3": "DDP", "opus": "Opus", "mp3": "MP3", "flac": "FLAC", "subrip": "SRT",
            "webvtt": "WebVTT", "mov_text": "TX3G", "ass": "ASS"}.get(c or "", (c or "?").upper())


def _channels(n):
    return {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(n or 0, "%sch" % n if n else "")


def _lang(st):
    code = (st.get("tags") or {}).get("language", "")
    for two, three in LANG3.items():
        if code in (two, three):
            return LANG_NAMES.get(two, code)
    return {"eng": "English", "und": ""}.get(code, code)


def release_name(info, probe, audio=False):
    """Scene-style name, e.g. Bluey.S03E07.Pass.the.Parcel.1080p.iVIEW.WEB-DL.AAC2.0.H.264-TasRip"""
    dot = lambda t: re.sub(r"\.+", ".", re.sub(r"[^A-Za-z0-9]+", ".", str(t))).strip(".")
    v = next((x for x in probe.get("streams", []) if x.get("codec_type") == "video"
              and not (x.get("disposition") or {}).get("attached_pic")), None)
    a = next((x for x in probe.get("streams", []) if x.get("codec_type") == "audio"), None)
    parts = []
    show, sn, en = info.get("series"), info.get("season_number"), info.get("episode_number")
    if show:
        parts.append(dot(show))
        if str(sn).isdigit() and str(en).isdigit():
            parts.append("S%02dE%02d" % (int(sn), int(en)))
        else:
            d = str(info.get("release_date") or info.get("upload_date") or "")
            if len(d) == 8:
                parts.append("%s.%s.%s" % (d[:4], d[4:6], d[6:]))
        ep = info.get("episode") or info.get("title")
        if ep and dot(ep).lower() != dot(show).lower():
            parts.append(dot(ep))
    else:
        parts.append(dot(info.get("title") or info.get("id") or "Untitled"))
        if info.get("release_year"):
            parts.append(str(info["release_year"]))
    src = _source_of(info)[1]
    if v and not audio:
        h = int(v.get("height") or 0)
        parts.append("%dp" % (2160 if h > 1440 else 1440 if h > 1080 else 1080 if h > 720 else 720 if h > 576 else 576 if h > 480 else h)
                     if h else "")
    parts += [src, "WEB-DL" if not audio else "WEB"]
    if a:
        parts.append(_codec_name(a.get("codec_name")) + (_channels(a.get("channels")) if not audio else ""))
    if v and not audio:
        parts.append(_codec_name(v.get("codec_name")))
    return ".".join(p for p in parts if p) + "-" + APP_NAME


NFO_LOGO = r"""
        _____          ____  _
       |_   _|_ _  ___|  _ \(_)_ __
         | |/ _` |/ __| |_) | | '_ \
         | | (_| |\__ \  _ <| | |_) |
         |_|\__,_||___/_| \_\_| .__/
                              |_|
"""


def scene_nfo(path, info, probe, audio=False):
    w = 66
    line = lambda label, val: "  %-14s: %s" % (label, val) if val not in (None, "", []) else None
    rule = lambda title="": ("  -[ %s ]" % title).ljust(w, "-") if title else "  " + "-" * (w - 2)
    fmt = probe.get("format", {})
    out = [NFO_LOGO.rstrip("\n"), "", rule(), "  " + release_name(info, probe, audio), rule(), ""]
    show, sn, en = info.get("series"), info.get("season_number"), info.get("episode_number")
    d = str(info.get("release_date") or info.get("upload_date") or "")
    aired = "%s-%s-%s" % (d[:4], d[4:6], d[6:]) if len(d) == 8 else ""
    out += [x for x in (line("Title", info.get("episode") or info.get("title")), line("Show", show),
                        line("Season", sn if str(sn).isdigit() else None), line("Episode", en if str(en).isdigit() else None),
                        line("Aired", aired), line("Genre", ", ".join(info.get("genres") or info.get("categories") or [])),
                        line("Rating", ("M" if info.get("age_limit") == 15 else "MA15+" if info.get("age_limit") == 16 else
                                        "R18+" if (info.get("age_limit") or 0) >= 18 else None)),
                        line("Source", "%s (%s)" % (_source_of(info)[0], "WEB" if audio else "WEB-DL")),
                        line("Link", info.get("webpage_url"))) if x]
    secs = float(fmt.get("duration") or info.get("duration") or 0)
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    out += ["", rule("FILE"), line("Name", os.path.basename(path)),
            line("Size", "%.2f GB (%s bytes)" % (size / 1024 ** 3, format(size, ",")) if size > 1024 ** 3 else
                 "%.1f MB (%s bytes)" % (size / 1024 ** 2, format(size, ","))),
            line("Runtime", "%dh %02dm %02ds" % (secs // 3600, secs % 3600 // 60, secs % 60) if secs >= 3600 else
                 "%dm %02ds" % (secs // 60, secs % 60) if secs else None),
            line("Container", (fmt.get("format_long_name") or "").split(" / ")[0] or os.path.splitext(path)[1][1:].upper()),
            line("Bitrate", "%d kb/s" % (int(fmt["bit_rate"]) // 1000) if str(fmt.get("bit_rate", "")).isdigit() else None)]
    vids = [x for x in probe.get("streams", []) if x.get("codec_type") == "video"
            and not (x.get("disposition") or {}).get("attached_pic")]
    for v in vids[:1]:
        fps = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"
        try:
            num, den = (int(x) for x in fps.split("/"))
            fps = ("%.3f" % (num / den)).rstrip("0").rstrip(".") if den else ""
        except ValueError:
            fps = ""
        br = v.get("bit_rate") or (v.get("tags") or {}).get("BPS")
        out += ["", rule("VIDEO"), line("Format", _codec_name(v.get("codec_name")) + (" (%s)" % v["profile"] if v.get("profile") else "")),
                line("Resolution", "%sx%s" % (v.get("width"), v.get("height"))),
                line("Frame rate", "%s fps" % fps if fps else None),
                line("Bitrate", "%d kb/s" % (int(br) // 1000) if str(br or "").isdigit() else None),
                line("Aspect", v.get("display_aspect_ratio"))]
    auds = [x for x in probe.get("streams", []) if x.get("codec_type") == "audio"]
    if auds:
        out += ["", rule("AUDIO")]
        for n, a in enumerate(auds, 1):
            br = a.get("bit_rate") or (a.get("tags") or {}).get("BPS")
            out.append(line("#%d" % n, " ".join(x for x in (_codec_name(a.get("codec_name")), _channels(a.get("channels")),
                                                            "%d kb/s" % (int(br) // 1000) if str(br or "").isdigit() else "",
                                                            "%s Hz" % a["sample_rate"] if a.get("sample_rate") else "",
                                                            _lang(a)) if x)))
    subs = [x for x in probe.get("streams", []) if x.get("codec_type") == "subtitle"]
    side = sorted(f for f in os.listdir(os.path.dirname(path) or ".")
                  if f.startswith(os.path.splitext(os.path.basename(path))[0] + ".") and f.lower().endswith((".srt", ".vtt", ".ass")))
    if subs or side:
        out += ["", rule("SUBTITLES")]
        for n, st in enumerate(subs, 1):
            forced = (st.get("disposition") or {}).get("forced")
            out.append(line("#%d" % n, " ".join(x for x in (_lang(st) or "Unknown", "(%s)" % _codec_name(st.get("codec_name")),
                                                            "[forced]" if forced else "") if x)))
        for f in side:
            out.append(line("External", f))
    desc = (info.get("description") or "").strip()
    if desc:
        import textwrap
        out += ["", rule("PLOT")] + ["  " + l for l in textwrap.wrap(re.sub(r"\s+", " ", desc), w - 4)]
    out += ["", rule(), "  Ripped %s with %s v%s (yt-dlp + ffmpeg)" % (time.strftime("%Y-%m-%d"), APP_NAME, VERSION), rule(), ""]
    return "\r\n".join(x for x in out if x is not None)


def kodi_nfo(path, info, probe):
    from xml.sax.saxutils import escape
    e = lambda tag, val: "  <%s>%s</%s>" % (tag, escape(str(val)), tag) if val not in (None, "", []) else None
    d = str(info.get("release_date") or info.get("upload_date") or "")
    aired = "%s-%s-%s" % (d[:4], d[4:6], d[6:]) if len(d) == 8 else ""
    secs = float((probe.get("format") or {}).get("duration") or info.get("duration") or 0)
    episode = bool(info.get("series"))
    root = "episodedetails" if episode else "movie"
    rows = [e("title", info.get("episode") or info.get("title")),
            e("showtitle", info.get("series")) if episode else None,
            e("season", info.get("season_number")) if episode else None,
            e("episode", info.get("episode_number")) if episode else None,
            e("plot", (info.get("description") or "").strip()),
            e("aired" if episode else "premiered", aired),
            e("year", info.get("release_year") or (d[:4] if d else "")) if not episode else None,
            e("runtime", int(round(secs / 60)) if secs else None),
            e("studio", _source_of(info)[0]),
            ('  <uniqueid type="%s" default="true">%s</uniqueid>' % (escape(_source_of(info)[1].lower()), escape(str(info["id"])))
             if info.get("id") else None)]
    rows += [e("genre", g) for g in (info.get("genres") or [])]
    if os.path.isfile(os.path.splitext(path)[0] + ".jpg"):
        rows.append(e("thumb", os.path.basename(os.path.splitext(path)[0] + ".jpg")))
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<%s>\n%s\n</%s>\n' % (
        root, "\n".join(r for r in rows if r), root)


def write_nfo(path, info, style, log=lambda t: None):
    """Write <file>.nfo next to a finished download. style: one of NFO_STYLES."""
    if style == "Off" or not os.path.isfile(path):
        return None
    audio = path.lower().endswith(AUDIO_EXTS + (".flac", ".wav"))
    if style.startswith("Kodi") and audio:
        return None
    probe = probe_media(path)
    text = kodi_nfo(path, info, probe) if style.startswith("Kodi") else scene_nfo(path, info, probe, audio)
    out = os.path.splitext(path)[0] + ".nfo"
    try:
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return out
    except OSError as e:
        log("Couldn't write %s: %s" % (os.path.basename(out), e))
        return None


def save_thumbnail(url, dest, log=lambda t: None):
    """Download a thumbnail picture to dest (.jpg), converting it if the site sent another format."""
    if not url or os.path.exists(dest):
        return None
    tmp = dest + ".download"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        with open(tmp, "rb") as f:
            is_jpeg = f.read(3) == b"\xff\xd8\xff"
        if is_jpeg:
            os.replace(tmp, dest)
        else:
            ffmpeg = tool_path("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("needs ffmpeg to convert it")
            subprocess.run([ffmpeg, "-y", "-v", "error", "-i", tmp, "-frames:v", "1", "-q:v", "2", dest],
                           capture_output=True, creationflags=NO_WINDOW)
            if not os.path.isfile(dest):
                raise RuntimeError("couldn't convert it to JPEG")
        return dest
    except Exception as e:
        log("Couldn't save the thumbnail: %s" % e)
        return None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ------------------------------------------------------------ the Tas Rip Pack's other tools
# jq, wget, mkvmerge (MKVToolNix) and SubtitleEdit, laid out the way the pack's Extractors folder
# was (mkvmerge.exe and jq.exe in bin, SubtitleEdit in bin\SubEdit), plus 7zr to unpack MKVToolNix.
def _need_windows(name):
    if not IS_WIN:
        raise RuntimeError("%s is only fetched automatically on Windows - install it with your package manager" % name)


def install_jq(log):
    _need_windows("jq")
    log("Downloading jq ...")
    url = _github_asset("jqlang/jq", r"^jq-windows-amd64\.exe$")
    if not url:
        raise RuntimeError("couldn't find jq's Windows download")
    fetch(url, os.path.join(BIN_DIR, "jq.exe"), log)


def install_wget(log):
    _need_windows("wget")
    log("Downloading wget ...")
    ver = "1.21.4"
    try:
        page = fetch_text("https://eternallybored.org/misc/wget/", timeout=20)
        found = re.findall(r"(\d+\.\d+(?:\.\d+)?)/64/wget\.exe", page)
        if found:
            ver = max(found, key=lambda v: tuple(int(x) for x in v.split(".")))
    except Exception:
        pass
    fetch("https://eternallybored.org/misc/wget/%s/64/wget.exe" % ver, os.path.join(BIN_DIR, "wget.exe"), log)


def install_7zr(log):
    _need_windows("7zr")
    log("Downloading 7zr (7-Zip's small unpacker, needed for MKVToolNix) ...")
    fetch("https://www.7-zip.org/a/7zr.exe", os.path.join(BIN_DIR, "7zr.exe"), log)


def install_mkvmerge(log):
    """mkvmerge.exe from the official MKVToolNix portable build (it runs on its own)."""
    _need_windows("mkvmerge")
    if not tool_path("7zr"):
        install_7zr(log)
    page = None
    for attempt in (1, 2):  # the MKVToolNix site can be slow from Australia
        try:
            page = fetch_text("https://mkvtoolnix.download/windows/releases/", timeout=90)
            break
        except Exception:
            if attempt == 2:
                raise
            log("MKVToolNix's site is slow - trying again ...")
            time.sleep(5)
    vers = set(re.findall(r'href="(\d+\.\d+(?:\.\d+)?)/"', page))
    if not vers:
        raise RuntimeError("couldn't find MKVToolNix's latest version")
    ver = max(vers, key=lambda v: tuple(int(x) for x in v.split(".")))
    log("Downloading MKVToolNix %s (for mkvmerge) ..." % ver)
    tmp = os.path.join(BIN_DIR, "_mkvtoolnix.7z")
    out = os.path.join(BIN_DIR, "_mkvtoolnix")
    try:
        url = "https://mkvtoolnix.download/windows/releases/%s/mkvtoolnix-64-bit-%s.7z" % (ver, ver)
        try:
            fetch(url, tmp, log)
        except Exception:
            log("MKVToolNix download stalled - trying again ...")
            time.sleep(5)
            fetch(url, tmp, log)
        shutil.rmtree(out, ignore_errors=True)
        r = subprocess.run([tool_path("7zr"), "x", "-y", "-o" + out, tmp], capture_output=True, text=True,
                           creationflags=NO_WINDOW)
        found = glob.glob(os.path.join(out, "*", "mkvmerge.exe")) + glob.glob(os.path.join(out, "mkvmerge.exe"))
        if not found:
            raise RuntimeError("mkvmerge.exe wasn't in the download (%s)" % (r.stderr or r.stdout).strip()[-200:])
        for exe in ("mkvmerge.exe", "mkvextract.exe", "mkvinfo.exe", "mkvpropedit.exe"):
            src = os.path.join(os.path.dirname(found[0]), exe)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(BIN_DIR, exe))
    finally:
        shutil.rmtree(out, ignore_errors=True)
        if os.path.exists(tmp):
            os.remove(tmp)
    log("mkvmerge installed")


def install_subtitleedit(log):
    """SubtitleEdit's portable build into bin\\SubEdit (needs .NET Framework 4.8, built into Windows 10/11)."""
    _need_windows("SubtitleEdit")
    log("Downloading SubtitleEdit (portable) ...")
    # SubtitleEdit 5 dropped "SubtitleEdit.exe /convert" (it has a separate SeConv tool), and the
    # old bats + TasRip use /convert, so get the newest 4.x portable build ("SE4xxx.zip").
    url = _github_asset("SubtitleEdit/subtitleedit", r"^SE4\d+\.zip$", deep=True)
    if not url:
        raise RuntimeError("couldn't find SubtitleEdit's portable download")
    tmp = os.path.join(BIN_DIR, "_subtitleedit.zip")
    dest = os.path.join(BIN_DIR, "SubEdit")
    try:
        fetch(url, tmp, log)
        shutil.rmtree(dest, ignore_errors=True)
        with zipfile.ZipFile(tmp) as z:
            z.extractall(dest)
        # the bats expect bin\SubEdit\SubtitleEdit.exe: lift it up if the zip had a top folder
        exe = tool_path("SubtitleEdit")
        if exe and os.path.dirname(exe) != dest:
            inner = os.path.dirname(exe)
            for f in os.listdir(inner):
                shutil.move(os.path.join(inner, f), os.path.join(dest, f))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if not tool_path("SubtitleEdit"):
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("SubtitleEdit.exe wasn't in the download")
    log("SubtitleEdit installed to bin\\\\SubEdit")


EXTRA_TOOLS = (("mkvmerge", "mkvmerge", "install_mkvmerge"), ("SubtitleEdit", "SubtitleEdit", "install_subtitleedit"),
               ("jq", "jq", "install_jq"), ("wget", "wget", "install_wget"), ("7zr", "7zr", "install_7zr"))
EXTRA_RETRY_FILE = os.path.join(BIN_DIR, ".extras-tried")


def install_extras(log, force=False):
    """The pack's extra tools, fetched quietly in the background (retried at most once a day)."""
    if not IS_WIN:
        return
    if not force and stamp_age(EXTRA_RETRY_FILE) < 20 * 3600:
        return
    write_stamp(EXTRA_RETRY_FILE)
    for label, name, fn in EXTRA_TOOLS:
        if not tool_path(name):
            try:
                globals()[fn](log)
            except Exception as e:
                log("Couldn't get %s (optional): %s" % (label, e))


# ------------------------------------------------------------ mkvmerge muxing and SubtitleEdit conversion
def _mkv_tracks(mkvmerge, path):
    r = subprocess.run([mkvmerge, "-J", path], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120, creationflags=NO_WINDOW)
    try:
        return json.loads(r.stdout).get("tracks", [])
    except ValueError:
        return []


def _mkv_tags(meta):
    from xml.sax.saxutils import escape
    blocks = []
    for level, items in ((70, (("TITLE", meta.get("show")),)),
                         (60, (("PART_NUMBER", meta.get("season_number")),)),
                         (50, (("TITLE", meta.get("title")), ("PART_NUMBER", meta.get("episode_sort")),
                               ("DESCRIPTION", meta.get("description"))))):
        simple = "".join("<Simple><Name>%s</Name><String>%s</String></Simple>" % (k, escape(str(v)))
                         for k, v in items if v not in (None, ""))
        if simple:
            blocks.append("<Tag><Targets><TargetTypeValue>%d</TargetTypeValue></Targets>%s</Tag>" % (level, simple))
    return '<?xml version="1.0" encoding="UTF-8"?>\n<Tags>%s</Tags>\n' % "".join(blocks) if blocks else None


def mux_mkvmerge(mkvmerge, media, subs, out, meta):
    """The Tas Rip Pack's mkvmerge mux: video (und) + English audio, subtitles tagged and titled,
    forced ones flagged forced, none on by default, HD video tagged BT.709. Raises RuntimeError."""
    tmp = out[:-4] + ".mrtmp.mkv"
    a = [mkvmerge, "--priority", "lower", "-o", tmp]
    if meta and meta.get("title"):
        a += ["--title", str(meta["title"])]
    tags = _mkv_tags(meta or {})
    tag_file = None
    if tags:
        tag_file = tmp + ".tags.xml"
        with open(tag_file, "w", encoding="utf-8") as f:
            f.write(tags)
        a += ["--global-tags", tag_file]
    for n, f in enumerate(media):
        tracks = _mkv_tracks(mkvmerge, f)
        a += ["--no-global-tags", "--no-subtitles"] + ([] if n == 0 else ["--no-video"])
        for t in tracks:
            if t.get("type") == "video" and n == 0:
                a += ["--language", "%d:und" % t["id"]]
                dims = str(t.get("properties", {}).get("pixel_dimensions", "0x0")).split("x")
                if len(dims) == 2 and dims[1].isdigit() and int(dims[1]) >= 720 \
                        and "color_matrix_coefficients" not in t.get("properties", {}):
                    a += ["--color-matrix-coefficients", "%d:1" % t["id"], "--color-transfer-characteristics",
                          "%d:1" % t["id"], "--color-primaries", "%d:1" % t["id"]]
            elif t.get("type") == "audio":
                a += ["--language", "%d:en" % t["id"]]
        a.append(f)
    for f in subs:
        base, forced = sub_info(f)
        name = LANG_NAMES.get(base, base.upper())
        a += ["--sub-charset", "0:UTF-8", "--language", "0:%s" % (base if base != "und" else "und"),
              "--track-name", "0:%s" % (("Forced " if forced else "") + name),
              "--default-track-flag", "0:no", "--forced-display-flag", "0:%s" % ("yes" if forced else "no"), f]
    try:
        r = subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW)
    finally:
        if tag_file and os.path.exists(tag_file):
            os.remove(tag_file)
    if r.returncode not in (0, 1) or not os.path.isfile(tmp):  # 1 = finished with warnings
        if os.path.exists(tmp):
            os.remove(tmp)
        err = [l for l in (r.stdout + r.stderr).splitlines() if l.startswith("Error")] or ["exit %d" % r.returncode]
        raise RuntimeError("mkvmerge couldn't mux the file: " + err[-1])
    os.replace(tmp, out)


def convert_subs_subtitleedit(folder, log=lambda t: None):
    """Convert subtitle files ffmpeg/yt-dlp couldn't (DFXP/TTML/odd WebVTT) to SRT with SubtitleEdit,
    the way the Tas Rip Pack did. Only touches subs.* files that have no .srt yet. -> files made"""
    se = tool_path("SubtitleEdit")
    if not se:
        return []
    made = []
    for f in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(f)
        if not f.startswith("subs.") or ext.lower() not in (".dfxp", ".ttml", ".xml", ".vtt", ".ass", ".ssa", ".sami", ".smi"):
            continue
        if os.path.exists(os.path.join(folder, stem + ".srt")):
            continue
        log("Converting %s to SRT with SubtitleEdit ..." % f)
        try:
            subprocess.run([se, "/convert", os.path.join(folder, f), "SubRip", "/outputfolder:" + folder,
                            "/encoding:utf-8", "/overwrite"], capture_output=True, timeout=180, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as e:
            log("SubtitleEdit couldn't convert it: %s" % e)
            continue
        if os.path.exists(os.path.join(folder, stem + ".srt")):
            made.append(os.path.join(folder, stem + ".srt"))
    return made


# ------------------------------------------------------------ Firefox sign-in, automatically
FIREFOX_SITES = {"7plus.com.au": "7plus", "sbs.com.au": "SBS On Demand", "9now.com.au": "9Now",
                 "abc.net.au": "ABC iView"}
_FF_CACHE = {}


def firefox_site(url):
    host = canonical_site(url_host(url))
    return next((d for d in FIREFOX_SITES if host_matches(host, d)), None)


def firefox_state(domain):
    """firefox_signin(), remembered for 10 minutes so a whole season doesn't re-read Firefox each time."""
    hit = _FF_CACHE.get(domain)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        state = firefox_signin(domain)
    except Exception:
        state = "no"
    _FF_CACHE[domain] = (time.time(), state)
    return state


# ------------------------------------------------------------ 1.0.8: keeping the tools up to date
TOOL_VERSIONS_FILE = os.path.join(BIN_DIR, ".tool-versions.json")
TOOL_UPDATE_FILE = os.path.join(BIN_DIR, ".last-tool-update")
# tool -> (files/folders it owns in bin, installer, days between checks)
TOOL_FILES = {
    "ffmpeg": (("ffmpeg" + EXE, "ffprobe" + EXE), "install_ffmpeg", 30),
    "N_m3u8DL-RE": (("N_m3u8DL-RE" + EXE,), "install_nm3u8", 7),
    "aria2c": (("aria2c" + EXE,), "install_aria2c", 7),
    "streamlink": (("streamlink",), "install_streamlink", 7),
    "mkvmerge": (("mkvmerge.exe", "mkvextract.exe", "mkvinfo.exe", "mkvpropedit.exe"), "install_mkvmerge", 7),
    "SubtitleEdit": (("SubEdit",), "install_subtitleedit", 7),
    "jq": (("jq.exe",), "install_jq", 30),
    "wget": (("wget.exe",), "install_wget", 30),
}


def _gh_asset_ref(repo, pattern, deep=False):
    """'url|updated' of a repo's newest matching release file (changes when a new build is out)."""
    for path in ("/releases/latest", "/releases?per_page=%d" % (100 if deep else 5)):
        data = gh_json(GITHUB_API + "/repos/" + repo + path)
        for rel in (data if isinstance(data, list) else [data]):
            for a in rel.get("assets", []):
                if re.search(pattern, a.get("name", ""), re.I):
                    return "%s|%s" % (a["browser_download_url"], a.get("updated_at", ""))
    return None


def latest_tool_ref(name):
    """Something that changes whenever a new version of the tool is published (or None)."""
    if name == "ffmpeg":
        return _gh_asset_ref("yt-dlp/FFmpeg-Builds", r"master-latest-win64-gpl\.zip$")
    if name == "N_m3u8DL-RE":
        return _gh_asset_ref("nilaoda/N_m3u8DL-RE", r"_%s_.*\.(zip|tar\.gz)$" % re.escape(NM3U8_ASSET))
    if name == "aria2c":
        return _gh_asset_ref("aria2/aria2", r"win-64bit.*\.zip$")
    if name == "streamlink":
        return _gh_asset_ref("streamlink/windows-builds", r"x86_64\.zip$")
    if name == "SubtitleEdit":
        return _gh_asset_ref("SubtitleEdit/subtitleedit", r"^SE4\d+\.zip$", deep=True)
    if name == "jq":
        return _gh_asset_ref("jqlang/jq", r"^jq-windows-amd64\.exe$")
    if name == "mkvmerge":
        page = fetch_text("https://mkvtoolnix.download/windows/releases/", timeout=90)
        vers = re.findall(r'href="(\d+\.\d+(?:\.\d+)?)/"', page)
        return max(vers, key=lambda v: tuple(int(x) for x in v.split("."))) if vers else None
    if name == "wget":
        page = fetch_text("https://eternallybored.org/misc/wget/", timeout=30)
        vers = re.findall(r"(\d+\.\d+(?:\.\d+)?)/64/wget\.exe", page)
        return max(vers, key=lambda v: tuple(int(x) for x in v.split("."))) if vers else None
    return None


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def tool_works(name):
    v = tool_version(name)
    return bool(v) and not v.startswith(("not installed", "error", "couldn't", "Couldn't"))


def update_tool(name, log, busy=lambda: False):
    """Replace one tool with its newest version - the old files are only deleted once the new
    one runs; if anything goes wrong the old ones go back. -> "updated", "same", "skipped" or an error"""
    files, installer, _ = TOOL_FILES[name]
    versions = _load_json(TOOL_VERSIONS_FILE, {})
    ref = latest_tool_ref(name)
    if not ref:
        return "no version information"
    have = [f for f in files if os.path.exists(os.path.join(BIN_DIR, f))]
    if not have:
        return "skipped"  # not installed here - first start / Reinstall handles that
    if versions.get(name) == ref:
        return "same"
    if name not in versions:
        newest = max(os.path.getmtime(os.path.join(BIN_DIR, f)) for f in have)
        if time.time() - newest < 30 * 86400:  # installed recently by an older TasRip: just note it
            versions[name] = ref
            _save_json(TOOL_VERSIONS_FILE, versions)
            return "same"
    if busy():
        return "skipped"
    old = os.path.join(BIN_DIR, ".old", name)
    shutil.rmtree(old, ignore_errors=True)
    os.makedirs(old, exist_ok=True)
    moved = []
    try:
        for f in have:
            os.replace(os.path.join(BIN_DIR, f), os.path.join(old, f))
            moved.append(f)
    except OSError as e:  # in use right now (a download is running it) - put back, try next time
        for f in moved:
            os.replace(os.path.join(old, f), os.path.join(BIN_DIR, f))
        return "skipped (in use: %s)" % e
    try:
        globals()[installer](log)
        if not tool_works(name):
            raise RuntimeError("the new version didn't run")
    except Exception as e:
        for f in files:
            target = os.path.join(BIN_DIR, f)
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            elif os.path.exists(target):
                os.remove(target)
        for f in moved:
            os.replace(os.path.join(old, f), os.path.join(BIN_DIR, f))
        shutil.rmtree(old, ignore_errors=True)
        return "kept the old version (%s)" % e
    shutil.rmtree(old, ignore_errors=True)
    versions[name] = ref
    _save_json(TOOL_VERSIONS_FILE, versions)
    return "updated"


def update_all_tools(log, busy=lambda: False, force=False):
    """Weekly check of every helper tool (yt-dlp updates itself daily). -> {tool: result}"""
    if not IS_WIN:
        return {}
    out = {}
    checked = _load_json(TOOL_UPDATE_FILE, {}) if not force else {}
    for name, (_, _, days) in TOOL_FILES.items():
        if busy():
            break
        if not force and time.time() - float(checked.get(name, 0)) < days * 86400:
            continue
        try:
            out[name] = update_tool(name, log, busy)
        except Exception as e:
            out[name] = "couldn't check (%s)" % e
        checked[name] = time.time()
        if out[name] == "updated":
            log("Updated %s: %s" % (name, tool_version(name)))
    _save_json(TOOL_UPDATE_FILE, checked)
    return out


# ------------------------------------------------------------ 1.0.8: antivirus, Mac junk
SEEN_TOOLS_FILE = os.path.join(BIN_DIR, ".tools-seen.json")


def vanished_tools():
    """Tools that were here before but have gone (usually an antivirus quarantining them)."""
    seen = _load_json(SEEN_TOOLS_FILE, [])
    gone = [t for t in seen if not tool_path(t)]
    now = sorted(set([t for t in seen if tool_path(t)] +
                     [n for _, n, _ in SETUP_TOOLS + EXTRA_TOOLS if tool_path(n)]))
    _save_json(SEEN_TOOLS_FILE, now)
    return gone


def is_virus_block(err):
    t = str(err).lower()
    return "winerror 225" in t or "contains a virus" in t or "potentially unwanted" in t or "winerror 216" in t


def defender_exclusion_command(path=None):
    """PowerShell command line that asks Windows (with its permission prompt) to exclude the folder."""
    path = (path or BASE_DIR).replace("'", "''")
    inner = "Add-MpPreference -ExclusionPath ''%s''" % path.replace("'", "''")
    return ["powershell", "-NoProfile", "-Command",
            "Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile -Command %s'" % inner]


APPLEDOUBLE = b"\x00\x05\x16\x07"


def clean_mac_junk(root=None, log=lambda t: None):
    """Delete the "._name" and .DS_Store files (and __MACOSX folders) a Mac leaves behind - they
    once made TasRip pick a fake engine. Only real AppleDouble stubs are removed. -> count"""
    root = root or BASE_DIR
    n = 0
    for dirpath, dirs, files in os.walk(root):
        if "__MACOSX" in dirs:
            shutil.rmtree(os.path.join(dirpath, "__MACOSX"), ignore_errors=True)
            dirs.remove("__MACOSX")
            n += 1
        dirs[:] = [d for d in dirs if d not in (WORK_DIR, ".old")]
        for f in files:
            full = os.path.join(dirpath, f)
            try:
                if f == ".DS_Store" or (f.startswith("._") and open(full, "rb").read(4) == APPLEDOUBLE):
                    os.remove(full)
                    n += 1
            except OSError:
                pass
    if n:
        log("Cleaned up %d leftover Mac file(s) (\"._\" / .DS_Store)." % n)
    return n


# ------------------------------------------------------------ 1.0.8: remembered queue
QUEUE_FILE = os.path.join(BASE_DIR, "queue.json")


def save_queue(items):
    keep = []
    for it in items:
        if it.get("live") or it["status"] not in ("Queued", "Working", "Failed", "Stopped"):
            continue
        keep.append({"url": it["url"], "opts": it["opts"], "title": it.get("title", ""),
                     "status": "Queued" if it["status"] == "Working" else it["status"],
                     "shown": it.get("shown", "")})
    if keep:
        _save_json(QUEUE_FILE, keep)
    elif os.path.exists(QUEUE_FILE):
        try:
            os.remove(QUEUE_FILE)
        except OSError:
            pass


def load_queue():
    d = _load_json(QUEUE_FILE, [])
    return [x for x in d if isinstance(x, dict) and x.get("url") and isinstance(x.get("opts"), dict)] \
        if isinstance(d, list) else []


# ------------------------------------------------------------ 1.0.8: subtitle check
_SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def srt_cues(path):
    try:
        text = open(path, encoding="utf-8-sig", errors="replace").read()
    except OSError:
        return []
    out = []
    for m in _SRT_TIME.finditer(text):
        g = [int(x) for x in m.groups()]
        out.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0))
    return sorted(out)


def _mmss(sec):
    sec = int(sec)
    return "%d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60) if sec >= 3600 else "%d:%02d" % (sec // 60, sec % 60)


def subtitle_problems(path, duration):
    """Signs a subtitle file is incomplete (the 7plus missing-lines problem). -> list of findings"""
    cues = srt_cues(path)
    if not cues:
        return ["no subtitle lines at all"]
    found = []
    last_end = max(e for _, e in cues)
    if duration and duration > 600 and last_end < duration * 0.75 - 60:
        found.append("they stop at %s of %s" % (_mmss(last_end), _mmss(duration)))
    gap, at = 0, 0
    for (s1, e1), (s2, _) in zip(cues, cues[1:]):
        if s2 - e1 > gap:
            gap, at = s2 - e1, e1
    if gap > 300 and (not duration or at < duration - 300):
        found.append("a %d-minute gap at %s" % (gap // 60, _mmss(at)))
    if duration and duration > 600 and len(cues) < duration / 60 * 1.5:
        found.append("only %d lines for a %d-minute show" % (len(cues), duration // 60))
    return found


# ------------------------------------------------------------ 1.0.8: expiry dates (where a site gives one)
_EXPIRY_KEY = re.compile(r"expir|available_?(until|to|end)|availability_?end|end_?date|offline_?date", re.I)


def expiry_of(info):
    """When an episode leaves the site, if the details yt-dlp returned say so (many sites don't).
    -> epoch seconds or None"""
    import datetime
    for k, v in (info or {}).items():
        if not _EXPIRY_KEY.search(str(k)) or v in (None, "", 0):
            continue
        try:
            if isinstance(v, (int, float)) or str(v).isdigit():
                n = float(v)
                if len(str(int(n))) == 8:  # YYYYMMDD
                    return datetime.datetime.strptime(str(int(n)), "%Y%m%d").timestamp()
                return n / 1000.0 if n > 1e11 else n
            text = str(v).replace("Z", "+00:00")
            return datetime.datetime.fromisoformat(text[:25]).timestamp()
        except (ValueError, OverflowError):
            continue
    return None


# ------------------------------------------------------------ 1.0.8: daytime speed limit
def scheduled_rate(o, now=None):
    """The speed limit to use right now: the daytime limit inside its hours, else the normal one."""
    if not o.get("sched_on"):
        return o.get("rate", "")
    lt = time.localtime(now) if now else time.localtime()
    cur = lt.tm_hour * 60 + lt.tm_min

    def mins(t):
        m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", str(t))
        return int(m.group(1)) * 60 + int(m.group(2)) if m else None
    a, b = mins(o.get("sched_from")), mins(o.get("sched_to"))
    if a is None or b is None or a == b:
        return o.get("rate", "")
    inside = a <= cur < b if a < b else (cur >= a or cur < b)
    return o.get("sched_rate", "") if inside else o.get("rate", "")


# ------------------------------------------------------------ 1.0.8: export / import setup
EXPORT_FILES = ("settings.json", "watchlist.json", "history.jsonl", "engines.json", "archive.txt", "queue.json")


def export_setup(dest):
    """Settings, watch list, history, engines and archive in one zip. Passwords (logins.dat) never."""
    n = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in EXPORT_FILES:
            src = os.path.join(BASE_DIR, f)
            if os.path.isfile(src):
                z.write(src, f)
                n += 1
        z.writestr("tasrip-setup.txt", "%s v%s setup export - %s\n" % (APP_NAME, VERSION, time.strftime("%Y-%m-%d %H:%M")))
    return n


def import_setup(src):
    """Restore an export_setup() zip (only the known files; the current ones are kept as .bak). -> names"""
    done = []
    with zipfile.ZipFile(src) as z:
        names = {os.path.basename(n): n for n in z.namelist()}
        if not any(f in names for f in EXPORT_FILES):
            raise RuntimeError("that isn't a %s setup file" % APP_NAME)
        for f in EXPORT_FILES:
            if f in names:
                target = os.path.join(BASE_DIR, f)
                if os.path.exists(target):
                    shutil.copy2(target, target + ".bak")
                with z.open(names[f]) as r, open(target, "wb") as w:
                    shutil.copyfileobj(r, w)
                done.append(f)
    return done


# ------------------------------------------------------------ Plex / Jellyfin naming
def plex_rel(info, ext, audio=False):
    """Where Plex/Jellyfin expect this file, relative to the save folder."""
    t = lambda v: nice_name(v) if v not in (None, "") else ""
    title = t(info.get("episode") or info.get("title")) or t(info.get("id")) or "Untitled"
    if audio:
        show = t(info.get("series") or info.get("playlist_title") or info.get("uploader")) or "Audio"
        return os.path.join("Audio", show, title + ext)
    show = t(info.get("series"))
    sn, en = info.get("season_number"), info.get("episode_number")
    if show or (sn is not None and en is not None):
        show = show or t(info.get("playlist_title") or info.get("uploader")) or "Unknown show"
        sn = int(sn) if str(sn).isdigit() else 1
        if str(en).isdigit():
            name = "%s - S%02dE%02d - %s" % (show, sn, int(en), title)
        else:  # date-based shows (news, talk shows)
            d = str(info.get("release_date") or info.get("upload_date") or "")
            day = "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 else ""
            name = "%s - %s - %s" % (show, day, title) if day else "%s - %s" % (show, title)
        return os.path.join("TV Shows", show, "Season %02d" % sn, name + ext)
    year = str(info.get("release_year") or (info.get("release_date") or info.get("upload_date") or "")[:4] or "")
    movie = t(info.get("title")) or title
    name = "%s (%s)" % (movie, year) if year.isdigit() else movie
    return os.path.join("Movies", name, name + ext)


def plex_move(path, info, outdir, audio=False, log=lambda t: None):
    """Move a downloaded file and its subtitles/thumbnail into the Plex layout. -> new path."""
    if not os.path.isfile(path):
        return path
    stem, ext = os.path.splitext(path)
    target = os.path.join(outdir, plex_rel(info, ext, audio))
    if os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(path)):
        return path
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tstem = os.path.splitext(target)[0]
    folder, base = os.path.split(stem)
    for f in os.listdir(folder or "."):  # sidecars: "Title [id].en.srt", "Title [id].jpg"
        full = os.path.join(folder, f)
        if f.startswith(base + ".") and os.path.isfile(full):
            try:
                os.replace(full, tstem + f[len(base):])
            except OSError as e:
                log("Couldn't move %s: %s" % (f, e))
    log("Filed for Plex/Jellyfin: %s" % os.path.relpath(target, outdir))
    return target


# ------------------------------------------------------------ radio and podcasts
AUDIO_PAGES = re.compile(r"abc\.net\.au/(?:listen|radio|radionational|triplej|classic|jazz|kidslisten)\b|"
                         r"sbs\.com\.au/(?:audio|language/[^?#]*podcast)|\.(?:rss|xml)(?:$|\?)|/feed/?(?:$|\?)|"
                         r"/podcast\.xml|feeds\.", re.I)


def is_audio_page(url):
    return bool(AUDIO_PAGES.search(url))


def find_feed(page_url, html=None):
    """The podcast/RSS feed a page advertises (ABC Listen, SBS Audio, most podcast pages), or None."""
    if html is None:
        try:
            html = fetch_text(page_url, timeout=20)
        except Exception:
            return None
    if html.lstrip()[:200].lower().startswith(("<?xml", "<rss", "<feed")) or "<rss" in html[:500].lower():
        return page_url  # already a feed
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        if re.search(r"""type\s*=\s*["']application/(?:rss|atom)\+xml""", tag, re.I):
            m = re.search(r"""href\s*=\s*["']([^"']+)""", tag, re.I)
            if m:
                return urllib.parse.urljoin(page_url, m.group(1).replace("&amp;", "&"))
    m = re.search(r"""["'](https?://[^"'\s]+?(?:/podcast\.xml|\.rss|/rss/?|/feed/?))["']""", html, re.I)
    return m.group(1) if m else None


# ------------------------------------------------------------ iView channel / category pages
IVIEW_LIST = re.compile(r"iview\.abc\.net\.au/(channel|category|collection)/([a-z0-9-]+)", re.I)


def iview_shows(url, log=lambda t: None):
    """Show pages linked from an iView channel or category page (ABC Kids, ABC Entertains ...).

    iView builds those pages with JavaScript, so this reads the page and iView's own data feed
    and collects every /show/ link it can find. -> [(title, url)]"""
    m = IVIEW_LIST.search(url)
    if not m:
        return []
    kind, slug = m.group(1).lower(), m.group(2).lower()
    sources = [url, "https://api.iview.abc.net.au/v3/%s/%s" % ("category" if kind != "channel" else "channel", slug)]
    if kind == "channel":
        sources.append("https://api.iview.abc.net.au/v3/category/" + slug)
    found = {}
    for src in sources:
        try:
            text = fetch_text(src, headers={"Accept": "application/json, text/html"}, timeout=20)
        except Exception as e:
            log("  (couldn't read %s: %s)" % (src, e))
            continue
        text = text.replace("\\/", "/")
        for show in re.findall(r"/show/([a-z0-9][a-z0-9-]*)", text):
            found.setdefault(show, None)
        for title, show in re.findall(r'"title"\s*:\s*"([^"]{1,120})"[^{}]{0,400}?/show/([a-z0-9][a-z0-9-]*)', text):
            if found.get(show) is None:
                found[show] = title
    return [(title or show.replace("-", " ").title(), "https://iview.abc.net.au/show/" + show)
            for show, title in found.items()]


# ------------------------------------------------------------ live recording
def stream_address(url, o, log=lambda t: None):
    """(kind, address) for a live page: ("hls", stream URL) via yt-dlp, else ("streamlink", url)."""
    if re.search(r"\.(m3u8|mpd)(\?|$)", url, re.I):
        return "hls", url
    exe = tool_path("yt-dlp")
    if exe:
        args = [exe, "--ignore-config", "--no-warnings", "-g", "-f", "b/bv*+ba", "--no-playlist"] + \
            tls_args() + plugin_args(exe)
        if o.get("cookies", "None") != "None":
            args += ["--cookies-from-browser", o["cookies"]]
        try:
            r = subprocess.run(args + ["--", url], capture_output=True, text=True, timeout=90,
                               encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            lines = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("http")]
            if r.returncode == 0 and lines:
                return "hls", lines[0]
            log("yt-dlp couldn't find a live stream there%s" % (": " + r.stderr.strip().splitlines()[-1]
                                                              if r.stderr.strip() else ""))
        except (OSError, subprocess.SubprocessError) as e:
            log("yt-dlp couldn't run: %s" % e)
    if tool_path("streamlink"):
        log("Trying Streamlink instead ...")
        return "streamlink", url
    raise RuntimeError("no live stream found on that page (and Streamlink isn't installed)")


def record_live(url, seconds, out, o, on_progress, set_proc, log, stopped=lambda: False):
    """Record a live stream for `seconds` into `out` (.mkv). Raises RuntimeError on failure."""
    kind, addr = stream_address(url, o, log)
    ffmpeg = tool_path("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is missing")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    ts = os.path.splitext(out)[0] + ".recording.ts"
    if kind == "hls":
        args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1",
                "-user_agent", "Mozilla/5.0", "-i", addr, "-t", str(int(seconds)), "-map", "0:v?", "-map", "0:a?",
                "-c", "copy", "-f", "mpegts", ts]
    else:
        args = [tool_path("streamlink"), "--hls-duration", "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60),
                "--force", "-o", ts, addr, "best"]
    log("Recording %d min from %s" % (seconds // 60, "the stream" if kind == "hls" else "Streamlink"))
    started = time.time()
    p = popen(args)
    set_proc(p)
    tail = []
    for line in p.stdout:
        line = line.strip()
        if line.startswith("out_time_ms="):
            try:
                on_progress(min(100.0, int(line[12:]) / 1e6 * 100 / seconds))
            except ValueError:
                pass
        elif kind != "hls":
            on_progress(min(100.0, (time.time() - started) * 100 / seconds))
        if line and not line.startswith(("frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time",
                                         "dup_frames=", "drop_frames=", "speed=", "progress=")):
            tail.append(line)
            tail = tail[-6:]
    p.wait()
    if not os.path.isfile(ts) or os.path.getsize(ts) < 10000:
        if os.path.exists(ts):
            os.remove(ts)
        if stopped():
            raise RuntimeError("stopped")
        raise RuntimeError(tail[-1] if tail else "nothing was recorded")
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", ts, "-map", "0",
                        "-c", "copy", out], capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode == 0 and os.path.isfile(out):
        os.remove(ts)
        return out
    log("Couldn't tidy the recording into MKV, kept it as .ts")
    final = os.path.splitext(out)[0] + ".ts"
    os.replace(ts, final)
    return final


# ------------------------------------------------------------ disk, notifications
def disk_free_gb(path):
    while path and not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    try:
        return shutil.disk_usage(path or ".").free / 1024 ** 3
    except OSError:
        return None


def notify(title, message, sound=True):
    """A Windows notification (tray balloon) and sound. Quietly does nothing elsewhere."""
    if not IS_WIN:
        return
    if sound:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
    q = lambda t: "'" + t.replace("'", "''") + "'"
    ps = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
          "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
          "$n.Visible = $true; $n.ShowBalloonTip(8000, %s, %s, 'Info'); Start-Sleep -Seconds 9; $n.Dispose()"
          % (q(title), q(message)))
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def set_titlebar_dark(win, dark):
    """Dark Windows title bar to match dark mode (Windows 10 2004+ / 11)."""
    if not IS_WIN:
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


# ------------------------------------------------------------ history and watch list
def add_history(entry):
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_history():
    out = []
    try:
        with open(HISTORY_FILE, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if isinstance(e, dict):
                        out.append(e)
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def load_watch():
    try:
        with open(WATCH_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return [w for w in d if isinstance(w, dict) and w.get("url")] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def save_watch(items):
    try:
        with open(WATCH_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def stamp_age(path):
    try:
        return time.time() - float(open(path).read().strip())
    except (OSError, ValueError):
        return float("inf")


def write_stamp(path):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def list_entries(exe, url, o, log=lambda t: None):
    """Everything a season / series / playlist / feed / channel page holds, for the episode
    picker. -> [{"title", "url", "season", "episode", "items"}]"""
    show = ten_show_slug(url)
    if show:
        out = []
        for season in ten_show_seasons(show, log):
            out += list_entries(exe, season, o, log) if "/tpv" not in season else \
                [{"title": season.rsplit("/", 1)[-1], "url": season}]
        return out
    if IVIEW_LIST.search(url):
        return [{"title": t, "url": u, "season": "", "episode": "show"} for t, u in iview_shows(url, log)]
    target = url
    if is_audio_page(url) or o.get("mode") == "Audio":
        target = find_feed(url) or url
    args = [exe, "--ignore-config", "--no-colors", "--encoding", "utf-8", "--flat-playlist", "--yes-playlist",
            "--simulate", "--print", "%()j"] + tls_args() + plugin_args(exe)
    if o.get("cookies", "None") != "None":
        args += ["--cookies-from-browser", o["cookies"]]
    r = subprocess.run(args + split_extra(o.get("extra", "")) + ["--", target], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300, creationflags=NO_WINDOW)
    out = []
    for n, line in enumerate((l for l in r.stdout.splitlines() if l.startswith("{")), 1):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        u = e.get("url") if str(e.get("url", "")).startswith("http") else e.get("webpage_url")
        entry = {"title": e.get("title") or e.get("episode") or e.get("id") or "item %d" % n,
                 "season": e.get("season_number") or "", "episode": e.get("episode_number") or "",
                 "date": e.get("release_date") or e.get("upload_date") or ""}
        if str(u or "").startswith("http") and u != target and "__youtubedl_smuggle" not in u:
            entry["url"] = u
        else:  # no separate address: download it from the list by its position
            entry.update(url=target, items=str(e.get("playlist_index") or n))
        out.append(entry)
    if not out and r.returncode != 0:
        err = [l for l in r.stderr.splitlines() if l.startswith("ERROR")]
        raise RuntimeError(err[-1][7:] if err else "couldn't read that page")
    return out


# ------------------------------------------------------------ app updates and migration
OLD_NAMES = ("MediaRipper",)


def migrate_old_install(log=print):
    """First start: copy settings, archive, engines and tools from an old MediaRipper folder
    next to this one, so nothing has to be set up or downloaded again. -> list of what was copied"""
    if os.path.isfile(SETTINGS_FILE):
        return []
    parent = os.path.dirname(BASE_DIR)
    try:
        candidates = [os.path.join(parent, d) for d in os.listdir(parent)
                      if d.lower().startswith(tuple(n.lower() for n in OLD_NAMES))]
    except OSError:
        return []
    candidates = [c for c in candidates if os.path.isfile(os.path.join(c, "settings.json"))
                  and os.path.abspath(c) != os.path.abspath(BASE_DIR)]
    if not candidates:
        return []
    old = max(candidates, key=lambda c: os.path.getmtime(os.path.join(c, "settings.json")))
    copied = []
    for name in ("settings.json", "archive.txt", "engines.json", "history.jsonl", "watchlist.json"):
        src = os.path.join(old, name)
        if os.path.isfile(src) and not os.path.exists(os.path.join(BASE_DIR, name)):
            shutil.copy2(src, os.path.join(BASE_DIR, name))
            copied.append(name)
    os.makedirs(BIN_DIR, exist_ok=True)
    for tool in ("yt-dlp", "ffmpeg", "ffprobe", "N_m3u8DL-RE", "aria2c"):
        src = os.path.join(old, "bin", tool + EXE)
        if os.path.isfile(src) and not os.path.exists(os.path.join(BIN_DIR, tool + EXE)):
            shutil.copy2(src, os.path.join(BIN_DIR, tool + EXE))
            copied.append(tool)
    for folder in ("engines", "streamlink"):
        src = os.path.join(old, "bin", folder)
        if os.path.isdir(src) and not os.path.exists(os.path.join(BIN_DIR, folder)):
            shutil.copytree(src, os.path.join(BIN_DIR, folder))
            copied.append(folder)
    if copied:
        log("Brought across from %s: %s" % (os.path.basename(old), ", ".join(copied)))
    return copied


def headless_update(full=False, log=print):
    """Update.bat: fetch missing tools, update yt-dlp, and (full) reinstall every helper tool."""
    global USE_NIGHTLY, USE_SYSTEM_CERTS
    os.makedirs(BIN_DIR, exist_ok=True)
    migrate_old_install(log)
    s = load_settings()
    USE_NIGHTLY, USE_SYSTEM_CERTS = bool(s.get("nightly")), bool(s.get("syscerts"))
    for label, name, fn in SETUP_TOOLS:
        if name == "yt-dlp" or (tool_path(name) and not full):
            continue
        try:
            globals()[fn](log)
        except Exception as e:
            log("Couldn't get %s: %s" % (label, e))
    exe = tool_path("yt-dlp")
    try:
        if not exe:
            install_ytdlp(log)
        else:
            log("Updating yt-dlp (%s) ..." % ("nightly" if USE_NIGHTLY else "stable"))
            cmd = [exe, "--update-to", "nightly" if USE_NIGHTLY else "stable"]
            r = subprocess.run(cmd + tls_args(), capture_output=True, text=True, encoding="utf-8", errors="replace")
            text = (r.stdout + r.stderr).strip()
            if r.returncode != 0 and "CERTIFICATE_VERIFY_FAILED" in text and not USE_SYSTEM_CERTS:
                log("Security check blocked (antivirus HTTPS scanning?) - retrying with Windows' certificate checks ...")
                USE_SYSTEM_CERTS = True
                r = subprocess.run(cmd + tls_args(), capture_output=True, text=True, encoding="utf-8", errors="replace")
                text = (r.stdout + r.stderr).strip()
            for line in text.splitlines()[-3:]:
                log("  " + line)
        write_stamp(LAST_UPDATE_FILE)
    except Exception as e:
        log("Couldn't update yt-dlp: %s" % e)
    install_extras(log, force=True) if not full else None
    if full and IS_WIN:
        for label, name, fn in EXTRA_TOOLS:
            try:
                globals()[fn](log)
            except Exception as e:
                log("Couldn't get %s: %s" % (label, e))
    for label, name, _ in SETUP_TOOLS + EXTRA_TOOLS:
        log("  %-12s %s" % (label, "ok" if tool_path(name) else "MISSING"))


SETUP_TOOLS = (("yt-dlp", "yt-dlp", "install_ytdlp"), ("ffmpeg", "ffmpeg", "install_ffmpeg"),
               ("N_m3u8DL-RE", "N_m3u8DL-RE", "install_nm3u8"), ("aria2c", "aria2c", "install_aria2c"),
               ("Streamlink", "streamlink", "install_streamlink"))


def thumb_supported(o):
    """yt-dlp errors out (failing the download) if asked to embed a thumbnail into WAV or WebM."""
    if o["mode"] == "Audio":
        return o["audio_fmt"] != "wav"
    return o["container"] != "webm"


# What yt-dlp is doing, from the tag at the start of its output lines. Shown in the Status
# column so the long quiet stretches (merging, embedding, retries) don't look like a hang.
PHASES = {
    "[Merger]": "Merging", "[SubtitlesConvertor]": "Converting subs", "[EmbedSubtitle]": "Embedding subs",
    "[Metadata]": "Adding metadata", "[EmbedThumbnail]": "Adding thumbnail", "[ExtractAudio]": "Converting audio",
    "[FixupM3u8]": "Fixing up", "[FixupStretched]": "Fixing up", "[FixupDuplicateMoov]": "Fixing up",
    "[FixupTimestamp]": "Fixing up", "[FixupDuration]": "Fixing up", "[MoveFiles]": "Finishing",
}


def phase_of(line):
    """Status text for a yt-dlp output line, or None if it doesn't change what's happening."""
    for tag, text in PHASES.items():
        if line.startswith(tag):
            return text
    low = line.lower()
    if "retrying" in low or "got error" in low:
        return "Retrying"
    if line.startswith("[download] Destination:"):
        return "Downloading"
    if line.startswith("[info]") and "subtitles" in low and "writing" in low:
        return "Getting subs"
    return None


def mask_args(args):
    """Copy of a command line that is safe to show: account details are starred out."""
    out, hide = [], False
    for a in args:
        out.append("****" if hide else a)
        hide = a in ("--username", "-u", "--password", "-p")
    return out


_SCRUB = (
    (re.compile(r"eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]*"), "[token]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/'\"\r\n]+"), r"\1[user]"),
    (re.compile(r"(/(?:Users|home)/)[^/'\"\r\n]+"), r"\1[user]"),
    (re.compile(r"(?i)([?&](?:hdnea|hdnts|hdntl|token|auth|sig|signature|key|policy|key-pair-id|"
                r"access_token|refresh_token)=)[^&\s'\"]+"), r"\1[removed]"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1[token]"),
)


def scrub(text):
    """Remove account details, tokens and Windows user names so a log is safe to share."""
    for rx, sub in _SCRUB:
        text = rx.sub(sub, text)
    return text


def diagnose(ytdlp, url, o, login, log):
    """List what a URL offers (formats, DRM flags, subtitles) without downloading. Saves a report."""
    eng = engine_for(url)
    if eng:
        ytdlp = eng[1]
        log("Using engine %s for %s" % (os.path.basename(eng[1]), eng[0]))
    a = [ytdlp, "--ignore-config", "--no-colors", "--encoding", "utf-8", "-v", "-F", "--list-subs"] + tls_args() + plugin_args(ytdlp) + [
         "--yes-playlist" if o["playlist"] else "--no-playlist", "--playlist-items", "1"]
    if o["cookies"] != "None":
        a += ["--cookies-from-browser", o["cookies"]]
    if login and login[0]:
        a += ["--username", login[0], "--password", login[1]]
    a += ["--", url]
    lines = ["%s v%s diagnostic   %s" % (APP_NAME, VERSION, time.strftime("%Y-%m-%d %H:%M")),
             "URL: " + url,
             "Engine: " + (os.path.basename(eng[1]) if eng else "standard yt-dlp"),
             "Options: mode=%s quality=%s container=%s compat=%s subs=%s cookies=%s login=%s" % (
                 o["mode"], o["quality"], o["container"], o["compat"], o["subs"], o["cookies"],
                 "yes" if login and login[0] else "no"), ""]
    p = popen(a)
    for raw in p.stdout:
        line = scrub(raw.rstrip())
        lines.append(line)
        log(line)
    lines.append("\nexit code %s" % p.wait())
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "diagnostic-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def needs_account_hint(url, o, login):
    """A hint for sites that only work signed in, when no sign-in method is selected."""
    if "sbs.com.au" in url.lower() and o["cookies"] == "None" and not (login and login[0]):
        return ("Note: SBS On Demand needs a signed-in account. Sign in to SBS in your browser and pick "
                "that browser under 'Browser cookies' (Firefox is the most reliable), or use "
                "Tools > Site login. After one successful run the session is remembered on this PC.")
    return None


def parse_progress(line):
    """Return (percent_float, percent_text, speed, eta, resolution, title) or None."""
    if not line.startswith(PROG_TAG + "|"):
        return None
    parts = line.split("|", 5)
    if len(parts) < 6:
        return None
    pct_txt = parts[1].strip()
    try:
        pct = float(pct_txt.rstrip("%"))
    except ValueError:
        pct = 0.0
    res = parts[4].strip()
    if not res[:1].isdigit():  # "NA", "unknown", "audio only"
        res = ""
    return pct, pct_txt, parts[2].strip(), parts[3].strip(), res, parts[5].strip()


# Some PCs have their secure connections inspected by antivirus "HTTPS scanning" or a network
# filter. Windows trusts the inspector's certificate, but Python's and yt-dlp's own certificate
# lists don't, so downloads fail with CERTIFICATE_VERIFY_FAILED. When that happens TasRip
# downloads with Windows' own curl.exe instead, and tells yt-dlp to use Windows' certificates.
USE_SYSTEM_CERTS = False
CERT_CLOCK_WORDS = ("certificate has expired", "not yet valid", "certificate is not yet valid")


def is_cert_error(e):
    """True if an exception (or message) is a certificate-verification failure."""
    if isinstance(e, ssl.SSLCertVerificationError):
        return True
    if isinstance(e, urllib.error.URLError) and isinstance(getattr(e, "reason", None), ssl.SSLError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(e)


def cert_hint(text):
    """Plain-English advice for a certificate failure message."""
    low = str(text).lower()
    if any(w in low for w in CERT_CLOCK_WORDS):
        return ("This PC's date or time looks wrong, so secure websites can't be checked. "
                "Fix the clock (Settings > Time & language > Set time automatically) and try again.")
    return ("A secure connection was blocked from being checked - usually antivirus 'HTTPS / web "
            "scanning' (Avast, AVG, Kaspersky, ESET, Bitdefender...) or a work/school network filter. "
            "TasRip will use Windows' own certificate checks instead.")


def plugin_args(exe):
    """Load TasRip's plugins (like the SBS fix) whichever yt-dlp is running - it otherwise
    only finds them if it sits in the bin folder. Custom site engines are left as they are."""
    if os.path.isdir(PLUGIN_DIR) and not os.path.abspath(exe).startswith(os.path.abspath(ENGINE_DIR)):
        return ["--plugin-dirs", PLUGIN_DIR]
    return []


def tls_args():
    """yt-dlp options for this PC's certificate setup."""
    return ["--compat-options", "no-certifi"] if USE_SYSTEM_CERTS else []


def system_curl():
    if IS_WIN:
        path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
        return path if os.path.isfile(path) else None
    return shutil.which("curl")


def system_download(url, dest, headers=None, timeout=600):
    """Download with the operating system's own downloader, which uses Windows' certificate
    checks (so it works behind antivirus HTTPS scanning). Raises RuntimeError on failure."""
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "%s/%s" % (APP_NAME, VERSION))
    curl = system_curl()
    if curl:
        a = [curl, "-L", "--fail", "--silent", "--show-error", "--retry", "2", "--max-time", str(timeout)]
        for k, v in headers.items():
            a += ["-H", "%s: %s" % (k, v)]
        a += ["-o", dest, url]
    elif IS_WIN:
        hdr = "; ".join("'%s'='%s'" % (k.replace("'", "''"), str(v).replace("'", "''")) for k, v in headers.items())
        a = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
             "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; "
             "Invoke-WebRequest -UseBasicParsing -Headers @{%s} -Uri '%s' -OutFile '%s'"
             % (hdr, url.replace("'", "''"), dest.replace("'", "''"))]
    else:
        raise RuntimeError("no system downloader (curl) available")
    p = subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW, timeout=timeout + 30)
    if p.returncode != 0 or not os.path.isfile(dest):
        if os.path.exists(dest):
            os.remove(dest)
        msg = (p.stderr or p.stdout or "").strip().splitlines()
        raise RuntimeError("download failed: " + (msg[-1] if msg else "exit code %d" % p.returncode))


def fetch_text(url, headers=None, timeout=30):
    """GET a page/API as text; falls back to the system downloader on certificate errors."""
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "%s/%s" % (APP_NAME, VERSION))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return r.read(16 * 1024 * 1024).decode("utf-8", "replace")
    except Exception as e:
        if not is_cert_error(e):
            raise
    fd, tmp = tempfile.mkstemp(prefix="tasrip_", suffix=".txt")
    os.close(fd)
    os.remove(tmp)
    try:
        system_download(url, tmp, headers, timeout=timeout)
        with open(tmp, encoding="utf-8", errors="replace") as f:
            return f.read()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def fetch(url, dest, log):
    """Download url to dest, reporting progress through log()."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    last = [-1]

    def hook(blocks, bs, total):
        if total > 0:
            step = int(blocks * bs * 10 / total)
            if step != last[0] and step <= 10:
                last[0] = step
                log("  %d%% of %.1f MB" % (step * 10, total / 1048576))

    req = urllib.request.Request(url, headers={"User-Agent": "%s/%s" % (APP_NAME, VERSION)})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                hook(got, 1, total)
        os.replace(tmp, dest)
        return
    except BaseException as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        if not is_cert_error(e):
            raise
        log(cert_hint(e))
        if any(w in str(e).lower() for w in CERT_CLOCK_WORDS):
            raise RuntimeError("the PC's date/time is wrong - fix the clock, then try again")
    log("Downloading with Windows' own downloader instead (no progress shown) ...")
    system_download(url, tmp, {"User-Agent": "%s/%s" % (APP_NAME, VERSION)})
    os.replace(tmp, dest)


def extract_tools(archive, names, dest):
    """Pull the named programs out of a .zip / .tar.gz / .tar.xz into dest. Returns those found."""
    found = []
    os.makedirs(dest, exist_ok=True)
    if archive.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                base = os.path.basename(member)
                if base in names and base not in found:
                    with z.open(member) as src, open(os.path.join(dest, base), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.append(base)
    else:
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                base = os.path.basename(m.name)
                if m.isfile() and base in names and base not in found:
                    with t.extractfile(m) as src, open(os.path.join(dest, base), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.append(base)
    for base in found:
        make_executable(os.path.join(dest, base))
    return found


def _download_and_extract(url, names, log):
    ext = ".zip" if ".zip" in url.lower() else ".tar.xz" if ".tar.xz" in url.lower() else ".tar.gz"
    tmp = os.path.join(BIN_DIR, "_download" + ext)
    try:
        fetch(url, tmp, log)
        return extract_tools(tmp, names, BIN_DIR)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def install_ytdlp(log):
    log("Downloading yt-dlp for %s ..." % OS_NAME)
    dest = os.path.join(BIN_DIR, "yt-dlp" + EXE)
    fetch(ytdlp_url(), dest, log)
    make_executable(dest)
    log("yt-dlp installed to bin%s" % (" (nightly build)" if USE_NIGHTLY else ""))


def ffmpeg_works(path):
    try:
        p = subprocess.run([path, "-version"], capture_output=True, timeout=30, creationflags=NO_WINDOW)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install_ffmpeg(log):
    """ffmpeg (and ffprobe) into bin, from GitHub or the backup source; checked that it runs."""
    names = ("ffmpeg" + EXE, "ffprobe" + EXE)
    last_err = None
    for label, urls in (("GitHub", FFMPEG_URLS), ("gyan.dev (backup source)", FFMPEG_BACKUP)):
        if not urls:
            continue
        log("Downloading ffmpeg for %s from %s (large, be patient) ..." % (OS_NAME, label))
        got = []
        try:
            for url in urls:
                got += _download_and_extract(url, names, log)
        except Exception as e:
            last_err = e
            log("That didn't work (%s)%s" % (e, " - trying the backup source ..." if label == "GitHub" else ""))
            continue
        exe = os.path.join(BIN_DIR, "ffmpeg" + EXE)
        if "ffmpeg" + EXE in got and ffmpeg_works(exe):
            log("ffmpeg installed to bin")
            return
        last_err = RuntimeError("the downloaded ffmpeg didn't run")
        log("The downloaded ffmpeg didn't run%s" % (" - trying the backup source ..." if label == "GitHub" else ""))
    raise RuntimeError("%s. You can install it yourself instead: %s" % (last_err, SYSTEM_INSTALL["ffmpeg"]))


def _github_asset(repo, pattern, deep=False):
    """URL of the newest release file in a GitHub repo whose name matches the regex
    (deep: look through the last 100 releases, for older lines like SubtitleEdit 4.x)."""
    for path in ("/releases/latest", "/releases?per_page=%d" % (100 if deep else 5)):
        data = gh_json(GITHUB_API + "/repos/" + repo + path)
        for rel in (data if isinstance(data, list) else [data]):
            for a in rel.get("assets", []):
                if re.search(pattern, a.get("name", ""), re.I):
                    return a["browser_download_url"]
    return None


def install_aria2c(log):
    if not IS_WIN:  # aria2 publishes no official macOS/Linux binaries; the system package works fine
        raise RuntimeError("aria2c has no official download for %s. Install it with:  %s  "
                           "- TasRip finds it automatically." % (OS_NAME, SYSTEM_INSTALL["aria2c"]))
    log("Downloading aria2c ...")
    try:
        url = _github_asset("aria2/aria2", r"win-64bit.*\.zip$") or ARIA2_WIN_FALLBACK
    except Exception:
        url = ARIA2_WIN_FALLBACK
    if "aria2c.exe" not in _download_and_extract(url, ("aria2c.exe",), log):
        raise RuntimeError("aria2c.exe wasn't in the download")
    log("aria2c installed to bin")


def install_streamlink(log):
    """Streamlink: a second engine for live streams, used when yt-dlp can't find one."""
    if not IS_WIN:
        raise RuntimeError("install Streamlink with your package manager (e.g. pipx install streamlink)")
    log("Downloading Streamlink (used for live TV when yt-dlp can't find the stream) ...")
    url = _github_asset("streamlink/windows-builds", r"x86_64\.zip$")
    if not url:
        raise RuntimeError("couldn't find a portable Streamlink build")
    tmp = os.path.join(BIN_DIR, "_streamlink.zip")
    try:
        fetch(url, tmp, log)
        shutil.rmtree(STREAMLINK_DIR, ignore_errors=True)
        with zipfile.ZipFile(tmp) as z:
            z.extractall(STREAMLINK_DIR)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if not tool_path("streamlink"):
        raise RuntimeError("streamlink.exe wasn't in the download")
    log("Streamlink installed to bin\\streamlink")


def install_nm3u8(log):
    log("Downloading N_m3u8DL-RE for %s ..." % OS_NAME)
    url = _github_asset("nilaoda/N_m3u8DL-RE", r"_%s_.*\.(zip|tar\.gz)$" % re.escape(NM3U8_ASSET))
    if not url:
        raise RuntimeError("couldn't find an N_m3u8DL-RE build for " + NM3U8_ASSET)
    if "N_m3u8DL-RE" + EXE not in _download_and_extract(url, ("N_m3u8DL-RE" + EXE,), log):
        raise RuntimeError("N_m3u8DL-RE wasn't in the download")
    log("N_m3u8DL-RE installed to bin")



# -------------------------------------------------------------------- plugins

def gh_json(url):
    return json.loads(fetch_text(url, {"Accept": "application/vnd.github+json"}))


def _repo_row(i):
    return {"full_name": i["full_name"], "desc": i.get("description") or "",
            "stars": i.get("stargazers_count", 0), "updated": (i.get("pushed_at") or "")[:10],
            "branch": i.get("default_branch") or "master", "url": i["html_url"]}


def fetch_catalog(log=lambda t: None):
    """Collect every yt-dlp plugin repo GitHub knows about, save it to the cache, return rows."""
    rows = {}
    for q in PLUGIN_QUERIES:
        for page in (1, 2, 3):
            url = GITHUB_API + "/search/repositories?" + urllib.parse.urlencode(
                {"q": q + " fork:false", "sort": "stars", "order": "desc", "per_page": 100, "page": page})
            try:
                items = gh_json(url).get("items", [])
            except Exception as e:
                if not rows:
                    raise
                log("Plugin list: skipped part of the scan (%s)" % e)
                break
            for i in items:
                text = (i["full_name"] + " " + (i.get("description") or "")).lower()
                if i.get("archived") or any(w in text for w in PLUGIN_SKIP):
                    continue
                name = i["name"].lower().replace("_", "-")
                if not q.startswith("topic:") and "yt-dlp" not in name and "ytdlp" not in name:
                    continue  # untagged repos only count if the name says it's for yt-dlp
                rows.setdefault(i["full_name"].lower(), _repo_row(i))
            if len(items) < 100:
                break
    out = sorted(rows.values(), key=lambda r: -r["stars"])
    try:
        with open(PLUGIN_CACHE, "w", encoding="utf-8") as f:
            json.dump({"fetched": time.time(), "rows": out}, f)
    except OSError:
        pass
    return out


def load_catalog_cache():
    """Returns (rows, age_seconds) from the last scan, or ([], None)."""
    try:
        with open(PLUGIN_CACHE, encoding="utf-8") as f:
            d = json.load(f)
        return d["rows"], time.time() - d["fetched"]
    except (OSError, ValueError, KeyError):
        return [], None


def parse_repo(text):
    """'owner/repo' or any github.com URL -> 'owner/repo' (None if not recognisable)."""
    t = text.strip()
    if "github.com" in t:
        t = t.split("github.com", 1)[1]
    parts = [x for x in t.replace(":", "/").split("/") if x]
    if len(parts) < 2 or " " in t:
        return None
    repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    return parts[0] + "/" + repo


def plugin_folder(full_name):
    return os.path.join(PLUGIN_DIR, full_name.replace("/", "."))


def extract_plugin(zpath, dest):
    """Copy the yt_dlp_plugins package out of a repo zip into dest. Returns file count."""
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        roots = set()
        for n in names:
            parts = n.split("/")
            if "yt_dlp_plugins" in parts[:-1]:
                roots.add("".join(x + "/" for x in parts[:parts.index("yt_dlp_plugins")]))
        roots = sorted(roots, key=len)
        if not roots:
            raise ValueError("no yt_dlp_plugins folder in this repo - it isn't a drop-in plugin "
                             "(it may need 'pip install' instead)")
        prefix = roots[0] + "yt_dlp_plugins/"
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        count = 0
        for n in names:
            if not n.startswith(prefix):
                continue
            rel = os.path.normpath(n[len(roots[0]):])
            if rel.startswith("..") or os.path.isabs(rel):  # never write outside dest
                continue
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(n) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


def install_plugin(full_name, branch, log):
    """Download a plugin repo from GitHub and unpack it where yt-dlp.exe will load it."""
    if not branch:
        branch = gh_json("%s/repos/%s" % (GITHUB_API, full_name)).get("default_branch") or "master"
    os.makedirs(PLUGIN_DIR, exist_ok=True)
    zpath = os.path.join(PLUGIN_DIR, "_download.zip")
    log("Downloading plugin %s (%s) ..." % (full_name, branch))
    try:
        fetch("https://github.com/%s/archive/refs/heads/%s.zip" % (full_name, branch), zpath, lambda t: None)
        dest = plugin_folder(full_name)
        count = extract_plugin(zpath, dest)
    finally:
        if os.path.exists(zpath):
            os.remove(zpath)
    with open(os.path.join(dest, "source.json"), "w", encoding="utf-8") as f:
        json.dump({"repo": full_name, "branch": branch, "url": "https://github.com/" + full_name,
                   "installed": time.strftime("%Y-%m-%d")}, f, indent=2)
    log("Installed %s (%d files) -> %s" % (full_name, count, dest))


def installed_plugins():
    """[(folder name, repo, installed date)] for everything in the plugin folder."""
    out = []
    if os.path.isdir(PLUGIN_DIR):
        for name in sorted(os.listdir(PLUGIN_DIR), key=str.lower):
            path = os.path.join(PLUGIN_DIR, name)
            if not os.path.isdir(os.path.join(path, "yt_dlp_plugins")):
                continue
            try:
                with open(os.path.join(path, "source.json"), encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                meta = {}
            out.append((name, meta.get("repo", "(added by hand)"), meta.get("installed", "")))
    return out


def python_exe():
    """The console python next to pythonw.exe (pip and PyInstaller need a real one)."""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        alt = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(alt):
            return alt
    return exe


BUILD_ENV = os.path.join(tempfile.gettempdir(), "tasrip_buildenv")


def _run_step(title, cmd, log, keep=("error", "warning: lib not found", "successfully")):
    """Run one build command, showing progress lines; raise with the output tail if it fails."""
    log(title)
    p = popen(cmd)
    tail = []
    for raw in p.stdout:
        line = raw.rstrip()
        tail = (tail + [line])[-25:]
        low = line.lower()
        if any(k in low for k in keep) or (" info: " in low and ("building" in low or "completed" in low)):
            log("  " + line.split(" INFO: ", 1)[-1])
    if p.wait() != 0:
        log("\n".join("  " + t for t in tail))
        raise RuntimeError("build step failed: " + title.rstrip(" ."))


def _venv_python(env):
    return os.path.join(env, "Scripts", "python.exe") if IS_WIN else os.path.join(env, "bin", "python")


def _build_python(log):
    """A private Python environment for the build, so this PC's own Python is never changed."""
    base = python_exe()
    vpy = _venv_python(BUILD_ENV)
    if not os.path.isfile(vpy):
        try:
            _run_step("Creating a private build environment (first build only) ...",
                      [base, "-m", "venv", BUILD_ENV], log)
        except RuntimeError:
            shutil.rmtree(BUILD_ENV, ignore_errors=True)
            log("  Couldn't create one; installing PyInstaller for this Windows user instead.")
            _run_step("Installing PyInstaller ...", [base, "-m", "pip", "install", "--user", "--upgrade",
                                                     "--disable-pip-version-check", "pyinstaller"], log)
            return base
    try:
        _run_step("Installing/updating PyInstaller ...",
                  [vpy, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", "pyinstaller"], log)
    except (RuntimeError, OSError):
        # Usually a build environment made by a Python that has since been upgraded/removed.
        log("  The private build environment is out of date - recreating it ...")
        shutil.rmtree(BUILD_ENV, ignore_errors=True)
        _run_step("Creating a private build environment ...", [base, "-m", "venv", BUILD_ENV], log)
        _run_step("Installing PyInstaller ...",
                  [vpy, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", "pyinstaller"], log)
    return vpy


def build_exe(log):
    """Build the standalone TasRip app (.exe / .app / binary) into the app folder, on this PC.

    Nothing prebuilt is shipped: each user builds their own exe from tasrip.py.
    """
    if sys.version_info < (3, 8):
        raise RuntimeError("Python 3.8 or newer is needed to build")
    py = _build_python(log)
    check = subprocess.run([py, "-c", "import tkinter; tkinter.Tcl()"], capture_output=True,
                           creationflags=NO_WINDOW)
    if check.returncode != 0:  # PyInstaller would quietly produce an exe that can't open a window
        raise RuntimeError("the build Python has no working Tkinter, so the app couldn't show its "
                           "window. " + TK_HINT)
    work = tempfile.mkdtemp(prefix="tasrip_build_")
    try:
        _run_step("Building the exe (a minute or two) ...",
                  [py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                   "--log-level", "WARN", "--name", APP_NAME, "--distpath", os.path.join(work, "dist"),
                   "--workpath", os.path.join(work, "build"), "--specpath", work,
                   os.path.abspath(__file__)], log)
        dist = os.path.join(work, "dist")
        app = os.path.join(dist, APP_NAME + ".app")
        exe = app if IS_MAC and os.path.isdir(app) else os.path.join(dist, APP_NAME + EXE)
        if not os.path.exists(exe):
            raise RuntimeError("PyInstaller finished but produced nothing")
        out = os.path.join(BASE_DIR, os.path.basename(exe))
        try:
            if os.path.isdir(exe):  # macOS .app bundle
                if os.path.isdir(out):
                    shutil.rmtree(out)
                shutil.copytree(exe, out, symlinks=True)
            else:
                shutil.copy2(exe, out)
                make_executable(out)
        except OSError as e:
            raise RuntimeError("couldn't write %s - close it if it's running, then build again (%s)"
                               % (os.path.basename(out), e))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    try:
        os.makedirs(BIN_DIR, exist_ok=True)
        with open(os.path.join(BIN_DIR, ".exe-version"), "w") as f:
            f.write(VERSION)
    except OSError:
        pass
    log("Built %s. It runs without Python - keep it in this folder next to bin." % out)
    return out


def kill_tree(proc):
    """Stop yt-dlp and any ffmpeg/aria2c child it spawned."""
    if proc.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=NO_WINDOW)
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def popen(args, stderr=subprocess.STDOUT):
    return subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=stderr, stdin=subprocess.DEVNULL,
        encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW,
        start_new_session=not IS_WIN)  # own process group, so Stop can end its children too


BUILDER = "build_exe.bat" if IS_WIN else "build.command" if IS_MAC else "build.sh"
APP_FILE = APP_NAME + (".exe" if IS_WIN else ".app" if IS_MAC else "")
TK_HINT = ("Reinstall Python from python.org with 'tcl/tk and IDLE' ticked." if IS_WIN else
           "Install Python from python.org (it includes Tk), or: brew install python-tk" if IS_MAC else
           "Install Tk for Python, e.g.  sudo apt install python3-tk")


def stale_exe_note():
    """If this is a built exe and the tasrip.py next to it is a newer version, say so."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        with open(os.path.join(BASE_DIR, "tasrip.py"), encoding="utf-8") as f:
            m = re.search(r'^VERSION = "([\d.]+)"', f.read(), re.M)
    except OSError:
        return None
    ver = lambda v: tuple(int(x) for x in v.split("."))
    if m and ver(m.group(1)) > ver(VERSION):
        return ("This %s is v%s, but the tasrip.py next to it is v%s. Run %s to rebuild it, "
                "or you'll keep getting the old version." % (APP_FILE, VERSION, m.group(1), BUILDER))
    return None


SWITCH_TO_NIGHTLY = False  # set when old settings are moved to the nightly default


def load_settings():
    s = dict(DEFAULTS)
    s["cfg_ver"] = ""  # unknown until read from the file
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        for k, v in (saved.items() if isinstance(saved, dict) else ()):
            if k not in DEFAULTS:
                continue
            if isinstance(DEFAULTS[k], bool):
                if isinstance(v, bool):
                    s[k] = v
            elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
                s[k] = str(v)
    except (OSError, ValueError):
        pass
    if not os.path.isfile(SETTINGS_FILE):
        s["cfg_ver"] = DEFAULTS["cfg_ver"]
    for k, choices in (("mode", ("Video", "Audio")), ("quality", QUALITIES), ("container", CONTAINERS),
                       ("audio_fmt", AUDIO_FORMATS), ("template", list(TEMPLATES)), ("frags", FRAGMENTS),
                       ("cookies", BROWSERS), ("enc_codec", ENC_CODECS), ("enc_quality", ENC_QUALITY),
                       ("enc_speed", ENC_SPEED), ("enc_resize", ENC_RESIZE), ("enc_audio", ENC_AUDIO),
                       ("enc_vendor", [""] + GPU_VENDORS), ("nfo", NFO_STYLES), ("min_quality", MIN_QUALITIES),
                       ("parallel", PARALLEL)):
        if s[k] not in choices:
            s[k] = DEFAULTS[k]
    if not str(s["outdir"]).strip():
        s["outdir"] = DEFAULTS["outdir"]
    if s.get("cfg_ver") != DEFAULTS["cfg_ver"]:  # first start on 1.0.1: nightly becomes the default
        global SWITCH_TO_NIGHTLY
        SWITCH_TO_NIGHTLY = not s["nightly"] or SWITCH_TO_NIGHTLY
        s["nightly"] = True
        s["cfg_ver"] = DEFAULTS["cfg_ver"]
    # A USB stick gets a different drive letter on each PC: don't trust a dead path.
    if not os.path.isdir(os.path.splitdrive(s["outdir"])[0] + os.sep):
        s["outdir"] = DEFAULTS["outdir"]
    return s


# ------------------------------------------------------------------------ GUI

class App:
    COLS = (("title", "Title / URL", 380), ("mode", "Mode", 90), ("status", "Status", 110),
            ("pct", "Progress", 80), ("speed", "Speed", 90), ("eta", "ETA", 70))

    def __init__(self, root):
        self.root = root
        root.title("%s v%s" % (APP_NAME, VERSION))
        root.geometry("1100x820")
        root.minsize(820, 600)
        self.q = queue.Queue()
        self.items = {}          # iid -> {"url", "opts", "status"}
        self.proc = None
        self.running = False
        self.stop_flag = False
        self.busy_tools = False
        self.plugin_rows = {}    # tree iid -> repo row
        self.cert_failed = False
        self.catalog = []        # every plugin from the last GitHub scan
        self.catalog_loading = False
        self.login = None        # (username, password) for this session only - never saved
        self.site_logins = {}    # site -> (email, password) entered without "remember"
        self.item_lines = None   # error/warning lines from the item being downloaded
        self.settings_win = None
        self.live_procs = {}     # iid -> running live recording process
        self.live_stop = set()
        self.run_stats = {"done": 0, "failed": 0, "new": []}
        self.history = load_history()
        self.watch = load_watch()

        s = load_settings()
        self.v = {k: (tk.BooleanVar(value=val) if isinstance(val, bool) else tk.StringVar(value=val))
                  for k, val in s.items()}
        global USE_SYSTEM_CERTS, USE_NIGHTLY
        USE_SYSTEM_CERTS = bool(s.get("syscerts"))
        USE_NIGHTLY = bool(s.get("nightly"))
        if SWITCH_TO_NIGHTLY:
            self.save_settings()
        self._build()
        self._mode_changed()
        self.root.bind_class("Toplevel", "<Map>", self._on_toplevel_map, add="+")
        self.apply_theme()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._pump)
        root.after(300, self._check_tools)
        auto_assign_engines(self.log)
        note = stale_exe_note()
        if note:
            root.after(600, lambda: (self.log(note), messagebox.showwarning(APP_NAME, note)))
        self.refresh_installed()
        try:
            self._restore_queue()
        except Exception as e:
            self.log("Couldn't restore last time's queue: %s" % e)

    # ---- layout
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        menu = tk.Menu(self.root)
        tools = tk.Menu(menu, tearoff=0)
        tools.add_command(label="Settings ...", command=self.settings_dialog)
        tools.add_command(label="Sign in to a site ...", command=self.signin_dialog)
        tools.add_command(label="Make a problem report", command=self.report_problem)
        tools.add_command(label="Test all channels ...", command=self.selftest_dialog)
        tools.add_command(label="Sign-in status ...", command=self.signin_status_dialog)
        tools.add_command(label="Antivirus helper ...", command=lambda: self.antivirus_dialog())
        tools.add_separator()
        tools.add_command(label="Record live TV ...", command=self.live_dialog)
        tools.add_command(label="Pick episodes from a season / feed ...", command=self.pick_clicked)
        tools.add_command(label="Update yt-dlp now", command=self.update_ytdlp)
        tools.add_command(label="Update all the other tools now", command=lambda: self._auto_tool_update(force=True))
        tools.add_command(label="Diagnose a URL ...", command=self.diagnose_clicked)
        tools.add_command(label="Site login for this session (email + password) ...", command=self.login_dialog)
        tools.add_command(label="Site engines (custom yt-dlp builds) ...", command=self.engines_dialog)
        tools.add_separator()
        tools.add_command(label="Build %s ..." % APP_FILE, command=self.build_exe_clicked,
                          state="disabled" if getattr(sys, "frozen", False) else "normal")
        tools.add_command(label="Export my setup ...", command=self.export_clicked)
        tools.add_command(label="Import a setup ...", command=self.import_clicked)
        tools.add_separator()
        tools.add_command(label="Open app folder", command=lambda: open_path(BASE_DIR))
        tools.add_command(label="Open logs folder", command=lambda: (os.makedirs(LOG_DIR, exist_ok=True), open_path(LOG_DIR)))
        menu.add_cascade(label="Tools", menu=tools)
        menu.add_command(label="\u263e Dark mode", command=self.toggle_dark)
        self.menubar, self.dark_index = menu, menu.index("end")
        self.root.config(menu=menu)
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=6, pady=(6, 2))
        page = ttk.Frame(self.tabs)
        self.tabs.add(page, text="  Downloads  ")
        self._build_watch_tab()
        self._build_history_tab()
        self._build_plugins_tab()
        sr = ttk.Frame(page)
        sr.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(sr, text="Search shows").pack(side="left")
        self.s_term = tk.StringVar()
        e = ttk.Entry(sr, textvariable=self.s_term, width=34)
        e.pack(side="left", padx=6)
        e.bind("<Return>", lambda ev: self.search_clicked())
        ttk.Combobox(sr, textvariable=self.v["search_in"], values=["All 5 channels"] + SEARCH_CHANNELS,
                     width=15, state="readonly").pack(side="left")
        ttk.Button(sr, text="Search", command=self.search_clicked).pack(side="left", padx=6)
        ttk.Label(sr, foreground="#888", text="iView, 10, SBS, 7plus and 9Now").pack(side="left", padx=4)
        top = ttk.LabelFrame(page, text="URLs (one per line - videos, playlists, channels)")
        top.pack(fill="x", **pad)
        self.urls = tk.Text(top, height=5, wrap="none", undo=True)
        self.urls.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        bcol = ttk.Frame(top)
        bcol.pack(side="right", fill="y", padx=6, pady=6)
        ttk.Button(bcol, text="Paste", command=self.paste).pack(fill="x")
        ttk.Button(bcol, text="Add to queue", command=self.add_urls).pack(fill="x", pady=4)
        ttk.Button(bcol, text="Pick episodes ...", command=self.pick_clicked).pack(fill="x")
        ttk.Button(bcol, text="Record live ...", command=self.live_dialog).pack(fill="x", pady=(4, 0))

        opt = ttk.LabelFrame(page, text="Options (applied to items when you add them)")
        opt.pack(fill="x", **pad)
        r = ttk.Frame(opt); r.pack(fill="x", padx=6, pady=3)
        ttk.Label(r, text="Mode").pack(side="left")
        for m in ("Video", "Audio"):
            ttk.Radiobutton(r, text=m, value=m, variable=self.v["mode"],
                            command=self._mode_changed).pack(side="left", padx=4)
        self.w_quality = self._combo(r, "Quality", "quality", QUALITIES, 8)
        self.w_container = self._combo(r, "Container", "container", CONTAINERS, 6)
        self.w_audio = self._combo(r, "Audio format", "audio_fmt", AUDIO_FORMATS, 6)
        self._combo(r, "Browser cookies", "cookies", BROWSERS, 9)
        ttk.Button(r, text="Settings ...", command=self.settings_dialog).pack(side="right")
        ttk.Button(r, text="Sign in to a site ...", command=self.signin_dialog).pack(side="right", padx=6)

        r = ttk.Frame(opt); r.pack(fill="x", padx=6, pady=3)
        self.w_compat = ttk.Checkbutton(r, text="Prefer H.264/AAC", variable=self.v["compat"])
        self.w_compat.pack(side="left")
        self.w_subs = ttk.Checkbutton(r, text="Subtitles (embed + .srt)", variable=self.v["subs"])
        self.w_subs.pack(side="left", padx=8)
        ttk.Checkbutton(r, text="Embed metadata", variable=self.v["meta"]).pack(side="left")
        ttk.Checkbutton(r, text="Embed thumbnail", variable=self.v["thumb"]).pack(side="left", padx=8)
        ttk.Checkbutton(r, text="Save thumbnail (.jpg)", variable=self.v["thumbfile"]).pack(side="left")
        ttk.Checkbutton(r, text="Whole playlist", variable=self.v["playlist"]).pack(side="left")
        ttk.Checkbutton(r, text="Skip already downloaded", variable=self.v["archive"]).pack(side="left", padx=8)
        self._combo(r, ".nfo", "nfo", NFO_STYLES, 18)

        r = ttk.Frame(opt); r.pack(fill="x", padx=6, pady=3)
        self._combo(r, "File naming", "template", list(TEMPLATES), 44, first=True)
        ttk.Label(r, text="Speed limit").pack(side="left", padx=(12, 4))
        ttk.Entry(r, textvariable=self.v["rate"], width=7).pack(side="left")
        ttk.Label(r, text="(e.g. 2M)").pack(side="left", padx=4)
        self._combo(r, "Fragments at once", "frags", FRAGMENTS, 4)
        ttk.Checkbutton(r, text="Use aria2c", variable=self.v["aria2"]).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(r, text="AU site recipes", variable=self.v["recipes"]).pack(side="left", padx=(12, 0))
        self._combo(r, "Ask if below", "min_quality", MIN_QUALITIES, 6)
        self._combo(r, "At once", "parallel", PARALLEL, 3)

        r = ttk.Frame(opt); r.pack(fill="x", padx=6, pady=3)
        ttk.Label(r, text="Save to").pack(side="left")
        ttk.Entry(r, textvariable=self.v["outdir"]).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(r, text="Browse", command=self.browse).pack(side="left")
        ttk.Button(r, text="Open", command=self.open_folder).pack(side="left", padx=4)

        r = ttk.Frame(opt); r.pack(fill="x", padx=6, pady=(3, 6))
        ttk.Label(r, text="Extra yt-dlp args").pack(side="left")
        ttk.Entry(r, textvariable=self.v["extra"]).pack(side="left", fill="x", expand=True, padx=4)

        bar = ttk.Frame(page); bar.pack(fill="x", **pad)
        self.b_start = ttk.Button(bar, text="Start", command=self.start)
        self.b_start.pack(side="left")
        self.b_stop = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.b_stop.pack(side="left", padx=4)
        ttk.Button(bar, text="Remove selected", command=self.remove_selected).pack(side="left")
        ttk.Button(bar, text="Retry failed", command=self.retry_failed).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear finished", command=self.clear_finished).pack(side="left")
        ttk.Button(bar, text="Report a problem", command=self.report_problem).pack(side="right")
        ttk.Button(bar, text="Test channels", command=self.selftest_dialog).pack(side="right", padx=6)

        panes = ttk.PanedWindow(page, orient="vertical")
        panes.pack(fill="both", expand=True, **pad)
        qf = ttk.Frame(panes)
        self.tree = ttk.Treeview(qf, columns=[c[0] for c in self.COLS], show="headings", height=8)
        for cid, text, width in self.COLS:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor="w" if cid == "title" else "center",
                             stretch=cid == "title")
        sb = ttk.Scrollbar(qf, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<Double-1>", self._row_double_click)
        self.tree.bind("<Button-3>", self._row_menu)
        panes.add(qf, weight=3)

        lf = ttk.Frame(panes)
        self.log_box = tk.Text(lf, height=8, state="disabled", wrap="word",
                               bg="#111", fg="#ddd", insertbackground="#ddd", font="TkFixedFont")
        sb2 = ttk.Scrollbar(lf, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sb2.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        panes.add(lf, weight=2)

        self.total = ttk.Progressbar(self.root, maximum=100)
        self.total.pack(fill="x", padx=8)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status, anchor="w").pack(fill="x", padx=8, pady=(2, 6))

    def _combo(self, parent, label, key, values, width, first=False):
        ttk.Label(parent, text=label).pack(side="left", padx=(0 if first else 12, 4))
        c = ttk.Combobox(parent, textvariable=self.v[key], values=values, width=width, state="readonly")
        c.pack(side="left")
        return c

    def _mode_changed(self):
        video = self.v["mode"].get() == "Video"
        for w in (self.w_quality, self.w_container):
            w.configure(state="readonly" if video else "disabled")
        for w in (self.w_compat, self.w_subs):
            w.configure(state="normal" if video else "disabled")
        self.w_audio.configure(state="disabled" if video else "readonly")

    # ---- plugins tab
    PCOLS = (("have", "", 30), ("repo", "Repository", 270), ("stars", "Stars", 60),
             ("updated", "Updated", 90), ("desc", "Description", 450))

    def _build_plugins_tab(self):
        page = ttk.Frame(self.tabs)
        self.tabs.add(page, text="  Plugins  ")
        pad = {"padx": 8, "pady": 4}
        r = ttk.Frame(page); r.pack(fill="x", **pad)
        ttk.Label(r, text="Filter").pack(side="left")
        self.p_term = tk.StringVar()
        ttk.Entry(r, textvariable=self.p_term, width=30).pack(side="left", padx=4)
        self.p_term.trace_add("write", lambda *a: self._show_catalog())
        ttk.Button(r, text="Refresh list from GitHub", command=self.catalog_refresh).pack(side="right")
        self.tabs.bind("<<NotebookTabChanged>>", self._tab_changed)

        f = ttk.Frame(page); f.pack(fill="both", expand=True, **pad)
        self.p_tree = ttk.Treeview(f, columns=[c[0] for c in self.PCOLS], show="headings", height=10,
                                   selectmode="browse")
        for cid, text, width in self.PCOLS:
            self.p_tree.heading(cid, text=text)
            self.p_tree.column(cid, width=width, anchor="center" if cid in ("have", "stars", "updated") else "w",
                               stretch=cid == "desc")
        sb = ttk.Scrollbar(f, command=self.p_tree.yview)
        self.p_tree.configure(yscrollcommand=sb.set)
        self.p_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.p_tree.bind("<Double-1>", lambda ev: self.plugin_open_page())

        r = ttk.Frame(page); r.pack(fill="x", **pad)
        ttk.Button(r, text="Install selected", command=self.plugin_install_selected).pack(side="left")
        ttk.Button(r, text="View on GitHub", command=self.plugin_open_page).pack(side="left", padx=4)
        self.p_status = tk.StringVar(value="")
        ttk.Label(r, textvariable=self.p_status).pack(side="left", padx=8)

        r = ttk.Frame(page); r.pack(fill="x", **pad)
        ttk.Label(r, text="Or install from GitHub URL / owner/repo").pack(side="left")
        self.p_url = tk.StringVar()
        ttk.Entry(r, textvariable=self.p_url).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(r, text="Install", command=self.plugin_install_url).pack(side="left")

        inst = ttk.LabelFrame(page, text="Installed (loaded automatically by yt-dlp)")
        inst.pack(fill="x", **pad)
        self.p_inst = ttk.Treeview(inst, columns=("folder", "repo", "date"), show="headings", height=5)
        for cid, text, width in (("folder", "Folder", 300), ("repo", "Source", 300), ("date", "Installed", 100)):
            self.p_inst.heading(cid, text=text)
            self.p_inst.column(cid, width=width, anchor="w")
        self.p_inst.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        b = ttk.Frame(inst); b.pack(side="right", fill="y", padx=6, pady=6)
        ttk.Button(b, text="Update", command=self.plugin_update).pack(fill="x")
        ttk.Button(b, text="Remove", command=self.plugin_remove).pack(fill="x", pady=4)
        ttk.Button(b, text="Open folder", command=self.plugin_open_folder).pack(fill="x")

        ttk.Label(page, foreground="#a40", wraplength=940, justify="left", text=(
            "Plugins are third-party Python code that runs on this PC with your permissions. "
            "Check the repository before installing anything you don't recognise.")
        ).pack(fill="x", padx=8, pady=(0, 6))

    def _bg(self, fn, done):
        """Run fn() off the UI thread, then post `done` (a message tuple) with the result."""
        def work():
            try:
                self.q.put(done + (fn(), None))
            except Exception as e:
                self.q.put(done + (None, e))
        threading.Thread(target=work, daemon=True).start()

    def _tab_changed(self, event=None):
        """First visit to the Plugins tab: show the saved list, re-scan GitHub if it's stale."""
        if self.tabs.index("current") != 1 or self.catalog or self.catalog_loading:
            return
        self.catalog, age = load_catalog_cache()
        self._show_catalog()
        if age is None or age > CACHE_MAX_AGE:
            self.catalog_refresh()

    def catalog_refresh(self):
        if self.catalog_loading:
            return
        self.catalog_loading = True
        self.p_status.set("Scanning GitHub for plugins ...")
        self._bg(lambda: fetch_catalog(self.log), ("plugin_results",))

    def _show_catalog(self):
        words = self.p_term.get().lower().split()
        have = {repo.lower() for _, repo, _ in installed_plugins()}
        self.p_tree.delete(*self.p_tree.get_children())
        self.plugin_rows = {}
        for row in self.catalog:
            text = (row["full_name"] + " " + row["desc"]).lower()
            if all(w in text for w in words):
                iid = self.p_tree.insert("", "end", values=(
                    "\u2713" if row["full_name"].lower() in have else "", row["full_name"],
                    row["stars"], row["updated"], row["desc"]))
                self.plugin_rows[iid] = row
        if not self.catalog_loading:
            self.p_status.set("%d of %d plugins shown. Double-click to read about one."
                              % (len(self.plugin_rows), len(self.catalog)))

    def _selected_row(self):
        sel = self.p_tree.selection()
        return self.plugin_rows.get(sel[0]) if sel else None

    def plugin_open_page(self):
        row = self._selected_row()
        if row:
            webbrowser.open(row["url"])

    def plugin_install_selected(self):
        row = self._selected_row()
        if row:
            self._plugin_install(row["full_name"], row["branch"])

    def plugin_install_url(self):
        full = parse_repo(self.p_url.get())
        if not full:
            return messagebox.showwarning(APP_NAME, "Enter a GitHub repo URL or owner/repo.")
        self._plugin_install(full, None)

    def _plugin_install(self, full_name, branch, ask=True):
        if self.running:
            return messagebox.showinfo(APP_NAME, "Wait for downloads to finish first.")
        if ask and not messagebox.askyesno(
                APP_NAME, "Install plugin from\nhttps://github.com/%s ?\n\nIt is third-party code "
                          "that will run every time yt-dlp runs." % full_name):
            return
        self.p_status.set("Installing %s ..." % full_name)
        self._bg(lambda: install_plugin(full_name, branch, self.log), ("plugin_installed", full_name))

    def _selected_installed(self):
        sel = self.p_inst.selection()
        return self.p_inst.item(sel[0], "values") if sel else None

    def plugin_update(self):
        v = self._selected_installed()
        if v and "/" in v[1]:
            self._plugin_install(v[1], None, ask=False)
        elif v:
            messagebox.showinfo(APP_NAME, "That one didn't come from GitHub, so there's nothing to update it from.")

    def plugin_remove(self):
        v = self._selected_installed()
        if v and not self.running and messagebox.askyesno(APP_NAME, "Remove plugin %s?" % v[0]):
            shutil.rmtree(os.path.join(PLUGIN_DIR, v[0]), ignore_errors=True)
            self.refresh_installed()
            self._show_catalog()

    def plugin_open_folder(self):
        os.makedirs(PLUGIN_DIR, exist_ok=True)
        open_path(PLUGIN_DIR)

    def refresh_installed(self):
        self.p_inst.delete(*self.p_inst.get_children())
        for row in installed_plugins():
            self.p_inst.insert("", "end", values=row)

    def _plugin_msg(self, msg):
        result, err = msg[-2], msg[-1]
        if msg[0] == "plugin_results":
            self.catalog_loading = False
            if err:
                self.p_status.set("Couldn't reach GitHub (%s)%s" % (
                    err, " - showing the saved list." if self.catalog else ""))
                return
            self.catalog = result
            self._show_catalog()
        else:
            self.refresh_installed()
            self._show_catalog()
            if err:
                self.p_status.set("Install failed")
                self.log("Plugin install failed (%s): %s" % (msg[1], err))
                messagebox.showerror(APP_NAME, "Couldn't install %s:\n\n%s" % (msg[1], err))
            else:
                self.p_status.set("Installed %s" % msg[1])

    # ---- dark mode
    DARK = {"bg": "#1f2023", "fg": "#e6e6e6", "field": "#2a2b2f", "button": "#33353a", "hover": "#41444b",
            "select": "#3d5a80", "border": "#45474d", "accent": "#7fb2f0", "muted": "#8a8d93"}

    def toggle_dark(self):
        self.v["dark"].set(not self.v["dark"].get())
        self.apply_theme()
        self.save_settings()

    def apply_theme(self):
        dark = bool(self.v["dark"].get())
        st = ttk.Style(self.root)
        if not hasattr(self, "_light_theme"):
            self._light_theme = st.theme_use()
        c = self.DARK
        if dark:
            st.theme_use("clam")
            st.configure(".", background=c["bg"], foreground=c["fg"], fieldbackground=c["field"],
                         bordercolor=c["border"], darkcolor=c["bg"], lightcolor=c["bg"], troughcolor=c["field"],
                         selectbackground=c["select"], selectforeground=c["fg"], insertcolor=c["fg"],
                         arrowcolor=c["fg"])
            st.map(".", foreground=[("disabled", c["muted"])])
            st.configure("TButton", background=c["button"], padding=(10, 3))
            st.map("TButton", background=[("pressed", c["select"]), ("active", c["hover"]), ("disabled", c["bg"])])
            for w in ("TCheckbutton", "TRadiobutton"):
                st.map(w, background=[("active", c["bg"])],
                       indicatorcolor=[("selected", c["accent"]), ("!selected", c["field"])])
            st.configure("TNotebook", background=c["bg"], tabmargins=(2, 4, 2, 0))
            st.configure("TNotebook.Tab", background=c["button"], foreground=c["fg"], padding=(10, 4))
            st.map("TNotebook.Tab", background=[("selected", c["bg"]), ("active", c["hover"])])
            st.configure("Treeview", background=c["field"], fieldbackground=c["field"], foreground=c["fg"])
            st.map("Treeview", background=[("selected", c["select"])], foreground=[("selected", c["fg"])])
            st.configure("Treeview.Heading", background=c["button"], foreground=c["fg"], relief="flat")
            st.map("Treeview.Heading", background=[("active", c["hover"])])
            st.map("TCombobox", fieldbackground=[("readonly", c["field"]), ("disabled", c["bg"])],
                   selectbackground=[("readonly", c["field"])], selectforeground=[("readonly", c["fg"])],
                   background=[("readonly", c["button"])])
            st.configure("TEntry", fieldbackground=c["field"], foreground=c["fg"])
            st.configure("TLabelframe", background=c["bg"], bordercolor=c["border"])
            st.configure("TLabelframe.Label", background=c["bg"], foreground=c["fg"])
            st.configure("TProgressbar", background=c["accent"], troughcolor=c["field"])
        else:
            st.theme_use(self._light_theme)
        lb_bg, lb_fg = (c["field"], c["fg"]) if dark else ("white", "black")
        for opt, val in (("*TCombobox*Listbox.background", lb_bg), ("*TCombobox*Listbox.foreground", lb_fg),
                         ("*TCombobox*Listbox.selectBackground", c["select"] if dark else "#0078d7"),
                         ("*Menu.background", lb_bg), ("*Menu.foreground", lb_fg)):
            self.root.option_add(opt, val)
        base = c["bg"] if dark else st.lookup(".", "background") or "SystemButtonFace"
        try:
            self.root.configure(bg=base)
            self.urls.configure(bg=c["field"] if dark else "white", fg=c["fg"] if dark else "black",
                                insertbackground=c["fg"] if dark else "black")
            self.menubar.entryconfig(self.dark_index, label="☀ Light mode" if dark else "☾ Dark mode")
        except (tk.TclError, AttributeError):
            pass
        for w in [self.root] + [x for x in self.root.winfo_children() if isinstance(x, tk.Toplevel)]:
            if w is not self.root:
                try:
                    w.configure(bg=base)
                except tk.TclError:
                    pass
            set_titlebar_dark(w, dark)

    def _on_toplevel_map(self, event):
        w = event.widget
        if isinstance(w, tk.Toplevel) and not getattr(w, "_themed", False):
            w._themed = True
            if self.v["dark"].get():
                try:
                    w.configure(bg=self.DARK["bg"])
                except tk.TclError:
                    pass
                set_titlebar_dark(w, True)

    # ---- queue helper
    def _maybe_ask_ten_login(self, url):
        site = login_site(url)
        if not site or getattr(self, "ten_asked", False) or self.login or self.site_logins.get(site) or saved_login(site):
            return
        self.ten_asked = True

        def ask():
            if messagebox.askyesno(APP_NAME, "10 needs your 10 email and password to download (it can't use a "
                                             "browser sign-in).\n\nSign in to 10 now?"):
                self.signin_dialog(preset="10")
        self.root.after(200, ask)

    def _enqueue(self, url, o, label=None, extra=None):
        self._maybe_ask_ten_login(url)
        if label is None:
            label = "Audio " + o["audio_fmt"] if o["mode"] == "Audio" else o["quality"] + " " + o["container"]
        iid = self.tree.insert("", "end", values=(url, label, "Queued", "", "", ""))
        self.items[iid] = dict({"url": url, "opts": dict(o), "status": "Queued"}, **(extra or {}))
        return iid

    # ---- show search
    def search_clicked(self):
        q = self.s_term.get().strip()
        if not q:
            return
        chans = SEARCH_CHANNELS if self.v["search_in"].get() not in SEARCH_CHANNELS else [self.v["search_in"].get()]
        win = tk.Toplevel(self.root)
        win.title("Search: %s" % q)
        win.geometry("820x480")
        status = tk.StringVar(value="Searching %s ..." % ", ".join(chans))
        ttk.Label(win, textvariable=status).pack(anchor="w", padx=10, pady=(10, 4))
        f = ttk.Frame(win)
        f.pack(fill="both", expand=True, padx=10, pady=4)
        tv = ttk.Treeview(f, columns=("chan", "title", "url"), show="headings", selectmode="extended")
        for cid, text, w in (("chan", "Channel", 110), ("title", "Show", 260), ("url", "Link", 400)):
            tv.heading(cid, text=text)
            tv.column(cid, width=w, anchor="w", stretch=cid == "url")
        sb = ttk.Scrollbar(f, command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        rows = {}
        pending = [len(chans)]
        counts = {}

        def chosen():
            return [rows[i] for i in tv.selection() if i in rows]

        def add_queue():
            for chan, title, url in chosen():
                self._enqueue(url, dict(self.opts(), playlist=True))
            self._summary()
            self.log("Added %d show(s) from the search" % len(chosen()))

        def pick():
            sel = chosen()
            if sel:
                self.pick_clicked(sel[0][2])

        def watch():
            for chan, title, url in chosen():
                self.w_url.set(url); self.w_name.set(title); self.w_match.set("")
                self.watch_add()
            if chosen():
                self.tabs.select(1)

        def browser():
            for chan, title, url in chosen()[:3]:
                webbrowser.open(url)

        def got(chan, found, where):
            pending[0] -= 1
            counts[chan] = len(found)
            for title, url in found:
                try:
                    iid = tv.insert("", "end", values=(chan, title, url))
                except tk.TclError:  # window closed
                    return
                rows[iid] = (chan, title, url)
            done = ", ".join("%s %d" % (c, n) for c, n in counts.items())
            try:
                status.set(("Searching ... " if pending[0] else "Found: ") + done +
                           ("" if pending[0] else "   (double-click a show to pick its episodes)"))
            except tk.TclError:
                pass
            if where == "web" and found:
                self.log("Search: %s results for '%s' came from a web search" % (chan, q))

        for chan in chans:
            def work(chan=chan):
                try:
                    found, where = search_channel(q, chan)
                except Exception as e:
                    found, where = [], "error"
                    self.log("Search on %s failed: %s" % (chan, e))
                self.q.put(("call", lambda: got(chan, found, where)))
            threading.Thread(target=work, daemon=True).start()
        tv.bind("<Double-1>", lambda ev: pick())
        b = ttk.Frame(win)
        b.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(b, text="Pick episodes ...", command=pick).pack(side="left")
        ttk.Button(b, text="Add whole show to queue", command=add_queue).pack(side="left", padx=6)
        ttk.Button(b, text="Add to watch list", command=watch).pack(side="left")
        ttk.Button(b, text="Open in browser", command=browser).pack(side="left", padx=6)
        ttk.Button(b, text="Close", command=win.destroy).pack(side="right")
        self.save_settings()

    def _write_sidecars(self, item):
        """.nfo next to each finished file (after any re-encode, so the details match the file)."""
        o = item["opts"]
        style = o.get("nfo", "Off")
        if style == "Off":
            return
        reencoded = set(item.get("reencoded") or [])
        for info in item.get("infos") or []:
            f = info.get("filepath")
            if f and os.path.isfile(f) and f.lower().endswith(MEDIA_EXTS + (".flac", ".wav")):
                if style.startswith("Scene") and os.path.normcase(os.path.abspath(f)) in reencoded:
                    # A scene .nfo labels the file WEB-DL (untouched from the site); a re-encoded
                    # file isn't one, so it gets no scene .nfo rather than a wrong label.
                    self.log("No scene .nfo for %s - it was re-encoded, so it isn't an untouched WEB-DL."
                             % os.path.basename(f))
                    continue
                out = write_nfo(f, info, style, self.log)
                if out:
                    self.log("Wrote %s" % os.path.basename(out))

    def _below_minimum(self, item, title, channel, best, swaps, seven):
        """If the best on offer is under the minimum quality, ask (or decide for watch-list runs).
        -> None to carry on, or a status label for a skipped item."""
        o = item["opts"]
        try:
            want = int(str(o.get("min_quality", "Off")).rstrip("p"))
        except ValueError:
            return None
        if not best or best >= want or swaps:  # unknown, fine, or a 1080p try still to come
            return None
        why = ("7plus gave the anonymous stream - it only gives 720p to accounts signed in through Firefox. "
               "Use 'Sign in to a site' > 7plus, close Firefox, then Retry."
               if seven == "anonymous" or (channel == "7plus" and seven != "signed") else
               "%s doesn't offer more for this one." % channel)
        label = "Only %dp - %s" % (best, "sign in to 7plus, then Retry" if "7plus gave" in why else "below %dp" % want)
        choice = getattr(self, "low_choice", None)
        if o.get("watch"):
            choice = "skip"
        if choice is None:
            ans, done = {}, threading.Event()
            self.q.put(("call", lambda: self._ask_low(title, channel, best, want, why, ans, done)))
            while not done.wait(0.5):
                if self.stop_flag:
                    return "Stopped before download"
            choice = ans.get("choice", "skip")
            if ans.get("all"):
                self.low_choice = choice
        self.log(("Downloading anyway at %dp (below the %dp minimum)." % (best, want)) if choice == "download" else
                 "Skipped %s: only %dp on offer (minimum %dp). %s" % (title, best, want, why))
        return None if choice == "download" else label

    def _ask_low(self, title, channel, best, want, why, ans, done):
        win = tk.Toplevel(self.root)
        win.title("Below %dp" % want)
        win.transient(self.root)
        win.resizable(False, False)
        ttk.Label(win, wraplength=460, justify="left", font=("Segoe UI", 10, "bold"), text=title).pack(anchor="w", padx=14, pady=(14, 4))
        ttk.Label(win, wraplength=460, justify="left", text="%s only offers %dp for this one - your minimum is %dp.\n\n%s"
                  % (channel, best, want, why)).pack(anchor="w", padx=14, pady=4)
        every = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text="Do the same for the rest of this run", variable=every).pack(anchor="w", padx=14, pady=4)

        def pick(choice):
            ans.update(choice=choice, all=every.get())
            win.destroy()
            done.set()
        b = ttk.Frame(win)
        b.pack(fill="x", padx=14, pady=(6, 14))
        ttk.Button(b, text="Download anyway (%dp)" % best, command=lambda: pick("download")).pack(side="left")
        ttk.Button(b, text="Skip it", command=lambda: pick("skip")).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", lambda: pick("skip"))
        win.lift()

    # ---- channel self-test
    SELFTEST = (("ABC iView", "abc.net.au", "Gardening Australia"), ("10", "10.com.au", "The Project"),
                ("SBS On Demand", "sbs.com.au", "Insight"), ("7plus", "7plus.com.au", "Home and Away"),
                ("9Now", "9now.com.au", "A Current Affair"))

    def channel_test(self, chan, domain, query):
        """Sign-in state, a current episode and what quality it would download at. -> dict"""
        res = {"chan": chan, "signin": "", "episode": "", "quality": "", "result": "", "ok": False}
        o = self.opts()
        login = None
        if domain == "10.com.au":
            login = self.login or saved_login(domain) or self.site_logins.get(domain)
            res["signin"] = "email + password set ✓" if login else "not signed in ✗ (needed)"
        else:
            _FF_CACHE.pop(domain, None)
            state = firefox_state(domain)
            res["signin"] = {"ready": "Firefox ✓", "close_firefox": "Firefox (close it) !",
                             "visited": "not signed in ✗", "no": "not signed in ✗",
                             "noprofile": "no Firefox"}.get(state, state)
            if state == "ready" and o.get("cookies", "None") == "None":
                o = dict(o, cookies="firefox")
        std = tool_path("yt-dlp")
        if not std:
            res["result"] = "yt-dlp isn't installed yet"
            return res
        try:
            shows, _ = search_channel(query, chan)
            if not shows:
                res["result"] = "search found nothing (the channel's search may be down)"
                return res
            entries = list_entries(std, shows[0][1], dict(o, mode="Video"), lambda t: None)
            ep = next((e for e in entries if e.get("url") and not e.get("items") and e.get("episode") != "show"), None)
            if not ep:
                res["result"] = "couldn't find an episode of %s" % shows[0][0]
                return res
            res["episode"] = "%s - %s" % (shows[0][0], ep.get("title", ""))[:70]
            eng = engine_for(ep["url"])
            exe = eng[1] if eng else std
            r = subprocess.run([exe, "--ignore-config", "--no-warnings", "--simulate", "--no-playlist", "--print", "%()j"]
                               + tls_args() + plugin_args(exe) + auth_args(o, login) + ["--", ep["url"]],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
                               creationflags=NO_WINDOW)
            info = next((json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")), None)
            if not info:
                errs = [l for l in r.stderr.splitlines() if l.startswith("ERROR")]
                res["result"] = "✗ " + (friendly_error(errs) or (errs[-1][7:] if errs else "couldn't read the episode"))
                return res
        except Exception as e:
            res["result"] = "✗ %s" % e
            return res
        heights = stream_heights(info)
        best = heights[0] if heights else 0
        rec = recipe_for(ep["url"])
        hd = None
        for u in swap_candidates(info, rec[1]) if rec else []:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": BROWSER_UA, "Range": "bytes=0-1023"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    hd = resp.status < 400
            except Exception:
                hd = False
            if hd:
                break
        res["quality"] = ("1080p (via the 1080p stream)" if hd else "%dp" % best if best else "not listed")
        seven = seven_stream_state(info) if domain == "7plus.com.au" else None
        if seven == "anonymous" or (domain == "7plus.com.au" and best and best < 720):
            res["result"] = "✗ anonymous stream - sign in to 7plus in Firefox, close Firefox"
        else:
            res["ok"] = True
            res["result"] = "✓ works" + (" (signed-in stream)" if seven == "signed" else "") + \
                (" - no 1080p for this episode" if hd is False else "")
        return res

    def selftest_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Test all channels")
        win.geometry("980x300")
        ttk.Label(win, text="Signs in, finds a current episode on each channel and checks what quality it would "
                            "download at (nothing is downloaded).").pack(anchor="w", padx=10, pady=(10, 4))
        tv = ttk.Treeview(win, columns=("chan", "signin", "episode", "quality", "result"), show="headings", height=6)
        for cid, text, w in (("chan", "Channel", 100), ("signin", "Signed in", 150), ("episode", "Test episode", 300),
                             ("quality", "Best quality", 160), ("result", "Result", 260)):
            tv.heading(cid, text=text)
            tv.column(cid, width=w, anchor="w", stretch=cid in ("episode", "result"))
        tv.pack(fill="both", expand=True, padx=10, pady=4)
        results = {}

        def run():
            for chan, _, _ in self.SELFTEST:
                try:
                    tv.delete(chan)
                except tk.TclError:
                    pass
                tv.insert("", "end", iid=chan, values=(chan, "", "", "", "testing ..."))
            for chan, domain, query in self.SELFTEST:
                def work(chan=chan, domain=domain, query=query):
                    res = self.channel_test(chan, domain, query)
                    results[chan] = res
                    self.log("Channel test - %s: %s | %s | %s | %s" % (chan, res["signin"], res["episode"],
                                                                       res["quality"], res["result"]))

                    def show():
                        try:
                            tv.item(chan, values=(chan, res["signin"], res["episode"], res["quality"], res["result"]))
                        except tk.TclError:
                            pass
                    self.q.put(("call", show))
                threading.Thread(target=work, daemon=True).start()

        def copy():
            text = "\n".join("%s: %s | %s | %s | %s" % (c, r["signin"], r["episode"], r["quality"], r["result"])
                             for c, r in results.items())
            self.root.clipboard_clear()
            self.root.clipboard_append("TasRip v%s channel test\n%s" % (VERSION, text))
        b = ttk.Frame(win)
        b.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(b, text="Run again", command=run).pack(side="left")
        ttk.Button(b, text="Copy results", command=copy).pack(side="left", padx=6)
        ttk.Button(b, text="Close", command=win.destroy).pack(side="right")
        run()

    # ---- 1.0.8: subtitle check for ordinary downloads
    def _check_item_subs(self, item):
        if item.get("sub_warn") is not None or not item["opts"].get("subs"):
            return
        for f in item.get("files") or []:
            if not f.lower().endswith(VIDEO_EXTS) or not os.path.isfile(f):
                continue
            stem = os.path.splitext(f)[0]
            folder = os.path.dirname(f) or "."
            dur = None
            for side in os.listdir(folder):
                full = os.path.join(folder, side)
                if not (full.startswith(stem + ".") and side.lower().endswith(".srt")) or sub_info(full)[1]:
                    continue
                dur = dur or media_duration(f)
                for problem in subtitle_problems(full, dur):
                    item.setdefault("sub_warn", []).append(problem)
                    self.log("Subtitle check (%s): %s - the site's file may be incomplete." % (side, problem))

    # ---- 1.0.8: report for one failed item
    def report_item(self, iid):
        item = self.items.get(iid)
        if not item:
            return
        self.log("Making a report for %s ..." % item["url"])
        lines = ["%s  %s" % (self.items[i]["status"], self.items[i]["url"]) for i in self.items]

        def work():
            try:
                path = make_problem_report(lines, item=item)
            except Exception as e:
                self.q.put(("call", lambda e=e: messagebox.showerror(APP_NAME, "Couldn't make the report: %s" % e)))
                return

            def done():
                self.log("Report saved: %s" % path)
                messagebox.showinfo(APP_NAME, "Saved a report for this download in the TasRip folder:\n\n%s\n\n"
                                              "It has everything TasRip logged for it. Send that file to whoever "
                                              "is helping you (passwords and your user name are removed)."
                                    % os.path.basename(path))
                show_in_folder(path)
            self.q.put(("call", done))
        threading.Thread(target=work, daemon=True).start()

    # ---- 1.0.8: antivirus helper
    def antivirus_dialog(self, names=None, reason=""):
        if getattr(self, "_av_open", False):
            return
        self._av_open = True
        win = tk.Toplevel(self.root)
        win.title("Antivirus helper")
        win.transient(self.root)
        win.resizable(False, False)
        what = ", ".join(names) if names else "one of TasRip's tools"
        ttk.Label(win, wraplength=500, justify="left", text=(
            "It looks like your antivirus has blocked or removed %s.\n\n"
            "Antivirus programs often flag tools like yt-dlp, ffmpeg and the custom yt-dlp builds because "
            "they're unsigned downloaders - it's a false alarm. TasRip will keep working with what's left "
            "(it falls back to the normal yt-dlp), but for everything to work, let your antivirus trust "
            "the TasRip folder:\n\n%s" % (what, BASE_DIR))).pack(anchor="w", padx=14, pady=(14, 6))
        if reason:
            ttk.Label(win, foreground="#888", wraplength=500, text="(Windows said: %s)" % reason[:160]).pack(anchor="w", padx=14)
        msg = tk.StringVar()
        b = ttk.Frame(win)
        b.pack(fill="x", padx=14, pady=8)

        def defender():
            try:
                subprocess.Popen(defender_exclusion_command(), creationflags=NO_WINDOW)
                msg.set("Windows will ask for permission - say Yes. Then press 'Reinstall missing tools'.")
                self.log("Asked Windows Defender to trust %s" % BASE_DIR)
            except OSError as e:
                msg.set("Couldn't ask Windows: %s" % e)

        def reinstall():
            jobs = [(label, globals()[fn]) for label, name, fn in self.SETUP_TOOLS + EXTRA_TOOLS if not tool_path(name)]
            if not jobs:
                msg.set("Nothing is missing right now.")
                return

            def job(log):
                for label, fn in jobs:
                    try:
                        fn(log)
                    except Exception as e:
                        log("Couldn't get %s: %s" % (label, e))
            self.tool_job(job, setup_done=True)
            msg.set("Reinstalling: %s ..." % ", ".join(l for l, _ in jobs))
        ttk.Button(b, text="Trust this folder in Windows Defender", command=defender).pack(side="left")
        ttk.Button(b, text="Reinstall missing tools", command=reinstall).pack(side="left", padx=6)
        ttk.Label(win, wraplength=500, justify="left", foreground="#888", text=(
            "Other antivirus (Avast, AVG, Norton, McAfee, Kaspersky, ESET, Bitdefender): open it, find "
            "'Exceptions' or 'Exclusions', add the folder above, then restore anything it quarantined from "
            "the TasRip folder.")).pack(anchor="w", padx=14, pady=4)
        ttk.Label(win, textvariable=msg, foreground="#0a5", wraplength=500).pack(anchor="w", padx=14, pady=4)

        def close():
            self._av_open = False
            win.destroy()
        ttk.Button(win, text="Close", command=close).pack(anchor="e", padx=14, pady=(0, 14))
        win.protocol("WM_DELETE_WINDOW", close)

    # ---- 1.0.8: sign-in status
    def signin_status_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Sign-in status")
        win.transient(self.root)
        tv = ttk.Treeview(win, columns=("site", "state"), show="headings", height=6, selectmode="browse")
        tv.heading("site", text="Site")
        tv.heading("state", text="Signed in?")
        tv.column("site", width=140, anchor="w")
        tv.column("state", width=520, anchor="w")
        tv.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        note = tk.StringVar()

        def fill():
            tv.delete(*tv.get_children())
            for name, _, domain in SIGNIN_SITES:
                tv.insert("", "end", iid=name, values=(name, "checking ..."))
            note.set("Browser cookies is set to: %s" % self.v["cookies"].get())

            def work():
                for name, _, domain in SIGNIN_SITES:
                    if domain == "10.com.au":
                        how = ("saved on this PC" if saved_login(domain) else "for this session"
                               if (self.login or self.site_logins.get(domain)) else None)
                        text = "✓ email + password %s" % how if how else \
                            "✗ not signed in - 10 needs your email + password (double-click)"
                    else:
                        _FF_CACHE.pop(domain, None)
                        st = firefox_state(domain)
                        text = {"ready": "✓ signed in (Firefox)",
                                "close_firefox": "! signed in, but close Firefox so it can be read",
                                "visited": ("✗ missing 7plus's login cookie - sign out and in again in Firefox (max 504p until then)"
                                            if domain == "7plus.com.au" else "✗ not signed in (Firefox has visited)"),
                                "no": "✗ not signed in", "noprofile": "✗ Firefox not found"}.get(st, st)
                    self.q.put(("call", lambda n=name, t=text: tv.item(n, values=(n, t)) if tv.exists(n) else None))
            threading.Thread(target=work, daemon=True).start()
        tv.bind("<Double-1>", lambda e: self.signin_dialog(preset=tv.identify_row(e.y) or None))
        ttk.Label(win, textvariable=note, foreground="#888").pack(anchor="w", padx=10)
        b = ttk.Frame(win)
        b.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(b, text="Sign in to a site ...", command=self.signin_dialog).pack(side="left")
        ttk.Button(b, text="Check again", command=fill).pack(side="left", padx=6)
        ttk.Label(b, foreground="#888", text="Double-click a site to sign in").pack(side="left", padx=6)
        ttk.Button(b, text="Close", command=win.destroy).pack(side="right")
        fill()

    # ---- 1.0.8: export / import
    def export_clicked(self):
        self.save_settings()
        save_queue(list(self.items.values()))
        dest = filedialog.asksaveasfilename(title="Export my TasRip setup", defaultextension=".zip",
                                            initialfile="TasRip-setup-%s.zip" % time.strftime("%Y%m%d"),
                                            filetypes=[("Zip file", "*.zip")])
        if not dest:
            return
        try:
            n = export_setup(dest)
        except Exception as e:
            return messagebox.showerror(APP_NAME, "Couldn't export: %s" % e)
        self.log("Exported %d file(s) to %s" % (n, dest))
        messagebox.showinfo(APP_NAME, "Saved your settings, watch list, history, site engines and archive to:\n\n%s\n\n"
                                      "Your 10 password is not included. On the other PC: Tools > Import setup."
                            % dest)

    def import_clicked(self):
        src = filedialog.askopenfilename(title="Import a TasRip setup", filetypes=[("Zip file", "*.zip")])
        if not src:
            return
        if not messagebox.askyesno(APP_NAME, "Replace this PC's settings, watch list, history and archive with the "
                                             "ones in that file? (The current ones are kept as .bak files.)"):
            return
        try:
            done = import_setup(src)
        except Exception as e:
            return messagebox.showerror(APP_NAME, "Couldn't import: %s" % e)
        self.log("Imported: %s" % ", ".join(done))
        messagebox.showinfo(APP_NAME, "Imported %s.\n\nTasRip will close now - start it again to use them." % ", ".join(done))
        self.running = False
        self.items = {}
        self.save_settings = lambda: None  # keep the imported settings.json
        self.root.destroy()

    # ---- 1.0.8: remembered queue
    def _save_queue_soon(self):
        if getattr(self, "_queue_save_pending", False):
            return
        self._queue_save_pending = True

        def go():
            self._queue_save_pending = False
            try:
                save_queue(list(self.items.values()))
            except Exception:
                pass
        self.root.after(1500, go)

    def _restore_queue(self):
        saved = load_queue()
        if not saved:
            return
        for it in saved:
            o = dict(self.opts(), **it["opts"])
            st = it.get("status") if it.get("status") in ("Queued", "Failed", "Stopped") else "Queued"
            label = "Audio " + o.get("audio_fmt", "") if o.get("mode") == "Audio" else "%s %s" % (o.get("quality", ""), o.get("container", ""))
            iid = self.tree.insert("", "end", values=(it["url"], label, it.get("shown") or st, "", it.get("title", ""), ""))
            self.items[iid] = {"url": it["url"], "opts": o, "status": st, "title": it.get("title", ""),
                               "shown": it.get("shown", "")}
        n_q = sum(1 for it in saved if it.get("status") in ("Queued", "Working"))
        self.log("Restored %d download(s) from last time%s." % (len(saved), " - press Start to carry on" if n_q else ""))
        self._summary()

    # ---- 1.0.8: helper tools kept up to date
    def _auto_tool_update(self, force=False):
        if not IS_WIN or (not force and not self.v["tools_autoupdate"].get()):
            return
        if self.running or self.busy_tools:
            self.root.after(15 * 60 * 1000, self._auto_tool_update)
            return
        if force:
            self.log("Checking every helper tool for a newer version ...")

        def work():
            res = update_all_tools(lambda t: self.q.put(("log", t)), busy=lambda: self.running, force=force)
            done = [n for n, r in res.items() if r == "updated"]
            bad = ["%s: %s" % (n, r) for n, r in res.items() if r.startswith(("kept", "couldn't"))]
            if done:
                self.q.put(("log", "Tools updated: %s" % ", ".join(done)))
            for b in bad:
                self.q.put(("log", "Tool update - %s" % b))
            if force and not done:
                self.q.put(("log", "All helper tools are up to date." if not bad else "Tool check finished."))
        threading.Thread(target=work, daemon=True).start()

    # ---- 1.0.8: first-start progress window
    def _setup_window(self, labels):
        win = tk.Toplevel(self.root)
        win.title("Setting up TasRip")
        win.transient(self.root)
        win.resizable(False, False)
        ttk.Label(win, wraplength=460, text="Downloading the tools TasRip uses - this happens once and takes a few "
                                            "minutes. You can keep using the window meanwhile.").pack(anchor="w", padx=14, pady=(14, 8))
        rows = {}
        grid = ttk.Frame(win)
        grid.pack(fill="x", padx=14)
        for n, label in enumerate(labels):
            ttk.Label(grid, text=label, width=14).grid(row=n, column=0, sticky="w", pady=2)
            bar = ttk.Progressbar(grid, length=260, maximum=100)
            bar.grid(row=n, column=1, padx=6, pady=2)
            st = tk.StringVar(value="waiting")
            ttk.Label(grid, textvariable=st, width=22).grid(row=n, column=2, sticky="w")
            rows[label] = (bar, st)
        ttk.Button(win, text="Hide", command=win.withdraw).pack(anchor="e", padx=14, pady=10)
        self._setup_rows, self._setup_win = rows, win

    def _setup_update(self, label, pct=None, text=None):
        row = getattr(self, "_setup_rows", {}).get(label)
        if not row:
            return
        try:
            if pct is not None:
                row[0].configure(value=pct)
            if text is not None:
                row[1].set(text)
            if all(r[1].get().startswith(("✓", "✗")) for r in self._setup_rows.values()):
                self.root.after(4000, lambda: self._setup_win.destroy())
        except tk.TclError:
            pass

    def _progress_log(self, label, log):
        """A log() for one tool's installer that turns '  40% of 80.0 MB' lines into its progress bar."""
        def out(text):
            m = re.match(r"^\s+(\d+)% of ([\d.]+) MB", text)
            if m:
                self.q.put(("call", lambda: self._setup_update(label, int(m.group(1)), "%s%% of %s MB" % m.groups())))
            else:
                log(text)
        return out

    # ---- episode picker
    def pick_clicked(self, url=None):
        lines = [] if url else [l.strip() for l in self.urls.get("1.0", "end").splitlines() if l.strip()]
        url = url or (lines[0] if lines else None) or simpledialog.askstring(
            "Pick episodes", "Paste a season, show, playlist, podcast or iView channel link:", parent=self.root)
        if not url:
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        exe = tool_path("yt-dlp")
        if not exe:
            return messagebox.showinfo(APP_NAME, "yt-dlp isn't set up yet - give it a moment and try again.")
        if lines:
            self.urls.delete("1.0", "2.0")
        o = self.opts()
        self.status.set("Reading the episode list ...")
        self.log("Reading the episode list for %s ..." % url)

        def work():
            try:
                entries = list_entries(exe, url, o, self.log)
            except Exception as e:
                self.q.put(("call", lambda e=e: (self.status.set("Ready"), messagebox.showerror(
                    APP_NAME, "Couldn't read the episodes on that page:\n\n%s" % e))))
                return
            self.q.put(("call", lambda: (self._summary(), self._pick_window(url, entries, o))))
        threading.Thread(target=work, daemon=True).start()

    def _pick_window(self, url, entries, o):
        if not entries:
            return messagebox.showinfo(APP_NAME, "No episodes found on that page. Paste it into the box and "
                                                 "press Add to queue to download it normally.")
        win = tk.Toplevel(self.root)
        win.title("Pick episodes - %d found" % len(entries))
        win.geometry("760x520")
        r = ttk.Frame(win)
        r.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(r, text="Show only titles containing").pack(side="left")
        flt = tk.StringVar()
        ttk.Entry(r, textvariable=flt, width=24).pack(side="left", padx=6)
        count = tk.StringVar()
        ttk.Label(r, textvariable=count).pack(side="right")
        f = ttk.Frame(win)
        f.pack(fill="both", expand=True, padx=10, pady=4)
        tv = ttk.Treeview(f, columns=("pick", "s", "e", "title"), show="headings", selectmode="extended")
        for cid, text, w, anchor in (("pick", "Get", 40, "center"), ("s", "Season", 60, "center"),
                                     ("e", "Ep", 60, "center"), ("title", "Title", 540, "w")):
            tv.heading(cid, text=text)
            tv.column(cid, width=w, anchor=anchor, stretch=cid == "title")
        sb = ttk.Scrollbar(f, command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        picked = set(range(len(entries)))
        shown = []
        TICK, NO = "\u2611", "\u2610"

        def fill():
            tv.delete(*tv.get_children())
            term = flt.get().strip().lower()
            shown[:] = []
            for n, e in enumerate(entries):
                if term and term not in str(e["title"]).lower():
                    continue
                shown.append(n)
                ep = e.get("episode", "")
                tv.insert("", "end", iid=str(n), values=(TICK if n in picked else NO, e.get("season", ""),
                                                          "" if ep == "show" else ep, e["title"]))
            count.set("%d of %d ticked" % (len(picked), len(entries)))

        def toggle(event=None, rows=None):
            for iid in rows or tv.selection():
                n = int(iid)
                picked.symmetric_difference_update({n})
                tv.set(iid, "pick", TICK if n in picked else NO)
            count.set("%d of %d ticked" % (len(picked), len(entries)))

        def click(event):
            iid = tv.identify_row(event.y)
            if iid and tv.identify_column(event.x) == "#1":
                toggle(rows=[iid])
                return "break"

        def set_all(on):
            if on:
                picked.update(shown)
            else:
                picked.difference_update(shown)
            fill()

        def add():
            n_added = 0
            for n, e in enumerate(entries):
                if n not in picked:
                    continue
                opts = dict(o, playlist=e.get("episode") == "show" or bool(e.get("items")), items=e.get("items", ""))
                self._enqueue(e["url"], opts)
                n_added += 1
            self.log("Added %d picked item(s) from %s" % (n_added, url))
            self.save_settings()
            self._summary()
            win.destroy()
        flt.trace_add("write", lambda *a: fill())
        tv.bind("<Button-1>", click)
        tv.bind("<space>", toggle)
        tv.bind("<Double-1>", lambda ev: toggle(rows=[tv.identify_row(ev.y)]) if tv.identify_row(ev.y) else None)
        b = ttk.Frame(win)
        b.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(b, text="Tick all shown", command=lambda: set_all(True)).pack(side="left")
        ttk.Button(b, text="Untick all shown", command=lambda: set_all(False)).pack(side="left", padx=6)
        ttk.Label(b, foreground="#888", text="Click a box (or press Space) to tick / untick").pack(side="left", padx=8)
        ttk.Button(b, text="Add ticked to queue", command=add).pack(side="right")
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="right", padx=6)
        fill()

    # ---- live recording
    def live_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Record live TV")
        win.transient(self.root)
        win.resizable(False, False)
        pad = {"padx": 12, "pady": 4}
        ttk.Label(win, wraplength=480, justify="left", text=(
            "Paste a channel's live page or a stream address (.m3u8). TasRip finds the stream with yt-dlp, "
            "and falls back to Streamlink if yt-dlp can't. DRM-protected channels can't be recorded."
            ).replace("TasRip", APP_NAME)).pack(anchor="w", padx=12, pady=(12, 6))
        url = tk.StringVar()
        r = ttk.Frame(win); r.pack(fill="x", **pad)
        ttk.Label(r, text="Live page / stream").pack(side="left")
        ttk.Entry(r, textvariable=url, width=50).pack(side="left", padx=6, fill="x", expand=True)
        mins = tk.StringVar(value="60")
        name = tk.StringVar(value="Live %s" % time.strftime("%Y-%m-%d %H%M"))
        r = ttk.Frame(win); r.pack(fill="x", **pad)
        ttk.Label(r, text="Record for").pack(side="left")
        ttk.Entry(r, textvariable=mins, width=6).pack(side="left", padx=4)
        ttk.Label(r, text="minutes").pack(side="left")
        when = tk.StringVar(value="now")
        at = tk.StringVar(value=time.strftime("%H:%M", time.localtime(time.time() + 3600)))
        r = ttk.Frame(win); r.pack(fill="x", **pad)
        ttk.Radiobutton(r, text="Start now", value="now", variable=when).pack(side="left")
        ttk.Radiobutton(r, text="Start at", value="at", variable=when).pack(side="left", padx=(12, 4))
        ttk.Entry(r, textvariable=at, width=6).pack(side="left")
        ttk.Label(r, text="(24-hour time, today or tomorrow)").pack(side="left", padx=4)
        r = ttk.Frame(win); r.pack(fill="x", **pad)
        ttk.Label(r, text="Save as").pack(side="left")
        ttk.Entry(r, textvariable=name, width=40).pack(side="left", padx=6)
        ttk.Label(win, foreground="#888", text="Saved in the Live folder inside your save folder. "
                                               "Keep this app open until it's done.").pack(anchor="w", **pad)

        def go():
            u = url.get().strip()
            if not u:
                return messagebox.showwarning(APP_NAME, "Paste the live page or stream address first.", parent=win)
            if not u.lower().startswith(("http://", "https://")):
                u = "https://" + u
            try:
                seconds = int(float(mins.get()) * 60)
                assert 60 <= seconds <= 24 * 3600
            except (ValueError, AssertionError):
                return messagebox.showwarning(APP_NAME, "Minutes should be between 1 and 1440.", parent=win)
            start = time.time()
            if when.get() == "at":
                m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", at.get())
                if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                    return messagebox.showwarning(APP_NAME, "Start time should look like 20:30.", parent=win)
                lt = time.localtime()
                start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(m.group(1)), int(m.group(2)), 0, 0, 0, -1))
                if start < time.time() - 60:
                    start += 24 * 3600
            self.schedule_live(u, seconds, start, name.get().strip() or "Live recording")
            win.destroy()
        b = ttk.Frame(win); b.pack(fill="x", padx=12, pady=(6, 12))
        ttk.Button(b, text="Record", command=go).pack(side="right")
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="right", padx=6)

    def schedule_live(self, url, seconds, start, name):
        o = self.opts()
        status = "Starting" if start <= time.time() + 1 else "Scheduled " + time.strftime("%a %H:%M", time.localtime(start))
        iid = self.tree.insert("", "end", values=(url, "Live %d min" % (seconds // 60), status, "", "", ""))
        self.items[iid] = {"url": url, "opts": o, "status": "Scheduled",
                           "live": {"seconds": seconds, "start": start, "name": name}}
        self.log("Live recording %s: %s for %d min - %s" % ("starting" if status == "Starting" else "scheduled",
                                                            url, seconds // 60, status))
        self.root.after(int(max(0, start - time.time()) * 1000), lambda: self._start_live(iid))
        self._summary()

    def _start_live(self, iid):
        item = self.items.get(iid)
        if not item or item["status"] in ("Recording", "Stopped") or iid in self.live_stop:
            return
        item["status"] = "Recording"
        live, o = item["live"], item["opts"]
        out = os.path.join(o["outdir"], "Live", (nice_name(live["name"]) or "Live recording") + ".mkv")
        n = 2
        while os.path.exists(out):
            out = os.path.join(o["outdir"], "Live", "%s (%d).mkv" % (nice_name(live["name"]) or "Live recording", n))
            n += 1

        def progress(pct):
            done = int(pct * live["seconds"] / 100 / 60)
            self.q.put(("item", iid, {"status": "Recording %d/%d min" % (done, live["seconds"] // 60),
                                      "pct": "%.0f%%" % pct}))

        def set_proc(p):
            self.live_procs[iid] = p
            if iid in self.live_stop:
                kill_tree(p)

        def work():
            self.q.put(("item", iid, {"status": "Finding the stream"}))
            try:
                final = record_live(item["url"], live["seconds"], out, o, progress, set_proc, self.log,
                                    stopped=lambda: iid in self.live_stop)
                stopped = iid in self.live_stop
                item["status"], item["files"] = ("Stopped" if stopped else "Done"), [final]
                shown = "Stopped (kept what was recorded)" if stopped else "Done ✓"
                self.log("Recording saved: %s" % final)
                item["infos"] = [{"filepath": final, "title": live["name"], "webpage_url": item["url"],
                                  "upload_date": time.strftime("%Y%m%d")}]
                self._write_sidecars(item)
                self.q.put(("call", lambda: self._finished_file(iid, item, live["name"])))
                if not stopped and o.get("notify", True):
                    notify(APP_NAME, "Recording finished: %s" % live["name"], o.get("sound", True))
            except Exception as e:
                item["status"] = "Stopped" if iid in self.live_stop else "Failed"
                shown = "Stopped" if item["status"] == "Stopped" else "Recording failed - " + str(e)[:80]
                self.log("Live recording failed: %s" % e)
            self.live_procs.pop(iid, None)
            self.q.put(("item", iid, {"status": shown, **({"pct": "100%"} if item["status"] == "Done" else {})}))
        threading.Thread(target=work, daemon=True).start()

    def stop_live(self, iid):
        self.live_stop.add(iid)
        p = self.live_procs.get(iid)
        if p:
            kill_tree(p)
        item = self.items.get(iid)
        if item and item["status"] == "Scheduled":
            item["status"] = "Stopped"
            self.tree.set(iid, "status", "Cancelled")

    # ---- after a file is saved
    def _finished_file(self, iid, item, title=None):
        """History entry for a finished download (runs on the window's thread)."""
        files = [f for f in item.get("files") or [] if os.path.exists(f)]
        if not files:
            return
        entry = {"time": time.strftime("%Y-%m-%d %H:%M"), "title": title or item.get("title") or
                 os.path.splitext(os.path.basename(files[0]))[0], "url": item["url"],
                 "site": canonical_site(url_host(item["url"])).replace("www.", ""), "files": files}
        add_history(entry)
        self.history = getattr(self, "history", [])
        self.history.append(entry)
        self._fill_history()

    # ---- history tab
    def _build_history_tab(self):
        page = ttk.Frame(self.tabs)
        self.tabs.add(page, text="  History  ")
        r = ttk.Frame(page); r.pack(fill="x", padx=8, pady=6)
        ttk.Label(r, text="Search").pack(side="left")
        self.h_term = tk.StringVar()
        ttk.Entry(r, textvariable=self.h_term, width=34).pack(side="left", padx=6)
        self.h_term.trace_add("write", lambda *a: self._fill_history())
        self.h_count = tk.StringVar()
        ttk.Label(r, textvariable=self.h_count).pack(side="left", padx=8)
        f = ttk.Frame(page); f.pack(fill="both", expand=True, padx=8, pady=4)
        self.h_tree = ttk.Treeview(f, columns=("when", "title", "site", "file"), show="headings", selectmode="browse")
        for cid, text, w in (("when", "Downloaded", 120), ("title", "Title", 360), ("site", "Site", 120),
                             ("file", "File", 320)):
            self.h_tree.heading(cid, text=text)
            self.h_tree.column(cid, width=w, anchor="w", stretch=cid in ("title", "file"))
        sb = ttk.Scrollbar(f, command=self.h_tree.yview)
        self.h_tree.configure(yscrollcommand=sb.set)
        self.h_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.h_tree.bind("<Double-1>", lambda e: self._history_action("folder"))
        b = ttk.Frame(page); b.pack(fill="x", padx=8, pady=(4, 8))
        for text, act in (("Play / open", "open"), ("Show in folder", "folder"), ("Copy link", "copy"),
                          ("Download again", "again")):
            ttk.Button(b, text=text, command=lambda a=act: self._history_action(a)).pack(side="left", padx=(0, 6))
        ttk.Button(b, text="Clear history", command=self._clear_history).pack(side="right")
        self._fill_history()

    def _fill_history(self):
        if not hasattr(self, "h_tree"):
            return
        self.h_tree.delete(*self.h_tree.get_children())
        term = self.h_term.get().strip().lower()
        shown = 0
        for n in range(len(self.history) - 1, -1, -1):
            e = self.history[n]
            files = e.get("files") or [""]
            if term and term not in ("%s %s %s %s" % (e.get("title"), e.get("site"), e.get("url"), files[0])).lower():
                continue
            self.h_tree.insert("", "end", iid=str(n), values=(e.get("time", ""), e.get("title", ""),
                                                               e.get("site", ""), os.path.basename(files[0])))
            shown += 1
            if shown >= 2000:
                break
        self.h_count.set("%d download(s)" % len(self.history))

    def _history_action(self, act):
        sel = self.h_tree.selection()
        if not sel:
            return
        e = self.history[int(sel[0])]
        f = next((x for x in e.get("files") or [] if os.path.exists(x)), None)
        if act == "copy":
            self.root.clipboard_clear()
            self.root.clipboard_append(e.get("url", ""))
        elif act == "again":
            self._enqueue(e["url"], self.opts())
            self._summary()
            self.tabs.select(0)
        elif not f:
            messagebox.showinfo(APP_NAME, "That file isn't there any more (moved or deleted).")
        elif act == "open":
            open_path(f)
        else:
            show_in_folder(f)

    def _clear_history(self):
        if messagebox.askyesno(APP_NAME, "Clear the download history? (The files themselves aren't touched.)"):
            self.history = []
            try:
                os.remove(HISTORY_FILE)
            except OSError:
                pass
            self._fill_history()

    # ---- watch list tab
    def _build_watch_tab(self):
        page = ttk.Frame(self.tabs)
        self.tabs.add(page, text="  Watch list  ")
        ttk.Label(page, wraplength=940, justify="left", text=(
            "Shows here are checked for new episodes, which are downloaded with the options on the "
            "Downloads tab. Only episodes that aren't in archive.txt are fetched.")).pack(anchor="w", padx=8, pady=(8, 2))
        add = ttk.LabelFrame(page, text="Add a show, season, channel or podcast")
        add.pack(fill="x", padx=8, pady=4)
        self.w_url, self.w_name, self.w_match = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.w_skip = tk.BooleanVar(value=True)
        r = ttk.Frame(add); r.pack(fill="x", padx=6, pady=3)
        ttk.Label(r, text="Link").pack(side="left")
        ttk.Entry(r, textvariable=self.w_url).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(r, text="Name").pack(side="left")
        ttk.Entry(r, textvariable=self.w_name, width=22).pack(side="left", padx=6)
        r = ttk.Frame(add); r.pack(fill="x", padx=6, pady=(3, 6))
        ttk.Label(r, text="Only titles containing").pack(side="left")
        ttk.Entry(r, textvariable=self.w_match, width=20).pack(side="left", padx=6)
        ttk.Label(r, text="(optional)").pack(side="left")
        ttk.Checkbutton(r, text="Skip the episodes already out (only get new ones)",
                        variable=self.w_skip).pack(side="left", padx=12)
        ttk.Button(r, text="Add to watch list", command=self.watch_add).pack(side="right")
        f = ttk.Frame(page); f.pack(fill="both", expand=True, padx=8, pady=4)
        self.w_tree = ttk.Treeview(f, columns=("name", "url", "match", "last", "got"), show="headings")
        for cid, text, w in (("name", "Name", 200), ("url", "Link", 360), ("match", "Titles containing", 120),
                             ("last", "Last checked", 120), ("got", "Episodes got", 90)):
            self.w_tree.heading(cid, text=text)
            self.w_tree.column(cid, width=w, anchor="w", stretch=cid == "url")
        self.w_tree.pack(fill="both", expand=True)
        b = ttk.Frame(page); b.pack(fill="x", padx=8, pady=(4, 8))
        ttk.Button(b, text="Check all now", command=lambda: self.watch_check()).pack(side="left")
        ttk.Button(b, text="Check selected", command=lambda: self.watch_check(self.w_tree.selection())).pack(side="left", padx=6)
        ttk.Button(b, text="Remove selected", command=self.watch_remove).pack(side="left")
        ttk.Checkbutton(b, text="Check once a day by itself", variable=self.v["watch_daily"],
                        command=self.save_settings).pack(side="left", padx=12)
        self.w_status = tk.StringVar()
        ttk.Label(b, textvariable=self.w_status).pack(side="right")
        self._fill_watch()

    def _fill_watch(self):
        self.w_tree.delete(*self.w_tree.get_children())
        for n, w in enumerate(self.watch):
            self.w_tree.insert("", "end", iid=str(n), values=(w.get("name", ""), w["url"], w.get("match", ""),
                                                               w.get("last", "never"), w.get("got", 0)))

    def watch_add(self):
        url = self.w_url.get().strip()
        if not url:
            return messagebox.showinfo(APP_NAME, "Paste the show's link first.")
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        if any(w["url"] == url for w in self.watch):
            return messagebox.showinfo(APP_NAME, "That's already on the watch list.")
        name = self.w_name.get().strip() or url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()[:60]
        entry = {"url": url, "name": name, "match": self.w_match.get().strip(), "last": "never", "got": 0,
                 "added": time.strftime("%Y-%m-%d")}
        self.watch.append(entry)
        save_watch(self.watch)
        self._fill_watch()
        self.w_url.set(""); self.w_name.set(""); self.w_match.set("")
        self.log("Watch list: added %s" % name)
        if self.w_skip.get():
            self._watch_mark_existing(entry)

    def _watch_mark_existing(self, entry):
        """Record the episodes already out in archive.txt, so only new ones get downloaded."""
        exe = tool_path("yt-dlp")
        if not exe:
            return
        self.w_status.set("Noting the episodes already out for %s ..." % entry["name"])
        o = self.opts()

        def work():
            urls = [entry["url"]]
            show = ten_show_slug(entry["url"])
            if show:
                urls = ten_show_seasons(show, self.log) or urls
            elif is_audio_page(entry["url"]):
                urls = [find_feed(entry["url"]) or entry["url"]]
            count = lambda: sum(1 for _ in open(ARCHIVE_FILE, encoding="utf-8")) if os.path.isfile(ARCHIVE_FILE) else 0
            before = count()

            def mark(u):
                args = [exe, "--ignore-config", "--no-warnings", "--simulate", "--force-write-archive",
                        "--download-archive", ARCHIVE_FILE, "--yes-playlist", "--ignore-errors"] + tls_args() + plugin_args(exe)
                if o["cookies"] != "None":
                    args += ["--cookies-from-browser", o["cookies"]]
                args += split_extra(o.get("extra", ""))
                if entry.get("match"):
                    args += ["--match-filters", "title~='(?i)%s'" % re.escape(entry["match"]).replace("'", "\\'")]
                try:
                    subprocess.run(args + ["--", u], capture_output=True, timeout=1800, creationflags=NO_WINDOW)
                except (OSError, subprocess.SubprocessError) as e:
                    self.log("Couldn't list %s: %s" % (u, e))
            for u in urls:
                mark(u)
            if count() == before and not show:  # a page yt-dlp can't read: try the feed it advertises
                feed = find_feed(entry["url"])
                if feed and feed not in urls:
                    mark(feed)
            after = count()
            msg = "%s: %d episode(s) already out - only new ones will be downloaded." % (entry["name"], after - before)
            self.log("Watch list: " + msg)
            self.q.put(("call", lambda: self.w_status.set(msg)))
        threading.Thread(target=work, daemon=True).start()

    def watch_remove(self):
        drop = {int(i) for i in self.w_tree.selection()}
        self.watch = [w for n, w in enumerate(self.watch) if n not in drop]
        save_watch(self.watch)
        self._fill_watch()

    def watch_check(self, rows=None, auto=False):
        todo = [self.watch[int(i)] for i in rows] if rows else list(self.watch)
        if not todo:
            if not auto:
                messagebox.showinfo(APP_NAME, "The watch list is empty - add a show first.")
            return
        known = {i["url"] for i in self.items.values() if i["status"] in ("Queued", "Working")}
        for w in todo:
            if w["url"] in known:
                continue
            o = dict(self.opts(), archive=True, playlist=True, watch=w.get("name") or w["url"],
                     match=w.get("match", ""))
            self._enqueue(w["url"], o, label="Watch: " + (w.get("name") or "")[:30])
            w["last"] = time.strftime("%Y-%m-%d %H:%M")
        save_watch(self.watch)
        self._fill_watch()
        write_stamp(LAST_WATCH_FILE)
        self.log("Watch list: checking %d show(s) for new episodes ..." % len(todo))
        self._summary()
        if not self.running:
            self.start()

    def _auto_watch(self):
        try:
            if self.watch and self.v["watch_daily"].get() and stamp_age(LAST_WATCH_FILE) > 20 * 3600 \
                    and tool_path("yt-dlp") and not self.busy_tools:
                self.watch_check(auto=True)
        finally:
            self.root.after(3600 * 1000, self._auto_watch)

    def _watch_got(self, name, n):
        for w in self.watch:
            if (w.get("name") or w["url"]) == name:
                w["got"] = int(w.get("got", 0)) + n
        save_watch(self.watch)
        self._fill_watch()

    # ---- settings window
    def settings_dialog(self):
        """Tools > Settings: the things that aren't on the main screen."""
        if self.settings_win is not None:
            try:
                self.settings_win.deiconify()
                self.settings_win.lift()
                return
            except tk.TclError:
                self.settings_win = None
        win = tk.Toplevel(self.root)
        self.settings_win = win
        win.title("Settings")
        win.transient(self.root)
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        pad = {"padx": 10, "pady": 4}

        def row(parent):
            f = ttk.Frame(parent)
            f.pack(fill="x", **pad)
            return f

        # General
        g = ttk.Frame(nb)
        nb.add(g, text="  General  ")
        ttk.Checkbutton(g, text="Dark mode", variable=self.v["dark"], command=self.apply_theme).pack(anchor="w", **pad)
        ttk.Checkbutton(g, text="Pop up a notification when the queue finishes or a recording ends",
                        variable=self.v["notify"]).pack(anchor="w", padx=10, pady=1)
        ttk.Checkbutton(g, text="Play a sound with it", variable=self.v["sound"]).pack(anchor="w", padx=30, pady=1)
        r = row(g)
        ttk.Label(r, text="Pause the queue when the save drive has less than").pack(side="left")
        ttk.Entry(r, textvariable=self.v["min_free_gb"], width=5).pack(side="left", padx=4)
        ttk.Label(r, text="GB free  (0 = never)").pack(side="left")
        ttk.Checkbutton(g, text="Check the watch list for new episodes once a day",
                        variable=self.v["watch_daily"]).pack(anchor="w", **pad)
        ttk.Checkbutton(g, text="Use Windows certificate checks (for antivirus HTTPS scanning / filtered networks)",
                        variable=self.v["syscerts"], command=self._syscerts_changed).pack(anchor="w", **pad)
        r = row(g)
        ttk.Checkbutton(r, text="Limit the speed to", variable=self.v["sched_on"]).pack(side="left")
        ttk.Entry(r, textvariable=self.v["sched_rate"], width=6).pack(side="left", padx=4)
        ttk.Label(r, text="between").pack(side="left")
        ttk.Entry(r, textvariable=self.v["sched_from"], width=6).pack(side="left", padx=4)
        ttk.Label(r, text="and").pack(side="left")
        ttk.Entry(r, textvariable=self.v["sched_to"], width=6).pack(side="left", padx=4)
        ttk.Label(r, text="(e.g. 2M, 07:00-23:00; full speed outside those hours)").pack(side="left", padx=4)

        # Encoding
        e = ttk.Frame(nb)
        nb.add(e, text="  Encoding  ")
        ttk.Checkbutton(e, text="Re-encode videos after downloading", variable=self.v["encode"]).pack(anchor="w", **pad)
        ttk.Label(e, foreground="#888", wraplength=640, justify="left", text=(
            "Off: the original stream is kept as-is - fastest, and no quality is lost. Turn it on to "
            "shrink files or change the format. The CPU works on every PC; the graphics card is "
            "optional and much faster where it's supported. Re-encoded files don't get a scene .nfo "
            "(that labels untouched WEB-DLs only). 'Upscale to 1080p' turns 720p into 1080p "
            "(Lanczos + light sharpening): it looks cleaner on a big TV but can't add real detail, and the "
            "files get bigger.")).pack(anchor="w", padx=10)
        r = row(e)
        self._combo(r, "Format", "enc_codec", ENC_CODECS, 13, first=True)
        self._combo(r, "Quality", "enc_quality", ENC_QUALITY, 9)
        self._combo(r, "Speed", "enc_speed", ENC_SPEED, 13)
        r = row(e)
        self._combo(r, "Resize to", "enc_resize", ENC_RESIZE, 16, first=True)
        self._combo(r, "Audio", "enc_audio", ENC_AUDIO, 10)
        r = row(e)
        ttk.Checkbutton(r, text="Use the graphics card", variable=self.v["enc_gpu"]).pack(side="left")
        self._combo(r, "Card", "enc_vendor", GPU_VENDORS, 8)
        hw_text = tk.StringVar(value="Not checked yet - press the button to test this PC's CPU and graphics card.")
        r = row(e)
        ttk.Button(r, text="Check my hardware and recommend",
                   command=lambda: self._detect_hw(hw_text)).pack(side="left")
        ttk.Label(e, textvariable=hw_text, wraplength=640, justify="left").pack(anchor="w", **pad)

        # Tools & engines
        t = ttk.Frame(nb)
        nb.add(t, text="  Tools & engines  ")
        r = row(t)
        ttk.Label(r, text="yt-dlp updates").pack(side="left")
        ttk.Radiobutton(r, text="Stable", value=False, variable=self.v["nightly"],
                        command=self._nightly_changed).pack(side="left", padx=8)
        ttk.Radiobutton(r, text="Nightly (newest site fixes)", value=True, variable=self.v["nightly"],
                        command=self._nightly_changed).pack(side="left")
        ttk.Checkbutton(t, text="Check for a newer yt-dlp once a day", variable=self.v["autoupdate"]).pack(anchor="w", **pad)
        ttk.Checkbutton(t, text="Keep the other tools up to date too (checked weekly, ffmpeg monthly; old version "
                               "kept until the new one works)", variable=self.v["tools_autoupdate"]).pack(anchor="w", padx=10, pady=1)
        ttk.Checkbutton(t, text="Build MKV files with mkvmerge, like the Tas Rip Pack (ffmpeg if it isn't installed)",
                        variable=self.v["use_mkvmerge"]).pack(anchor="w", padx=10, pady=1)
        tv = ttk.Treeview(t, columns=("tool", "state"), show="headings", height=10, selectmode="browse")
        tv.heading("tool", text="Helper tool")
        tv.heading("state", text="Installed version")
        tv.column("tool", width=130, anchor="w")
        tv.column("state", width=520, anchor="w")
        tv.pack(fill="x", **pad)
        r = row(t)

        def reinstall():
            sel = tv.selection()
            fn = next((f for label, _, f in self.SETUP_TOOLS + EXTRA_TOOLS if sel and label == sel[0]), None)
            if fn:
                self.tool_job(globals()[fn])
        ttk.Button(r, text="Update yt-dlp now", command=self.update_ytdlp).pack(side="left")
        ttk.Button(r, text="Reinstall selected tool", command=reinstall).pack(side="left", padx=6)
        ttk.Button(r, text="Refresh", command=lambda: self._fill_tool_versions(tv)).pack(side="left")
        ttk.Button(r, text="Site engines ...", command=self.engines_dialog).pack(side="right")
        ttk.Label(t, text="Site recipes (untick one to download that site the plain way):").pack(anchor="w", **pad)
        r = row(t)
        off = set(filter(None, self.v["recipes_off"].get().split(",")))
        flags = {}

        def recipes_changed():
            self.v["recipes_off"].set(",".join(site for site, var in flags.items() if not var.get()))
        for site, rec in RECIPES.items():
            if "." not in site or site[0].isdigit() and site.count(".") == 3:
                continue
            flags[site] = tk.BooleanVar(value=site not in off)
            ttk.Checkbutton(r, text=rec["name"], variable=flags[site], command=recipes_changed).pack(side="left", padx=(0, 12))
        self._fill_tool_versions(tv)

        bottom = ttk.Frame(win)
        bottom.pack(fill="x", padx=10, pady=(4, 10))

        def close():
            self.save_settings()
            self.settings_win = None
            win.destroy()
        ttk.Button(bottom, text="Close", command=close).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", close)

    def _fill_tool_versions(self, tv):
        """Show each helper tool's version in the Settings list (looked up off the window's thread)."""
        def work():
            rows = [(label, tool_version(name)) for label, name, _ in self.SETUP_TOOLS + EXTRA_TOOLS]

            def show():
                try:
                    tv.delete(*tv.get_children())
                    for label, state in rows:
                        tv.insert("", "end", iid=label, values=(label, state))
                except tk.TclError:  # Settings was closed meanwhile
                    pass
            self.q.put(("call", show))
        threading.Thread(target=work, daemon=True).start()

    def _detect_hw(self, text_var):
        text_var.set("Checking this PC's CPU and graphics card (test-encoding a few frames on each) ...")

        def work():
            try:
                hw = detect_hardware()
                vendor, codec, why = recommend_encoder(hw)
            except Exception as e:
                self.q.put(("call", lambda e=e: text_var.set("Couldn't check the hardware: %s" % e)))
                return
            working = [enc for enc, ok in hw["works"].items() if ok]

            def show():
                self.v["enc_codec"].set(codec)
                if vendor != "CPU":
                    self.v["enc_vendor"].set(vendor)
                text_var.set("CPU: %s (%d threads)\nGraphics: %s\nEncoders that work here: %s\n\n%s" % (
                    hw["cpu"], hw["threads"], ", ".join(hw["gpus"]) or "none found",
                    ", ".join(working) or "none", why))
                self.log("Hardware check: %s | %s | %s" % (hw["cpu"], ", ".join(hw["gpus"]) or "no graphics card",
                                                          ", ".join(working)))
            self.q.put(("call", show))
        threading.Thread(target=work, daemon=True).start()

    # ---- sign-in helper
    def signin_dialog(self, preset=None):
        """Walk through signing in to a site in Firefox, then check it worked and switch to it."""
        win = tk.Toplevel(self.root)
        win.title("Sign in to a site")
        win.transient(self.root)
        win.resizable(False, False)
        ttk.Label(win, wraplength=460, justify="left", text=(
            "Some sites only let signed-in accounts download. For SBS, 9Now, 7plus and iView TasRip uses "
            "your sign-in from Firefox (it never sees that password). 10 needs your email and password "
            "instead.")).pack(anchor="w", padx=14, pady=(14, 8))
        names = [n for n, _, _ in SIGNIN_SITES]
        site = tk.StringVar(value=preset if preset in names else names[0])
        r = ttk.Frame(win)
        r.pack(fill="x", padx=14, pady=4)
        ttk.Label(r, text="Site").pack(side="left")
        site_box = ttk.Combobox(r, textvariable=site, values=names, state="readonly", width=18)
        site_box.pack(side="left", padx=6)
        result = tk.StringVar(value="")

        def chosen():
            return next(x for x in SIGNIN_SITES if x[0] == site.get())

        def open_site():
            ff = firefox_exe()
            if not ff:
                result.set("Firefox isn't installed. It's the browser that works best with TasRip - "
                           "press 'Get Firefox', install it, then come back here.")
                return
            try:
                subprocess.Popen([ff, chosen()[1]])
                result.set("Firefox is opening %s. Sign in there (usually 'Sign in' at the top right), "
                           "then close Firefox completely and press step 3." % chosen()[0])
            except OSError as e:
                result.set("Couldn't open Firefox: %s" % e)

        def check():
            name, _, domain = chosen()
            result.set("Checking Firefox for your %s sign-in ..." % name)

            def work():
                state = firefox_signin(domain)

                def show():
                    msg = {
                        "ready": "Signed in to %s - all set. TasRip will use it from now on." % name,
                        "close_firefox": "Nearly there - your %s sign-in is in Firefox, but Firefox needs to be "
                                         "closed before it can be used. Close every Firefox window, then press "
                                         "Check again." % name,
                        "visited": ("Firefox has been to 7plus, but it doesn't have 7plus's login cookie that "
                                    "downloads need (so you'd only get 504p). Sign out of 7plus in Firefox, sign in "
                                    "again, close Firefox, then press Check again."
                                    if domain == "7plus.com.au" else
                                    "Firefox has been to %s but isn't signed in. Sign in on the site, close "
                                    "Firefox, then press Check again." % name),
                        "no": "No %s sign-in found in Firefox yet. Do steps 1 and 2 first." % name,
                        "noprofile": "Couldn't find Firefox's settings on this PC - open Firefox once, then try again.",
                    }[state]
                    if state == "ready":
                        self.v["cookies"].set("firefox")
                        self.save_settings()
                        msg += " (Browser cookies is now set to Firefox.)"
                    try:
                        result.set(msg)
                    except tk.TclError:
                        pass
                    self.log("Sign-in check for %s: %s" % (name, state))
                self.q.put(("call", show))
            threading.Thread(target=work, daemon=True).start()

        holder = ttk.Frame(win)
        holder.pack(fill="x", padx=14, pady=6)
        steps = ttk.Frame(holder)
        ttk.Button(steps, text="1.  Open the site in Firefox", command=open_site).pack(fill="x", pady=2)
        ttk.Label(steps, text="2.  Sign in on the site, then close Firefox").pack(anchor="w", pady=4)
        ttk.Button(steps, text="3.  Check my sign-in", command=check).pack(fill="x", pady=2)

        # 10: email + password (yt-dlp can't use a browser sign-in for 10)
        pwf = ttk.Frame(holder)
        ttk.Label(pwf, wraplength=440, justify="left", text=(
            "10 doesn't work with a browser sign-in - TasRip needs your 10 email and password. "
            "They're sent only to 10.")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        known = saved_login("10.com.au") or self.site_logins.get("10.com.au") or ("", "")
        user, pw = tk.StringVar(value=known[0]), tk.StringVar(value=known[1])
        remember = tk.BooleanVar(value=bool(saved_login("10.com.au")) or IS_WIN)
        ttk.Label(pwf, text="Email").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(pwf, textvariable=user, width=36).grid(row=1, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(pwf, text="Password").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(pwf, textvariable=pw, width=36, show="*").grid(row=2, column=1, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(pwf, text="Remember on this PC (encrypted so only your Windows account can read it)",
                        variable=remember, state="normal" if IS_WIN else "disabled").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=4)

        def sign_in_10():
            u, p = user.get().strip(), pw.get()
            if not u or not p:
                return result.set("Type your 10 email and password first.")
            result.set("Checking with 10 ...")

            def work():
                state = check_ten_login(u, p)

                def show():
                    if state == "bad":
                        msg = "10 didn't accept that email and password. Check them (try signing in on 10.com.au)."
                    else:
                        self.site_logins["10.com.au"] = (u, p)
                        saved = False
                        if remember.get():
                            try:
                                save_login("10.com.au", u, p)
                                saved = True
                            except Exception as e:
                                self.log("Couldn't remember the 10 login: %s" % e)
                        else:
                            forget_login("10.com.au")
                        msg = ("Signed in to 10 - all set." if state == "ok" else
                               "Couldn't reach 10 to check (%s), but TasRip will use these details." % state)
                        msg += " Remembered on this PC." if saved else " Used until TasRip is closed."
                        msg += " Retry any 10 downloads that failed."
                    try:
                        result.set(msg)
                    except tk.TclError:
                        pass
                    self.log("10 sign-in: %s" % (state if state in ("ok", "bad") else "not checked (%s)" % state))
                self.q.put(("call", show))
            threading.Thread(target=work, daemon=True).start()

        def forget_10():
            forget_login("10.com.au")
            self.site_logins.pop("10.com.au", None)
            user.set("")
            pw.set("")
            result.set("Forgot the 10 email and password.")
        b10 = ttk.Frame(pwf)
        b10.grid(row=4, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Button(b10, text="Sign in to 10", command=sign_in_10).pack(side="left")
        ttk.Button(b10, text="Forget", command=forget_10).pack(side="left", padx=6)

        def site_changed(event=None):
            ten = site.get() == "10"
            (steps.pack_forget if ten else pwf.pack_forget)()
            (pwf if ten else steps).pack(fill="x")
            result.set("")
        site_box.bind("<<ComboboxSelected>>", site_changed)
        site_changed()
        ttk.Label(win, textvariable=result, wraplength=460, justify="left", foreground="#0a5").pack(anchor="w", padx=14, pady=8)
        b = ttk.Frame(win)
        b.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(b, text="Get Firefox", command=lambda: webbrowser.open("https://www.mozilla.org/firefox/")).pack(side="left")
        ttk.Button(b, text="Close", command=win.destroy).pack(side="right")

    # ---- problem report
    def report_problem(self):
        self.log("Making a problem report ...")
        lines = ["%s  %s" % (self.items[i]["status"], self.items[i]["url"]) for i in self.items]

        def work():
            try:
                path = make_problem_report(lines)
            except Exception as e:
                self.q.put(("call", lambda e=e: messagebox.showerror(APP_NAME, "Couldn't make the report: %s" % e)))
                return

            def done():
                self.log("Problem report saved: %s" % path)
                messagebox.showinfo(APP_NAME, "Saved a problem report in the TasRip folder:\n\n%s\n\n"
                                              "Send that file to whoever is helping you. Your passwords, "
                                              "email and Windows user name are not in it." % os.path.basename(path))
                show_in_folder(path)
            self.q.put(("call", done))
        threading.Thread(target=work, daemon=True).start()

    # ---- finished rows
    def _row_file(self, iid):
        item = self.items.get(iid) or {}
        for f in reversed(item.get("files") or []):
            if os.path.exists(f):
                return f
        return None

    def _row_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        item = self.items.get(iid)
        if not item:
            return
        f = self._row_file(iid)
        if item["status"] == "Failed":
            self.report_item(iid)
        elif f:
            show_in_folder(f)
        elif item["status"] == "Done":
            open_path(item["opts"]["outdir"])

    def _row_menu(self, event):
        iid = self.tree.identify_row(event.y)
        item = self.items.get(iid)
        if not item:
            return
        self.tree.selection_set(iid)
        f = self._row_file(iid)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Play / open the file", state="normal" if f else "disabled",
                      command=lambda: open_path(f))
        m.add_command(label="Show in folder", command=lambda: show_in_folder(f) if f else open_path(item["opts"]["outdir"]))
        m.add_separator()
        if item.get("live"):
            m.add_command(label="Stop recording" if item["status"] == "Recording" else "Cancel recording",
                          state="normal" if item["status"] in ("Recording", "Scheduled") else "disabled",
                          command=lambda: self.stop_live(iid))
        m.add_command(label="Retry", state="normal" if item["status"] in ("Failed", "Stopped", "Done") else "disabled",
                      command=lambda: self._retry_one(iid))
        m.add_command(label="Remove from list", state="disabled" if item["status"] in ("Working", "Recording") else "normal",
                      command=self.remove_selected)
        m.add_command(label="Copy link", command=lambda: (self.root.clipboard_clear(), self.root.clipboard_append(item["url"])))
        m.add_command(label="Make a report for this item", state="normal" if item.get("log") else "disabled",
                      command=lambda: self.report_item(iid))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _retry_one(self, iid):
        it = self.items.get(iid)
        if it and it.get("live"):
            if it["status"] not in ("Recording", "Scheduled"):
                self.live_stop.discard(iid)
                it["status"] = "Scheduled"
                self._start_live(iid)
            return
        if it and it["status"] != "Working":
            it["status"] = "Queued"
            self.tree.item(iid, values=(it["url"], self.tree.set(iid, "mode"), "Queued", "", "", ""))
            self._summary()

    # ---- small helpers
    def log(self, text):
        full = getattr(self, "item_log", None)
        if full is not None and len(full) < 5000:
            full.append(text)
        lines = getattr(self, "item_lines", None)
        if lines is not None and (text.startswith(("ERROR", "WARNING", "Couldn't", "ffmpeg ")) or "failed" in text.lower()):
            lines.append(text)
        if "CERTIFICATE_VERIFY_FAILED" in text or "SSLCertVerificationError" in text:
            self.cert_failed = True
        low = text.lower()
        if ("not available from your location" in low or "geo restriction" in low or "geo-restrict" in low) \
                and not getattr(self, "geo_warned", False):
            self.geo_warned = True
            self.q.put(("log", "Note: the site thinks this connection is outside Australia. Some Australian "
                               "connections get flagged this way (it's the site, not TasRip). If the show "
                               "plays in your browser on this PC, try again later; if it doesn't, the site is "
                               "blocking this connection - an Australian VPN server is the only known fix."))
        if text.startswith("[SBS] ") and not getattr(self, "sbs_warned", False):
            self.sbs_warned = True  # the old built-in SBS support, which SBS has shut off (HTTP 410)
            self.q.put(("log", "Note: the SBS fix plugin isn't loaded, so SBS will fail. Check that "
                               "bin\\yt-dlp-plugins\\sbsfix2026 exists (re-extract the TasRip zip "
                               "if not), or reinstall it from the Plugins tab."))
        self.q.put(("log", text))

    def opts(self):
        return {k: var.get() for k, var in self.v.items()}

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.opts(), f, indent=2)
        except OSError:
            pass

    def paste(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        self.urls.insert("end", text.strip() + "\n")

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.v["outdir"].get() or BASE_DIR)
        if d:
            self.v["outdir"].set(os.path.normpath(d))
            self.save_settings()

    def open_folder(self):
        d = self.v["outdir"].get()
        try:
            os.makedirs(d, exist_ok=True)
            if not open_path(d):
                raise OSError("no file manager could be opened - the folder is: " + d)
        except OSError as e:
            messagebox.showerror(APP_NAME, "Can't open the save folder:\n%s\n\n%s" % (d, e))

    # ---- queue management
    def add_urls(self):
        lines = [l.strip() for l in self.urls.get("1.0", "end").splitlines() if l.strip()]
        # "10.com.au/show" or "www.sbs.com.au/..." pasted without https:// - add it
        lines = ["https://" + l if re.match(r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/|$)", l, re.I) else l
                 for l in lines]
        bad = [l for l in lines if not l.lower().startswith(("http://", "https://"))]
        if bad:
            messagebox.showwarning(APP_NAME, "Not a URL, skipped:\n" + "\n".join(bad[:5]))
        known = {i["url"] for i in self.items.values() if i["status"] in ("Queued", "Working")}
        o = self.opts()
        for url in lines:
            if url in bad or url in known:
                continue
            known.add(url)
            self._enqueue(url, o)
        self.urls.delete("1.0", "end")
        self.save_settings()
        self._summary()

    def remove_selected(self):
        for iid in self.tree.selection():
            if self.items.get(iid, {}).get("status") not in ("Working", "Recording"):
                if self.items.get(iid, {}).get("live"):
                    self.stop_live(iid)
                self.tree.delete(iid)
                self.items.pop(iid, None)
        self._summary()

    def clear_finished(self):
        for iid, it in list(self.items.items()):
            if it["status"] in ("Done", "Failed", "Stopped"):
                self.tree.delete(iid)
                del self.items[iid]
        self._summary()

    def retry_failed(self):
        for iid, it in self.items.items():
            if it["status"] in ("Failed", "Stopped") and not it.get("live"):
                it["status"] = "Queued"
                self.tree.item(iid, values=(it["url"], self.tree.set(iid, "mode"), "Queued", "", "", ""))
        self._summary()

    def _summary(self):
        if hasattr(self, "root"):
            self._save_queue_soon()
        st = [i["status"] for i in self.items.values()]
        done = sum(x in ("Done", "Failed", "Stopped") for x in st)
        self.total.configure(value=100 * done / len(st) if st else 0)
        self.status.set("%d queued, %d done, %d failed%s" % (
            st.count("Queued"), st.count("Done"), st.count("Failed"),
            "  -  running" if self.running else ""))

    # ---- per-download-thread state (several downloads can run at once)
    _tls = threading.local()

    def _proc_get(self):
        return getattr(App._tls, "proc", None)

    def _proc_set(self, p):
        running = self.__dict__.setdefault("_running_procs", set())
        old = getattr(App._tls, "proc", None)
        if old is not None:
            running.discard(old)
        App._tls.proc = p
        if p is not None:
            running.add(p)
    proc = property(_proc_get, _proc_set)
    item_lines = property(lambda self: getattr(App._tls, "item_lines", None),
                          lambda self, v: setattr(App._tls, "item_lines", v))
    item_log = property(lambda self: getattr(App._tls, "item_log", None),
                        lambda self, v: setattr(App._tls, "item_log", v))
    cert_failed = property(lambda self: getattr(App._tls, "cert_failed", False),
                           lambda self, v: setattr(App._tls, "cert_failed", v))

    def _kill_all(self):
        for p in list(self.__dict__.get("_running_procs", ())):
            kill_tree(p)

    # ---- tools (yt-dlp / ffmpeg)
    SETUP_TOOLS = SETUP_TOOLS

    def _check_tools(self):
        """Fetch any missing tool (yt-dlp, ffmpeg, N_m3u8DL-RE, aria2c) by itself, no questions."""
        gone = vanished_tools()
        if gone and IS_WIN:
            self.log("These tools were here before but have disappeared: %s - usually an antivirus "
                     "quarantining them. Getting them again ..." % ", ".join(gone))
            self.root.after(1500, lambda: self.antivirus_dialog(names=gone))
        extras = [(label, globals()[fn]) for label, name, fn in EXTRA_TOOLS if not tool_path(name)] if IS_WIN else []
        missing = [(label, globals()[fn]) for label, name, fn in self.SETUP_TOOLS if not tool_path(name)]
        if extras and not missing:
            threading.Thread(target=install_extras, args=(lambda t: self.q.put(("log", t)),), daemon=True).start()
        if not missing:
            self.log("Ready. yt-dlp: " + tool_path("yt-dlp"))
            if SWITCH_TO_NIGHTLY and tool_path("yt-dlp") == os.path.join(BIN_DIR, "yt-dlp" + EXE):
                self.log("yt-dlp now uses the nightly builds by default (newest site fixes) - switching ...")
                self.update_ytdlp(quiet=True)
            else:
                self._auto_update()
            self.root.after(20000, self._auto_watch)
            self.root.after(5 * 60 * 1000, self._auto_tool_update)
            return
        names = ", ".join(label for label, _ in missing)
        self.log("Setting up: downloading %s into the bin folder - one moment ..." % names)
        self.status.set("Setting up: downloading %s ..." % names)

        jobs = missing + (extras if stamp_age(EXTRA_RETRY_FILE) > 20 * 3600 else [])
        self._setup_window([label for label, _ in jobs])

        def job(log):
            if extras:
                write_stamp(EXTRA_RETRY_FILE)
            for label, fn in jobs:
                self.q.put(("call", lambda l=label: self._setup_update(l, 0, "downloading ...")))
                try:
                    fn(self._progress_log(label, log))
                    self.q.put(("call", lambda l=label: self._setup_update(l, 100, "\u2713 done")))
                except Exception as e:  # keep going: one tool failing shouldn't stop the rest
                    log("Couldn't get %s: %s" % (label, e))
                    self.q.put(("call", lambda l=label: self._setup_update(l, None, "\u2717 failed (see log)")))
            vanished_tools()  # remember what's installed now
        self.tool_job(job, setup_done=True)

    def _offer_ffmpeg(self):
        self._check_tools()

    def tool_job(self, fn, then_ffmpeg=False, setup_done=False):
        if self.busy_tools or self.running:
            messagebox.showinfo(APP_NAME, "Wait for the current job to finish first.")
            return
        self.busy_tools = True

        def work():
            try:
                fn(self.log)
            except Exception as e:  # network errors, no build for this OS, etc.
                self.log("Failed: %s" % e)
            self.q.put(("tools_done", then_ffmpeg, setup_done))
        threading.Thread(target=work, daemon=True).start()

    def _nightly_changed(self):
        global USE_NIGHTLY
        USE_NIGHTLY = bool(self.v["nightly"].get())
        self.save_settings()
        self.log("yt-dlp channel: %s. Switching now ..." % ("nightly" if USE_NIGHTLY else "stable"))
        self.update_ytdlp()

    def _syscerts_changed(self):
        global USE_SYSTEM_CERTS
        USE_SYSTEM_CERTS = bool(self.v["syscerts"].get())
        self.log("Windows certificate checks %s." % ("on" if USE_SYSTEM_CERTS else "off"))
        self.save_settings()

    def login_dialog(self):
        """Ask for an account to pass to yt-dlp. Held in memory until the app closes."""
        win = tk.Toplevel(self.root)
        win.title("Site login")
        win.transient(self.root)
        win.resizable(False, False)
        ttk.Label(win, wraplength=360, justify="left", text=(
            "For sites that need an account and can't use browser cookies. The details are sent "
            "to yt-dlp for downloads in this session only and are not written to disk.")
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 8), sticky="w")
        user = tk.StringVar(value=self.login[0] if self.login else "")
        pw = tk.StringVar(value=self.login[1] if self.login else "")
        ttk.Label(win, text="Email / username").grid(row=1, column=0, padx=12, pady=4, sticky="w")
        e1 = ttk.Entry(win, textvariable=user, width=34)
        e1.grid(row=1, column=1, padx=(0, 12), pady=4)
        ttk.Label(win, text="Password").grid(row=2, column=0, padx=12, pady=4, sticky="w")
        e2 = ttk.Entry(win, textvariable=pw, width=34, show="*")
        e2.grid(row=2, column=1, padx=(0, 12), pady=4)

        def use():
            self.login = (user.get().strip(), pw.get()) if user.get().strip() else None
            self.log("Site login set for this session." if self.login else "Site login cleared.")
            win.destroy()

        def clear():
            user.set("")
            pw.set("")
            use()

        b = ttk.Frame(win)
        b.grid(row=3, column=0, columnspan=2, pady=12)
        ttk.Button(b, text="Use", command=use).pack(side="left", padx=4)
        ttk.Button(b, text="Clear", command=clear).pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="left", padx=4)
        win.bind("<Return>", lambda ev: use())
        e1.focus_set()
        win.grab_set()

    def engines_dialog(self):
        """Assign your own yt-dlp builds (in bin/engines) to particular sites."""
        win = tk.Toplevel(self.root)
        win.title("Site engines")
        win.transient(self.root)
        ttk.Label(win, wraplength=560, justify="left", text=(
            "Use a different yt-dlp build for particular sites - for example a modified build that "
            "handles one service better. URLs from an assigned site use that build; everything else "
            "uses the standard yt-dlp. Builds are copied into bin\\engines. They are third-party "
            "programs that run with your permissions, so only add ones you trust. Plugins in "
            "bin\\yt-dlp-plugins (like the SBS fix) are not loaded by these builds.")
        ).pack(fill="x", padx=12, pady=(12, 6))
        tree = ttk.Treeview(win, columns=("site", "exe", "ok"), show="headings", height=7)
        for cid, text, width in (("site", "Site", 200), ("exe", "Build (in bin\\engines)", 260), ("ok", "", 90)):
            tree.heading(cid, text=text)
            tree.column(cid, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=12, pady=4)

        def refresh():
            auto_assign_engines(self.log)
            tree.delete(*tree.get_children())
            for site, exe in sorted(load_engines().items()):
                found = os.path.isfile(os.path.join(ENGINE_DIR, os.path.basename(exe)))
                tree.insert("", "end", iid=site, values=(site, exe, "" if found else "missing"))

        def add():
            path = filedialog.askopenfilename(parent=win, title="Choose a yt-dlp build",
                                              filetypes=([("Programs", "*.exe")] if IS_WIN else []) +
                                                        [("All files", "*")])
            if not path:
                return
            name = os.path.basename(path)
            site = simpledialog.askstring(
                APP_NAME, "Which site should use %s?\n\nEnter the site's address, e.g. 9now.com.au "
                          "(covers its sub-sites too)." % name, initialvalue=suggest_site(name), parent=win)
            site = url_host("https://" + (site or "").strip().split("://")[-1])
            if not site:
                return
            try:
                os.makedirs(ENGINE_DIR, exist_ok=True)
                dest = os.path.join(ENGINE_DIR, name)
                if os.path.abspath(path) != os.path.abspath(dest):
                    shutil.copy2(path, dest)
                make_executable(dest)
                engines = load_engines()
                engines[site] = name
                save_engines(engines)
            except OSError as e:
                return messagebox.showerror(APP_NAME, "Couldn't add it:\n%s" % e, parent=win)
            self.log("Engine set: %s -> %s" % (site, name))
            refresh()

        def remove():
            sel = tree.selection()
            if not sel:
                return
            engines = load_engines()
            engines.pop(sel[0], None)
            try:
                save_engines(engines)
            except OSError as e:
                return messagebox.showerror(APP_NAME, "Couldn't save:\n%s" % e, parent=win)
            self.log("Engine removed for %s (the exe stays in bin\\engines)" % sel[0])
            refresh()

        def open_dir():
            os.makedirs(ENGINE_DIR, exist_ok=True)
            open_path(ENGINE_DIR)

        b = ttk.Frame(win)
        b.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(b, text="Add build ...", command=add).pack(side="left")
        ttk.Button(b, text="Remove", command=remove).pack(side="left", padx=4)
        ttk.Button(b, text="Open engines folder", command=open_dir).pack(side="left")
        ttk.Button(b, text="Close", command=win.destroy).pack(side="right")
        refresh()

    def diagnose_clicked(self):
        """Ask for a URL, list what the site offers for it, and save a shareable report."""
        if self.busy_tools or self.running:
            return messagebox.showinfo(APP_NAME, "Wait for the current job to finish first.")
        exe = tool_path("yt-dlp")
        if not exe:
            return self._check_tools()
        sel = self.tree.selection()
        start = self.items[sel[0]]["url"] if sel and sel[0] in self.items else ""
        url = simpledialog.askstring(
            APP_NAME, "URL to diagnose (nothing is downloaded).\n\nUses the current 'Browser cookies' "
                      "setting and any site login.", initialvalue=start, parent=self.root)
        if not url or not url.strip():
            return
        self.busy_tools = True
        self.tabs.select(0)
        o, login = self.opts(), self.login
        site = login_site(url.strip())
        if site and not login:
            login = saved_login(site) or self.site_logins.get(site)
        self.log("\nDiagnosing %s ..." % url.strip())

        def work():
            try:
                path = diagnose(exe, url.strip(), o, login, self.log)
                self.log("\nReport saved: %s\nAccount details and tokens are removed; it is safe to share." % path)
            except Exception as e:
                self.log("Diagnose failed: %s" % e)
            self.q.put(("tools_done", False))
        threading.Thread(target=work, daemon=True).start()

    def build_exe_clicked(self):
        if messagebox.askyesno(
                APP_NAME, "Build %s in this folder?\n\nIt runs on this computer without needing "
                          "Python. The build uses its own private Python environment, so this "
                          "computer's Python isn't changed. Takes a few minutes the first time; "
                          "progress shows in the log." % APP_FILE):
            self.tabs.select(0)
            self.tool_job(build_exe)

    def update_ytdlp(self, quiet=False):
        """Update yt-dlp on the chosen channel (stable or nightly). quiet: the automatic daily check."""
        exe = tool_path("yt-dlp")
        if not exe:
            return self.tool_job(install_ytdlp)
        if self.busy_tools or self.running:
            if not quiet:
                messagebox.showinfo(APP_NAME, "Wait for the current job to finish first.")
            return
        self.busy_tools = True
        channel = "nightly" if USE_NIGHTLY else "stable"

        def work():
            try:
                p = popen([exe, "--update-to", channel] + tls_args())
                lines = [line.rstrip() for line in p.stdout]
                code = p.wait()
                shown = [l for l in lines if l and not l.startswith("[debug]")]
                if quiet and code == 0 and any("up to date" in l.lower() for l in shown):
                    self.log("yt-dlp is up to date (%s)." % channel)
                else:
                    for l in shown:
                        self.log(l)
                if code == 0:
                    try:
                        with open(LAST_UPDATE_FILE, "w") as f:
                            f.write(str(time.time()))
                    except OSError:
                        pass
            except OSError as e:
                self.log("Update failed: %s" % e)
            self.q.put(("tools_done", False))
        threading.Thread(target=work, daemon=True).start()

    def _auto_update(self):
        """Sites change often, so check for a newer yt-dlp once a day, in the background."""
        try:
            last = float(open(LAST_UPDATE_FILE).read().strip())
        except (OSError, ValueError):
            last = 0
        if not self.v["autoupdate"].get():
            return
        if time.time() - last >= AUTO_UPDATE_EVERY and tool_path("yt-dlp") == os.path.join(BIN_DIR, "yt-dlp" + EXE):
            self.log("Checking for a newer yt-dlp ...")
            self.update_ytdlp(quiet=True)

    # ---- downloading
    def start(self):
        if self.running or self.busy_tools:
            return
        if self.urls.get("1.0", "end").strip():
            self.add_urls()
        if not any(i["status"] == "Queued" for i in self.items.values()):
            return messagebox.showinfo(APP_NAME, "Nothing queued. Paste some URLs first.")
        exe = tool_path("yt-dlp")
        if not exe:
            return self._check_tools()
        wants_aria = any(i["opts"].get("aria2") for i in self.items.values() if i["status"] == "Queued")
        if wants_aria and not tool_path("aria2c"):
            if IS_WIN:
                if messagebox.askyesno(APP_NAME, "aria2c is ticked but not installed.\n\nDownload it now? "
                                                 "(Press Start again when it's done.)"):
                    return self.tool_job(install_aria2c)
            else:
                messagebox.showinfo(APP_NAME, "aria2c is ticked but not installed. Install it with:\n\n  %s"
                                              "\n\nDownloading without it for now." % SYSTEM_INSTALL["aria2c"])
        auto_assign_engines(self.log)
        self.run_stats = {"done": 0, "failed": 0, "new": []}
        self.low_choice = None
        try:
            self.parallel_n = int(self.v["parallel"].get())
        except (ValueError, KeyError):
            self.parallel_n = 1
        self.running, self.stop_flag = True, False
        self.b_start.configure(state="disabled")
        self.b_stop.configure(state="normal")
        threading.Thread(target=self._worker, args=(exe,), daemon=True).start()

    def stop(self):
        self.stop_flag = True
        for iid in list(getattr(self, "live_procs", {})):
            self.stop_live(iid)
        self._kill_all()
        self.log("Stopping ...")

    def _next(self):
        # Runs on a worker thread: only touch plain data here, never Tk widgets. Claims the item
        # under a lock so two download threads never take the same one.
        with self.__dict__.setdefault("_claim_lock", threading.Lock()):
            for iid, item in list(self.items.items()):
                if item["status"] == "Queued":
                    item["status"] = "Working"
                    return iid
        return None

    def _worker(self, exe):
        """Run the queue with 1-3 downloads at once (Settings: 'At once')."""
        if not hasattr(self, "run_stats"):
            self.run_stats = {"done": 0, "failed": 0, "new": []}
        n = max(1, min(3, int(getattr(self, "parallel_n", 1) or 1)))
        try:
            extra = [threading.Thread(target=self._worker_loop, args=(exe,), daemon=True) for _ in range(n - 1)]
            for i, t in enumerate(extra):
                time.sleep(0.4 if i == 0 else 0.2)  # stagger the starts a little
                t.start()
            self._worker_loop(exe)
            for t in extra:
                t.join()
        finally:
            self.q.put(("finished",))

    def _bump(self, key, value=1):
        with self.__dict__.setdefault("_stats_lock", threading.Lock()):
            if key == "new":
                self.run_stats["new"].append(value)
            else:
                self.run_stats[key] = self.run_stats.get(key, 0) + value

    def _worker_loop(self, exe):
        if True:
            while not self.stop_flag:
                iid = self._next()
                if iid is None:
                    break
                item = self.items.get(iid)
                if item is None:  # removed from the queue a moment ago
                    continue
                free, need = disk_free_gb(item["opts"]["outdir"]), 0.0
                try:
                    need = float(item["opts"].get("min_free_gb") or 0)
                except ValueError:
                    pass
                if free is not None and need > 0 and free < need:
                    msg = ("The save drive has only %.1f GB free (the limit in Settings is %g GB), so the queue "
                           "is paused. Free up some space or pick another save folder, then press Start." % (free, need))
                    self.log(msg)
                    self.q.put(("item", iid, {"status": "Waiting - drive low on space"}))
                    self.q.put(("call", lambda m=msg: messagebox.showwarning(APP_NAME, m)))
                    if item["opts"].get("notify", True):
                        notify(APP_NAME, "Paused - the save drive is nearly full", item["opts"].get("sound", True))
                    item["status"] = "Queued"
                    self.stop_flag = True
                    break
                item["status"] = "Working"
                self.q.put(("item", iid, {"status": "Starting"}))
                label = None
                self.item_lines = []
                self.item_log = ["%s  %s" % (time.strftime("%H:%M:%S"), item["url"])]
                item.pop("sub_warn", None)
                item["files"] = []
                item["infos"] = []
                item["started"] = time.time()
                try:
                    self.cert_failed = False
                    result, errors, *extra = self._run_item(exe, iid, item)
                    label = extra[0] if extra else None
                    if result == "Failed" and self.cert_failed and not USE_SYSTEM_CERTS and not self.stop_flag:
                        self._use_system_certs()
                        self.log("Retrying with Windows' certificate checks ...")
                        self.cert_failed = False
                        result, errors, *extra = self._run_item(exe, iid, item)
                        label = extra[0] if extra else None
                        if result == "Failed" and self.cert_failed:
                            self.log("Still failing the security check. If the error says 'expired' or "
                                     "'not yet valid', fix this PC's date and time. Otherwise turn off "
                                     "your antivirus's HTTPS/web scanning for a moment and try again.")
                    if result == "Done" and not self.stop_flag:
                        result, label = self._encode_item(iid, item, label)
                    if result == "Done":
                        self._write_sidecars(item)
                    if result == "Done":
                        self._check_item_subs(item)
                except Exception as e:  # e.g. the save folder's drive was unplugged
                    self.log("Couldn't download %s: %s" % (item["url"], e))
                    if is_virus_block(e):
                        self.q.put(("call", lambda e=e: self.antivirus_dialog(reason=str(e))))
                    result, errors = "Failed", 0
                self.proc = None
                item["status"] = result
                if result == "Failed":
                    why = label or friendly_error(self.item_lines) or "Failed - see the log"
                    self.log("FAILED: %s - %s%s" % (item["url"], why, " (%d error lines above)" % errors if errors else ""))
                    exp = item.get("expires")
                    if exp and exp - time.time() < 7 * 86400:
                        why += " (leaves %s)" % time.strftime("%a %d %b", time.localtime(exp))
                        notify(APP_NAME, "%s failed and leaves the site %s - retry before then" % (
                            item.get("title") or "An episode", time.strftime("%a %d %b", time.localtime(exp))),
                            item["opts"].get("sound", True))
                    shown = why + "  - double-click for a report"
                elif result == "Done":
                    shown = label or ("Done \u2713 - subtitles look incomplete" if item.get("sub_warn") else "Done \u2713")
                else:
                    shown = label or result
                item["shown"] = shown
                item["log"] = self.item_log or []
                self.item_lines = None
                self.item_log = None
                self._bump("done" if result == "Done" else "failed" if result == "Failed" else "other")
                watch = item["opts"].get("watch")
                got = [f for f in item.get("files") or [] if os.path.exists(f)]
                if result == "Done" and got:
                    self.q.put(("call", lambda i=iid, it=item: self._finished_file(i, it)))
                    if watch:
                        self._bump("new", item.get("title") or os.path.basename(got[0]))
                        self.q.put(("call", lambda w=watch, n=len(got): self._watch_got(w, n)))
                if watch and result == "Done" and not got:  # nothing new for this show: tidy the row away
                    self.q.put(("call", lambda i=iid: (self.tree.delete(i) if self.tree.exists(i) else None,
                                                       self.items.pop(i, None), self._summary())))
                    continue
                self.q.put(("item", iid, {"status": shown, "speed": "", "eta": "",
                                          **({"pct": "100%"} if result == "Done" else {})}))

    def _encode_item(self, iid, item, label):
        """Re-encode this item's newly downloaded videos if Settings > Encoding says so."""
        o = item["opts"]
        if not o.get("encode") or o["mode"] != "Video":
            return "Done", label
        files = [f for f in item.get("files") or [] if f.lower().endswith(VIDEO_EXTS) and os.path.isfile(f)
                 and os.path.getmtime(f) >= item.get("started", 0) - 5]  # never re-encode older files
        for n, f in enumerate(files, 1):
            if self.stop_flag:
                return "Stopped", label
            name = os.path.basename(f)
            before = os.path.getsize(f)
            self.log("Re-encoding %s (%s) ..." % (name, encoder_name(o)))
            last = [0.0]

            def on_pct(v, n=n):
                if v - last[0] >= 1 or v >= 99:
                    last[0] = v
                    self.q.put(("item", iid, {"status": "Encoding %d%%" % v + (" (%d/%d)" % (n, len(files)) if len(files) > 1 else ""),
                                              "pct": "%.0f%%" % v, "speed": "", "eta": ""}))
            try:
                used = encode_file(f, o, on_pct, self._set_proc, self.log, stopped=lambda: self.stop_flag)
            except RuntimeError as e:
                if self.stop_flag:
                    return "Stopped", label
                self.log("Couldn't encode %s: %s (the original download was kept)" % (name, e))
                return "Failed", "Re-encoding failed - the original was kept"
            self.log("Re-encoded %s with %s: %.0f MB -> %.0f MB" % (name, used, before / 1048576, os.path.getsize(f) / 1048576))
            item.setdefault("reencoded", []).append(os.path.normcase(os.path.abspath(f)))
        return "Done", label

    def _use_system_certs(self):
        """Called from the download thread when yt-dlp hits a certificate failure."""
        global USE_SYSTEM_CERTS
        USE_SYSTEM_CERTS = True
        self.log(cert_hint("CERTIFICATE_VERIFY_FAILED"))
        self.q.put(("syscerts_on",))

    def _set_proc(self, p):
        self.proc = p
        if self.stop_flag:  # Stop was pressed while this was starting
            kill_tree(p)

    def _queue_after(self, iid, urls):
        """Insert URLs into the queue right after this row (done on the window's thread)."""
        done = threading.Event()
        self.q.put(("expand", iid, urls, done))
        done.wait(10)

    def _run_item(self, exe, iid, item):
        """Download one queue item. Returns (result, error_count[, status label])."""
        o = item["opts"]
        base = item.setdefault("base_rate", o.get("rate", ""))
        rate = scheduled_rate(dict(o, rate=base))
        if rate != o.get("rate", ""):
            o = item["opts"] = dict(o, rate=rate)
        if o.get("sched_on") and rate and rate != base:
            self.log("Daytime speed limit: %s (until %s)." % (rate, o.get("sched_to")))
        os.makedirs(o["outdir"], exist_ok=True)
        site = login_site(item["url"])
        login = self.login or (saved_login(site) if site else None) or \
            (self.site_logins.get(site) if site else None)
        ff = firefox_site(item["url"])
        if ff and o.get("cookies", "None") == "None":
            state = firefox_state(ff)
            if state == "ready":
                self.log("Using your Firefox sign-in for %s." % FIREFOX_SITES[ff])
                o = item["opts"] = dict(o, cookies="firefox")
            elif state == "close_firefox":
                self.log("Your %s sign-in is in Firefox, but Firefox is open so it can't be read yet - close "
                         "Firefox and Retry for the best quality." % FIREFOX_SITES[ff])
            elif ff == "7plus.com.au":
                self.log("Note: 7plus only gives full quality to signed-in accounts (otherwise often around "
                         "504p). Use 'Sign in to a site' > 7plus.")
        if site and not login:
            self.log("Note: %s needs your %s email and password (it can't use browser sign-ins). Press "
                     "'Sign in to a site' and choose %s." % (PASSWORD_SITES[site], PASSWORD_SITES[site], PASSWORD_SITES[site]))
        show = ten_show_slug(item["url"])
        if show:
            self.q.put(("item", iid, {"status": "Finding seasons"}))
            self.log("\n10 show page: %s - looking up its seasons ..." % show)
            seasons = ten_show_seasons(show, self.log)
            if seasons:
                kind = "season" if "/episodes/" in seasons[0] and "/tpv" not in seasons[0] else "episode"
                self._queue_after(iid, seasons)
                self.log("Queued %d %s(s): %s" % (len(seasons), kind, ", ".join(u.rsplit("/", 1)[-1] for u in seasons)))
                return "Done", 0, "Queued %d %ss" % (len(seasons), kind)
            self.log("Couldn't find the seasons for this show. On 10's site, open the show, pick a season "
                     "under Episodes and paste that address (it ends in /episodes/season-...), or paste "
                     "a single episode's address.")
            return "Failed", 1, "Show page - paste a season URL"
        if IVIEW_LIST.search(item["url"]):
            self.log("This is an iView channel/category page with lots of shows. Use 'Pick episodes' to "
                     "choose the shows you want from it.")
            return "Done", 0, "Channel page - use 'Pick episodes'"
        if is_audio_page(item["url"]) and o["mode"] == "Video":
            self.log("Radio / podcast page - saving it as audio (%s)." % o["audio_fmt"])
            o = item["opts"] = dict(o, mode="Audio")
        if o["mode"] == "Audio" and is_audio_page(item["url"]) and not item.get("feed"):
            feed = find_feed(item["url"])
            if feed and feed != item["url"]:
                self.log("Found its podcast feed: %s" % feed)
                item["feed"] = feed
        eng = engine_for(item["url"])
        if eng:
            standard = exe
            exe = eng[1]
            self.log("Using engine %s for %s" % (os.path.basename(exe), eng[0]))
            try:
                return self._run_item_with(exe, iid, item, o, login)
            except OSError as e:  # e.g. WinError 216/193: antivirus-damaged or wrong file
                BAD_ENGINES.add(exe)
                if is_virus_block(e):
                    self.q.put(("call", lambda n=os.path.basename(exe), e=e: self.antivirus_dialog(
                        names=[n], reason=str(e))))
                self.log("The %s engine won't start on this PC (%s) - using the standard yt-dlp instead. "
                         "If your antivirus quarantined it, restore it or set this site to 'standard yt-dlp' "
                         "in Settings > Tools & engines." % (eng[0], str(e).split(".")[0]))
                exe = standard
        return self._run_item_with(exe, iid, item, o, login)

    def _run_item_with(self, exe, iid, item, o, login):
        hint = needs_account_hint(item["url"], o, login)
        if hint:
            self.log(hint)
        rec = recipe_for(item["url"]) if o.get("recipes", True) and o["mode"] == "Video" else None
        if rec and rec[0] in set(filter(None, o.get("recipes_off", "").split(","))):
            self.log("%s recipe is switched off in Settings - downloading the plain way." % rec[1]["name"])
            rec = None
        if rec:
            try:
                return self._run_recipe(exe, iid, item, rec, login)
            except RecipeFallback as e:
                if self.stop_flag:
                    return "Stopped", 0
                self.log("%s recipe skipped (%s) - downloading the normal way." % (rec[1]["name"], e))
        work = None
        run_opts = o
        if o["mode"] == "Audio":
            # Audio jobs run in a private folder. Otherwise, if the same video was also saved as
            # Video, yt-dlp reuses that file for the audio and then deletes it after converting.
            work = os.path.join(o["outdir"], AUDIO_WORK)
            os.makedirs(work, exist_ok=True)
            run_opts = dict(o, outdir=work)
        args = build_args(exe, item.get("feed") or item["url"], run_opts, login)
        self.log("\n> " + subprocess.list2cmdline(mask_args(args[:1] + args[8:])))
        fd, saved_list = tempfile.mkstemp(prefix="tasrip_files_", suffix=".txt")
        os.close(fd)
        args = args[:-2] + ["--print-to-file", "after_move:" + SAVED_FIELDS, saved_list] + args[-2:]
        if o["thumb"] and not thumb_supported(o):
            self.log("Note: %s files can't hold a thumbnail, so that option is skipped for this item."
                     % (o["audio_fmt"] if o["mode"] == "Audio" else o["container"]).upper())
        code, errors = self._run_ytdlp(args, iid, o)
        infos = []
        try:
            with open(saved_list, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        infos.append(json.loads(line))
                    except ValueError:
                        pass
            os.remove(saved_list)
        except OSError:
            pass
        infos = [i for i in infos if isinstance(i, dict) and i.get("filepath")]
        if work:
            move_finished(work, o["outdir"], self.log)
            for i in infos:
                if i["filepath"].startswith(work):
                    i["filepath"] = os.path.join(o["outdir"], os.path.relpath(i["filepath"], work))
        if o["template"] == PLEX:
            for i in infos:
                i["filepath"] = plex_move(i["filepath"], i, o["outdir"], o["mode"] == "Audio", self.log)
        item["files"] = [i["filepath"] for i in infos]
        item["infos"] = infos
        if code != 0 and not self.stop_flag and not item.get("feed") and \
                any("unsupported url" in l.lower() for l in self.item_lines or []):
            feed = find_feed(item["url"])
            if feed and feed != item["url"]:
                self.log("That page has a podcast/RSS feed - downloading from it as audio instead: %s" % feed)
                item["feed"] = feed
                item["opts"] = dict(o, mode="Audio")
                return self._run_item(exe, iid, item)
        if self.stop_flag:
            return "Stopped", errors
        return ("Done" if code == 0 else "Failed"), errors

    def _run_ytdlp(self, args, iid, o, title=None):
        """Run a yt-dlp download, feeding progress and stages to the queue row. -> (code, errors)"""
        errors, tail, told, phase, parts, last_post = 0, "", None, None, 0, 0.0
        try:
            self._set_proc(popen(args))
        except OSError as e:
            self.log("Could not run yt-dlp: %s" % e)
            return 1, 1
        for raw in self.proc.stdout:
            line = raw.rstrip()
            prog = parse_progress(line)
            if prog:
                pct, pct_txt, speed, eta, res, ptitle = prog
                if res and o["mode"] == "Video" and told != res:
                    told = res
                    want = o["quality"]
                    height = res.split("x")[-1]
                    short = want != "Best" and height.isdigit() and int(height) < int(want.rstrip("p"))
                    self.log("Getting %s%s" % (res, (
                        " - the best this site offers for this title without DRM (you asked for up to %s)"
                        % want) if short else ""))
                now = time.monotonic()
                fresh = phase != "Downloading"
                if fresh or pct >= 100 or now - last_post >= 0.25:  # don't flood the window
                    last_post = now
                    phase = "Downloading"
                    label = "Downloading" + (" part %d" % parts if parts > 1 else "")
                    if ptitle and iid in self.items:
                        self.items[iid]["title"] = title or ptitle
                    self.q.put(("item", iid, {"title": tail + (title or ptitle), "pct": pct_txt,
                                              "speed": speed, "eta": eta, "status": label}))
                continue
            new = phase_of(line)
            if new == "Downloading":
                parts += 1  # video and audio arrive as separate parts, each starting at 0%
            elif new and new != phase:
                phase = new
                upd = {"status": new}
                if new != "Retrying":
                    upd.update(speed="", eta="")
                self.q.put(("item", iid, upd))
            if line.startswith("[download] Downloading item "):
                tail = "[%s] " % line[28:].replace(" of ", "/")
                parts = 0
            if line.startswith("ERROR:"):
                errors += 1
            if line:
                self.log(line)
        return self.proc.wait(), errors

    def _clear_work_video(self, work):
        for f in os.listdir(work):  # leftovers of a failed attempt
            if f.startswith("video"):
                path = os.path.join(work, f)
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)

    def _run_recipe(self, exe, iid, item, rec, login):
        """Tas Rip Pack-style download for an Australian service. Raises RecipeFallback if the
        page can't be read the recipe way (the caller then downloads normally)."""
        site, r = rec
        o, url = item["opts"], item["url"]
        auth = auth_args(o, login)
        have_ff = os.path.isfile(os.path.join(BIN_DIR, "ffmpeg" + EXE))

        def status(text, **kw):
            self.q.put(("item", iid, dict(status=text, **kw)))
        status("Reading page", speed="", eta="")
        self.log("\n%s recipe: %s" % (r["name"], url))

        # 1. Read the page. Each video found prints its save path, then its details as JSON;
        # a season/series page prints one pair per episode (works on old and new yt-dlp alike).
        extra = split_extra(o.get("extra", ""))
        code, out = run_capture(
            [exe, "--ignore-config", "--no-colors", "--encoding", "utf-8", "--simulate", "--flat-playlist"] + tls_args() + plugin_args(exe) + [
             "--yes-playlist" if o["playlist"] else "--no-playlist", "--windows-filenames",
             "-P", o["outdir"], "-o", TEMPLATES.get(o["template"], TEMPLATES["Title"]),
             "--print", "filename", "--print", "%()j"] + auth + extra + ["--", url],
            self.log, self._set_proc)
        if self.stop_flag:
            return "Stopped", 0
        found, fname = [], None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    found.append((fname, json.loads(line)))
                except ValueError:
                    pass
                fname = None
            elif line:
                fname = line
        if code != 0 or not found:
            raise RecipeFallback("couldn't read the page")
        fname, info = found[0]
        if len(found) > 1 or info.get("_type") in ("url", "url_transparent", "playlist"):
            urls = []
            for _, e in found:  # a series page lists links to each episode's own page
                u = e.get("url") if e.get("_type") in ("url", "url_transparent") else e.get("webpage_url")
                if (u or "").startswith(("http://", "https://")) and u != url and u not in urls:
                    if o.get("match") and e.get("title") and o["match"].lower() not in str(e["title"]).lower():
                        continue
                    urls.append(u)
            if not urls and o.get("match"):
                self.log("Nothing on %s matches '%s'." % (url, o["match"]))
                return "Done", 0, "Nothing matches the filter"
            if not urls:  # several videos on this one page, not links to episode pages
                raise RecipeFallback("this page holds several videos rather than episode links")
            self._queue_after(iid, urls)
            self.log("Queued %d episode(s) from %s" % (len(urls), info.get("playlist_title") or url))
            return "Done", 0, "Queued %d episodes" % len(urls)

        # 2. Where it will be saved, and whether it's already there.
        if not fname:
            raise RecipeFallback("no file name")
        # Always MKV, like the pack: only MKV keeps the forced-subtitle flags and track titles.
        final = os.path.splitext(fname)[0] + ".mkv"
        if o["template"] == PLEX:
            final = os.path.join(o["outdir"], plex_rel(info, ".mkv"))
        title = info.get("title") or os.path.basename(final)
        item["title"] = title
        if o.get("match") and o["match"].lower() not in title.lower():
            self.log("Skipped (title doesn't contain '%s'): %s" % (o["match"], title))
            return "Done", 0, "Skipped (filter)"
        self.q.put(("item", iid, {"title": title}))
        exp = expiry_of(info)
        if exp:
            item["expires"] = exp
            self.log("Available until %s." % time.strftime("%a %d %b %Y", time.localtime(exp)))
        aid = archive_id(info)
        if o["archive"] and aid and in_archive(aid):
            self.log("Already downloaded (in archive.txt): " + title)
            return "Done", 0, "Skipped (archive)"
        if os.path.exists(final):
            self.log("Already downloaded: " + final)
            return "Done", 0
        os.makedirs(os.path.dirname(final), exist_ok=True)
        if o["thumb"]:
            self.log("Note: thumbnails aren't added to %s downloads." % r["name"])
        work = os.path.join(o["outdir"], WORK_DIR, safe_name(info.get("id") or title))
        os.makedirs(work, exist_ok=True)

        # 3. Video: N_m3u8DL-RE (7plus), the higher-quality stream, the standard stream, the page.
        manifest = manifest_of(info)
        heights = stream_heights(info)
        if heights:
            self.log("Picture sizes on offer: %s" % ", ".join("%dp" % h for h in heights))
        cap = None if o["quality"] == "Best" else int(o["quality"].rstrip("p"))
        swaps = swap_candidates(info, r) if cap is None or cap >= 1080 else []
        seven = seven_stream_state(info) if host_matches(url_host(url), "7plus.com.au") else None
        best = min(heights[0], cap) if heights and cap else (heights[0] if heights else 0)
        self.log("About to download: %s | %s | %s%s%s" % (
            title, r["name"], "%dp" % best if best else "quality not listed",
            " (trying 1080p first)" if swaps else "",
            {"signed": " | 7plus signed-in stream \u2713", "anonymous": " | 7plus ANONYMOUS stream (max 504p)"}.get(seven, "")))
        low = self._below_minimum(item, title, r["name"], best, swaps, seven)
        if low:
            return "Failed", 0, low
        tries = []
        if cap is None or cap >= 1080:
            tries += [("higher-quality stream", u) for u in swap_candidates(info, r)]
        if tries:
            tries.append(("standard stream", manifest))
        tries.append(("page", url))
        nm = tool_path("N_m3u8DL-RE") if r.get("nm3u8") else None
        if r.get("nm3u8") and not nm:
            self.log("Tip: %s downloads best with N_m3u8DL-RE (Tools > Get N_m3u8DL-RE). "
                     "Using yt-dlp for now." % r["name"])
        if nm and manifest:
            tries.insert(0, ("N_m3u8DL-RE", manifest))
        media = []
        fallback_small = None
        for label, src in tries:
            if self.stop_flag:
                return "Stopped", 0
            self._clear_work_video(work)
            self.log("Downloading with N_m3u8DL-RE ..." if label == "N_m3u8DL-RE" else "Downloading the %s ..." % label)
            status("Downloading", pct="")
            if label == "N_m3u8DL-RE":
                args = [nm, src, "--save-dir", work, "--save-name", "video", "--tmp-dir",
                        os.path.join(work, "tmp"), "--auto-select", "--no-log", "--no-ansi-color",
                        "--disable-update-check", "--thread-count", o.get("frags") or "4",
                        "--sub-format", "SRT"] + (["--max-speed", o["rate"].strip()] if o.get("rate", "").strip() else [])
                ff = tool_path("ffmpeg")
                if ff:
                    args += ["--ffmpeg-binary-path", ff]
                last = [0.0]

                def on_pct(v):
                    if abs(v - last[0]) >= 0.5 or v >= 100:
                        last[0] = v
                        status("Downloading", pct="%.1f%%" % v)
                code = run_nm3u8(args, self.log, on_pct, self._set_proc)
            else:
                args = [exe, "--newline", "--no-colors", "--encoding", "utf-8", "--ignore-config",
                        "--progress-template", PROG_TEMPLATE, "--windows-filenames",
                        "-N", o.get("frags") if o.get("frags") in FRAGMENTS else "4",
                        "-f", "bv*+ba/b", "--merge-output-format", "mkv", "--no-playlist",
                        "-P", work, "-o", "video.%(ext)s"]
                sort = (["res:%d" % cap] if cap else []) + (["vcodec:h264", "acodec:aac"] if o["compat"] else [])
                if sort:
                    args += ["-S", ",".join(sort)]
                if have_ff:
                    args += ["--ffmpeg-location", BIN_DIR]
                if o.get("rate", "").strip():
                    args += ["-r", o["rate"].strip()]
                args += aria2_args(o) + tls_args() + plugin_args(exe) + extra + (auth if label == "page" else [])
                args += ["--", src]
                code, _ = self._run_ytdlp(args, iid, o, title)
            if self.stop_flag:
                return "Stopped", 0
            media = sorted((os.path.join(work, f) for f in os.listdir(work)
                            if f.startswith("video") and f.lower().endswith(MEDIA_EXTS)
                            and not f.endswith(".small")),
                           key=os.path.getsize, reverse=True)
            if code == 0 and media:
                got = video_height(media[0])
                want = min(heights[0], cap) if heights and cap else (heights[0] if heights else 0)
                if got:
                    self.log("Got %dp%s" % (got, " (best on offer: %dp)" % heights[0] if heights and heights[0] != got else ""))
                if label == "N_m3u8DL-RE" and got and want and got < want and len(tries) > 1:
                    self.log("N_m3u8DL-RE picked a smaller picture than the site offers - trying yt-dlp instead.")
                    keep = media[0] + ".small"
                    os.replace(media[0], keep)
                    media = []
                    fallback_small = keep
                    continue
                break
            self.log("%s didn't work%s" % ("N_m3u8DL-RE" if label == "N_m3u8DL-RE" else "The " + label,
                                            " - trying the next option." if src != url else "."))
            media = []
        if not media and fallback_small and os.path.exists(fallback_small):
            self.log("The other options didn't work - keeping N_m3u8DL-RE's download.")
            os.replace(fallback_small, fallback_small[:-6])
            media = [fallback_small[:-6]]
        if not media:
            return "Failed", 1

        # 4. Subtitles, fetched separately like the pack did.
        if o["subs"]:
            status("Getting subs", pct="")
            # From the episode page first; 7plus (and sometimes others) keep captions inside the
            # stream instead, which is where the Tas Rip Pack fetched them from.
            for src in [url] + ([manifest] if manifest else []):
                run_capture([exe, "--ignore-config", "--no-colors", "--encoding", "utf-8", "--skip-download"]
                            + tls_args() + plugin_args(exe) + [
                             "--write-subs", "--sub-langs", "all,-live_chat", "--convert-subs", "srt",
                             "--no-playlist", "-P", work, "-o", "subs.%(ext)s"]
                            + (["--ffmpeg-location", BIN_DIR] if have_ff else [])
                            + (auth if src == url else []) + extra + ["--", src],
                            self.log, self._set_proc)
                if self.stop_flag:
                    return "Stopped", 0
                if any(f.startswith("subs.") and f.lower().endswith(".srt") for f in os.listdir(work)):
                    break
                if src == url and manifest:
                    self.log("No subtitles on the page - checking the stream itself ...")
        if o["subs"]:
            convert_subs_subtitleedit(work, self.log)
        subs = pick_subs([os.path.join(work, f) for f in os.listdir(work)]) if o["subs"] else []
        if subs and media:
            dur = media_duration(media[0])
            for sf in subs:
                if sub_info(sf)[1] or not sf.lower().endswith(".srt"):
                    continue  # forced subtitles are meant to be sparse
                for problem in subtitle_problems(sf, dur):
                    item.setdefault("sub_warn", []).append(problem)
                    self.log("Subtitle check (%s): %s - the site's file may be incomplete." % (os.path.basename(sf), problem))

        # 5. Mux into the final file.
        ffmpeg = tool_path("ffmpeg")
        if not ffmpeg:
            self.log("ffmpeg is needed to finish %s downloads (Tools > Get ffmpeg)." % r["name"])
            return "Failed", 1
        status("Muxing", pct="")
        meta = {}
        if o["meta"]:
            meta = {"title": info.get("episode") or info.get("title"), "show": info.get("series"),
                    "season_number": info.get("season_number"), "episode_sort": info.get("episode_number"),
                    "description": (info.get("description") or "")[:500]}
        os.makedirs(os.path.dirname(final) or ".", exist_ok=True)
        tracks = media[:1] + [m for m in media[1:] if m.lower().endswith(AUDIO_EXTS)]
        mkvmerge = tool_path("mkvmerge") if o.get("use_mkvmerge", True) and final.lower().endswith(".mkv") else None
        try:
            if mkvmerge:
                try:
                    mux_mkvmerge(mkvmerge, tracks, subs, final, meta)
                    self.log("Muxed with mkvmerge")
                except RuntimeError as e:
                    self.log("%s - using ffmpeg instead" % e)
                    mux_pack(ffmpeg, tracks, subs, final, meta)
            else:
                mux_pack(ffmpeg, tracks, subs, final, meta)
        except RuntimeError as e:
            self.log(str(e))
            return "Failed", 1
        self.log("Saved %s%s" % (final, " with %d subtitle track(s)" % len(subs) if subs else ""))
        item["files"] = [final]
        item["infos"] = [dict(info, filepath=final, webpage_url=info.get("webpage_url") or url)]
        if o.get("thumbfile") and info.get("thumbnail"):
            if save_thumbnail(info["thumbnail"], os.path.splitext(final)[0] + ".jpg", self.log):
                self.log("Saved the thumbnail")
        shutil.rmtree(work, ignore_errors=True)
        try:
            os.rmdir(os.path.join(o["outdir"], WORK_DIR))
        except OSError:
            pass
        if o["archive"] and aid:
            try:
                with open(ARCHIVE_FILE, "a", encoding="utf-8") as f:
                    f.write(aid + "\n")
            except OSError:
                pass
        return "Done", 0

    # ---- UI thread message pump
    def _pump(self):
        logs, rows = [], {}
        try:
            for _ in range(2000):  # bounded, so a burst of output can't freeze the window
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    logs.append(msg[1])
                elif kind == "item":
                    rows.setdefault(msg[1], {}).update(msg[2])  # keep only the latest values
                else:
                    self._flush(logs, rows)
                    logs, rows = [], {}
                    if kind == "finished":
                        self.running = False
                        self.b_start.configure(state="normal")
                        self.b_stop.configure(state="disabled")
                        self._summary()
                        self.log("\nQueue stopped." if self.stop_flag else "\nAll done.")
                        st = self.run_stats
                        if not self.stop_flag and self.v["notify"].get() and (st["done"] or st["failed"]):
                            if st["new"]:
                                text = "New episodes: " + ", ".join(st["new"][:4]) + (" ..." if len(st["new"]) > 4 else "")
                            else:
                                text = "%d done%s" % (st["done"], ", %d failed" % st["failed"] if st["failed"] else "")
                            notify(APP_NAME + " - queue finished", text, self.v["sound"].get())
                    elif kind in ("plugin_results", "plugin_installed"):
                        self._plugin_msg(msg)
                    elif kind == "call":
                        try:
                            msg[1]()
                        except Exception as e:  # a window closed meanwhile, etc.
                            self.q.put(("log", "(display update skipped: %s)" % e))
                    elif kind == "syscerts_on":
                        self.v["syscerts"].set(True)
                        self.save_settings()
                    elif kind == "expand":
                        try:
                            self._expand(msg[1], msg[2])
                        finally:
                            msg[3].set()
                    elif kind == "tools_done":
                        self.busy_tools = False
                        if msg[1] and not tool_path("ffmpeg"):
                            self._offer_ffmpeg()
                        elif msg[1] or (len(msg) > 2 and msg[2]):
                            still = [label for label, name, _ in self.SETUP_TOOLS if not tool_path(name)]
                            if not getattr(self, "watch_timer", False):
                                self.watch_timer = True
                                self.root.after(20000, self._auto_watch)
                            if not still:
                                self.log("Setup finished - ready to download.")
                            elif tool_path("yt-dlp") and tool_path("ffmpeg"):
                                self.log("Ready to download. Still missing (optional): %s - it'll try again "
                                         "next start, or use the Tools menu." % ", ".join(still))
                            else:
                                self.log("Setup didn't finish (missing: %s). Check the internet connection "
                                         "and restart TasRip, or use the Tools menu." % ", ".join(still))
                            self._summary()
        except queue.Empty:
            pass
        self._flush(logs, rows)
        self.root.after(100, self._pump)

    def _expand(self, parent, urls):
        """Put a season's episodes into the queue right after the season row."""
        p = self.items.get(parent)
        if not p:
            return
        idx = self.tree.index(parent) if self.tree.exists(parent) else None
        mode = self.tree.set(parent, "mode") if self.tree.exists(parent) else ""
        known = {i["url"] for i in self.items.values() if i["status"] in ("Queued", "Working")}
        new = []
        for n, url in enumerate(u for u in urls if u not in known):
            iid = self.tree.insert("", "end" if idx is None else idx + 1 + n,
                                   values=(url, mode, "Queued", "", "", ""))
            new.append((iid, {"url": url, "opts": dict(p["opts"], items=""), "status": "Queued"}))
        order = list(self.items.items())  # keep the worker's order the same as the screen's
        at = next(i for i, (k, _) in enumerate(order) if k == parent) + 1
        self.items = dict(order[:at] + new + order[at:])
        self._summary()

    def _write_log_file(self, logs):
        """Keep a copy of the log on disk (logs/tasrip-YYYY-MM-DD.txt) for problem reports."""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            path = os.path.join(LOG_DIR, "tasrip-%s.txt" % time.strftime("%Y-%m-%d"))
            with open(path, "a", encoding="utf-8") as f:
                if not getattr(self, "log_started", False):
                    self.log_started = True
                    f.write("\n===== %s v%s started %s =====\n" % (APP_NAME, VERSION, time.strftime("%H:%M:%S")))
                f.write("\n".join(scrub(l) for l in logs) + "\n")
        except OSError:
            pass

    def _flush(self, logs, rows):
        if logs:
            self._write_log_file(logs)
            self.log_box.configure(state="normal")
            self.log_box.insert("end", "\n".join(logs) + "\n")
            lines = int(self.log_box.index("end-1c").split(".")[0])
            if lines > 3000:
                self.log_box.delete("1.0", "%d.0" % (lines - 2500))
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        if rows:
            for iid, cols in rows.items():
                if self.tree.exists(iid):
                    for col, val in cols.items():
                        self.tree.set(iid, col, val)
            self._summary()

    def on_close(self):
        if (self.running or self.live_procs) and not messagebox.askyesno(
                APP_NAME, "Downloads or recordings are running. Quit anyway?"):
            return
        self.stop_flag = True
        self._kill_all()
        for p in list(self.live_procs.values()):
            kill_tree(p)
        self.save_settings()
        try:
            save_queue(list(self.items.values()))
        except Exception:
            pass
        self.root.destroy()


def main():
    if "--build" in sys.argv:  # used by build_exe.bat: build without opening the window
        try:
            build_exe(print)
        except Exception as e:
            print("\nBUILD FAILED: %s" % e)
            sys.exit(1)
        return
    if "--update" in sys.argv:  # used by the Update bat
        clean_mac_junk(BASE_DIR, print)
        headless_update(full="--full" in sys.argv)
        return
    if tk is None:
        sys.exit("Tkinter is missing, so the window can't open. " + TK_HINT)
    os.makedirs(BIN_DIR, exist_ok=True)
    try:
        migrate_old_install(lambda t: None)
    except Exception:
        pass
    try:
        clean_mac_junk(BASE_DIR)
    except Exception:
        pass
    if IS_WIN:
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
