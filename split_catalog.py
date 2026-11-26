import gzip, hashlib, re, threading, time, os, io
from pathlib import Path
import sqlite3, json, fcntl
ROOT = Path(os.getenv("IPTV_CACHE_DIR", "/tmp/meu-iptv-cache"))
ROOT.mkdir(parents=True, exist_ok=True)
def db():
    c = sqlite3.connect(str(ROOT / "catalog.sqlite"), timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, diagnostic TEXT, vod BLOB, series BLOB, live BLOB, metadata TEXT)")
    return c
def read_shared(kind=None):
    with db() as c:
        if kind in KINDS:
            row = c.execute(f"SELECT {kind}, metadata FROM snapshots WHERE id=1").fetchone()
            if row and row[0] is not None:
                m=json.loads(row[1])[kind]; return (m[0],row[0],m[1])
            return None
        row=c.execute("SELECT diagnostic, metadata FROM snapshots WHERE id=1").fetchone()
        return (json.loads(row[0]),json.loads(row[1])) if row else ({},{})
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
        if counts[kind]:
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
            with db() as c: c.execute("UPDATE snapshots SET diagnostic=? WHERE id=1",(json.dumps(status),))
        app.logger.warning('Catalog status: %s', kw)
    def publish(lines, **progress):
        prepared = dict(build(lines))
        with lock:
            snapshot.update(prepared)
            status.update(progress)
            status['published_at'] = time.time()
            meta={k:[v[0],v[2],len(v[1])] for k,v in snapshot.items()}
            with db() as c:
                c.execute('INSERT OR REPLACE INTO snapshots VALUES (1,?,?,?,?,?)',(json.dumps(status),*[snapshot[k][1] if k in snapshot else None for k in KINDS],json.dumps(meta)))
        for kind, value in prepared.items():
            app.logger.warning('Catalog %s ready: %d', kind, value[2])
    def local():
        state(stage='reading_local', file_exists=path.is_file(), file_bytes=path.stat().st_size if path.is_file() else 0)
        with path.open(encoding='utf-8-sig') as source: publish(source)
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
    def worker():
        while True:
            try:
                if not read_shared()[1]: local()
            except Exception as exc:
                state(stage='local_error',error=type(exc).__name__)
                app.logger.exception('Local catalog failed')
            try:
                state(stage='refreshing_external')
                def progress(lines, channels):
                    from itertools import chain
                    with path.open(encoding='utf-8-sig') as source:
                        publish(chain(source, lines), stage='refreshing_external', channels_completed=len(channels), youtube_count=sum(c.get('count',0) for c in channels))
                with app.test_request_context():
                    progressive = getattr(playlist, 'progressive', None)
                    if progressive:
                        progressive(progress)
                    response = app.make_response(playlist())
                    if response.status_code != 200: raise RuntimeError('External catalog unavailable')
                    from itertools import chain
                    with path.open(encoding='utf-8-sig') as source:
                        publish(chain(source, io.StringIO(response.get_data(as_text=True))))
                state(stage='ready',refresh_error=None)
            except Exception as exc:
                state(stage='refresh_error',refresh_error=type(exc).__name__)
                app.logger.exception('External catalog failed')
            time.sleep(900)
    def leader():
        with (ROOT / 'refresh.lock').open('a') as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            if not read_shared()[1]:
                try: local()
                except Exception as exc:
                    state(stage='local_error',error=type(exc).__name__)
                    app.logger.exception('Local catalog failed')
            worker()
    threading.Thread(target=leader,daemon=True).start()
