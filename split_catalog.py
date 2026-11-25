import gzip, hashlib, re, threading, time
from flask import Response, jsonify, request

_lock = threading.Lock()
_snapshot = {}

def install(app, playlist, private):
    from pathlib import Path
    def local_playlist():
        path = Path(__file__).with_name("ReelSeries_Organizado.m3u")
        return path.read_text(encoding="utf-8-sig") if path.is_file() else "#EXTM3U\n"
    def publish(text, merge=False):
        global _snapshot
        groups = {k:["#EXTM3U"] for k in ("vod","series","live")}
        seen = set()
        entry = None
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("#EXTINF:"): entry = line
            elif entry and line.startswith(("http://","https://")):
                match = re.search(r'group-title="([^"]*)"',entry)
                group = (match.group(1) if match else "").lower()
                kind = "live" if group.startswith("tv") or "ao vivo" in group else "series" if group.startswith(("séries","series","novelas")) else "vod"
                if (kind,line) not in seen:
                    groups[kind].extend((entry,line)); seen.add((kind,line))
                entry = None
        new = {}
        for kind, lines in groups.items():
            raw = ("\n".join(lines)+"\n").encode()
            new[kind] = (hashlib.sha256(raw).hexdigest(),gzip.compress(raw,mtime=0),(len(lines)-1)//2)
        with _lock:
            _snapshot = new
    @app.get("/catalog/health")
    def split_health():
        with _lock:
            return jsonify({k:{"count":v[2],"bytes":len(v[1]),"version":v[0]} for k,v in _snapshot.items()})
    def worker():
        global _snapshot
        try: publish(local_playlist())
        except Exception: app.logger.exception("Local catalog failed")
        while True:
            try:
                with app.test_request_context():
                    response = app.make_response(playlist())
                    if response.status_code != 200: raise RuntimeError('catalog unavailable')
                    publish(response.get_data(as_text=True))
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
