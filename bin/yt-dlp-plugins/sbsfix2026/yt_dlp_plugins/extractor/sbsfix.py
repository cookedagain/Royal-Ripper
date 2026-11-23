# SBS extractor override for yt-dlp.
# Adapted from user-tested experimental SBS extractor (17 Nov 2026).
# Uses the yt-dlp plugin_name API to replace the built-in SBS extractor.
# No credentials or downloaded video are bundled.
#
# MediaRipper changes (marked 'MediaRipper:' below):
#   - can sign in from a browser session: if --cookies-from-browser / --cookies supplies the
#     SBS 'auth.refresh-token' cookie, it is exchanged for an access token, so no password
#     is needed. The resulting session is cached exactly like a normal login.
#   - a failed token refresh no longer tries to log in with empty credentials.

import json
import time

from yt_dlp.extractor.sbs import SBSIE as _BuiltInSBSIE
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import (
    ExtractorError,
    determine_ext,
    float_or_none,
    int_or_none,
    join_nonempty,
    jwt_decode_hs256,
    parse_duration,
    parse_iso8601,
    parse_qs,
    str_or_none,
    traverse_obj,
    url_or_none,
)


class _SBSPatchedIE(_BuiltInSBSIE, plugin_name='sbsfix2026'):
    _NETRC_MACHINE = 'sbs'
    _ACCESS_TOKEN = None
    _REFRESH_TOKEN = None
    _LOGIN_API_BASE = 'https://auth.sbs.com.au'
    _LOGIN_API_KEY = '74165a3a-0ae7-4d27-ac1a-e71e81062a89'
    _CACHE_KEY = 'sbs_data'
    _REFRESH_COOKIE = 'auth.refresh-token'
    _COOKIE_TRIED = None  # MediaRipper: refresh token already attempted this run
    _BROWSER_TOKEN = False  # MediaRipper: refresh token found in the loaded cookies (False = not looked yet)
    _BROWSER_ACCESS = None  # MediaRipper: unexpired access token found in the loaded cookies
    _COOKIE_NOTE = None  # MediaRipper: what the cookie scan saw, for the log
    # MediaRipper: SBS has used both keys; the second is tried if the first is refused
    _LOGIN_API_KEYS = ('74165a3a-0ae7-4d27-ac1a-e71e81062a89', '49a46461-b9eb-4904-b519-176c59c386ef')

    def _is_jwt_expired(self, token):
        return jwt_decode_hs256(token)['exp'] - time.time() < 300

    def _parse_and_cache_login_response(self, data=None):
        if not data:
            self._ACCESS_TOKEN, self._REFRESH_TOKEN = self.cache.load(self._NETRC_MACHINE, self._CACHE_KEY, default=[None, None])
            if self._ACCESS_TOKEN and not self._is_jwt_expired(self._ACCESS_TOKEN):
                return self._ACCESS_TOKEN
            elif self._REFRESH_TOKEN:
                return self._refresh_access_token()
            return False

        self._ACCESS_TOKEN = data.get('accessToken')
        if not self._ACCESS_TOKEN:
            raise ExtractorError('Login failed unable to get access token')
        if refresh_cookie := self._get_cookies('https://sbs.com.au').get(self._REFRESH_COOKIE):
            self._REFRESH_TOKEN = refresh_cookie.value
        self.cache.store(self._NETRC_MACHINE, self._CACHE_KEY, [self._ACCESS_TOKEN, self._REFRESH_TOKEN])
        return self._ACCESS_TOKEN, self._REFRESH_TOKEN

    def _refresh_access_token(self):
        if not self._REFRESH_TOKEN:
            self.report_warning('Unable to get refresh token')
            return self._relogin()
        self._set_cookie('.sbs.com.au', self._REFRESH_COOKIE, self._REFRESH_TOKEN)
        try:
            data = self._download_json(
                f'{self._LOGIN_API_BASE}/refresh',
                None, 'Refreshing access token',
                data=json.dumps({
                    'deviceName': 'Chrome - Linux',
                }).encode(),
                headers={
                    'x-api-key': self._LOGIN_API_KEY,
                },
            )
        except ExtractorError as e:
            if isinstance(e.cause, HTTPError) and e.cause.status in (400, 401, 403):
                self.report_warning('Unable to get refresh token')
                return self._relogin()
            raise
        return self._parse_and_cache_login_response(data)

    # MediaRipper: --------------------------------------------------------------
    def _scan_cookies(self):
        """Look through every loaded cookie for an SBS session (any path, any SBS sub-domain)."""
        if self._BROWSER_TOKEN is not False:
            return
        self._BROWSER_TOKEN = None
        seen, exact, loose, total = [], None, None, 0
        for c in self._downloader.cookiejar:
            total += 1
            domain = (c.domain or '').lstrip('.').lower()
            if not (domain == 'sbs.com.au' or domain.endswith('.sbs.com.au')) or not c.value:
                continue
            seen.append(f'{c.name} ({domain}{c.path or "/"})')
            name = c.name.lower()
            if c.name == self._REFRESH_COOKIE:
                exact = exact or c.value
            elif 'refresh' in name:
                loose = loose or c.value
            elif 'access' in name and c.value.startswith('eyJ') and c.value.count('.') == 2:
                try:
                    if not self._is_jwt_expired(c.value):
                        self._BROWSER_ACCESS = c.value
                except Exception:
                    pass
        self._BROWSER_TOKEN = exact or loose
        if not total:
            self._COOKIE_NOTE = None  # no cookies were supplied at all
        elif not seen:
            self._COOKIE_NOTE = ('no SBS cookies were found in that browser profile. Check you are signed in to '
                                 'SBS On Demand in that browser (a normal window, not private) and in its default profile')
        elif not (self._BROWSER_TOKEN or self._BROWSER_ACCESS):
            self._COOKIE_NOTE = ('SBS cookies were found but none looks like a login session. Cookie names seen '
                                 '(names only, no values): ' + ', '.join(sorted(set(seen))[:40]))

    def _browser_refresh_token(self):
        """The SBS refresh token from the loaded cookies (e.g. --cookies-from-browser), if any."""
        self._scan_cookies()
        return self._BROWSER_TOKEN

    def _login_from_browser_cookie(self):
        """Sign in from the browser's cookies. True on success."""
        self._scan_cookies()
        # 1) an access token the browser already holds: used as-is, the browser session is untouched
        access = self._BROWSER_ACCESS
        if access and access != self._COOKIE_TRIED:
            self._BROWSER_ACCESS = None
            self._ACCESS_TOKEN = access
            self.to_screen('Using the SBS sign-in from the browser')
            return True
        # 2) the refresh token: exchanged for a new access token
        token = self._BROWSER_TOKEN
        if not token or token == self._COOKIE_TRIED:
            if self._COOKIE_NOTE:
                self.report_warning('Browser cookies: ' + self._COOKIE_NOTE, only_once=True)
            return False
        self._COOKIE_TRIED = token
        self._REFRESH_TOKEN = token
        self._set_cookie('.sbs.com.au', self._REFRESH_COOKIE, token)
        data, status = None, None
        for key in self._LOGIN_API_KEYS:
            try:
                data = self._download_json(
                    f'{self._LOGIN_API_BASE}/refresh',
                    None, 'Signing in with the browser session',
                    data=json.dumps({
                        'deviceName': 'Chrome - Linux',
                    }).encode(),
                    headers={
                        'x-api-key': key,
                    },
                )
                break
            except ExtractorError as e:
                status = getattr(e.cause, 'status', None)
                if status not in (401, 403):
                    break
        if not data:
            self.report_warning(
                'SBS did not accept the browser session'
                + (f' (HTTP {status})' if status else '')
                + '. Sign in to SBS On Demand in that browser again, or log in with a username and password')
            self._ACCESS_TOKEN = self._REFRESH_TOKEN = None
            return False
        self._parse_and_cache_login_response(data)
        self.to_screen('Signed in to SBS using the browser session')
        return True

    def _relogin(self):
        """The saved session is no longer valid: use credentials if given, else the browser session."""
        username, password = self._get_login_info()
        if username and password:
            return self._perform_login(username, password, force=True)
        self._COOKIE_TRIED = self._REFRESH_TOKEN  # that one just failed; don't resend it
        self._ACCESS_TOKEN = self._REFRESH_TOKEN = None
        return self._login_from_browser_cookie()
    # -----------------------------------------------------------------------------

    def _perform_login(self, username, password, force=False):
        self._browser_refresh_token()  # MediaRipper: note it before anything replaces it
        if not force:
            if self._parse_and_cache_login_response():
                return
        try:
            login = self._download_json(
                f'{self._LOGIN_API_BASE}/login',
                None, 'Logged in',
                data=json.dumps({
                    'deviceName': 'Chrome - Linux',
                    'email': username,
                    'password': password,
                }).encode(),
                headers={
                    'x-api-key': self._LOGIN_API_KEY,
                },
            )
        except ExtractorError as e:
            if isinstance(e.cause, HTTPError) and e.cause.status == 401:
                raise ExtractorError('Invalid username/password')
            raise
        return self._parse_and_cache_login_response(login)

    def _call_api(self, api, video_id, note='Downloading JSON metadata', headers=None, **kwargs):
        headers = headers or {}
        if kwargs.get('data') is not None:
            headers.setdefault('Content-Type', 'application/json')
        if access_token := self._ACCESS_TOKEN:
            headers['Authorization'] = f'Bearer {access_token}'
        return self._download_json(url_or_request=api, video_id=video_id, note=note, headers=headers, **kwargs)

    IE_DESC = 'sbs.com.au'
    _VALID_URL = r'''(?x)
        https?://(?:www\.)?sbs\.com\.au/(?:
            ondemand(?:
                /video/(?:single/)?|
                /(?:movie|tv-program)/[^/]+/|
                /(?:tv|news)-series/(?:[^/]+/){3}|
                .*?\bplay=|/watch/
            )|news/(?:embeds/)?video/
        )(?P<id>[0-9]+)'''
    _EMBED_REGEX = [r'''(?x)]
            (?:
                <meta\s+property="og:video"\s+content=|
                <iframe[^>]+?src=
            )
            ("|')(?P<url>https?://(?:www\.)?sbs\.com\.au/ondemand/video/.+?)\1''']

    _TESTS = [{
        # Original URL is handled by the generic IE which finds the iframe:
        # http://www.sbs.com.au/thefeed/blog/2014/08/21/dingo-conservation
        'url': 'http://www.sbs.com.au/ondemand/video/single/320403011771/?source=drupal&vertical=thefeed',
        'md5': '31f84a7a19b53635db63c73f8ab0c4a7',
        'info_dict': {
            'id': '320403011771',  # '_rFBPRPO4pMR',
            'ext': 'mp4',
            'title': 'Dingo Conservation (The Feed)',
            'description': 'md5:f250a9856fca50d22dec0b5b8015f8a5',
            'thumbnail': r're:https?://.*\.jpg',
            'duration': 308,
            'timestamp': 1408613220,
            'upload_date': '20140821',
            'uploader': 'SBSC',
        },
        'skip': '400 Bad Bad Request',
        'expected_warnings': ['Unable to download JSON metadata'],
    }, {
        'url': 'https://www.sbs.com.au/ondemand/tv-series/hudson-and-rex/season-8/hudson-and-rex-s8-ep1/2487482947557',
        'info_dict': {
            'id': '2487482947557',
            'ext': 'mp4',
            'title': 'Hudson & Rex S8 Ep1 - Into The Wilds',
            'description': 'md5:f70e534ea13ecd54bb944b0a5d032b37',
            'uploader': 'SBSC',
            'age_limit': 14,
            'duration': 2507.0,
            'tags': ['Mature Themes', 'Violence'],
            'series': 'Hudson & Rex',
            'series_id': '06f4f06e-9e32-578a-823f-edc27cec5f2b',
            'season': 'Season 8',
            'season_number': 8,
            'episode': 'Episode 1',
            'episode_number': 1,
            'release_year': 2025,
            'thumbnail': r're:https?://image.pr.sbsod.com/.+',
        },
        'params': {'skip_download': 'm3u8'},
        'skip': 'Login required',
    }, {
        'url': 'http://www.sbs.com.au/ondemand/video/320403011771/Dingo-Conservation-The-Feed',
        'only_matching': True,
    }, {
        'url': 'http://www.sbs.com.au/news/video/471395907773/The-Feed-July-9',
        'only_matching': True,
    }, {
        'url': 'https://www.sbs.com.au/ondemand/?play=1836638787723',
        'only_matching': True,
    }, {
        'url': 'https://www.sbs.com.au/ondemand/program/inside-windsor-castle?play=1283505731842',
        'only_matching': True,
    }, {
        'url': 'https://www.sbs.com.au/news/embeds/video/1840778819866',
        'only_matching': True,
    }, {
        'url': 'https://www.sbs.com.au/ondemand/watch/1698704451971',
        'only_matching': True,
    }, {
        'url': 'https://www.sbs.com.au/ondemand/movie/coherence/1469404227931',
        'only_matching': True,
    }, {
        'note': 'Live stream',
        'url': 'https://www.sbs.com.au/ondemand/video/1726824003663/sbs-24x7-live-stream-nsw',
        'only_matching': True,
    }, {
        'url': 'https://www.sbs.com.au/ondemand/news-series/dateline/dateline-2022/dateline-s2022-ep26/2072245827515',
        'only_matching': True,
    }, {
        'url': 'https://www.sbs.com.au/ondemand/tv-series/the-handmaids-tale/season-5/the-handmaids-tale-s5-ep1/2065631811776',
        'only_matching': True,
    }, {
        'url': 'https://www.sbs.com.au/ondemand/tv-program/autun-romes-forgotten-sister/2116212803602',
        'only_matching': True,
    }]

    _GEO_COUNTRIES = ['AU']
    _GEO_BYPASS = False
    _AUS_TV_PARENTAL_GUIDELINES = {
        'P': 0,
        'C': 7,
        'G': 0,
        'PG': 0,
        'M': 14,
        'MA15+': 15,
        'MAV15+': 15,
        'R18+': 18,
    }

    def _real_extract(self, url):
        video_id = self._match_id(url)
        # yt-dlp only calls _perform_login if --username/--netrc is provided.
        # Restore the existing SBS access/refresh token from yt-dlp's cache
        # for normal URL-only invocations, without storing any passwords.
        self._browser_refresh_token()  # MediaRipper: note it before anything replaces it
        if not self._ACCESS_TOKEN:
            self._parse_and_cache_login_response()
        if not self._ACCESS_TOKEN:  # MediaRipper: no saved session - try the browser's
            self._login_from_browser_cookie()
        try:
            media = self._call_api(
                f'https://playback.pr.sbsod.com/stream/{video_id}',
                video_id,
                data=json.dumps({
                    'deviceClass': 'web',
                    'drm': {'level': 'L3', 'type': 'widevine'},
                    'streamOptions': {'audio': 'demuxed'},
                    'streamProviders': ['GoogleDAI_DRM_HLS', 'GoogleDAI_AES_HLS', 'AES_HLS'],
                }).encode(),
            )
        except ExtractorError as e:
            if isinstance(e.cause, HTTPError) and e.cause.status in (401, 400):
                if not self._ACCESS_TOKEN:
                    self.raise_login_required()
                self._refresh_access_token()
                try:
                    media = self._call_api(
                        f'https://playback.pr.sbsod.com/stream/{video_id}',
                        video_id,
                        note='Retrying playback after token refresh',
                        data=json.dumps({
                            'deviceClass': 'web',
                            'drm': {'level': 'L3', 'type': 'widevine'},
                            'streamOptions': {'audio': 'demuxed'},
                            'streamProviders': ['GoogleDAI_DRM_HLS', 'GoogleDAI_AES_HLS', 'AES_HLS'],
                        }).encode(),
                    )
                except ExtractorError as retry_error:
                    if isinstance(retry_error.cause, HTTPError) and retry_error.cause.status in (401, 400):
                        # Diagnostic only. Never dump URLs, credentials, headers, or raw responses.
                        safe_parts = []
                        try:
                            raw_body = retry_error.cause.response.read(4096)
                            response_body = json.loads(raw_body)
                            if isinstance(response_body, dict):
                                for field in ('error', 'errorCode', 'code', 'message', 'detail', 'reason', 'status'):
                                    val = response_body.get(field)
                                    if not isinstance(val, (str, int, float, bool)):
                                        continue
                                    value = str(val).strip()
                                    # Only allow short, plain diagnostic messages through.
                                    if (value and len(value) <= 140
                                            and not any(word in value.lower() for word in
                                                ('http', '@', 'eyj', 'bearer', 'cookie', 'password', 'secret',
                                                 'authorization', 'session_id', 'access_token', 'refresh_token'))):
                                        safe_parts.append(f'{field}: {value}')
                        except Exception:
                            pass
                        self.report_warning('SBS playback API diagnostic: '
                                            + ('; '.join(safe_parts) if safe_parts
                                               else f'HTTP {retry_error.cause.status}; no safe error fields available'))
                        self.report_warning(
                            f'SBS playback still returns HTTP {retry_error.cause.status} after token refresh')
                        self.raise_login_required()
                    raise
            else:
                raise

        formats, subtitles = [], {}
        for fmt_url in traverse_obj(media, ('streamProviders', ..., 'url')):
            ext = determine_ext(fmt_url)
            if ext == 'm3u8':
                fmts, subs = self._extract_m3u8_formats_and_subtitles(fmt_url, video_id)
            elif ext == 'mpd':
                fmts, subs = self._extract_mpd_formats_and_subtitles(fmt_url, video_id)
            else:
                continue
            formats.extend(fmts)
            self._merge_subtitles(subs, target=subtitles)

        for sub in traverse_obj(media, ('streamProviders', ..., 'textTracks', lambda _, y: y.get('url'))):
            lang = sub.get('lang', 'en')
            ext = traverse_obj(sub,
                               ('format', {lambda x: 'vtt' if x == 'WebVTT' else None}),
                               ('url', {parse_qs}, 'format', 0),
                               ('url', {determine_ext(default_ext='vtt')}),
                               )
            subtitles.setdefault(lang, []).append({
                **traverse_obj(sub, {
                    'url': ('url', {url_or_none}),
                    'name': ('name', {str_or_none}),
                }),
                'ext': ext,
            })

        media.update(self._download_json(
            f'https://catalogue.pr.sbsod.com/mpx-media/{video_id}',
            video_id, fatal=not media) or {})

        # For named episodes, use the catalogue's title to set episode, rather than generic 'Episode N'.
        if traverse_obj(media, ('partOfSeries', {dict})):
            media['epName'] = traverse_obj(media, ('title', {str}))

        # Need to set different language for forced subs or else they have priority over full subs
        fixed_subtitles = {}
        for lang, subs in subtitles.items():
            for sub in subs:
                fixed_lang = lang
                if sub['url'].lower().endswith('_fe.vtt'):
                    fixed_lang += '-forced'
                fixed_subtitles.setdefault(fixed_lang, []).append(sub)

        thumbnails = []
        for thumb in traverse_obj(media, ('images', lambda _, y: y.get('id'))):
            tid = thumb.get('id')
            thumb_root = 'https://image.pr.sbsod.com'
            width, height, label = self._search_regex(
                r'\|(?P<w>\d+)\|(?P<h>\d+)\|(?P<l>[^\|]+)',
                thumb.get('category'),
                'thumbail info',
                group=('w', 'h', 'l'),
                default=[None, None, None],
            )
            thumbnails.append({
                'url': join_nonempty(thumb_root, tid, delim='/'),
                'width': int_or_none(width),
                'height': int_or_none(height),
                'id': f'{label}-{tid[:5]}',
            })

        return {
            'id': video_id,
            **traverse_obj(media, {
                'title': (('cdpTitle', 'title'), {str}),
                'description': ('description', {str}),
                'channel': ('taxonomy', 'channel', 'name', {str}),
                'series': ((('partOfSeries', 'name'), 'seriesTitle'), {str}),
                'series_id': ((('partOfSeries', 'uuid'), 'seriesID'), {str}),
                'season_number': ('seasonNumber', {int_or_none}),
                'episode': ('epName', {str}),
                'episode_number': ('episodeNumber', {int_or_none}),
                'timestamp': (('datePublished', ('publication', 'startDate')), {parse_iso8601}),
                'release_year': ('releaseYear', {int_or_none}),
                'duration': ('duration', ({float_or_none}, {parse_duration})),
                'is_live': ('liveStream', {bool}),
                'age_limit': (
                    ('classificationID', 'contentRating'), {str.upper}, {self._AUS_TV_PARENTAL_GUIDELINES.get}),
            }, get_all=False),
            **traverse_obj(media, {
                'categories': (('genres', ...), ('taxonomy', ('genre', 'subgenre'), 'name'), {str}),
                'tags': (('consumerAdviceTexts', ('sbsSubCertification', 'consumerAdvice')), ..., {str}),
            }),
            'thumbnails': thumbnails,
            'formats': formats,
            'subtitles': fixed_subtitles,
            'uploader': 'SBSC',
        }
