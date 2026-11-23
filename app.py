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

# Series metadata is intentionally separate from the legacy M3U playlist.
# M3U continues to expose every video so existing IPTV installations keep working.
SERIES_NAMES = {'cruelistambul': 'Cruel Istambul', 'sbtnovelas': 'SBT Novelas'}
EPISODE_RE = re.compile(r'(?i)(?:\bepis[oó]dio\s*(?:n[º°.]?\s*)?|\bcap[ií]tulo\s*(?:n[º°.]?\s*)?|\bep\.?\s*)(\d{1,4})\b')
SEASON_RE = re.compile(r'(?i)(?:\btemporada\s*|\bt\s*)(\d{1,3})\b')


def series_catalog(items):
    """Group conservatively: an upload is not automatically a full episode."""
    groups = {}
    for video in items:
        if video.get('kind') not in ('series', 'novelas'):
            continue
        channel_key = next((c['key'] for c in CHANNELS if c['name'] == video.get('channel')), None)
        if not channel_key:
            continue
        # Dedicated Cruel Istambul channel is one show; SBT Novelas is a
        # multi-show source and must not be collapsed into one fictitious show.
        if channel_key == 'cruelistambul':
            name, series_id = 'Cruel Istambul', 'youtube-series-cruelistambul'
        else:
            # Without verified show metadata, do not invent a series identity.
            name, series_id = 'Não identificada — ' + video['channel'], 'youtube-unclassified-' + channel_key
        group = groups.setdefault(series_id, {'id':series_id,'title':name,'source':video['channel'],
            'poster':video.get('thumbnail'), 'seasons':{},'extras':[], 'needsReview':channel_key != 'cruelistambul'})
        title = video.get('title','')
        episode_match = EPISODE_RE.search(title)
        season_match = SEASON_RE.search(title)
        entry = {'id':video['id'],'title':title,'url':video['url'],
                 'thumbnail':video.get('thumbnail'),'publishedAt':video.get('publishedAt','')}
        if episode_match and channel_key == 'cruelistambul':
            episode_number = int(episode_match.group(1))
            season_number = int(season_match.group(1)) if season_match else None
            entry['episodeNumber'] = episode_number
            entry['seasonNumber'] = season_number
            key = str(season_number) if season_number is not None else 'unknown'
            season = group['seasons'].setdefault(key, {'seasonNumber':season_number,'episodes':[]})
            season['episodes'].append(entry)
        else:
            entry['reason'] = 'episode_number_not_verified' if channel_key == 'cruelistambul' else 'show_not_identified'
            group['extras'].append(entry)
    result=[]
    for group in groups.values():
        seasons=list(group.pop('seasons').values())
        for season in seasons:
            season['episodes'].sort(key=lambda e:(e['episodeNumber'],e['publishedAt'],e['id']))
        seasons.sort(key=lambda x:(x['seasonNumber'] is None,x['seasonNumber'] or 0))
        group['seasons']=seasons
        group['episodeCount']=sum(len(season['episodes']) for season in seasons)
        group['extraCount']=len(group['extras'])
        result.append(group)
    return result


@app.get('/series.json')
def series_endpoint():
    try: items = get_catalog()
    except YoutubeError as exc: return jsonify({'error':str(exc)}),502
    groups = series_catalog(items)
    return jsonify({'updatedAt':_cache['updated'],'seriesCount':len(groups),
                    'note':'Only explicitly numbered episodes are classified. Unverified videos are kept as extras.',
                    'series':groups})


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
    lines = ['#EXTM3U']
    for v in videos:
        lines.append(f'#EXTINF:-1 tvg-id="youtube-{escape(v["id"])}" tvg-logo="{escape(v["thumbnail"])}" group-title="{escape(v["group"])}",{escape(v["title"])}')
        lines.append(v['url'])
    return Response('\n'.join(lines)+'\n',mimetype='audio/x-mpegurl',headers={'Cache-Control':'public, max-age=300','Content-Disposition':'inline; filename="meu-iptv-multicanais.m3u"'})

if __name__ == '__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
