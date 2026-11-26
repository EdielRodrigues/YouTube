"""Optional catalog sources. Only explicitly approved items are published."""
import os, time, threading, json, re
from urllib.parse import quote, urljoin
import requests

LOCK = threading.Lock()
CACHE = {}
LICENSE = re.compile(r'^https?://creativecommons\.org/(?:licenses/(?:by|by-sa)/[1-4]\.0|publicdomain/(?:zero|mark)/1\.0)/?$', re.I)

def get(url, **params):
    r = requests.get(url, params=params, timeout=(5, 15), headers={'User-Agent':'MeuIPTV-Catalog/1.0'})
    r.raise_for_status()
    return r.json()

def https(url):
    return isinstance(url,str) and url.startswith('https://')

def item(source, ident, title, url, cover, credit, license_url, group='Filmes'):
    return dict(id=source.lower().replace(' ','-')+'-'+ident, title=title, url=url, thumbnail=cover, channel=source, group=group+' | '+source, kind='series' if group=='Séries' else 'filmes', publishedAt='', attribution=credit, license=license_url)

def archive(entry):
    ident=entry['id']; data=get('https://archive.org/metadata/'+quote(ident,safe=''))
    m=data['metadata']; lic=m.get('licenseurl','')
    if not isinstance(lic,str) or not LICENSE.fullmatch(lic) or m.get('access-restricted-item')=='true': return None
    files=[f for f in data.get('files',[]) if f.get('name','').lower().endswith('.mp4') and f.get('private')!='true']
    if not files: return None
    f=next((f for f in files if f.get('source')=='original'),files[0])
    return item('Internet Archive',ident,str(m.get('title',ident)), 'https://archive.org/download/'+quote(ident,safe='')+'/'+quote(f['name'],safe='/'), 'https://archive.org/services/img/'+quote(ident,safe=''),str(m.get('creator','')),lic,entry.get('group','Filmes'))

def peertube(entry):
    base=entry['instance'].rstrip('/'); ident=entry['id']
    if not https(base): return None
    v=get(base+'/api/v1/videos/'+quote(ident,safe=''))
    lic={1:'https://creativecommons.org/licenses/by/4.0/',2:'https://creativecommons.org/licenses/by-sa/4.0/',7:'https://creativecommons.org/publicdomain/zero/1.0/'}.get((v.get('licence') or {}).get('id'))
    if not lic or v.get('nsfw') or (v.get('privacy') or {}).get('id')!=1 or v.get('isLive'): return None
    urls=[p.get('playlistUrl') for p in v.get('streamingPlaylists',[])]+[f.get('fileUrl') for f in v.get('files',[])]
    url=next((u for u in urls if https(u)),None)
    if not url: return None
    cover=urljoin(base+'/',v.get('thumbnailPath') or '')
    return item('PeerTube',v.get('uuid',ident),v['name'],url,cover,(v.get('account') or {}).get('displayName',''),lic,entry.get('group','Filmes'))

def dailymotion(entry):
    ident=entry['id']; v=get('https://api.dailymotion.com/video/'+quote(ident,safe=''),fields='id,title,thumbnail_url,owner.screenname,allow_embed,private')
    if v.get('private') or not v.get('allow_embed'): return None
    # Explicit permission must be verified separately; official embed only.
    if not entry.get('permission'): return None
    return item('Dailymotion',ident,v['title'],'https://www.dailymotion.com/embed/video/'+quote(ident,safe=''),v.get('thumbnail_url',''),v.get('owner.screenname',''),entry['permission'],entry.get('group','Filmes'))

FETCHERS={'archive':archive,'peertube':peertube,'dailymotion':dailymotion}

def snapshot():
    with LOCK:
        if not CACHE or time.time()>CACHE.get('expires',0):
            if not CACHE.get('running'):
                CACHE['running']=True
                threading.Thread(target=refresh,daemon=True).start()
        return list(CACHE.get('items',[])),dict(CACHE.get('status',{}))

def discover_archive():
    query = 'mediatype:movies AND (language:por OR language:Portuguese OR language:pt) AND (licenseurl:*creativecommons.org/licenses/by/* OR licenseurl:*creativecommons.org/licenses/by-sa/* OR licenseurl:*creativecommons.org/publicdomain/*)'
    data = get('https://archive.org/advancedsearch.php', q=query, **{'fl[]':'identifier'}, rows=80, page=1, output='json')
    return [{'source':'archive','id':r['identifier']} for r in data.get('response',{}).get('docs',[]) if r.get('identifier')]

def discover_peertube():
    base='https://video.blender.org'
    data=get(base+'/api/v1/videos', count=25, **{'licenceOneOf[]':[1,2,7], 'nsfw':'false', 'isLive':'false'})
    return [{'source':'peertube','instance':base,'id':r['uuid']} for r in data.get('data',[]) if r.get('uuid')]

def discover_commons():
    data = get('https://commons.wikimedia.org/w/api.php', action='query', format='json', generator='search', gsrsearch='filetype:video (film OR animation OR documentary)', gsrnamespace=6, gsrlimit=50, prop='imageinfo', iiprop='url|mime|extmetadata', iiurlwidth=320, iiextmetadatafilter='LicenseUrl|Artist|Credit|Restrictions')
    return [{'source':'commons','id':str(v['pageid']),'page':v} for v in data.get('query',{}).get('pages',{}).values()]

def commons(entry):
    page=entry['page']; info=(page.get('imageinfo') or [{}])[0]; meta=info.get('extmetadata',{})
    def field(k): return str(meta.get(k,{}).get('value',''))
    lic=field('LicenseUrl'); url=info.get('url','')
    if not LICENSE.fullmatch(lic) or not https(url) or not info.get('mime','').startswith('video/') or field('Restrictions'): return None
    return item('Wikimedia Commons',entry['id'],page['title'].removeprefix('File:').rsplit('.',1)[0],url,info.get('thumburl',''),re.sub('<[^>]+>','',field('Artist')),lic)

def discover_global_peertube():
    data=get('https://sepiasearch.org/api/v1/search/videos', search='filme', count=30, **{'licenceOneOf[]':[1,2,7], 'nsfw':'false'})
    from urllib.parse import urlsplit
    out=[]
    for v in data.get('data',[]):
        u=urlsplit(v.get('url',''))
        if u.scheme=='https' and u.hostname and v.get('uuid'): out.append({'source':'peertube','instance':'https://'+u.hostname,'id':v['uuid']})
    return out

FETCHERS['commons']=commons

def refresh():
    items=[]; status={k:{'count':0,'errors':0} for k in FETCHERS}
    try:
        approved=json.loads(os.getenv('EXTERNAL_APPROVED_JSON','[]'))
        for key, discover in [('archive',discover_archive),('peertube',discover_peertube),('peertube',discover_global_peertube),('commons',discover_commons)]:
            try: approved.extend(discover())
            except Exception: status[key]['errors']+=1
        seen_entries=set()
        for e in approved[:300]:
            identity=(e.get('source'),e.get('instance'),e.get('id'))
            if identity in seen_entries: continue
            seen_entries.add(identity)
            k=e.get('source')
            if k not in FETCHERS: continue
            try:
                v=FETCHERS[k](e)
                if v and v['url'] not in {x['url'] for x in items}:
                    items.append(v); status[k]['count']+=1
            except Exception: status[k]['errors']+=1
        with LOCK:
            old=CACHE.get('items',[])
            for k in FETCHERS:
                if status[k]['errors'] and not status[k]['count']:
                    name={'archive':'Internet Archive','peertube':'PeerTube','dailymotion':'Dailymotion','commons':'Wikimedia Commons'}[k]
                    retained=[x for x in old if x['channel']==name]; items.extend(retained); status[k]['count']=len(retained)
            CACHE.update(items=items,status=status)
    except Exception:
        with LOCK: CACHE['status']={'error':'EXTERNAL_APPROVED_JSON inválido'}
    finally:
        with LOCK: CACHE.update(running=False,expires=time.time()+3600)

def install(app):
    from flask import jsonify
    @app.get('/external/health')
    def external_health():
        items,status=snapshot(); return jsonify(count=len(items),sources=status,refreshing=CACHE.get('running',False))
    @app.get('/external/catalog.json')
    def external_catalog():
        items,status=snapshot(); return jsonify(count=len(items),videos=items,sources=status)
