import os, time, threading
from datetime import datetime, timezone
from flask import Flask, Response, jsonify, request
import requests

app = Flask(__name__)
API_BASE = 'https://www.googleapis.com/youtube/v3'
CHANNEL_HANDLE = os.getenv('YOUTUBE_CHANNEL_HANDLE', 'vportuguese').lstrip('@')
CACHE_SECONDS = int(os.getenv('CACHE_SECONDS', '21600'))
MAX_VIDEOS = min(max(int(os.getenv('MAX_VIDEOS', '500')), 1), 5000)
_cache = {'expires': 0, 'items': [], 'updated': None, 'error': None}
_lock = threading.Lock()

class YoutubeError(Exception):
    pass

def api_get(resource, **params):
    key = os.getenv('YOUTUBE_API_KEY', '').strip()
    if not key:
        raise YoutubeError('Configure YOUTUBE_API_KEY nas variáveis de ambiente do Render.')
    try:
        r = requests.get(f'{API_BASE}/{resource}', params={**params, 'key': key}, timeout=25)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as exc:
        detail = ''
        try:
            detail = r.json().get('error', {}).get('message', '')
        except ValueError:
            pass
        raise YoutubeError(f'Erro na API do YouTube ({r.status_code}): {detail}') from exc
    except requests.RequestException as exc:
        raise YoutubeError('Falha de conexão com a API do YouTube.') from exc

def fetch_videos():
    channel = api_get('channels', part='contentDetails,snippet', forHandle=CHANNEL_HANDLE)
    channels = channel.get('items', [])
    if not channels:
        raise YoutubeError(f'Canal @{CHANNEL_HANDLE} não encontrado.')
    uploads = channels[0]['contentDetails']['relatedPlaylists']['uploads']
    items, token = [], None
    while len(items) < MAX_VIDEOS:
        args = {'part': 'snippet,contentDetails', 'playlistId': uploads,
                'maxResults': min(50, MAX_VIDEOS - len(items))}
        if token:
            args['pageToken'] = token
        page = api_get('playlistItems', **args)
        for item in page.get('items', []):
            snippet = item.get('snippet', {})
            vid = item.get('contentDetails', {}).get('videoId') or snippet.get('resourceId', {}).get('videoId')
            title = snippet.get('title', '')
            if not vid or title in ('Private video', 'Deleted video'):
                continue
            thumbs = snippet.get('thumbnails', {})
            cover = next((thumbs[k]['url'] for k in ('maxres','standard','high','medium','default') if k in thumbs),
                         f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg')
            items.append({'id': vid, 'title': title, 'url': f'https://www.youtube.com/watch?v={vid}',
                          'thumbnail': cover, 'publishedAt': snippet.get('publishedAt', ''),
                          'channel': snippet.get('videoOwnerChannelTitle') or snippet.get('channelTitle', '')})
        token = page.get('nextPageToken')
        if not token:
            break
    return items

def get_catalog():
    now = time.time()
    if _cache['expires'] > now:
        return _cache['items']
    with _lock:
        if _cache['expires'] > time.time():
            return _cache['items']
        try:
            items = fetch_videos()
            _cache.update(items=items, updated=datetime.now(timezone.utc).isoformat(),
                          expires=time.time()+CACHE_SECONDS, error=None)
        except YoutubeError as exc:
            _cache['error'] = str(exc)
            if _cache['updated']:
                _cache['expires'] = time.time()+300
                return _cache['items']
            raise
        return _cache['items']

def m3u_escape(value):
    return str(value).replace('"', "'").replace('\n',' ').replace('\r',' ')

@app.get('/')
def home():
    return jsonify({'service': 'YouTube IPTV Catalog', 'routes': ['/playlist.m3u', '/catalog.json', '/health'],
                    'note': 'Links oficiais do YouTube, não streams HLS/M3U8.'})

@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'api_key_configured': bool(os.getenv('YOUTUBE_API_KEY')),
                    'last_update': _cache['updated'], 'cached_count': len(_cache['items']),
                    'last_error': _cache['error']})

@app.get('/catalog.json')
def catalog():
    try:
        items = get_catalog()
        return jsonify({'channel': '@'+CHANNEL_HANDLE, 'updatedAt': _cache['updated'],
                        'count': len(items), 'videos': items})
    except YoutubeError as exc:
        return jsonify({'error': str(exc)}), 502

@app.get('/playlist.m3u')
def playlist():
    try:
        items = get_catalog()
    except YoutubeError as exc:
        return Response(str(exc), status=502, mimetype='text/plain')
    lines = ['#EXTM3U']
    for v in items:
        lines.append(f'#EXTINF:-1 tvg-id="youtube-{v["id"]}" tvg-logo="{m3u_escape(v["thumbnail"])}" group-title="Filmes YouTube | V Português",{m3u_escape(v["title"])}')
        lines.append(v['url'])
    return Response('\n'.join(lines)+'\n', mimetype='audio/x-mpegurl',
                    headers={'Content-Disposition': 'inline; filename="v-portugues.m3u"',
                             'Cache-Control': 'public, max-age=300'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '10000')))
