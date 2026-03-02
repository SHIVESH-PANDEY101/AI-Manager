"""
NEXUS AI Manager - Complete Backend
FastAPI + SQLite + JWT Auth
Run: pip install fastapi uvicorn python-jose passlib[bcrypt] python-multipart aiofiles jinja2 httpx
Then: uvicorn main:app --reload
"""

from fastapi import FastAPI, Request, Depends, HTTPException, status, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
#from passlib.context import CryptContext
from pwdlib import PasswordHash
from jose import JWTError, jwt
import sqlite3, json, httpx, os, uuid, asyncio
from contextlib import contextmanager
from dotenv import load_dotenv
load_dotenv()

# ══════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════
SECRET_KEY    = "nexus-super-secret-key-change-in-production-2024"
ALGORITHM     = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

GEMINI_API_KEY = "AIzaSyBDrWKvtUekr67S_Xx8NQwFW5uyj0PWKSM"
GEMINI_URL    = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
DB_PATH = "nexus.db"
#pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
pwd_ctx = PasswordHash.recommended()

def _safe_password(password: str) -> str:
    """Bcrypt max 72 bytes — truncate for Python 3.13 compatibility"""
    return password.encode("utf-8")[:72].decode("utf-8", errors="ignore")

# ── Gemini API call with auto-retry on rate limit ──
async def call_gemini(payload: dict, timeout: int = 30) -> dict:
    """Call Gemini API with automatic retry on 429 rate limit errors."""
    for attempt in range(3):
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(GEMINI_URL, json=payload)
            result = resp.json()
            if resp.status_code == 429:
                # Get retry delay from response or default to 10s
                retry_after = 10
                try:
                    details = result.get("error", {}).get("details", [])
                    for d in details:
                        for v in d.get("violations", []):
                            rt = v.get("quotaValue", "")
                            if rt: retry_after = min(float(str(rt).split(".")[0]) + 2, 30)
                except: pass
                print(f"[NEXUS AI] Rate limited. Waiting {retry_after}s before retry {attempt+1}/3...")
                await asyncio.sleep(retry_after)
                continue
            return result
    return {"error": {"message": "Rate limit exceeded after 3 retries. Please wait a moment and try again."}}

app = FastAPI(title="NEXUS AI Manager")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")
#templates = Jinja2Templates(directory="templates")
templates = Jinja2Templates(directory="nexus-ai-manager-FINAL/nexus/templates")

# ══════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            email       TEXT UNIQUE NOT NULL,
            password    TEXT NOT NULL,
            role        TEXT DEFAULT 'customer',
            plan        TEXT DEFAULT 'free',
            plan_expiry TEXT,
            avatar      TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            last_login  TEXT,
            is_active   INTEGER DEFAULT 1,
            ai_calls_today INTEGER DEFAULT 0,
            ai_calls_reset TEXT DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS events (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            title       TEXT NOT NULL,
            date        TEXT NOT NULL,
            time        TEXT,
            category    TEXT DEFAULT 'Personal',
            duration    REAL DEFAULT 1,
            location    TEXT,
            description TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            title       TEXT NOT NULL,
            description TEXT,
            category    TEXT DEFAULT 'Personal',
            priority    TEXT DEFAULT 'med',
            status      TEXT DEFAULT 'todo',
            due_date    TEXT,
            project     TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS habits (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            name        TEXT NOT NULL,
            icon        TEXT DEFAULT '✅',
            category    TEXT DEFAULT 'Health',
            frequency   TEXT DEFAULT 'daily',
            streak      INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS habit_logs (
            id          TEXT PRIMARY KEY,
            habit_id    TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            date        TEXT NOT NULL,
            done        INTEGER DEFAULT 0,
            FOREIGN KEY(habit_id) REFERENCES habits(id)
        );

        CREATE TABLE IF NOT EXISTS goals (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            title       TEXT NOT NULL,
            description TEXT,
            category    TEXT DEFAULT 'Personal',
            icon        TEXT DEFAULT '🎯',
            color       TEXT DEFAULT '#3b82f6',
            target_date TEXT,
            progress    INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS notes (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            title       TEXT DEFAULT '',
            content     TEXT DEFAULT '',
            tags        TEXT DEFAULT '[]',
            pinned      INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            name        TEXT NOT NULL,
            type        TEXT NOT NULL,
            amount      REAL NOT NULL,
            category    TEXT DEFAULT 'Other',
            date        TEXT DEFAULT (date('now')),
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS chat_history (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS billing (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            plan         TEXT NOT NULL,
            amount       REAL NOT NULL,
            status       TEXT DEFAULT 'pending',
            payment_method TEXT DEFAULT 'card',
            invoice_id   TEXT,
            created_at   TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS plans (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            price_month REAL NOT NULL,
            price_year  REAL NOT NULL,
            ai_calls    INTEGER DEFAULT 100,
            features    TEXT DEFAULT '[]',
            is_popular  INTEGER DEFAULT 0
        );
        """)

        # Seed plans
        existing = db.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        if existing == 0:
            plans = [
                (str(uuid.uuid4()), "Free",    0,     0,     20,  json.dumps(["20 AI calls/day","5 Events","10 Tasks","3 Habits","Basic Analytics"]), 0),
                (str(uuid.uuid4()), "Pro",      9.99,  99.99, 200, json.dumps(["200 AI calls/day","Unlimited Events","Unlimited Tasks","Unlimited Habits","Advanced Analytics","Priority Support","Export Data","Calendar Sync"]), 1),
                (str(uuid.uuid4()), "Team",     29.99, 299.99,1000, json.dumps(["1000 AI calls/day","Everything in Pro","5 Team Members","Admin Dashboard","API Access","Custom AI Prompts","White Label","24/7 Support"]), 0),
            ]
            db.executemany("INSERT INTO plans VALUES (?,?,?,?,?,?,?)", plans)

        # Seed admin
        admin_exists = db.execute("SELECT id FROM users WHERE email='admin@nexus.ai'").fetchone()
        if not admin_exists:
            db.execute("INSERT INTO users (id,name,email,password,role,plan) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), "Admin", "admin@nexus.ai",
                 pwd_ctx.hash("admin123"), "admin", "team"))

        # Seed demo user
        demo_exists = db.execute("SELECT id FROM users WHERE email='demo@nexus.ai'").fetchone()
        if not demo_exists:
            uid = str(uuid.uuid4())
            db.execute("INSERT INTO users (id,name,email,password,role,plan) VALUES (?,?,?,?,?,?)",
                (uid, "Alex Demo", "demo@nexus.ai", pwd_ctx.hash("demo123"), "customer", "pro"))
            # Seed demo data
            today = datetime.now().strftime('%Y-%m-%d')
            db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)", [
                (str(uuid.uuid4()), uid, "Team Standup",        today, "09:00", "Work",     0.5, "Zoom",     "", datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Dentist Appointment", today, "11:00", "Health",   1.0, "Clinic",   "", datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Lunch with Sara",     today, "13:00", "Social",   1.0, "Downtown", "", datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Project Review",      today, "15:00", "Work",     1.5, "Office",   "", datetime.now().isoformat()),
            ])
            db.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)", [
                (str(uuid.uuid4()), uid, "Design landing page mockups", "",  "Work",     "high", "todo",       today, "Design", datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Review Q4 budget report",     "",  "Work",     "high", "inprogress", today, "Finance",datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Fix login page bug",          "",  "Work",     "med",  "inprogress", today, "Dev",    datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Read Atomic Habits Ch.5",     "",  "Personal", "low",  "todo",       today, "",       datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Deploy API update",           "",  "Work",     "high", "done",       today, "Dev",    datetime.now().isoformat()),
            ])
            db.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)", [
                (str(uuid.uuid4()), uid, "Monthly Salary",   "income",  5500, "Salary",        today, datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Groceries",        "expense",  120, "Food",          today, datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Netflix",          "expense",   15, "Entertainment", today, datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Gym Membership",   "expense",   40, "Health",        today, datetime.now().isoformat()),
                (str(uuid.uuid4()), uid, "Freelance Project","income",   800, "Investment",    today, datetime.now().isoformat()),
            ])

# ══════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════
def hash_password(password: str) -> str:
    return pwd_ctx.hash(_safe_password(password))

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(_safe_password(plain), hashed)

def create_token(data: dict) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

def get_current_user(request: Request):
    token = request.cookies.get("nexus_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE id=?", (payload.get("sub"),)).fetchone()
        return dict(user) if user else None

def require_user(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user

def require_admin(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user

# ══════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════
class EventCreate(BaseModel):
    title: str; date: str; time: str = ""; category: str = "Personal"
    duration: float = 1; location: str = ""; description: str = ""

class TaskCreate(BaseModel):
    title: str; description: str = ""; category: str = "Personal"
    priority: str = "med"; status: str = "todo"; due_date: str = ""; project: str = ""

class HabitCreate(BaseModel):
    name: str; icon: str = "✅"; category: str = "Health"; frequency: str = "daily"

class GoalCreate(BaseModel):
    title: str; description: str = ""; category: str = "Personal"
    icon: str = "🎯"; color: str = "#3b82f6"; target_date: str = ""

class NoteCreate(BaseModel):
    title: str = ""; content: str = ""; tags: List[str] = []

class NoteUpdate(BaseModel):
    title: str = ""; content: str = ""; tags: List[str] = []

class TransactionCreate(BaseModel):
    name: str; type: str; amount: float; category: str = "Other"; date: str = ""

class ChatMessage(BaseModel):
    message: str

class AIRequest(BaseModel):
    prompt: str; context: str = ""

class TaskStatusUpdate(BaseModel):
    status: str

class GoalProgressUpdate(BaseModel):
    progress: int

# ══════════════════════════════════════════
# PAGES — AUTH
# ══════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard" if user["role"]=="customer" else "/admin")
    return RedirectResponse("/login")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard" if user["role"]=="customer" else "/admin")
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "default_tab": "register"})

@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    with get_db() as db:
        plans = [dict(p) for p in db.execute("SELECT * FROM plans").fetchall()]
        for p in plans:
            p["features"] = json.loads(p["features"])
    user = get_current_user(request)
    return templates.TemplateResponse("pricing.html", {"request": request, "plans": plans, "user": user})

# ══════════════════════════════════════════
# PAGES — CUSTOMER
# ══════════════════════════════════════════
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    if user["role"] == "admin": return RedirectResponse("/admin")
    with get_db() as db:
        today = datetime.now().strftime('%Y-%m-%d')
        events = [dict(e) for e in db.execute("SELECT * FROM events WHERE user_id=? AND date=? ORDER BY time", (user["id"], today)).fetchall()]
        tasks  = [dict(t) for t in db.execute("SELECT * FROM tasks  WHERE user_id=? AND status!='done' ORDER BY priority DESC LIMIT 5", (user["id"],)).fetchall()]
        habits = [dict(h) for h in db.execute("SELECT * FROM habits WHERE user_id=? LIMIT 4", (user["id"],)).fetchall()]
        goals  = [dict(g) for g in db.execute("SELECT * FROM goals  WHERE user_id=? ORDER BY progress DESC LIMIT 3", (user["id"],)).fetchall()]
        task_done  = db.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status='done'", (user["id"],)).fetchone()[0]
        task_total = db.execute("SELECT COUNT(*) FROM tasks WHERE user_id=?", (user["id"],)).fetchone()[0]
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user,
        "events": events, "tasks": tasks, "habits": habits, "goals": goals,
        "task_done": task_done, "task_total": task_total, "today": today})

@app.get("/calendar", response_class=HTMLResponse)
async def calendar(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    with get_db() as db:
        events = [dict(e) for e in db.execute("SELECT * FROM events WHERE user_id=? ORDER BY date,time", (user["id"],)).fetchall()]
    return templates.TemplateResponse("calendar.html", {"request": request, "user": user, "events": json.dumps(events), "page": "calendar"})

@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    with get_db() as db:
        tasks = [dict(t) for t in db.execute("SELECT * FROM tasks WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()]
    return templates.TemplateResponse("tasks.html", {"request": request, "user": user, "tasks": json.dumps(tasks), "page": "tasks"})

@app.get("/habits", response_class=HTMLResponse)
async def habits_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    with get_db() as db:
        habits = [dict(h) for h in db.execute("SELECT * FROM habits WHERE user_id=?", (user["id"],)).fetchall()]
        today = datetime.now().strftime('%Y-%m-%d')
        for h in habits:
            logs = db.execute("SELECT * FROM habit_logs WHERE habit_id=? ORDER BY date DESC LIMIT 30", (h["id"],)).fetchall()
            h["history"] = [dict(l) for l in logs]
            h["today_done"] = any(l["date"]==today and l["done"] for l in logs)
    return templates.TemplateResponse("habits.html", {"request": request, "user": user, "habits": json.dumps(habits), "page": "habits"})

@app.get("/goals", response_class=HTMLResponse)
async def goals_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    with get_db() as db:
        goals = [dict(g) for g in db.execute("SELECT * FROM goals WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()]
    return templates.TemplateResponse("goals.html", {"request": request, "user": user, "goals": json.dumps(goals), "page": "goals"})

@app.get("/notes", response_class=HTMLResponse)
async def notes_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    with get_db() as db:
        notes = [dict(n) for n in db.execute("SELECT * FROM notes WHERE user_id=? ORDER BY pinned DESC, updated_at DESC", (user["id"],)).fetchall()]
        for n in notes: n["tags"] = json.loads(n["tags"])
    return templates.TemplateResponse("notes.html", {"request": request, "user": user, "notes": json.dumps(notes), "page": "notes"})

@app.get("/budget", response_class=HTMLResponse)
async def budget_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    with get_db() as db:
        transactions = [dict(t) for t in db.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()]
    return templates.TemplateResponse("budget.html", {"request": request, "user": user, "transactions": json.dumps(transactions), "page": "budget"})

@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    with get_db() as db:
        task_done  = db.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status='done'", (user["id"],)).fetchone()[0]
        task_total = db.execute("SELECT COUNT(*) FROM tasks WHERE user_id=?", (user["id"],)).fetchone()[0]
        habit_count= db.execute("SELECT COUNT(*) FROM habits WHERE user_id=?", (user["id"],)).fetchone()[0]
        goal_avg   = db.execute("SELECT AVG(progress) FROM goals WHERE user_id=?", (user["id"],)).fetchone()[0] or 0
        income  = db.execute("SELECT SUM(amount) FROM transactions WHERE user_id=? AND type='income'", (user["id"],)).fetchone()[0] or 0
        expense = db.execute("SELECT SUM(amount) FROM transactions WHERE user_id=? AND type='expense'", (user["id"],)).fetchone()[0] or 0
    return templates.TemplateResponse("analytics.html", {"request": request, "user": user,
        "task_done": task_done, "task_total": task_total, "habit_count": habit_count,
        "goal_avg": round(goal_avg,1), "income": income, "expense": expense})

@app.get("/billing", response_class=HTMLResponse)
async def billing_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    with get_db() as db:
        plans   = [dict(p) for p in db.execute("SELECT * FROM plans").fetchall()]
        for p in plans: p["features"] = json.loads(p["features"])
        history = [dict(b) for b in db.execute("SELECT * FROM billing WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()]
    return templates.TemplateResponse("billing.html", {"request": request, "user": user, "plans": plans, "history": history})

@app.get("/ai", response_class=HTMLResponse)
async def ai_redirect(request: Request):
    return RedirectResponse("/chat")

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    with get_db() as db:
        history = [dict(h) for h in db.execute("SELECT * FROM chat_history WHERE user_id=? ORDER BY created_at DESC LIMIT 50", (user["id"],)).fetchall()]
        history.reverse()
    return templates.TemplateResponse("chat.html", {"request": request, "user": user, "history": history})

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    return templates.TemplateResponse("settings.html", {"request": request, "user": user})

# ══════════════════════════════════════════
# PAGES — ADMIN
# ══════════════════════════════════════════
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin": return RedirectResponse("/login")
    with get_db() as db:
        total_users  = db.execute("SELECT COUNT(*) FROM users WHERE role='customer'").fetchone()[0]
        active_users = db.execute("SELECT COUNT(*) FROM users WHERE role='customer' AND is_active=1").fetchone()[0]
        pro_users    = db.execute("SELECT COUNT(*) FROM users WHERE plan='pro'").fetchone()[0]
        team_users   = db.execute("SELECT COUNT(*) FROM users WHERE plan='team'").fetchone()[0]
        total_events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        total_tasks  = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        total_revenue= db.execute("SELECT SUM(amount) FROM billing WHERE status='paid'").fetchone()[0] or 0
        users        = [dict(u) for u in db.execute("SELECT id,name,email,role,plan,created_at,last_login,is_active FROM users ORDER BY created_at DESC LIMIT 20").fetchall()]
        recent_billing=[dict(b) for b in db.execute("""
            SELECT b.*,u.name,u.email FROM billing b
            JOIN users u ON b.user_id=u.id
            ORDER BY b.created_at DESC LIMIT 10""").fetchall()]
    return templates.TemplateResponse("admin.html", {"request": request, "user": user,
        "total_users":total_users, "active_users":active_users, "pro_users":pro_users,
        "team_users":team_users, "total_events":total_events, "total_tasks":total_tasks,
        "total_revenue":total_revenue, "users":users, "recent_billing":recent_billing})

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin": return RedirectResponse("/login")
    with get_db() as db:
        users = [dict(u) for u in db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()]
    return templates.TemplateResponse("admin_users.html", {"request": request, "user": user, "users": users})

@app.get("/admin/billing", response_class=HTMLResponse)
async def admin_billing(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin": return RedirectResponse("/login")
    with get_db() as db:
        billing = [dict(b) for b in db.execute("""
            SELECT b.*,u.name,u.email FROM billing b
            JOIN users u ON b.user_id=u.id
            ORDER BY b.created_at DESC""").fetchall()]
        revenue = db.execute("SELECT SUM(amount) FROM billing WHERE status='paid'").fetchone()[0] or 0
        pending = db.execute("SELECT SUM(amount) FROM billing WHERE status='pending'").fetchone()[0] or 0
    return templates.TemplateResponse("admin_billing.html", {"request": request, "user": user,
        "billing": billing, "revenue": revenue, "pending": pending})

# ══════════════════════════════════════════
# API — AUTH
# ══════════════════════════════════════════
@app.post("/api/auth/login")
async def api_login(email: str = Form(...), password: str = Form(...)):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user or not verify_password(password, user["password"]):
        raise HTTPException(400, "Invalid email or password")
    if not user["is_active"]:
        raise HTTPException(400, "Account suspended. Contact support.")
    with get_db() as db:
        db.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.now().isoformat(), user["id"]))
    token = create_token({"sub": user["id"], "role": user["role"]})
    redirect_url = "/admin" if user["role"]=="admin" else "/dashboard"
    response = RedirectResponse(redirect_url, status_code=303)
    response.set_cookie("nexus_token", token, httponly=True, max_age=60*60*24*7, samesite="lax")
    return response

@app.post("/api/auth/register")
async def api_register(name: str=Form(...), email: str=Form(...), password: str=Form(...), plan: str=Form("free")):
    with get_db() as db:
        existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            raise HTTPException(400, "Email already registered")
        uid = str(uuid.uuid4())
        db.execute("INSERT INTO users (id,name,email,password,role,plan) VALUES (?,?,?,?,?,?)",
            (uid, name, email, hash_password(password), "customer", plan))
    token = create_token({"sub": uid, "role": "customer"})
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("nexus_token", token, httponly=True, max_age=60*60*24*7, samesite="lax")
    return response

@app.get("/api/auth/logout")
async def logout():
    response = RedirectResponse("/login")
    response.delete_cookie("nexus_token")
    return response

# ══════════════════════════════════════════
# API — EVENTS
# ══════════════════════════════════════════
@app.post("/api/events")
async def create_event(data: EventCreate, request: Request):
    user = require_user(request)
    eid = str(uuid.uuid4())
    with get_db() as db:
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid, user["id"], data.title, data.date, data.time, data.category, data.duration, data.location, data.description, datetime.now().isoformat()))
    return {"id": eid, "message": "Event created"}

@app.get("/api/events")
async def get_events(request: Request):
    user = require_user(request)
    with get_db() as db:
        events = [dict(e) for e in db.execute("SELECT * FROM events WHERE user_id=? ORDER BY date,time", (user["id"],)).fetchall()]
    return events

@app.delete("/api/events/{eid}")
async def delete_event(eid: str, request: Request):
    user = require_user(request)
    with get_db() as db:
        db.execute("DELETE FROM events WHERE id=? AND user_id=?", (eid, user["id"]))
    return {"message": "Deleted"}

# ══════════════════════════════════════════
# API — TASKS
# ══════════════════════════════════════════
@app.post("/api/tasks")
async def create_task(data: TaskCreate, request: Request):
    user = require_user(request)
    tid = str(uuid.uuid4())
    with get_db() as db:
        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, user["id"], data.title, data.description, data.category, data.priority, data.status, data.due_date, data.project, datetime.now().isoformat()))
    return {"id": tid, "message": "Task created"}

@app.get("/api/tasks")
async def get_tasks(request: Request):
    user = require_user(request)
    with get_db() as db:
        tasks = [dict(t) for t in db.execute("SELECT * FROM tasks WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()]
    return tasks

@app.patch("/api/tasks/{tid}/status")
async def update_task_status(tid: str, data: TaskStatusUpdate, request: Request):
    user = require_user(request)
    with get_db() as db:
        db.execute("UPDATE tasks SET status=? WHERE id=? AND user_id=?", (data.status, tid, user["id"]))
    return {"message": "Updated"}

@app.delete("/api/tasks/{tid}")
async def delete_task(tid: str, request: Request):
    user = require_user(request)
    with get_db() as db:
        db.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (tid, user["id"]))
    return {"message": "Deleted"}

# ══════════════════════════════════════════
# API — HABITS
# ══════════════════════════════════════════
@app.post("/api/habits")
async def create_habit(data: HabitCreate, request: Request):
    user = require_user(request)
    hid = str(uuid.uuid4())
    with get_db() as db:
        db.execute("INSERT INTO habits VALUES (?,?,?,?,?,?,?,?)",
            (hid, user["id"], data.name, data.icon, data.category, data.frequency, 0, datetime.now().isoformat()))
    return {"id": hid}

@app.post("/api/habits/{hid}/toggle")
async def toggle_habit(hid: str, request: Request):
    user = require_user(request)
    today = datetime.now().strftime('%Y-%m-%d')
    with get_db() as db:
        existing = db.execute("SELECT * FROM habit_logs WHERE habit_id=? AND user_id=? AND date=?", (hid, user["id"], today)).fetchone()
        if existing:
            new_done = 0 if existing["done"] else 1
            db.execute("UPDATE habit_logs SET done=? WHERE id=?", (new_done, existing["id"]))
        else:
            db.execute("INSERT INTO habit_logs VALUES (?,?,?,?,?)", (str(uuid.uuid4()), hid, user["id"], today, 1))
            new_done = 1
        if new_done:
            db.execute("UPDATE habits SET streak=streak+1 WHERE id=?", (hid,))
        else:
            db.execute("UPDATE habits SET streak=MAX(0,streak-1) WHERE id=?", (hid,))
    return {"done": bool(new_done)}

@app.delete("/api/habits/{hid}")
async def delete_habit(hid: str, request: Request):
    user = require_user(request)
    with get_db() as db:
        db.execute("DELETE FROM habits WHERE id=? AND user_id=?", (hid, user["id"]))
        db.execute("DELETE FROM habit_logs WHERE habit_id=? AND user_id=?", (hid, user["id"]))
    return {"message": "Deleted"}

# ══════════════════════════════════════════
# API — GOALS
# ══════════════════════════════════════════
@app.post("/api/goals")
async def create_goal(data: GoalCreate, request: Request):
    user = require_user(request)
    gid = str(uuid.uuid4())
    with get_db() as db:
        db.execute("INSERT INTO goals VALUES (?,?,?,?,?,?,?,?,?,?)",
            (gid, user["id"], data.title, data.description, data.category, data.icon, data.color, data.target_date, 0, datetime.now().isoformat()))
    return {"id": gid}

@app.patch("/api/goals/{gid}/progress")
async def update_goal_progress(gid: str, data: GoalProgressUpdate, request: Request):
    user = require_user(request)
    with get_db() as db:
        db.execute("UPDATE goals SET progress=? WHERE id=? AND user_id=?", (data.progress, gid, user["id"]))
    return {"message": "Updated"}

@app.delete("/api/goals/{gid}")
async def delete_goal(gid: str, request: Request):
    user = require_user(request)
    with get_db() as db:
        db.execute("DELETE FROM goals WHERE id=? AND user_id=?", (gid, user["id"]))
    return {"message": "Deleted"}

# ══════════════════════════════════════════
# API — NOTES
# ══════════════════════════════════════════
@app.post("/api/notes")
async def create_note(data: NoteCreate, request: Request):
    user = require_user(request)
    nid = str(uuid.uuid4())
    with get_db() as db:
        db.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?)",
            (nid, user["id"], data.title, data.content, json.dumps(data.tags), 0, datetime.now().isoformat(), datetime.now().isoformat()))
    return {"id": nid}

@app.put("/api/notes/{nid}")
async def update_note(nid: str, data: NoteUpdate, request: Request):
    user = require_user(request)
    with get_db() as db:
        db.execute("UPDATE notes SET title=?,content=?,tags=?,updated_at=? WHERE id=? AND user_id=?",
            (data.title, data.content, json.dumps(data.tags), datetime.now().isoformat(), nid, user["id"]))
    return {"message": "Updated"}

@app.delete("/api/notes/{nid}")
async def delete_note(nid: str, request: Request):
    user = require_user(request)
    with get_db() as db:
        db.execute("DELETE FROM notes WHERE id=? AND user_id=?", (nid, user["id"]))
    return {"message": "Deleted"}

# ══════════════════════════════════════════
# API — TRANSACTIONS
# ══════════════════════════════════════════
@app.post("/api/transactions")
async def create_transaction(data: TransactionCreate, request: Request):
    user = require_user(request)
    tid = str(uuid.uuid4())
    with get_db() as db:
        db.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
            (tid, user["id"], data.name, data.type, data.amount, data.category,
             data.date or datetime.now().strftime('%Y-%m-%d'), datetime.now().isoformat()))
    return {"id": tid}

@app.delete("/api/transactions/{tid}")
async def delete_transaction(tid: str, request: Request):
    user = require_user(request)
    with get_db() as db:
        db.execute("DELETE FROM transactions WHERE id=? AND user_id=?", (tid, user["id"]))
    return {"message": "Deleted"}

# ══════════════════════════════════════════
# API — AI (Gemini) ← ONLY THIS SECTION WAS FIXED
# ══════════════════════════════════════════
@app.post("/api/ai/chat")
async def ai_chat(data: ChatMessage, request: Request):
    user = require_user(request)
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        today = datetime.now().strftime('%Y-%m-%d')
        if u["ai_calls_reset"] != today:
            db.execute("UPDATE users SET ai_calls_today=0, ai_calls_reset=? WHERE id=?", (today, user["id"]))
            calls_today = 0
        else:
            calls_today = u["ai_calls_today"]
        limits = {"free": 20, "pro": 200, "team": 1000}
        limit = limits.get(u["plan"], 20)
        if calls_today >= limit:
            return JSONResponse({"error": f"Daily AI limit reached ({limit} calls). Upgrade your plan!"}, status_code=429)
        history = [dict(h) for h in db.execute("SELECT role,content FROM chat_history WHERE user_id=? ORDER BY created_at ASC LIMIT 10", (user["id"],)).fetchall()]
        today_str = datetime.now().strftime('%Y-%m-%d')
        events = [dict(e) for e in db.execute("SELECT title,time,category FROM events WHERE user_id=? AND date=?", (user["id"], today_str)).fetchall()]
        tasks  = [dict(t) for t in db.execute("SELECT title,priority,status FROM tasks WHERE user_id=? AND status!='done' LIMIT 5", (user["id"],)).fetchall()]
        habits = [dict(h) for h in db.execute("SELECT name,streak FROM habits WHERE user_id=?", (user["id"],)).fetchall()]
        goals  = [dict(g) for g in db.execute("SELECT title,progress FROM goals WHERE user_id=?", (user["id"],)).fetchall()]

    # ── THE FIX: Single clean message — no system_instruction, no role alternation issues ──
    full_prompt = f"""You are NEXUS, a friendly AI life manager for {u['name']}.
Today: {datetime.now().strftime('%A, %B %d, %Y')} | Plan: {u['plan'].title()}

User's data:
- Events today: {json.dumps(events) if events else 'none'}
- Active tasks: {json.dumps(tasks) if tasks else 'none'}
- Habits: {json.dumps(habits) if habits else 'none'}
- Goals: {json.dumps(goals) if goals else 'none'}

Recent conversation:
{chr(10).join([f"{'User' if m['role']=='user' else 'NEXUS'}: {m['content']}" for m in history[-6:]]) if history else 'No previous messages'}

User says: {data.message}

Reply as NEXUS. Be warm, helpful, specific. Keep response under 150 words."""

    payload = {
        "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": 0.8, "maxOutputTokens": 512}
    }

    result = await call_gemini(payload, timeout=30)
    print(f"[NEXUS AI] Chat response received")

    # Parse response safely
    try:
        ai_text = result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        error = result.get("error", {}).get("message", "")
        if "quota" in error.lower() or "429" in error.lower() or "rate" in error.lower():
            ai_text = "⏳ AI is rate limited. Please wait 30 seconds and try again. (Free tier: 15 requests/minute)"
        else:
            ai_text = f"⚠️ {error}" if error else "Sorry, I couldn't get a response. Please try again."
        print(f"[NEXUS AI] Error: {json.dumps(result)[:300]}")

    with get_db() as db:
        db.execute("INSERT INTO chat_history VALUES (?,?,?,?,?)", (str(uuid.uuid4()), user["id"], "user", data.message, datetime.now().isoformat()))
        db.execute("INSERT INTO chat_history VALUES (?,?,?,?,?)", (str(uuid.uuid4()), user["id"], "model", ai_text, datetime.now().isoformat()))
        db.execute("UPDATE users SET ai_calls_today=ai_calls_today+1 WHERE id=?", (user["id"],))

    return {"reply": ai_text, "calls_used": calls_today + 1, "calls_limit": limit}

@app.post("/api/ai/briefing")
async def ai_briefing(request: Request):
    user = require_user(request)
    with get_db() as db:
        today = datetime.now().strftime('%Y-%m-%d')
        events = db.execute("SELECT title FROM events WHERE user_id=? AND date=?", (user["id"], today)).fetchall()
        tasks  = db.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status!='done'", (user["id"],)).fetchone()[0]
        habits = [dict(h) for h in db.execute("SELECT name,streak FROM habits WHERE user_id=?", (user["id"],)).fetchall()]
    top = max(habits, key=lambda h: h["streak"], default={"name":"None","streak":0})
    prompt = f"""Generate a warm, motivating 60-word morning briefing for {user['name']}.
Today: {datetime.now().strftime('%A, %B %d')}
Events: {len(events)} ({', '.join([e[0] for e in events]) or 'none'})
Pending tasks: {tasks}
Best streak: {top['name']} at {top['streak']} days
Be uplifting and specific. End with one power tip for the day."""
    result = await call_gemini({"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
    text = result.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","Good morning! Have a productive day! 🚀")
    return {"briefing": text}

@app.post("/api/ai/suggest-tasks")
async def ai_suggest_tasks(request: Request):
    user = require_user(request)
    with get_db() as db:
        goals = [dict(g) for g in db.execute("SELECT title FROM goals WHERE user_id=?", (user["id"],)).fetchall()]
        tasks = [dict(t) for t in db.execute("SELECT title FROM tasks WHERE user_id=? AND status!='done' LIMIT 5", (user["id"],)).fetchall()]
    prompt = f"""Based on goals: {[g['title'] for g in goals]} and current tasks: {[t['title'] for t in tasks]}, suggest exactly 3 specific, actionable tasks for today. Return ONLY a JSON array of strings, like: ["Task 1","Task 2","Task 3"]"""
    result = await call_gemini({"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
    text = result.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","[]")
    try:
        import re
        arr = re.search(r'\[.*?\]', text, re.DOTALL)
        tasks_list = json.loads(arr.group()) if arr else []
    except:
        tasks_list = []
    return {"tasks": tasks_list}

# ══════════════════════════════════════════
# API — BILLING
# ══════════════════════════════════════════
@app.post("/api/billing/upgrade")
async def upgrade_plan(request: Request, plan: str = Form(...)):
    user = require_user(request)
    with get_db() as db:
        p = db.execute("SELECT * FROM plans WHERE name=?", (plan.title(),)).fetchone()
        if not p:
            raise HTTPException(400, "Invalid plan")
        bid = str(uuid.uuid4())
        invoice_id = "INV-" + datetime.now().strftime('%Y%m%d') + "-" + bid[:8].upper()
        db.execute("INSERT INTO billing VALUES (?,?,?,?,?,?,?,?)",
            (bid, user["id"], plan, p["price_month"], "paid", "card", invoice_id, datetime.now().isoformat()))
        db.execute("UPDATE users SET plan=? WHERE id=?", (plan.lower(), user["id"]))
    return RedirectResponse("/billing?success=1", status_code=303)

@app.post("/api/billing/cancel")
async def cancel_plan(request: Request):
    user = require_user(request)
    with get_db() as db:
        db.execute("UPDATE users SET plan='free' WHERE id=?", (user["id"],))
    return RedirectResponse("/billing?cancelled=1", status_code=303)

# ══════════════════════════════════════════
# API — ADMIN ACTIONS
# ══════════════════════════════════════════
@app.post("/api/admin/users/{uid}/toggle")
async def admin_toggle_user(uid: str, request: Request):
    admin = get_current_user(request)
    if not admin or admin["role"] != "admin":
        raise HTTPException(403, "Forbidden")
    with get_db() as db:
        user = db.execute("SELECT is_active FROM users WHERE id=?", (uid,)).fetchone()
        if user:
            db.execute("UPDATE users SET is_active=? WHERE id=?", (0 if user["is_active"] else 1, uid))
    return {"message": "Updated"}

@app.post("/api/admin/users/{uid}/plan")
async def admin_change_plan(uid: str, request: Request, plan: str = Form(...)):
    admin = get_current_user(request)
    if not admin or admin["role"] != "admin":
        raise HTTPException(403, "Forbidden")
    with get_db() as db:
        db.execute("UPDATE users SET plan=? WHERE id=?", (plan, uid))
    return RedirectResponse("/admin/users", status_code=303)

@app.post("/api/settings/update")
async def update_settings(request: Request, name: str = Form(...)):
    user = require_user(request)
    with get_db() as db:
        db.execute("UPDATE users SET name=? WHERE id=?", (name, user["id"]))
    return RedirectResponse("/settings?saved=1", status_code=303)

@app.post("/api/settings/password")
async def change_password(request: Request, current: str = Form(...), new_pass: str = Form(...)):
    user = require_user(request)
    with get_db() as db:
        u = db.execute("SELECT password FROM users WHERE id=?", (user["id"],)).fetchone()
        if not verify_password(current, u["password"]):
            raise HTTPException(400, "Current password incorrect")
        db.execute("UPDATE users SET password=? WHERE id=?", (hash_password(new_pass), user["id"]))
    return RedirectResponse("/settings?saved=1", status_code=303)

# ══════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════
@app.on_event("startup")
async def startup():
    init_db()
    print("✅ NEXUS AI Manager started!")
    print("📧 Admin:    admin@nexus.ai / admin123")
    print("👤 Demo:     demo@nexus.ai  / demo123")
    print("🌐 Open:     http://localhost:8000")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)