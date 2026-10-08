#!/usr/bin/env python3
"""Rumble playlist collector and yt-dlp launcher."""
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

BASE = Path('/run/media/hacky/RECOVERY/rumble')
YTDLP = Path.home() / 'rumble-venv/bin/yt-dlp'
ENDPOINT = 'https://rumble.com/-playlists/htmx/get-playlist-details'


def fetch(url, params=None):
    args = ['curl', '-fsSL', '--retry', '2', '--connect-timeout', '15', '--max-time', '90',
            '-A', 'Mozilla/5.0', '-H', 'Referer: https://rumble.com/']
    if params:
        args += ['-H', 'HX-Request: true', '-G']
        for key, value in params.items():
            args += ['--data-urlencode', f'{key}={value}']
    args += [url]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'curl request failed')
    return result.stdout


def extract(html, playlist_id):
    soup = BeautifulSoup(html, 'html.parser')
    found = []
    for item in soup.select(f'[data-playlist-id="{playlist_id}"][data-video-id]'):
        parent = item.find_parent('li') or item
        link = parent.find('a', href=re.compile(r'^/v[a-zA-Z0-9]+.*\.html(?:\?|$)'))
        if link:
            url = urljoin('https://rumble.com', link['href']).split('?', 1)[0]
            found.append(url)
    return list(dict.fromkeys(found)), soup


def main():
    print('=== RUMBLE PLAYLIST DOWNLOADER ===')
    folder_name = input('Folder name (inside RECOVERY/rumble): ').strip()
    if not folder_name or folder_name in ('.', '..') or '/' in folder_name or '\\' in folder_name:
        sys.exit('Invalid folder name. Use a simple name without slashes.')
    if any(ord(c) < 32 for c in folder_name):
        sys.exit('Invalid folder name: control characters are not allowed.')
    if not BASE.parent.is_mount():
        sys.exit(f'ERROR: RECOVERY drive is not mounted at {BASE.parent}')
    BASE.mkdir(exist_ok=True)
    playlist_dir = BASE / folder_name
    playlist_dir.mkdir(exist_ok=True)
    print(f'Working folder: {playlist_dir}')
    playlist_url = input('Paste Rumble playlist URL: ').strip()
    parsed = urlparse(playlist_url)
    match = re.fullmatch(r'/playlists/([A-Za-z0-9_-]+)/?', parsed.path)
    if parsed.hostname not in ('rumble.com', 'www.rumble.com') or parsed.scheme != 'https' or not match:
        sys.exit('Invalid Rumble playlist URL. Expected https://rumble.com/playlists/ID')
    playlist_id = match.group(1)
    urls = []
    seen = set()
    def add(items):
        for url in items:
            if url not in seen:
                seen.add(url)
                urls.append(url)

    print('Fetching playlist page 1...')
    first_html = fetch(playlist_url)
    first, soup = extract(first_html, playlist_id)
    add(first)
    (playlist_dir / 'playlist_page_1.html').write_text(first_html)
    print(f'Page 1: {len(first)} videos')

    # Rumble typically returns 10 items per page, with the rest loaded via HTMX.
    # Read pagination metadata from the page rather than assuming the entire playlist is visible.
    shuffle = None
    page_size = 10
    for tag in soup.select('[hx-vals]'):
        vals = tag.get('hx-vals', '')
        if playlist_id in vals:
            try:
                obj = json.loads(vals)
            except ValueError:
                continue
            shuffle = obj.get('shuffle_param', shuffle)
            try:
                page_size = int(obj.get('page_size', page_size))
            except (TypeError, ValueError):
                pass
            break

    empty_pages = 0
    for page in range(2, 51):
        params = {'playlist_id': playlist_id, 'page_size': page_size,
                  'pagination': 1, 'page_num': page}
        if shuffle:
            params['shuffle_param'] = shuffle
        html = fetch(ENDPOINT, params)
        items, soup = extract(html, playlist_id)
        (playlist_dir / f'playlist_page_{page}.html').write_text(html)
        before = len(urls)
        add(items)
        print(f'Page {page}: {len(items)} shown, {len(urls)-before} new')
        if not items:
            break
        if len(urls) == before:
            empty_pages += 1
        else:
            empty_pages = 0
        if empty_pages >= 2:
            print('No new videos on two consecutive pages; stopping.')
            break
        # Stop only if no HTMX next-page control and page is shorter than page size.
        if len(items) < page_size and not any('get-playlist-details' in (tag.get('hx-get') or '') for tag in soup.select('[hx-get]')):
            break
    else:
        print('WARNING: Reached 50-page safety limit.')

    if not urls:
        sys.exit('No video links found; no download started.')
    url_file = playlist_dir / 'video_urls.txt'
    url_file.write_text('\n'.join(urls) + '\n')
    # Also maintain the requested common path for convenience.
    (BASE / 'video_urls.txt').write_text('\n'.join(urls) + '\n')
    print(f'\nCollected {len(urls)} unique video URLs')
    print(f'List: {BASE / "video_urls.txt"}')
    print(f'Playlist folder: {playlist_dir}')
    answer = input('Download all collected videos now? [y/N]: ').strip().lower()
    if answer not in ('y', 'yes'):
        print('List saved. No downloads started.')
        return
    if not YTDLP.is_file():
        sys.exit(f'yt-dlp not found at {YTDLP}')
    command = [str(YTDLP), '--impersonate', 'chrome',
               '--cookies-from-browser', 'firefox', '--referer', 'https://rumble.com/',
               '--batch-file', str(url_file), '--continue',
               '--download-archive', str(playlist_dir / 'downloaded.txt'),
               '--retries', '10', '--fragment-retries', '10',
               '--abort-on-unavailable-fragments', '-P', str(playlist_dir),
               '-o', '%(title).100B [%(id)s].%(ext)s']
    print('\nStarting downloads. Press Ctrl+C to stop; rerun to resume.\n')
    raise SystemExit(subprocess.call(command))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError) as exc:
        sys.exit(f'ERROR: {exc}')
