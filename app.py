"""TutorBook server: Flask + SQLite. Run: python app.py"""
import csv, hmac, io, json, os, secrets, sqlite3, uuid
from werkzeug.utils import secure_filename
from flask import Flask, Response, g, jsonify, redirect, request, send_file, send_from_directory, session

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "tutorbook.db")
PASSWORD = os.environ.get("TUTORBOOK_PASSWORD", "")  

def secret():
    p = os.path.join(BASE, ".secret")
    if not os.path.exists(p):
        open(p, "w").write(secrets.token_hex(32))
    return open(p).read()

app = Flask(__name__, static_folder=None)
app.secret_key = secret()
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True, PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 90)

SCHEMA = """
create table if not exists batches(id text primary key, name text, rate real, time text, days text);
create table if not exists students(id text primary key, name text, parent text, phone text, batch_id text, rate real);
create table if not exists sessions(id text primary key, batch_id text, date text, type text);
create table if not exists attendance(session_id text, student_id text, status text, primary key(session_id, student_id));
create table if not exists payments(id text primary key, student_id text, amount real, date text);
create table if not exists settings(key text primary key, value text);
create table if not exists courses(id text primary key, name text, subject text, description text, topics text, batch_ids text);
create table if not exists feedback(id text primary key, student_id text, date text, rating integer, text text);
create table if not exists materials(id text primary key, title text, subject text, batch_id text, url text, file text, fname text, note text, date text);
"""

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()

def num(x):
    try:
        return float(x) if x not in ("", None) else None
    except ValueError:
        return None

def read_state(c):
    q = lambda sql: [dict(r) for r in c.execute(sql)]
    att = {}
    for a in q("select * from attendance"):
        att.setdefault(a["session_id"], {})[a["student_id"]] = a["status"]
    return {
        "batches": [dict(id=b["id"], name=b["name"], rate=b["rate"], time=b["time"], days=json.loads(b["days"] or "[]")) for b in q("select * from batches")],
        "students": [dict(id=s["id"], name=s["name"], parent=s["parent"], phone=s["phone"], batchId=s["batch_id"], rate=s["rate"]) for s in q("select * from students")],
        "sessions": [dict(id=s["id"], batchId=s["batch_id"], date=s["date"], type=s["type"], att=att.get(s["id"], {})) for s in q("select * from sessions order by date")],
        "payments": [dict(id=p["id"], sid=p["student_id"], amt=p["amount"], date=p["date"]) for p in q("select * from payments")],
        "courses": [dict(id=x["id"], name=x["name"], subject=x["subject"], desc=x["description"], topics=json.loads(x["topics"] or "[]"), batchIds=json.loads(x["batch_ids"] or "[]")) for x in q("select * from courses")],
        "feedback": [dict(id=x["id"], sid=x["student_id"], date=x["date"], rating=x["rating"], text=x["text"]) for x in q("select * from feedback")],
        "materials": [dict(id=x["id"], title=x["title"], subject=x["subject"], batchId=x["batch_id"], url=x["url"], file=x["file"], fname=x["fname"], note=x["note"], date=x["date"]) for x in q("select * from materials")],
        "set": {r["key"]: json.loads(r["value"]) for r in q("select * from settings")},
    }

def write_state(c, s):
    with c:  
        for t in ("batches", "students", "sessions", "attendance", "payments", "settings", "courses", "feedback", "materials"):
            c.execute(f"delete from {t}")
        c.executemany("insert into batches values(?,?,?,?,?)", [(b["id"], b["name"], num(b.get("rate")) or 0, b.get("time", ""), json.dumps(b.get("days", []))) for b in s.get("batches", [])])
        c.executemany("insert into students values(?,?,?,?,?,?)", [(x["id"], x["name"], x.get("parent", ""), x.get("phone", ""), x.get("batchId"), num(x.get("rate"))) for x in s.get("students", [])])
        for x in s.get("sessions", []):
            c.execute("insert into sessions values(?,?,?,?)", (x["id"], x["batchId"], x["date"], x["type"]))
            c.executemany("insert into attendance values(?,?,?)", [(x["id"], k, v) for k, v in x.get("att", {}).items()])
        c.executemany("insert into payments values(?,?,?,?)", [(p["id"], p["sid"], p["amt"], p["date"]) for p in s.get("payments", [])])
        c.executemany("insert into courses values(?,?,?,?,?,?)", [(x["id"], x["name"], x.get("subject", ""), x.get("desc", ""), json.dumps(x.get("topics", [])), json.dumps(x.get("batchIds", []))) for x in s.get("courses", [])])
        c.executemany("insert into feedback values(?,?,?,?,?)", [(x["id"], x["sid"], x["date"], x.get("rating"), x["text"]) for x in s.get("feedback", [])])
        c.executemany("insert into materials values(?,?,?,?,?,?,?,?,?)", [(x["id"], x["title"], x.get("subject", ""), x.get("batchId", ""), x.get("url", ""), x.get("file", ""), x.get("fname", ""), x.get("note", ""), x.get("date", "")) for x in s.get("materials", [])])
        c.executemany("insert into settings values(?,?)", [(k, json.dumps(v)) for k, v in s.get("set", {}).items()])

def month_bills(st, month):
    """Same rules as the app: present = billed; absent billed unless turned off;
    excused and holidays free; unpaid earlier months carry forward."""
    batch_rate = {b["id"]: b["rate"] for b in st["batches"]}
    bill_absent = st["set"].get("billAbsent", True)
    months = sorted({x["date"][:7] for x in st["sessions"]})

    def counts(s, m):
        c = dict(P=0, A=0, E=0, H=0)
        for x in st["sessions"]:
            if not x["date"].startswith(m):
                continue
            if x["type"] == "holiday":
                c["H"] += x["batchId"] == s["batchId"]
            elif x["att"].get(s["id"]):
                c[x["att"][s["id"]]] += 1
        return c

    rows = []
    for s in st["students"]:
        r = s["rate"] or batch_rate.get(s["batchId"]) or 0
        billed = lambda m: r * (lambda c: c["P"] + (c["A"] if bill_absent else 0))(counts(s, m))
        paid = lambda ok: sum(p["amt"] for p in st["payments"] if p["sid"] == s["id"] and ok(p["date"][:7]))
        prev = max(0, sum(billed(m) for m in months if m < month) - paid(lambda m: m < month))
        cur, got, c = billed(month), paid(lambda m: m == month), counts(s, month)
        b = next((b["name"] for b in st["batches"] if b["id"] == s["batchId"]), "")
        rows.append([s["name"], s["parent"], s["phone"], b, r, c["P"], c["A"], c["E"], c["H"], cur, prev, got, max(0, prev + cur - got)])
    return rows

@app.before_request
def guard():
    if not PASSWORD or request.endpoint in ("login",) or session.get("ok"):
        return None
    return (jsonify(error="login required"), 401) if request.path.startswith("/api") else redirect("/login")

LOGIN = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>TutorBook login</title>
<body style="font:16px system-ui;max-width:340px;margin:20vh auto;padding:16px"><h2>TutorBook</h2>
<form method=post><input type=password name=password placeholder=Password autofocus style="width:100%;padding:10px;font-size:16px">
<button style="margin-top:10px;padding:10px 16px;font-size:16px">Log in</button></form><p style=color:#c23a2b>{err}</p></body>"""

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", ""), PASSWORD):
            session.permanent = True
            session["ok"] = True
            return redirect("/")
        return LOGIN.format(err="Wrong password."), 401
    return LOGIN.format(err="")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/")
def index():
    return send_from_directory(os.path.join(BASE, "static"), "index.html")

@app.route("/api/state", methods=["GET", "PUT"])
def state():
    if request.method == "PUT":
        write_state(db(), request.get_json(force=True))
        return jsonify(ok=True)
    return jsonify(read_state(db()))

@app.route("/export/fees.csv")
def export_fees():
    month = request.args.get("month", "")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Student", "Parent", "Phone", "Batch", "Rate", "Attended", "Absent", "Excused", "Holidays", "This month", "Previous dues", "Paid", "Pending"])
    w.writerows(month_bills(read_state(db()), month))
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=fees-{month}.csv"})

UPLOADS = os.path.join(BASE, "uploads")
os.makedirs(UPLOADS, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  

@app.route("/api/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="no file"), 400
    stored = f"{uuid.uuid4().hex[:10]}-{secure_filename(f.filename) or 'file'}"
    f.save(os.path.join(UPLOADS, stored))
    return jsonify(file=stored, name=f.filename)

@app.route("/files/<path:name>")
def files(name):
    risky = name.rsplit(".", 1)[-1].lower() in {"html", "htm", "svg", "xml", "js"} 
    return send_from_directory(UPLOADS, name, as_attachment=risky)

@app.route("/backup.db")
def backup():
    return send_file(DB, as_attachment=True, download_name="tutorbook-backup.db")

sqlite3.connect(DB).executescript(SCHEMA)

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))