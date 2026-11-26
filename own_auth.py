import os, json, re, hmac, secrets
from datetime import datetime, timezone, timedelta
from flask import request, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
import firebase_admin
from firebase_admin import credentials, firestore, auth

def db():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(json.loads(os.environ['FIREBASE_SERVICE_ACCOUNT_JSON'])))
    return firestore.client()

def phone(value):
    n = re.sub(r'[^0-9]', '', str(value))
    return n[2:] if len(n)==13 and n.startswith('55') else n

def install(app, playlist):
    def valid(row):
        expiry = row.get("expiracao")
        return row.get("ativo") is True and (not expiry or expiry > datetime.now(timezone.utc))
    def public(row):
        return {"ativo": row.get("ativo", False), "assinatura": row.get("assinatura", "MEU IPTV PRO"), "nome": row.get("nome", ""), "telefone": row.get("telefone", ""), "maxConexoes": row.get("maxConexoes", 1), "criadoEm": row["criadoEm"].isoformat() if row.get("criadoEm") else "", "expiracao": row["expiracao"].isoformat() if row.get("expiracao") else "", "playlist": request.url_root.rstrip("/") + "/private/list/" + row["playlistToken"] + ".m3u" if row.get("playlistToken") else ""}
    def authorize():
        a = request.authorization
        if not a or a.type != 'basic': return False
        n = phone(a.username or '')
        if len(n) not in (10,11) or not a.password or len(a.password)>256: return False
        row = db().collection('iptvClientesPrivados').document(n).get().to_dict() or {}
        return row if valid(row) and check_password_hash(row.get('senhaHash',''), a.password) else False
    def private(handler):
        try:
            if not authorize(): return jsonify(error='Acesso negado'), 401
            response = app.make_response(handler())
            response.headers['Cache-Control'] = 'private, no-store'
            return response
        except Exception:
            return jsonify(error='Autenticação ou catálogo indisponível'), 503
    @app.get('/private/account')
    def account():
        def info():
            row = authorize()
            return jsonify(status='Active', expiracao=str(int(row['expiracao'].timestamp())) if row.get('expiracao') else '', maxConexoes=row.get('maxConexoes',1), conexoesAtivas=0)
        return private(info)
    @app.get('/private/playlist.m3u')
    def private_catalog(): return private(playlist)
    @app.get('/private/list/<token>.m3u')
    def token_playlist(token):
        def handler():
            row = authorize()
            if not row or not hmac.compare_digest(row.get('playlistToken',''), token): return jsonify(error='Acesso negado'),401
            return playlist()
        return private(handler)
    def admin():
        try:
            db()
            token = request.headers.get('Authorization','').removeprefix('Bearer ')
            uid = auth.verify_id_token(token, check_revoked=True)['uid']
            return hmac.compare_digest(uid, os.environ['IPTV_ADMIN_UID'])
        except Exception: return False
    @app.get('/admin/iptv/clientes')
    def list_iptv_clients():
        if not admin(): return jsonify(error='Acesso negado'),403
        return jsonify([public(d.to_dict()) for d in db().collection('iptvClientesPrivados').stream()])
    @app.post('/admin/iptv/clientes')
    def create():
        if not admin(): return jsonify(error='Acesso negado'),403
        data = request.get_json(silent=True) or {}
        n = phone(data.get('telefone','')); name = str(data.get('nome','')).strip(); password = str(data.get('senha',''))
        if len(n) not in (10,11) or not name or not 8<=len(password)<=256: return jsonify(error='Dados inválidos'),400
        max_conn = data.get('maxConexoes',1); minutes = data.get('minutos',43200)
        if type(max_conn) is not int or not 1 <= max_conn <= 100 or type(minutes) is not int or not (minutes in (60,120) or 1440 <= minutes <= 5256000 and minutes % 1440 == 0): return jsonify(error='Limites inválidos'),400
        now = datetime.now(timezone.utc)
        row = {'nome':name,'telefone':n,'senhaHash':generate_password_hash(password),'ativo':True,'maxConexoes':max_conn,'criadoEm':now,'expiracao':now+timedelta(minutes=minutes),'playlistToken':secrets.token_urlsafe(32)}
        ref = db().collection('iptvClientesPrivados').document(n)
        from google.api_core.exceptions import AlreadyExists
        try: ref.create(row)
        except AlreadyExists: return jsonify(error='Cliente já cadastrado'),409
        return jsonify(public(row)),201

    @app.put('/admin/iptv/clientes/<telephone>')
    def edit_iptv_client(telephone):
        if not admin(): return jsonify(error='Acesso negado'),403
        n = phone(telephone)
        data = request.get_json(silent=True) or {}
        name = data.get('nome'); label = data.get('assinatura'); maximum = data.get('maxConexoes'); active = data.get('ativo'); expiry = data.get('expiracao')
        if len(n) not in (10,11) or not isinstance(name,str) or not 1 <= len(name.strip()) <= 200 or not isinstance(label,str) or len(label)>200 or type(maximum) is not int or not 1 <= maximum <= 100 or type(active) is not bool or not isinstance(expiry,str): return jsonify(error='Dados inválidos'),400
        try:
            end = datetime.fromisoformat(expiry.replace('Z','+00:00')) if expiry else None
            if end and end.tzinfo is None: raise ValueError()
        except ValueError: return jsonify(error='Vencimento inválido'),400
        ref = db().collection('iptvClientesPrivados').document(n)
        if not ref.get().exists: return jsonify(error='Cliente não encontrado'),404
        changes = dict(nome=name.strip(), assinatura=label.strip() or 'MEU IPTV PRO', maxConexoes=maximum, ativo=active)
        if end is not None: changes['expiracao'] = end.astimezone(timezone.utc)
        ref.update(changes)
        return jsonify(public(ref.get().to_dict()))

    from split_catalog import install as install_split
    install_split(app, playlist, private)
