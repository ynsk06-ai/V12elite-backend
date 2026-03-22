
# ═══════════════════════════════════════════════════════════════
# BIST AI SOCIAL - Auth + Chat + Forum Backend
# Kullanici kayit/giris, gercek zamanli sohbet, forum
# ═══════════════════════════════════════════════════════════════

import os, json, sqlite3, time, hashlib, asyncio, uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from contextlib import asynccontextmanager

from fastapi import WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

import jwt
from passlib.context import CryptContext

# ─── CONFIG ───────────────────────────────────────────────────
JWT_SECRET   = os.environ.get("JWT_SECRET", "bist-ai-gizli-anahtar-2026-degistirin")
JWT_EXP_DAYS = 30
DB_PATH      = os.environ.get("DB_PATH", "/tmp/bist_social.db")
BACKUP_PATH  = "/tmp/bist_social_backup.json"
MAX_MSG_LEN  = 500
MAX_USERS    = 10000
MAX_TOPICS   = 5000
MAX_COMMENTS = 50000

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

# ─── VERİTABANI ────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Kullanicilar
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        email_hash TEXT UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT,
        avatar TEXT DEFAULT '?',
        role TEXT DEFAULT 'user',
        bio TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        last_seen TEXT,
        is_active INTEGER DEFAULT 1,
        badges TEXT DEFAULT '[]'
    )""")
    
    # Ozel mesajlar
    c.execute("""CREATE TABLE IF NOT EXISTS direct_messages (
        id TEXT PRIMARY KEY,
        from_user TEXT NOT NULL,
        to_user TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        is_read INTEGER DEFAULT 0,
        FOREIGN KEY(from_user) REFERENCES users(id),
        FOREIGN KEY(to_user) REFERENCES users(id)
    )""")
    
    # Grup sohbet odalar
    c.execute("""CREATE TABLE IF NOT EXISTS chat_rooms (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        type TEXT DEFAULT 'public',
        created_by TEXT,
        created_at TEXT NOT NULL,
        is_active INTEGER DEFAULT 1
    )""")
    
    # Grup mesajlari
    c.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
        id TEXT PRIMARY KEY,
        room_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        edited_at TEXT,
        is_deleted INTEGER DEFAULT 0,
        FOREIGN KEY(room_id) REFERENCES chat_rooms(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    
    # Forum kategorileri
    c.execute("""CREATE TABLE IF NOT EXISTS forum_categories (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        icon TEXT DEFAULT '?',
        color TEXT DEFAULT '#00D4FF',
        order_idx INTEGER DEFAULT 0,
        topic_count INTEGER DEFAULT 0
    )""")
    
    # Forum konulari
    c.execute("""CREATE TABLE IF NOT EXISTS forum_topics (
        id TEXT PRIMARY KEY,
        category_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        tags TEXT DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT,
        view_count INTEGER DEFAULT 0,
        reply_count INTEGER DEFAULT 0,
        like_count INTEGER DEFAULT 0,
        is_pinned INTEGER DEFAULT 0,
        is_locked INTEGER DEFAULT 0,
        is_deleted INTEGER DEFAULT 0,
        FOREIGN KEY(category_id) REFERENCES forum_categories(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    
    # Forum yorumlari
    c.execute("""CREATE TABLE IF NOT EXISTS forum_comments (
        id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        content TEXT NOT NULL,
        parent_id TEXT,
        created_at TEXT NOT NULL,
        edited_at TEXT,
        like_count INTEGER DEFAULT 0,
        is_deleted INTEGER DEFAULT 0,
        FOREIGN KEY(topic_id) REFERENCES forum_topics(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    
    # Begeni/oy tablosu
    c.execute("""CREATE TABLE IF NOT EXISTS likes (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, target_type, target_id)
    )""")
    
    # Varsayilan odalar ve kategoriler
    now = datetime.utcnow().isoformat()
    
    # Odalar
    default_rooms = [
        ("genel", "Genel Sohbet", "Herkese acik genel sohbet odasi", "public"),
        ("sinyaller", "Sinyal Tartisma", "BIST sinyallerini tartisın", "public"),
        ("strateji", "Strateji", "Trading stratejileri paylasim", "public"),
        ("haber", "Haberler", "Piyasa haberleri ve yorumlar", "public"),
    ]
    for rid, name, desc, rtype in default_rooms:
        c.execute("INSERT OR IGNORE INTO chat_rooms VALUES (?,?,?,?,NULL,?,1)",
                  (rid, name, desc, rtype, now))
    
    # Forum kategorileri
    default_cats = [
        ("sinyal-analiz",  "Sinyal Analizi",    "Sinyalleri birlikte analiz edelim", "?", "#00D4FF", 1),
        ("strateji",       "Trading Stratejisi","Al-sat stratejileri ve fikirler",   "?", "#FFB800", 2),
        ("teknik-analiz",  "Teknik Analiz",     "Grafik ve indikatör tartısmaları",  "?", "#00E676", 3),
        ("hisseler",       "Hisse Yorumları",   "Belirli hisseler hakkında yorumlar","?", "#C084FC", 4),
        ("genel",          "Genel",             "Her türlü konu",                    "?", "#888888", 5),
        ("paylasim",       "Kazanım Paylaşım",  "Başarılı işlemleri paylaşın",       "?", "#FF7043", 6),
    ]
    for cid, name, desc, icon, color, order in default_cats:
        c.execute("INSERT OR IGNORE INTO forum_categories VALUES (?,?,?,?,?,?,0)",
                  (cid, name, desc, icon, color, order))
    
    conn.commit()
    conn.close()
    print("DB initialized:", DB_PATH)

# ─── JWT ──────────────────────────────────────────────────────
def create_token(user_id: str, username: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(days=JWT_EXP_DAYS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except:
        return None

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(401, "Token gerekli")
    payload = verify_token(credentials.credentials)
    if not payload:
        raise HTTPException(401, "Gecersiz veya suresi dolmus token")
    return payload

def get_optional_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        return None
    return verify_token(credentials.credentials)

# ─── MODELLER ─────────────────────────────────────────────────
class RegisterReq(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    display_name: Optional[str] = None

class LoginReq(BaseModel):
    username: str    # username veya email
    password: str

class SendMsgReq(BaseModel):
    to_user: str
    content: str

class ChatMsgReq(BaseModel):
    room_id: str
    content: str

class CreateTopicReq(BaseModel):
    category_id: str
    title: str
    content: str
    tags: Optional[List[str]] = []

class CreateCommentReq(BaseModel):
    topic_id: str
    content: str
    parent_id: Optional[str] = None

class UpdateProfileReq(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar: Optional[str] = None

# ─── WEBSOCKET MANAGER ────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.connections: Dict[str, Dict] = {}  # user_id -> {ws, username, room}
        self.room_members: Dict[str, set] = {}  # room_id -> set of user_ids
    
    async def connect(self, ws: WebSocket, user_id: str, username: str):
        await ws.accept()
        self.connections[user_id] = {"ws": ws, "username": username, "room": "genel"}
        if "genel" not in self.room_members:
            self.room_members["genel"] = set()
        self.room_members["genel"].add(user_id)
    
    def disconnect(self, user_id: str):
        if user_id in self.connections:
            room = self.connections[user_id].get("room")
            if room and room in self.room_members:
                self.room_members[room].discard(user_id)
            del self.connections[user_id]
    
    async def join_room(self, user_id: str, room_id: str):
        if user_id in self.connections:
            old_room = self.connections[user_id].get("room")
            if old_room and old_room in self.room_members:
                self.room_members[old_room].discard(user_id)
            self.connections[user_id]["room"] = room_id
            if room_id not in self.room_members:
                self.room_members[room_id] = set()
            self.room_members[room_id].add(user_id)
    
    async def broadcast_room(self, room_id: str, message: dict, exclude: str = None):
        if room_id not in self.room_members:
            return
        dead = []
        for uid in self.room_members[room_id]:
            if uid == exclude:
                continue
            if uid in self.connections:
                try:
                    await self.connections[uid]["ws"].send_json(message)
                except:
                    dead.append(uid)
        for uid in dead:
            self.disconnect(uid)
    
    async def send_private(self, to_user_id: str, message: dict):
        if to_user_id in self.connections:
            try:
                await self.connections[to_user_id]["ws"].send_json(message)
            except:
                self.disconnect(to_user_id)
    
    def get_online_users(self) -> List[str]:
        return [v["username"] for v in self.connections.values()]
    
    def get_room_users(self, room_id: str) -> List[str]:
        if room_id not in self.room_members:
            return []
        return [self.connections[uid]["username"] 
                for uid in self.room_members[room_id] 
                if uid in self.connections]

ws_manager = ConnectionManager()

# ─── AUTH ENDPOINTLERI ─────────────────────────────────────────
def _register_social_routes(app):
    
    @app.post("/social/auth/register")
    async def register(req: RegisterReq):
        if len(req.username) < 3 or len(req.username) > 30:
            raise HTTPException(400, "Kullanici adi 3-30 karakter olmali")
        if len(req.password) < 6:
            raise HTTPException(400, "Sifre en az 6 karakter olmali")
        
        # Gecersiz karakterler
        import re as _re
        if not _re.match(r'^[a-zA-Z0-9_]+$', req.username):
            raise HTTPException(400, "Kullanici adi sadece harf, rakam ve alt cizgi icermeli")
        
        conn = get_db()
        try:
            # Email hash (gizlilik icin)
            email_hash = None
            if req.email:
                email_hash = hashlib.sha256(req.email.lower().encode()).hexdigest()
                # Email zaten var mi?
                ex = conn.execute("SELECT id FROM users WHERE email_hash=?", (email_hash,)).fetchone()
                if ex:
                    raise HTTPException(409, "Bu email zaten kayitli")
            
            # Username var mi?
            ex = conn.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (req.username,)).fetchone()
            if ex:
                raise HTTPException(409, "Bu kullanici adi zaten alinmis")
            
            uid = str(uuid.uuid4())
            pw_hash = pwd_ctx.hash(req.password)
            display = req.display_name or req.username
            now = datetime.utcnow().isoformat()
            
            conn.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?,1,'[]')",
                (uid, req.username, email_hash, pw_hash, display, "?", "user", "", now, now)
            )
            conn.commit()
            
            token = create_token(uid, req.username)
            return {
                "token": token,
                "user": {"id": uid, "username": req.username, "display_name": display, "avatar": "?"}
            }
        finally:
            conn.close()
    
    @app.post("/social/auth/login")
    async def login(req: LoginReq):
        conn = get_db()
        try:
            # Username veya email ile giris
            user = None
            if "@" in req.username:
                email_hash = hashlib.sha256(req.username.lower().encode()).hexdigest()
                user = conn.execute("SELECT * FROM users WHERE email_hash=?", (email_hash,)).fetchone()
            else:
                user = conn.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (req.username,)).fetchone()
            
            if not user:
                raise HTTPException(401, "Kullanici bulunamadi")
            if not user["is_active"]:
                raise HTTPException(403, "Hesap askıya alindi")
            if not pwd_ctx.verify(req.password, user["password_hash"]):
                raise HTTPException(401, "Sifre yanlis")
            
            # Last seen guncelle
            conn.execute("UPDATE users SET last_seen=? WHERE id=?",
                        (datetime.utcnow().isoformat(), user["id"]))
            conn.commit()
            
            token = create_token(user["id"], user["username"])
            return {
                "token": token,
                "user": {
                    "id": user["id"], "username": user["username"],
                    "display_name": user["display_name"], "avatar": user["avatar"],
                    "role": user["role"], "bio": user["bio"]
                }
            }
        finally:
            conn.close()
    
    @app.get("/social/auth/me")
    async def get_me(user=Depends(get_current_user)):
        conn = get_db()
        try:
            u = conn.execute("SELECT id,username,display_name,avatar,role,bio,created_at,badges FROM users WHERE id=?",
                           (user["sub"],)).fetchone()
            if not u:
                raise HTTPException(404, "Kullanici bulunamadi")
            return dict(u)
        finally:
            conn.close()
    
    @app.put("/social/auth/profile")
    async def update_profile(req: UpdateProfileReq, user=Depends(get_current_user)):
        conn = get_db()
        try:
            updates = []
            vals = []
            if req.display_name is not None:
                updates.append("display_name=?"); vals.append(req.display_name[:50])
            if req.bio is not None:
                updates.append("bio=?"); vals.append(req.bio[:200])
            if req.avatar is not None:
                # Sadece emoji izin ver
                updates.append("avatar=?"); vals.append(req.avatar[:4])
            if updates:
                vals.append(user["sub"])
                conn.execute(f"UPDATE users SET {','.join(updates)} WHERE id=?", vals)
                conn.commit()
            return {"ok": True}
        finally:
            conn.close()
    
    # ─── KULLANICI LİSTESİ ────────────────────────────────────
    @app.get("/social/users")
    async def list_users(user=Depends(get_current_user)):
        conn = get_db()
        try:
            users = conn.execute(
                "SELECT id,username,display_name,avatar,role,last_seen FROM users WHERE is_active=1 ORDER BY last_seen DESC LIMIT 100"
            ).fetchall()
            online = ws_manager.get_online_users()
            result = []
            for u in users:
                d = dict(u)
                d["online"] = d["username"] in online
                result.append(d)
            return result
        finally:
            conn.close()
    
    @app.get("/social/users/{username}")
    async def get_user(username: str, user=Depends(get_optional_user)):
        conn = get_db()
        try:
            u = conn.execute(
                "SELECT id,username,display_name,avatar,role,bio,created_at,badges FROM users WHERE lower(username)=lower(?)",
                (username,)
            ).fetchone()
            if not u:
                raise HTTPException(404, "Kullanici bulunamadi")
            d = dict(u)
            d["online"] = d["username"] in ws_manager.get_online_users()
            # Topic ve yorum sayisi
            conn2 = get_db()
            d["topic_count"] = conn2.execute("SELECT COUNT(*) FROM forum_topics WHERE user_id=? AND is_deleted=0", (d["id"],)).fetchone()[0]
            d["comment_count"] = conn2.execute("SELECT COUNT(*) FROM forum_comments WHERE user_id=? AND is_deleted=0", (d["id"],)).fetchone()[0]
            conn2.close()
            return d
        finally:
            conn.close()
    
    # ─── ÖZEL MESAJLAR ────────────────────────────────────────
    @app.get("/social/dm/conversations")
    async def get_conversations(user=Depends(get_current_user)):
        conn = get_db()
        try:
            uid = user["sub"]
            rows = conn.execute("""
                SELECT DISTINCT 
                  CASE WHEN from_user=? THEN to_user ELSE from_user END as other_id,
                  u.username, u.display_name, u.avatar,
                  MAX(dm.created_at) as last_at,
                  SUM(CASE WHEN dm.to_user=? AND dm.is_read=0 THEN 1 ELSE 0 END) as unread
                FROM direct_messages dm
                JOIN users u ON u.id = CASE WHEN dm.from_user=? THEN dm.to_user ELSE dm.from_user END
                WHERE dm.from_user=? OR dm.to_user=?
                GROUP BY other_id
                ORDER BY last_at DESC
            """, (uid,uid,uid,uid,uid)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    @app.get("/social/dm/{username}")
    async def get_dm_history(username: str, limit: int = 50, user=Depends(get_current_user)):
        conn = get_db()
        try:
            uid = user["sub"]
            other = conn.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
            if not other:
                raise HTTPException(404, "Kullanici bulunamadi")
            oid = other["id"]
            
            msgs = conn.execute("""
                SELECT dm.*, u.username, u.display_name, u.avatar
                FROM direct_messages dm
                JOIN users u ON u.id = dm.from_user
                WHERE (dm.from_user=? AND dm.to_user=?) OR (dm.from_user=? AND dm.to_user=?)
                ORDER BY dm.created_at DESC LIMIT ?
            """, (uid,oid,oid,uid,limit)).fetchall()
            
            # Okundu isaretle
            conn.execute("UPDATE direct_messages SET is_read=1 WHERE to_user=? AND from_user=?", (uid,oid))
            conn.commit()
            
            return list(reversed([dict(m) for m in msgs]))
        finally:
            conn.close()
    
    @app.post("/social/dm/send")
    async def send_dm(req: SendMsgReq, user=Depends(get_current_user)):
        if len(req.content.strip()) == 0 or len(req.content) > MAX_MSG_LEN:
            raise HTTPException(400, f"Mesaj 1-{MAX_MSG_LEN} karakter olmali")
        
        conn = get_db()
        try:
            uid = user["sub"]
            to = conn.execute("SELECT id,username FROM users WHERE lower(username)=lower(?)", (req.to_user,)).fetchone()
            if not to:
                raise HTTPException(404, "Alici bulunamadi")
            
            mid = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()
            conn.execute("INSERT INTO direct_messages VALUES (?,?,?,?,?,0)",
                        (mid, uid, to["id"], req.content.strip(), now))
            conn.commit()
            
            # Alici online ise websocket ile bildir
            sender = conn.execute("SELECT username,display_name,avatar FROM users WHERE id=?", (uid,)).fetchone()
            await ws_manager.send_private(to["id"], {
                "type": "dm",
                "id": mid,
                "from_user": uid,
                "from_username": sender["username"],
                "from_avatar": sender["avatar"],
                "content": req.content.strip(),
                "created_at": now
            })
            return {"id": mid, "created_at": now}
        finally:
            conn.close()
    
    # ─── GRUP SOHBET ──────────────────────────────────────────
    @app.get("/social/chat/rooms")
    async def get_rooms(user=Depends(get_current_user)):
        conn = get_db()
        try:
            rooms = conn.execute("SELECT * FROM chat_rooms WHERE is_active=1 ORDER BY name").fetchall()
            result = []
            for r in rooms:
                d = dict(r)
                d["online_count"] = len(ws_manager.get_room_users(d["id"]))
                result.append(d)
            return result
        finally:
            conn.close()
    
    @app.get("/social/chat/{room_id}/messages")
    async def get_room_messages(room_id: str, limit: int = 50, user=Depends(get_current_user)):
        conn = get_db()
        try:
            msgs = conn.execute("""
                SELECT cm.*, u.username, u.display_name, u.avatar
                FROM chat_messages cm
                JOIN users u ON u.id = cm.user_id
                WHERE cm.room_id=? AND cm.is_deleted=0
                ORDER BY cm.created_at DESC LIMIT ?
            """, (room_id, limit)).fetchall()
            return list(reversed([dict(m) for m in msgs]))
        finally:
            conn.close()
    
    @app.get("/social/chat/online")
    async def get_online(user=Depends(get_current_user)):
        return {"users": ws_manager.get_online_users(), "count": len(ws_manager.connections)}
    
    # ─── FORUM ────────────────────────────────────────────────
    @app.get("/social/forum/categories")
    async def get_categories(user=Depends(get_optional_user)):
        conn = get_db()
        try:
            cats = conn.execute("SELECT * FROM forum_categories ORDER BY order_idx").fetchall()
            return [dict(c) for c in cats]
        finally:
            conn.close()
    
    @app.get("/social/forum/topics")
    async def get_topics(category_id: str = None, limit: int = 20, offset: int = 0,
                         search: str = None, user=Depends(get_optional_user)):
        conn = get_db()
        try:
            q = """
                SELECT ft.*, u.username, u.display_name, u.avatar,
                       fc.name as category_name, fc.color as category_color
                FROM forum_topics ft
                JOIN users u ON u.id = ft.user_id
                JOIN forum_categories fc ON fc.id = ft.category_id
                WHERE ft.is_deleted=0
            """
            params = []
            if category_id:
                q += " AND ft.category_id=?"; params.append(category_id)
            if search:
                q += " AND (ft.title LIKE ? OR ft.content LIKE ?)"; params += [f"%{search}%",f"%{search}%"]
            q += " ORDER BY ft.is_pinned DESC, ft.updated_at DESC LIMIT ? OFFSET ?"
            params += [limit, offset]
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    @app.get("/social/forum/topics/{topic_id}")
    async def get_topic(topic_id: str, user=Depends(get_optional_user)):
        conn = get_db()
        try:
            topic = conn.execute("""
                SELECT ft.*, u.username, u.display_name, u.avatar,
                       fc.name as category_name, fc.color as category_color
                FROM forum_topics ft
                JOIN users u ON u.id = ft.user_id
                JOIN forum_categories fc ON fc.id = ft.category_id
                WHERE ft.id=? AND ft.is_deleted=0
            """, (topic_id,)).fetchone()
            if not topic:
                raise HTTPException(404, "Konu bulunamadi")
            
            # Goruntuleme say
            conn.execute("UPDATE forum_topics SET view_count=view_count+1 WHERE id=?", (topic_id,))
            conn.commit()
            
            d = dict(topic)
            d["view_count"] += 1
            return d
        finally:
            conn.close()
    
    @app.post("/social/forum/topics")
    async def create_topic(req: CreateTopicReq, user=Depends(get_current_user)):
        if len(req.title.strip()) < 5:
            raise HTTPException(400, "Baslik en az 5 karakter olmali")
        if len(req.content.strip()) < 20:
            raise HTTPException(400, "Icerik en az 20 karakter olmali")
        
        conn = get_db()
        try:
            # Kategori var mi
            cat = conn.execute("SELECT id FROM forum_categories WHERE id=?", (req.category_id,)).fetchone()
            if not cat:
                raise HTTPException(404, "Kategori bulunamadi")
            
            tid = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO forum_topics VALUES (?,?,?,?,?,?,?,?,0,0,0,0,0,0)",
                (tid, req.category_id, user["sub"], req.title.strip(),
                 req.content.strip(), json.dumps(req.tags or []), now, now)
            )
            conn.execute("UPDATE forum_categories SET topic_count=topic_count+1 WHERE id=?",
                        (req.category_id,))
            conn.commit()
            return {"id": tid, "created_at": now}
        finally:
            conn.close()
    
    @app.get("/social/forum/topics/{topic_id}/comments")
    async def get_comments(topic_id: str, user=Depends(get_optional_user)):
        conn = get_db()
        try:
            comments = conn.execute("""
                SELECT fc.*, u.username, u.display_name, u.avatar
                FROM forum_comments fc
                JOIN users u ON u.id = fc.user_id
                WHERE fc.topic_id=? AND fc.is_deleted=0
                ORDER BY fc.created_at ASC
            """, (topic_id,)).fetchall()
            return [dict(c) for c in comments]
        finally:
            conn.close()
    
    @app.post("/social/forum/comments")
    async def create_comment(req: CreateCommentReq, user=Depends(get_current_user)):
        if len(req.content.strip()) < 2:
            raise HTTPException(400, "Yorum en az 2 karakter olmali")
        
        conn = get_db()
        try:
            topic = conn.execute("SELECT id,is_locked,user_id FROM forum_topics WHERE id=? AND is_deleted=0",
                               (req.topic_id,)).fetchone()
            if not topic:
                raise HTTPException(404, "Konu bulunamadi")
            if topic["is_locked"] and user.get("role") != "admin":
                raise HTTPException(403, "Bu konu kilitli")
            
            cid = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO forum_comments VALUES (?,?,?,?,?,?,NULL,0,0)",
                (cid, req.topic_id, user["sub"], req.content.strip(), req.parent_id, now)
            )
            conn.execute("UPDATE forum_topics SET reply_count=reply_count+1, updated_at=? WHERE id=?",
                        (now, req.topic_id))
            conn.commit()
            
            # Konu sahibine bildirim (online ise)
            if topic["user_id"] != user["sub"]:
                sender = conn.execute("SELECT username,avatar FROM users WHERE id=?", (user["sub"],)).fetchone()
                topic_data = conn.execute("SELECT title FROM forum_topics WHERE id=?", (req.topic_id,)).fetchone()
                await ws_manager.send_private(topic["user_id"], {
                    "type": "notification",
                    "subtype": "new_comment",
                    "topic_id": req.topic_id,
                    "topic_title": topic_data["title"] if topic_data else "",
                    "from_username": sender["username"] if sender else "?",
                    "from_avatar": sender["avatar"] if sender else "?",
                    "created_at": now
                })
            return {"id": cid, "created_at": now}
        finally:
            conn.close()
    
    @app.post("/social/forum/like/{target_type}/{target_id}")
    async def toggle_like(target_type: str, target_id: str, user=Depends(get_current_user)):
        if target_type not in ("topic", "comment"):
            raise HTTPException(400, "Gecersiz hedef tipi")
        
        conn = get_db()
        try:
            lid = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()
            existing = conn.execute(
                "SELECT id FROM likes WHERE user_id=? AND target_type=? AND target_id=?",
                (user["sub"], target_type, target_id)
            ).fetchone()
            
            if existing:
                conn.execute("DELETE FROM likes WHERE id=?", (existing["id"],))
                delta = -1
                liked = False
            else:
                conn.execute("INSERT INTO likes VALUES (?,?,?,?,?)",
                            (lid, user["sub"], target_type, target_id, now))
                delta = 1
                liked = True
            
            table = "forum_topics" if target_type == "topic" else "forum_comments"
            conn.execute(f"UPDATE {table} SET like_count=like_count+? WHERE id=?",
                        (delta, target_id))
            conn.commit()
            return {"liked": liked, "delta": delta}
        finally:
            conn.close()
    
    # ─── WEBSOCKET ────────────────────────────────────────────
    @app.websocket("/social/ws/{token}")
    async def websocket_endpoint(websocket: WebSocket, token: str):
        payload = verify_token(token)
        if not payload:
            await websocket.close(code=4001)
            return
        
        uid = payload["sub"]
        username = payload["username"]
        
        # DB'den kullanici bilgisi al
        conn = get_db()
        u = conn.execute("SELECT display_name, avatar FROM users WHERE id=?", (uid,)).fetchone()
        conn.close()
        
        display_name = u["display_name"] if u else username
        avatar = u["avatar"] if u else "?"
        
        await ws_manager.connect(websocket, uid, username)
        
        # Online oldugunu odaya bildir
        await ws_manager.broadcast_room("genel", {
            "type": "user_joined",
            "user_id": uid,
            "username": username,
            "display_name": display_name,
            "avatar": avatar,
            "room": "genel"
        }, exclude=uid)
        
        # Hos geldin
        await websocket.send_json({
            "type": "connected",
            "user_id": uid,
            "username": username,
            "online_users": ws_manager.get_room_users("genel"),
            "room": "genel"
        })
        
        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type", "chat")
                
                if msg_type == "chat":
                    room = data.get("room", "genel")
                    content = data.get("content", "").strip()
                    if not content or len(content) > MAX_MSG_LEN:
                        continue
                    
                    # Odaya katil
                    if room != ws_manager.connections.get(uid, {}).get("room"):
                        await ws_manager.join_room(uid, room)
                    
                    # Kaydet
                    conn = get_db()
                    mid = str(uuid.uuid4())
                    now = datetime.utcnow().isoformat()
                    conn.execute("INSERT INTO chat_messages VALUES (?,?,?,?,?,NULL,0)",
                                (mid, room, uid, content, now))
                    conn.commit()
                    conn.close()
                    
                    # Odaya yayinla
                    await ws_manager.broadcast_room(room, {
                        "type": "chat",
                        "id": mid,
                        "room": room,
                        "user_id": uid,
                        "username": username,
                        "display_name": display_name,
                        "avatar": avatar,
                        "content": content,
                        "created_at": now
                    })
                
                elif msg_type == "join_room":
                    room = data.get("room", "genel")
                    await ws_manager.join_room(uid, room)
                    members = ws_manager.get_room_users(room)
                    await websocket.send_json({
                        "type": "room_joined",
                        "room": room,
                        "members": members
                    })
                    # Odaya bildir
                    await ws_manager.broadcast_room(room, {
                        "type": "user_joined",
                        "username": username, "display_name": display_name,
                        "avatar": avatar, "room": room
                    }, exclude=uid)
                
                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                
                elif msg_type == "typing":
                    room = data.get("room", "genel")
                    await ws_manager.broadcast_room(room, {
                        "type": "typing",
                        "username": username,
                        "room": room
                    }, exclude=uid)
        
        except WebSocketDisconnect:
            pass
        finally:
            room = ws_manager.connections.get(uid, {}).get("room", "genel")
            ws_manager.disconnect(uid)
            await ws_manager.broadcast_room(room, {
                "type": "user_left",
                "username": username,
                "room": room
            })

# ─── ISTATISTIK ────────────────────────────────────────────────
def _register_stats_route(app):
    @app.get("/social/stats")
    async def social_stats():
        conn = get_db()
        try:
            users = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
            topics = conn.execute("SELECT COUNT(*) FROM forum_topics WHERE is_deleted=0").fetchone()[0]
            comments = conn.execute("SELECT COUNT(*) FROM forum_comments WHERE is_deleted=0").fetchone()[0]
            messages = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE is_deleted=0").fetchone()[0]
            return {
                "users": users, "topics": topics,
                "comments": comments, "messages": messages,
                "online": len(ws_manager.connections)
            }
        finally:
            conn.close()
