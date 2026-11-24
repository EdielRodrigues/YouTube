import os, json, re, hmac
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
    def authorize():
        a = request.authorization
        if not a or a.type != 'basic': return False
        n = phone(a.username or '')
        if len(n) not in (10,11) or not a.password or len(a.password)>256: return False
        row = db().collection('iptvClientesPrivados').document(n).get().to_dict() or {}
        return row.get('ativo') is True and check_password_hash(row.get('senhaHash',''), a.password)
    def private(handler):
        try:
            if not authorize(): return jsonify(error='Acesso negado'), 401
            response = app.make_response(handler())
            response.headers['Cache-Control'] = 'private, no-store'
            return response
        except Exception:
            return jsonify(error='Autenticação ou catálogo indisponível'), 503
    @app.get('/private/account')
    def account(): return private(lambda: jsonify(status='Active'))
    @app.get('/private/playlist.m3u')
    def private_catalog(): return private(playlist)
    @app.post('/admin/iptv/clientes')
    def create():
        try:
            db()
            token = request.headers.get('Authorization','').removeprefix('Bearer ')
            uid = auth.verify_id_token(token, check_revoked=True)['uid']
            if not hmac.compare_digest(uid, os.environ['IPTV_ADMIN_UID']): return jsonify(error='Acesso negado'),403
        except Exception: return jsonify(error='Acesso negado'),403
        data = request.get_json(silent=True) or {}
        n = phone(data.get('telefone','')); name = str(data.get('nome','')).strip(); password = str(data.get('senha',''))
        if len(n) not in (10,11) or not name or not 8<=len(password)<=256: return jsonify(error='Dados inválidos'),400
        db().collection('iptvClientesPrivados').document(n).set({'nome':name,'telefone':n,'senhaHash':generate_password_hash(password),'ativo':True})
        return jsonify(ok=True),201
