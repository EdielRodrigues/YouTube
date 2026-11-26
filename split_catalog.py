import gzip, hashlib, re, threading, time, os, io
from pathlib import Path
import json, fcntl
ROOT = Path(os.getenv("IPTV_CACHE_DIR", "/tmp/meu-iptv-cache"))
ROOT.mkdir(parents=True, exist_ok=True)
import zipfile
SNAP = ROOT / 'catalog.snapshot.zip'
def read_shared(kind=None):
    try:
        with zipfile.ZipFile(SNAP) as z:
            diagnostic, meta = json.loads(z.read('metadata.json'))
            if kind in KINDS:
                if kind not in meta: return None
                m = meta[kind]
                return (m[0], z.read(kind), m[1])
            return diagnostic, meta
    except FileNotFoundError:
        return None if kind in KINDS else ({}, {})
def write_shared(status, snapshot):
    meta = {k:[v[0],v[2],len(v[1])] for k,v in snapshot.items()}
    tmp = ROOT / ('catalog.%s.tmp' % os.getpid())
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_STORED) as z:
        z.writestr('metadata.json', json.dumps([status, meta]))
        for k,v in snapshot.items(): z.writestr(k,v[1])
    os.replace(tmp, SNAP)
def youtube_health():
    try: return json.loads((ROOT / "youtube.json").read_text())
    except (OSError, ValueError): return {}
def save_youtube_health(value):
    tmp=ROOT / ("youtube.%s.tmp" % os.getpid())
    tmp.write_text(json.dumps(value)); os.replace(tmp,ROOT / "youtube.json")

KINDS = ('vod', 'series', 'live')

def build(lines):
    groups = {k: io.BytesIO(b'#EXTM3U\n') for k in KINDS}
    for buf in groups.values(): buf.seek(0, 2)
    counts = dict.fromkeys(KINDS, 0)
    seen = set()
    entry = None
    for raw in lines:
        line = raw.strip()
        if line.startswith('#EXTINF:'):
            entry = line
        elif entry and line and not line.startswith('#'):
            if line.startswith(('http://', 'https://')):
                match = re.search(r'group-title="([^"]*)"', entry)
                group = (match.group(1) if match else '').casefold()
                kind = 'live' if group.startswith('tv') or 'ao vivo' in group else 'series' if group.startswith(('séries','series','novelas')) else 'vod'
                if (kind, line) not in seen:
                    seen.add((kind, line))
                    groups[kind].write((entry+'\n'+line+'\n').encode('utf-8'))
                    counts[kind] += 1
            entry = None
    for kind, buf in groups.items():
        if True:
            raw = buf.getvalue()
            yield kind, (hashlib.sha256(raw).hexdigest(), gzip.compress(raw, compresslevel=1, mtime=0), counts[kind])

def install(app, playlist, private):
    from flask import Response, jsonify
    lock = threading.Lock()
    snapshot = {}
    status = {'stage':'starting','error':None,'refresh_error':None,'process_id':os.getpid(),'youtube_count':0,'channels_completed':0}
    path = Path(os.getenv('IPTV_PLAYLIST_PATH') or str(Path(__file__).resolve().with_name('ReelSeries_Organizado.m3u')))
    def state(**kw):
        with lock:
            status.update(kw)
            if snapshot: write_shared(status, snapshot)
        app.logger.warning('Catalog status: %s', kw)
    def publish(lines, **progress):
        prepared = dict(build(lines))
        with lock:
            snapshot.update(prepared)
            status.update(progress)
            status['published_at'] = time.time()
            write_shared(status, snapshot)
        for kind, value in prepared.items():
            app.logger.warning('Catalog %s ready: %d', kind, value[2])
    def local():
        state(stage='reading_local', file_exists=path.is_file(), file_bytes=path.stat().st_size if path.is_file() else 0)
        if path.is_file():
            with path.open(encoding='utf-8-sig') as source: publish(source)
        else: publish([])
        with lock: missing = [k for k in KINDS if k not in snapshot]
        if missing: raise RuntimeError('Missing categories: '+','.join(missing))
        state(stage='local_ready', error=None)
    @app.get('/catalog/health')
    def split_catalog_health():
        diagnostic, meta = read_shared()
        return jsonify({**{k:{'count':v[1],'bytes':v[2],'version':v[0]} for k,v in meta.items()},'diagnostic':diagnostic})
    @app.get('/private/v')
    def split_catalog_versions():
        def handler():
            _, meta = read_shared()
            data = {k:{'version':v[0],'count':v[1]} for k,v in meta.items()}
            return jsonify(data) if data else (jsonify(error='Preparando catálogo'),503)
        return private(handler)
    @app.get('/private/c/<kind>')
    def split_catalog_download(kind):
        def handler():
            value = read_shared(kind)
            if not value: return jsonify(error='Catálogo indisponível'),503
            version,payload,count = value
            return Response(payload,mimetype='application/gzip',headers={'X-Catalog-Version':version,'X-Catalog-Count':str(count)})
        return private(handler)
    # Independent publisher; retain previously published entries on source failure.
    merge_lock = threading.Lock()
    youtube_lines = []
    external_lines = []
    def entries(lines):
        pending = None
        for raw in lines:
            line = raw.strip()
            if line.startswith('#EXTINF:'): pending = line
            elif pending and line.startswith(('https://','http://')):
                yield pending, line
                pending = None
    def merge_publish(**progress):
        with merge_lock:
            combined = {}
            if path.is_file():
                with path.open(encoding='utf-8-sig') as source:
                    combined.update((url, tag) for tag,url in entries(source))
            for lines in (youtube_lines, external_lines):
                combined.update((url, tag) for tag,url in entries(lines))
            publish((line for url,tag in combined.items() for line in (tag,url)), **progress)
    def external_worker():
        from external_sources import snapshot as external_snapshot
        from html import escape
        while True:
            try:
                items, sources = external_snapshot()
                lines = []
                for v in items:
                    if v.get('channel') == 'Dailymotion' or not str(v.get('url','')).startswith('https://'): continue
                    def clean(key, default=''):
                        return escape(str(v.get(key) or default).replace('\n',' ').replace('\r',' '), quote=True)
                    lines.extend((f'#EXTINF:-1 tvg-id="{clean("id")}" tvg-logo="{clean("thumbnail")}" group-title="{clean("group","Filmes")}",{clean("title")}',v['url']))
                if lines:
                    with merge_lock: external_lines[:] = lines
                    merge_publish(external_count=len(lines)//2, external_error=None)
            except Exception as exc:
                state(external_error=type(exc).__name__)
                app.logger.exception('Independent external refresh failed')
            time.sleep(15)
    def worker():
        # Restore previously published remote entries (local file is handled separately).
        if not path.is_file():
            for kind in KINDS:
                value = snapshot.get(kind)
                if value: youtube_lines.extend(gzip.decompress(value[1]).decode('utf-8').splitlines())
        threading.Thread(target=external_worker, daemon=True).start()
        while True:
            try:
                state(stage='refreshing_external')
                def progress(lines, channels):
                    count = sum(c.get('count',0) for c in channels)
                    if count:
                        with merge_lock:
                            old = {url:tag for tag,url in entries(youtube_lines)}
                            old.update((url,tag) for tag,url in entries(lines))
                            youtube_lines[:] = [line for url,tag in old.items() for line in (tag,url)]
                    merge_publish(stage='refreshing_external', channels_completed=len(channels), youtube_count=count)
                with app.test_request_context():
                    progressive = getattr(playlist, 'progressive', None)
                    if progressive: progressive(progress)
                    else:
                        response = app.make_response(playlist())
                        if response.status_code != 200: raise RuntimeError('YouTube unavailable')
                        with merge_lock: youtube_lines[:] = response.get_data(as_text=True).splitlines()
                        merge_publish()
                state(stage='ready',refresh_error=None)
            except Exception as exc:
                state(stage='refresh_error',refresh_error=type(exc).__name__)
                app.logger.exception('YouTube refresh failed; external publisher remains active')
            time.sleep(900)
    def leader():
        with (ROOT / 'refresh.lock').open('a') as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            diagnostic, meta = read_shared()
            for k in meta:
                value = read_shared(k)
                if value: snapshot[k] = value
            if diagnostic: status.update(diagnostic)
            if not snapshot:
                try: local()
                except Exception as exc:
                    state(stage='local_error',error=type(exc).__name__)
                    app.logger.exception('Local catalog failed')
            worker()
    threading.Thread(target=leader,daemon=True).start()
