import os, time, threading, re, json
from datetime import datetime, timezone
from flask import Flask, Response, jsonify
import requests

app = Flask(__name__)
API = 'https://www.googleapis.com/youtube/v3'
CACHE_SECONDS = max(300, int(os.getenv('CACHE_SECONDS', '21600')))
MAX_PER_CHANNEL = max(1, min(5000, int(os.getenv('MAX_VIDEOS', '500'))))
_lock = threading.Lock()
_cache = {'expires': 0, 'items': [], 'updated': None, 'error': None, 'channels': []}

# Every candidate requested is included. For entries without a verified channel ID/handle,
# supply YOUTUBE_CHANNEL_<key> in Render. Never guess an identity from a channel name.
CHANNELS = [
 {'key':'vportuguese','name':'V Português','group':'Filmes YouTube | V Português','handle':'vportuguese','type':'filmes'},
 {'key':'netmovies','name':'NetMovies','group':'Filmes | NetMovies','type':'filmes','id':'UCK5RRQ3TdwAbbCWrtnXsN3Q'},
 {'key':'filmesparaassistir','name':'Filmes para Assistir','group':'Filmes | Filmes para Assistir','id':'UCotImOBoeKFxKpRsZULzxag','type':'filmes'},
 {'key':'filmelier','name':'Filmelier TV BR','group':'Filmes | Filmelier TV BR','type':'filmes','id':'UCCRmnBThm-dlqovUoeftVzg'},
 {'key':'o2play','name':'O2 Play','group':'Filmes | O2 Play','type':'filmes'},
 {'key':'swenfamily','name':'Swen Family Português','group':'Filmes | Swen Family','type':'filmes','id':'UCFTqGyR5DrbmLemBVh1ibSA'},
 {'key':'cinerama','name':'Cinerama Português','group':'Filmes | Cinerama','id':'UCshSGG_bV53GmIeqPAJUGqw','type':'filmes'},
 {'key':'sbtnovelas','name':'SBT Novelas','group':'Novelas | SBT','type':'novelas'},
 {'key':'cruelistambul','name':'Cruel Istambul','group':'Séries | Cruel Istambul','type':'series','id':'UCLoQ2GOM8m-ZnLHmQq2UC2w'},
 {'key':'seriesturcas','name':'Séries Turcas Dubladas em Português','group':'Séries | Séries Turcas Dubladas','type':'series','id':'UC93qAkwrguZpZY7sP2Zp_tw'},
 {'key':'recordnews','name':'Record News','group':'TV ao vivo | Record News','handle':'recordnews','type':'ao_vivo'},
 {'key':'sbtnews','name':'SBT News','group':'TV ao vivo | SBT News','handle':'sbtnews','type':'ao_vivo'},
 {'key':'cnnbrasil','name':'CNN Brasil','group':'TV ao vivo | CNN Brasil','handle':'cnnbrasil','type':'ao_vivo'},
 {'key':'bandjornalismo','name':'Band Jornalismo','group':'TV ao vivo | Band Jornalismo','handle':'bandjornalismo','type':'ao_vivo'},
]

class YoutubeError(Exception): pass

def api_get(resource, **params):
    key = os.getenv('YOUTUBE_API_KEY', '').strip()
    if not key: raise YoutubeError('YOUTUBE_API_KEY não configurada no Render')
    try:
        r = requests.get(f'{API}/{resource}', params={**params, 'key':key}, timeout=25)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        try: message = r.json().get('error',{}).get('message','')
        except ValueError: message = r.text[:150]
        raise YoutubeError(f'YouTube API HTTP {r.status_code}: {message}') from e
    except requests.RequestException as e:
        raise YoutubeError('Falha na conexão com a API YouTube') from e

def resolve(channel):
    override = os.getenv('YOUTUBE_CHANNEL_' + channel['key'].upper(), '').strip()
    ident = override or channel.get('id') or channel.get('handle')
    if not ident: return None, 'Configure YOUTUBE_CHANNEL_' + channel['key'].upper()
    if ident.startswith('https://www.youtube.com/') or ident.startswith('https://youtube.com/'):
        ident = ident.rstrip('/').split('/')[-1]
    if ident.startswith('UC') and len(ident) == 24: params = {'id':ident}
    else: params = {'forHandle':ident.lstrip('@')}
    data = api_get('channels', part='contentDetails,snippet', **params)
    found = data.get('items',[])
    if not found: return None, f'Canal não encontrado: {ident}'
    return found[0], None

def fetch_channel(channel, info):
    if channel['type'] == 'ao_vivo':
        # search.list costs 100 quota units per call. Only run for live channels, once per cache refresh.
        data = api_get('search', part='snippet', channelId=info['id'], eventType='live', type='video', maxResults=10)
        rows = []
        for entry in data.get('items',[]):
            vid = entry.get('id',{}).get('videoId')
            if vid: rows.append({'videoId':vid, 'snippet':entry.get('snippet',{})})
    else:
        uploads = info['contentDetails']['relatedPlaylists']['uploads']
        rows, token = [], None
        while len(rows) < MAX_PER_CHANNEL:
            args = {'part':'snippet,contentDetails','playlistId':uploads,'maxResults':min(50,MAX_PER_CHANNEL-len(rows))}
            if token: args['pageToken'] = token
            page = api_get('playlistItems', **args)
            rows.extend(page.get('items',[]))
            token = page.get('nextPageToken')
            if not token: break
    videos = []
    for row in rows:
        sn = row.get('snippet',{})
        vid = row.get('videoId') or row.get('contentDetails',{}).get('videoId') or sn.get('resourceId',{}).get('videoId')
        title = sn.get('title','').strip()
        if not vid or not title or title in ('Private video','Deleted video'): continue
        thumbs = sn.get('thumbnails',{})
        cover = next((thumbs[k]['url'] for k in ('maxres','standard','high','medium','default') if k in thumbs),f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg')
        videos.append({'id':vid,'title':title,'url':f'https://www.youtube.com/watch?v={vid}',
                       'thumbnail':cover,'publishedAt':sn.get('publishedAt',''),
                       'channel':channel['name'],'group':channel['group'],'kind':channel['type']})
    return videos

def get_catalog():
    if _cache['expires'] > time.time(): return _cache['items']
    with _lock:
        if _cache['expires'] > time.time(): return _cache['items']
        all_items, statuses, errors = [], [], []
        previous = {}
        for old in _cache['items']: previous.setdefault(old['channel'],[]).append(old)
        for channel in CHANNELS:
            try:
                info, issue = resolve(channel)
                if issue: raise YoutubeError(issue)
                videos = fetch_channel(channel,info)
                all_items.extend(videos)
                statuses.append({'name':channel['name'],'key':channel['key'],'count':len(videos),'status':'ok'})
            except (YoutubeError, KeyError, ValueError) as exc:
                error = str(exc)
                errors.append(f'{channel["name"]}: {error}')
                retained = previous.get(channel['name'],[])
                all_items.extend(retained)
                statuses.append({'name':channel['name'],'key':channel['key'],'count':len(retained),'status':'error','message':error})
        if not all_items and not _cache['updated']:
            _cache.update(channels=statuses,error='; '.join(errors),expires=time.time()+300)
            raise YoutubeError('Nenhum canal carregado. Confira /health e a chave da API.')
        _cache.update(items=all_items,channels=statuses,updated=datetime.now(timezone.utc).isoformat(),
                      error='; '.join(errors) if errors else None,expires=time.time()+CACHE_SECONDS)
        return all_items

# Series catalog is separate from the legacy M3U. Playlist membership is the
# source of truth for editions/collections, not a guessed season number.
SERIES_PLAYLIST_CACHE = {'expires': 0, 'payload': None, 'assignments': {}}
SERIES_PLAYLIST_LOCK = threading.Lock()
EPISODE_RE = re.compile(r'(?i)(?:\bepis[oó]dio\s*(?:n[º°.]?\s*)?|\bcap[ií]tulo\s*(?:n[º°.]?\s*)?|\bep\.?\s*)(\d{1,4})\b')
SEASON_RE = re.compile(r'(?i)\btemporada\s*(\d{1,3})\b')

def playlist_collections(channel_info, available):
    """Read the channel's own public playlists; retain only catalogued videos."""
    collections = []
    token = None
    # Bounded to avoid runaway quota use for channels with hundreds of lists.
    for _ in range(4):
        args = {'part':'snippet,contentDetails', 'channelId':channel_info['id'], 'maxResults':50}
        if token: args['pageToken'] = token
        page = api_get('playlists', **args)
        for pl in page.get('items',[]):
            pid = pl.get('id')
            name = pl.get('snippet',{}).get('title','').strip()
            if not pid or not name: continue
            matched = []
            next_token = None
            for _ in range(10):
                kw = {'part':'snippet,contentDetails','playlistId':pid,'maxResults':50}
                if next_token: kw['pageToken'] = next_token
                batch = api_get('playlistItems', **kw)
                for row in batch.get('items',[]):
                    vid = row.get('contentDetails',{}).get('videoId') or row.get('snippet',{}).get('resourceId',{}).get('videoId')
                    if vid in available and vid not in {v['id'] for v in matched}:
                        item = dict(available[vid])
                        match = EPISODE_RE.search(item['title'])
                        if match: item['episodeNumber'] = int(match.group(1))
                        matched.append(item)
                next_token = batch.get('nextPageToken')
                if not next_token: break
            if matched:
                # Keep playlist order as published; numbers are hints, not a basis
                # for combining different editions or assuming a season.
                collections.append({'id':pid,'title':name,'type':'playlist',
                                    'episodeCount':len(matched),'episodes':matched})
        token = page.get('nextPageToken')
        if not token: break
    return collections

# Playlists are grouped by production, not by the whole YouTube channel.
# These names are identifiers for matching playlist titles, not claims that a
# particular show is present until its playlist is returned by the API.
KNOWN_SHOWS = (
    ('Série Madrugador', ('madrugador', 'erkenci kus', 'erkenci kuş', 'pássaro sonhador')),
    ('A Noiva de Istambul', ('noiva de istambul', 'istanbullu gelin', 'istanbullu gelin')),
    ('Amor Para Alugar', ('amor para alugar', 'kiralik ask', 'kiralık aşk')),
    ('Amor na Ilha', ('amor na ilha', 'ada masali', 'ada masalı')),
)

def normalized(text):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))

def production_title(playlist_title):
    clean = normalized(playlist_title)
    for show, aliases in KNOWN_SHOWS:
        if any(normalized(alias) in clean for alias in aliases):
            return show
    # Unknown productions retain their original playlist title, with only
    # generic episode/edition labels stripped when possible.
    title = re.split(r'(?i)\s*[-|:]\s*(?:epis[oó]dios?|cap[ií]tulos?|temporada|vers[aã]o|dublado|completo)', playlist_title)[0].strip()
    return title or playlist_title

def build_series_catalog(items):
    series_items = [v for v in items if v.get('kind') in ('series','novelas')]
    result, assignments = [], {}
    for channel in CHANNELS:
        if channel['type'] not in ('series','novelas'): continue
        videos = [v for v in series_items if v.get('channel') == channel['name']]
        if not videos: continue
        available = {v['id']:v for v in videos}
        info, error = resolve(channel)
        if error: raise YoutubeError(error)
        collections = playlist_collections(info, available)
        if channel['key'] != 'seriesturcas':
            included = {v['id'] for c in collections for v in c['episodes']}
            result.append({'id':'youtube-source-'+channel['key'], 'title':channel['name'],
                           'source':channel['name'], 'poster':videos[0].get('thumbnail'),
                           'classification':'channel_collections', 'collections':collections,
                           'unclassified':[v for v in videos if v['id'] not in included],
                           'collectionCount':len(collections)})
            continue
        productions = {}
        for collection in collections:
            title = production_title(collection['title'])
            entry = productions.setdefault(title, {'title':title, 'source':channel['name'],
                'classification':'playlist_production', 'collections':[], 'unclassified':[]})
            entry['collections'].append(collection)
            for video in collection['episodes']:
                assignments.setdefault(video['id'], title)
        for title, entry in productions.items():
            first = entry['collections'][0]['episodes'][0]
            entry.update(id='youtube-series-'+re.sub(r'[^a-z0-9]+','-',normalized(title)).strip('-'),
                         poster=first.get('thumbnail'), collectionCount=len(entry['collections']),
                         unclassifiedCount=0)
            result.append(entry)
        # Videos without any public playlist remain available in the M3U,
        # but must not be misrepresented as episodes of a particular series.
    return result, assignments

def series_catalog(items):
    return build_series_catalog(items)[0]

@app.get('/series.json')
def series_endpoint():
    try:
        items = get_catalog()
        if SERIES_PLAYLIST_CACHE['expires'] > time.time():
            return jsonify(SERIES_PLAYLIST_CACHE['payload'])
        with SERIES_PLAYLIST_LOCK:
            if SERIES_PLAYLIST_CACHE['expires'] > time.time():
                return jsonify(SERIES_PLAYLIST_CACHE['payload'])
            result, assignments = build_series_catalog(items)
            payload = {'updatedAt':_cache['updated'], 'seriesCount':len(result),
                       'note':'Collections follow the channel public playlists; editions and specials are not assumed to be official seasons.',
                       'series':result}
            SERIES_PLAYLIST_CACHE.update(payload=payload,assignments=assignments,expires=time.time()+CACHE_SECONDS)
            return jsonify(payload)
    except (YoutubeError, KeyError, ValueError) as exc:
        return jsonify({'error':str(exc)}),502


def escape(value): return re.sub(r'[\r\n]+',' ',str(value)).replace('"',"'")

@app.get('/')
def home(): return jsonify({'service':'MEU IPTV Multi-Canais','routes':['/health','/playlist.m3u','/catalog.json','/series.json'],'channels':len(CHANNELS)})

@app.get('/health')
def health():
    return jsonify({'status':'ok','api_key_configured':bool(os.getenv('YOUTUBE_API_KEY')),
                    'cached_count':len(_cache['items']),'last_update':_cache['updated'],
                    'last_error':_cache['error'],'channels':_cache['channels']})

@app.get('/catalog.json')
def catalog():
    try: videos = get_catalog()
    except YoutubeError as e: return jsonify({'error':str(e)}),502
    return jsonify({'channel':'multicanais','updatedAt':_cache['updated'],'count':len(videos),'videos':videos})

@app.get('/playlist.m3u')
def playlist():
    try: videos = get_catalog()
    except YoutubeError as e: return Response(str(e),status=502,mimetype='text/plain')
    # Reuse cached playlist assignments; refresh when needed so the Android
    # app sees a separate category/cover for each identified production.
    assignments = SERIES_PLAYLIST_CACHE.get('assignments', {})
    if SERIES_PLAYLIST_CACHE['expires'] <= time.time():
        try:
            _, assignments = build_series_catalog(videos)
            SERIES_PLAYLIST_CACHE['assignments'] = assignments
            SERIES_PLAYLIST_CACHE['expires'] = time.time() + CACHE_SECONDS
        except (YoutubeError, KeyError, ValueError):
            assignments = SERIES_PLAYLIST_CACHE.get('assignments', {})
    lines = ['#EXTM3U']
    for v in videos:
        group = ('Séries | ' + assignments[v['id']]) if v['channel'] == 'Séries Turcas Dubladas em Português' and v['id'] in assignments else v['group']
        lines.append(f'#EXTINF:-1 tvg-id="youtube-{escape(v["id"])}" tvg-logo="{escape(v["thumbnail"])}" group-title="{escape(group)}",{escape(v["title"])}')
        lines.append(v['url'])
    return Response('\n'.join(lines)+'\n',mimetype='audio/x-mpegurl',headers={'Cache-Control':'public, max-age=300','Content-Disposition':'inline; filename="meu-iptv-multicanais.m3u"'})

if __name__ == '__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
