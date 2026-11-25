import gzip, hashlib, re, threading, time
from flask import Response, jsonify, request

_lock = threading.Lock()
_snapshot = {}

def install(app, playlist, private):
    def worker():
        global _snapshot
        while True:
            try:
                with app.test_request_context():
                    response = app.make_response(playlist())
                    if response.status_code != 200: raise RuntimeError('catalog unavailable')
                    groups = {k:['#EXTM3U'] for k in ('vod','series','live')}
                    entry = None
                    for line in response.get_data(as_text=True).splitlines():
                        if line.startswith('#EXTINF:'): entry = line
                        elif entry and line.startswith(('http://','https://')):
                            match = re.search(r'group-title="([^"]*)"',entry)
                            group = (match.group(1) if match else '').lower()
                            kind = 'live' if group.startswith('tv') or 'ao vivo' in group else 'series' if group.startswith(('séries','series','novelas')) else 'vod'
                            groups[kind].extend((entry,line)); entry=None
                    new = {}
                    for kind, lines in groups.items():
                        raw = ('\n'.join(lines)+'\n').encode()
                        version = hashlib.sha256(raw).hexdigest()
                        new[kind] = (version,gzip.compress(raw,mtime=0),(len(lines)-1)//2)
                    with _lock: _snapshot = new
            except Exception: app.logger.exception('Split catalog refresh failed')
            time.sleep(900)
    @app.get('/private/v')
    def versions():
        def handler():
            with _lock: data = {k:{'version':v[0],'count':v[2]} for k,v in _snapshot.items()}
            return jsonify(data) if data else (jsonify(error='Preparando catálogo'),503)
        return private(handler)
    @app.get('/private/c/<kind>')
    def download(kind):
        def handler():
            with _lock: value = _snapshot.get(kind)
            if not value: return jsonify(error='Catálogo indisponível'),503
            version, payload, count = value
            return Response(payload,mimetype='application/gzip',headers={'X-Catalog-Version':version,'X-Catalog-Count':str(count)})
        return private(handler)
    threading.Thread(target=worker,daemon=True).start()
