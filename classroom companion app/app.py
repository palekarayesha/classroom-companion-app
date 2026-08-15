import os
import re
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import openai
import webbrowser
from threading import Timer
import sys

# ---------- PATH HELPERS FOR PYINSTALLER ----------
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# ---------- CONFIG ----------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "notes_metadata.db")
print("Database path used:", DB_PATH)
STUDENTS_XLSX = os.path.join(DATA_DIR, "students.xlsx")
TEACHERS_XLSX = os.path.join(DATA_DIR, "teachers.xlsx")
ADMINS_XLSX = os.path.join(DATA_DIR, "admins.xlsx")

for d in [DATA_DIR, UPLOAD_DIR, os.path.join(BASE_DIR, "static", "images")]:
    os.makedirs(d, exist_ok=True)

ALLOWED_EXT = {"pdf", "ppt", "pptx"}
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "change_this_to_random_secret_string")
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB

# OpenAI key (optional)
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
if OPENAI_KEY:
    openai.api_key = OPENAI_KEY

# ---------- UTILITIES / VALIDATIONS ----------
def valid_name(name):
    return bool(re.fullmatch(r"[A-Za-z ]{2,100}", name.strip()))

def valid_phone(phone):
    return bool(re.fullmatch(r"\d{10}", phone.strip()))

def valid_email(email):
    return bool(re.fullmatch(r"[^@]+@[^@]+\.[^@]+", email.strip()))

def valid_password(password):
    if len(password) < 8: return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[a-z]", password): return False
    if not re.search(r"\d", password): return False
    if not re.search(r"[^\w\s]", password): return False
    return True

def valid_roll(value):
    return bool(re.fullmatch(r"\d+", value.strip()))

def valid_enroll(val):
    return bool(re.fullmatch(r"[A-Za-z0-9]+", val.strip()))

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

# ---------- DB INITIALIZATION ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT,
        filename TEXT,
        uploader_email TEXT,
        uploaded_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        subtitle TEXT,
        created_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        subtitle TEXT,
        date TEXT,
        created_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS quizzes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        data TEXT,
        created_by TEXT,
        created_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS quiz_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_id INTEGER,
        student_email TEXT,
        score INTEGER,
        attempted_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS attendance_uploads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        uploaded_at TEXT,
        uploaded_by TEXT
    )""")
    conn.commit()
    conn.close()

init_db()

# ---------- EXCEL SEEDING ----------
def seed_excel(path, cols):
    if not os.path.exists(path):
        df = pd.DataFrame(columns=cols)
        df.to_excel(path, index=False)

seed_excel(STUDENTS_XLSX, ["full_name", "contact", "email", "gender", "roll_no", "enrollment_no", "password_hash"])
seed_excel(TEACHERS_XLSX, ["full_name", "email", "password_hash"])
seed_excel(ADMINS_XLSX, ["full_name", "email", "password_hash"])

def ensure_seed_admin():
    df = pd.read_excel(ADMINS_XLSX)
    if df.empty:
        df = pd.DataFrame([{"full_name":"Admin User","email":"admin@git.edu.in","password_hash":generate_password_hash("Admin@123")}])
        df.to_excel(ADMINS_XLSX, index=False)
ensure_seed_admin()

# ---------- USER STORAGE ----------
def add_user(role, userdict):
    if role == 'student':
        path = STUDENTS_XLSX
    elif role == 'teacher':
        path = TEACHERS_XLSX
    else:
        path = ADMINS_XLSX
    df = pd.read_excel(path)
    df = pd.concat([df, pd.DataFrame([userdict])], ignore_index=True)
    df.to_excel(path, index=False)

def find_user(email):
    for path, role in [(STUDENTS_XLSX,'student'), (TEACHERS_XLSX,'teacher'), (ADMINS_XLSX,'admin')]:
        df = pd.read_excel(path)
        if 'email' in df.columns and email in df['email'].astype(str).values:
            row = df[df['email']==email].iloc[0].to_dict()
            row['role'] = role
            return row
    return None

# ---------- AUTH HELPERS ----------
def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*a, **kw):
        if 'user' not in session:
            flash("Please login first.", "warning")
            return redirect(url_for('login'))
        return fn(*a, **kw)
    return wrapper

def admin_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*a, **kw):
        if 'user' not in session or session['user'].get('role') != 'admin':
            flash("Admin access required.", "danger")
            return redirect(url_for('home'))
        return fn(*a, **kw)
    return wrapper

# ---------- ROUTES ----------
@app.route("/")
@app.route("/welcome")
def welcome():
    return render_template("welcome.html")
@app.route("/register", methods=['GET','POST'])
def register():
    if request.method == 'POST':
        role = request.form.get('role')
        full_name = request.form.get('full_name','').strip()
        contact = request.form.get('contact','').strip()
        email = request.form.get('email','').strip().lower()
        gender = request.form.get('gender','')
        roll_no = request.form.get('roll_no','').strip()
        enrollment_no = request.form.get('enrollment_no','').strip()
        password = request.form.get('password','')
        confirm = request.form.get('confirm','')

        errors = []

        # Name validation
        if not valid_name(full_name):
            errors.append("Name must contain only alphabets and spaces (2-100 characters).")

        # Contact validation - only required/validated for students
        if role == 'student':
            if not contact:
                errors.append("Contact number is required for students.")
            elif not re.fullmatch(r"\d{10}", contact):
                errors.append("Contact must be exactly 10 digits (numbers only).")

        # Email validation - must be a gmail address
        if not email:
            errors.append("Email is required.")
        else:
            # basic format check
            if not valid_email(email):
                errors.append("Email address format looks invalid.")
            # enforce Gmail
            elif not email.lower().endswith("@gmail.com"):
                errors.append("Email must be a Gmail address ending with @gmail.com.")

        # Password validation
        if not valid_password(password):
            errors.append("Password must be at least 8 characters with an uppercase, lowercase, digit and special character.")
        if password != confirm:
            errors.append("Password and confirm password do not match.")

        # Student-specific roll/enrollment validations
        if role == 'student':
            if not roll_no:
                errors.append("Roll number is required for students.")
            elif not valid_roll(roll_no):
                errors.append("Roll number must contain only digits (no letters, spaces or special characters).")
            if not enrollment_no:
                errors.append("Enrollment number is required for students.")
            elif not valid_enroll(enrollment_no):
                errors.append("Enrollment number cannot contain special characters or spaces.")

        # Check duplicate email across all user sheets
        existing = find_user(email) if email else None
        if existing:
            errors.append("An account with this email already exists. Please login or use a different email.")

        # If any errors, re-render template with errors and prefilled form data
        if errors:
            return render_template("register.html", errors=errors, form=request.form)

        # All validations passed -> create user with hashed password
        password_hash = generate_password_hash(password)
        if role == 'student':
            userdict = {
                "full_name": full_name,
                "contact": contact,
                "email": email,
                "gender": gender,
                "roll_no": roll_no,
                "enrollment_no": enrollment_no,
                "password_hash": password_hash
            }
            add_user('student', userdict)
        elif role == 'teacher':
            userdict = {"full_name": full_name, "email": email, "password_hash": password_hash}
            add_user('teacher', userdict)
        else:  # admin
            userdict = {"full_name": full_name, "email": email, "password_hash": password_hash}
            add_user('admin', userdict)

        flash("Registration successful. Please login.", "success")
        return redirect(url_for('login'))

    # GET
    return render_template("register.html")


@app.route("/login", methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        user = find_user(email)
        if not user:
            flash("No user found with this email. Please register.", "danger")
            return redirect(url_for('register'))
        if not check_password_hash(user.get('password_hash',''), password):
            flash("Invalid credentials. Please check email/password.", "danger")
            return redirect(url_for('login'))

        session['user'] = {"email": user['email'], "name": user.get('full_name','User'), "role": user.get('role')}
        flash(f"Welcome back, {session['user']['name']}!", "success")
        return redirect(url_for('home'))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop('user', None)
    flash("Logged out.", "info")
    return redirect(url_for('welcome'))

@app.route("/home")
@login_required
def home():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,title,subtitle,created_at FROM notices ORDER BY id DESC")
    notices = cur.fetchall()
    cur.execute("SELECT id,title,subtitle,date,created_at FROM events ORDER BY id DESC")
    events = cur.fetchall()
    conn.close()
    return render_template("home.html", user=session['user'], notices=notices, events=events)

@app.route("/notes")
@login_required
def notes():
    subjects = ["TCS","SE","DWM","CN","IP"]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,subject,filename,uploader_email,uploaded_at FROM notes ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return render_template("notes.html", subjects=subjects, rows=rows)

@app.route("/notes/subject/<subj>")
@login_required
def subject(subj):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,filename,uploader_email,uploaded_at FROM notes WHERE subject = ?", (subj,))
    rows = cur.fetchall()
    conn.close()
    return render_template("subject.html", subject=subj, rows=rows)

@app.route("/upload_note", methods=['POST'])
@login_required
def upload_note():
    subject = request.form.get('subject')
    file = request.files.get('file')
    if not file or file.filename == "":
        flash("No file selected.", "danger")
        return redirect(url_for('notes'))
    if not allowed_file(file.filename):
        flash("Unsupported file type. Allowed: pdf, ppt, pptx", "danger")
        return redirect(url_for('notes'))
    filename = secure_filename(f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{file.filename}")
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO notes (subject,filename,uploader_email,uploaded_at) VALUES (?,?,?,?)",
                (subject, filename, session['user']['email'], datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    flash("Note uploaded successfully.", "success")
    return redirect(url_for('subject', subj=subject))

@app.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route("/profile", methods=['GET','POST'])
@login_required
def profile():
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        avatar = request.files.get('avatar')
        if name and valid_name(name):
            # update session only; to persist across accounts you could write back to excel
            session['user']['name'] = name
            flash("Name updated.", "success")
        if avatar and avatar.filename != "":
            fn = secure_filename(f"avatar_{session['user']['email']}_{avatar.filename}")
            avatar.save(os.path.join(BASE_DIR, "static", "images", fn))
            session['user']['avatar'] = url_for('static', filename=f"images/{fn}")
            flash("Avatar updated.", "success")
    return render_template("profile.html", user=session['user'])

@app.route("/admin", methods=['GET','POST'])
@login_required
@admin_required
def admin_panel():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'notice':
            title = request.form.get('title'); subtitle = request.form.get('subtitle')
            cur.execute("INSERT INTO notices (title,subtitle,created_at) VALUES (?,?,?)",
                        (title, subtitle, datetime.utcnow().isoformat()))
        elif action == 'event':
            title = request.form.get('title'); subtitle = request.form.get('subtitle'); date = request.form.get('date')
            cur.execute("INSERT INTO events (title,subtitle,date,created_at) VALUES (?,?,?,?)",
                        (title, subtitle, date or "", datetime.utcnow().isoformat()))
        conn.commit()
        flash("Saved.", "success")
    cur.execute("SELECT id,title,subtitle,created_at FROM notices ORDER BY id DESC")
    notices = cur.fetchall()
    cur.execute("SELECT id,title,subtitle,date,created_at FROM events ORDER BY id DESC")
    events = cur.fetchall()
    conn.close()
    return render_template("admin.html", notices=notices, events=events)

@app.route("/delete_note/<int:note_id>", methods=['POST'])
@login_required
@admin_required
def delete_note(note_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT filename FROM notes WHERE id = ?", (note_id,))
    row = cur.fetchone()
    if row:
        filename = row[0]
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        except:
            pass
        cur.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
    conn.close()
    flash("Note deleted.", "info")
    return redirect(url_for('notes'))

@app.route("/attendance", methods=['GET','POST'])
@login_required
@admin_required
def attendance():
    if request.method == 'POST':
        f = request.files.get('attendance_file')
        if not f or f.filename == "":
            flash("No file selected.", "danger")
            return redirect(url_for('attendance'))
        fname = secure_filename(f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{f.filename}")
        path = os.path.join(DATA_DIR, fname)
        f.save(path)  # saved but students won't have route to download
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT INTO attendance_uploads (filename,uploaded_at,uploaded_by) VALUES (?,?,?)",
                    (fname, datetime.utcnow().isoformat(), session['user']['email']))
        conn.commit()
        conn.close()
        flash("Attendance uploaded.", "success")
        return redirect(url_for('attendance'))
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,filename,uploaded_at,uploaded_by FROM attendance_uploads ORDER BY id DESC")
    uploads = cur.fetchall()
    conn.close()
    return render_template("attendance.html", uploads=uploads)

@app.route("/assignments", methods=['GET', 'POST'])
@login_required
def assignments():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            details TEXT,
            due TEXT,
            filename TEXT,
            uploaded_by TEXT,
            uploaded_at TEXT
        )
    """)
    conn.commit()

    role = session['user']['role']

    # Handle uploads (only teacher/admin)
    if request.method == 'POST' and role in ['teacher', 'admin']:
        title = request.form.get('title', '').strip()
        details = request.form.get('details', '').strip()
        due = request.form.get('due', '').strip()
        file = request.files.get('file')

        if not title or not details or not due:
            flash("All fields are required.", "danger")
        elif not file or file.filename == "":
            flash("Please attach an assignment file.", "danger")
        else:
            ext = file.filename.rsplit(".", 1)[-1].lower()
            if ext not in {"pdf", "docx"}:
                flash("Only PDF or DOCX files are allowed.", "danger")
            else:
                filename = secure_filename(f"assignment_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                filepath = os.path.join(UPLOAD_DIR, filename)
                file.save(filepath)

                cur.execute(
                    "INSERT INTO assignments (title, details, due, filename, uploaded_by, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (title, details, due, filename, session['user']['email'], datetime.utcnow().isoformat())
                )
                conn.commit()
                flash("Assignment uploaded successfully.", "success")

    # Delete (only teacher/admin)
    delete_id = request.args.get('delete')
    if delete_id and role in ['teacher', 'admin']:
        cur.execute("SELECT filename FROM assignments WHERE id=?", (delete_id,))
        row = cur.fetchone()
        if row:
            try:
                os.remove(os.path.join(UPLOAD_DIR, row[0]))
            except Exception:
                pass
        cur.execute("DELETE FROM assignments WHERE id=?", (delete_id,))
        conn.commit()
        flash("Assignment deleted.", "info")

    # Fetch all assignments
    cur.execute("SELECT id, title, details, due, filename, uploaded_by, uploaded_at FROM assignments ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    return render_template("assignments.html", assignments=rows, role=role)


# Quizzes: create (teachers) and take (students once)
@app.route("/quizzes", methods=['GET','POST'])
@login_required
def quizzes():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if request.method == 'POST':
        title = request.form.get('title')
        data = request.form.get('data')  # optionally JSON or plain text
        # If teacher asked to auto-generate using AI:
        gen = request.form.get('generate_ai')
        if gen and OPENAI_KEY:
            topic = request.form.get('ai_topic','')
            try:
                prompt = f"Create 5 multiple choice questions (4 options each) on: {topic}. Return as JSON list of objects with 'q','options' and 'answer' fields."
                resp = openai.ChatCompletion.create(
                    model="gpt-4o-mini",
                    messages=[{"role":"system","content":"You are a helpful quiz generator."},
                              {"role":"user","content":prompt}],
                    temperature=0.2
                )
                ai_text = resp.choices[0].message.content
                data = ai_text
            except Exception as e:
                data = f"(AI error: {e})"
        cur.execute("INSERT INTO quizzes (title,data,created_by,created_at) VALUES (?,?,?,?)",
                    (title, data or "", session['user']['email'], datetime.utcnow().isoformat()))
        conn.commit()
        flash("Quiz created.", "success")
    cur.execute("SELECT id,title,created_by,created_at FROM quizzes ORDER BY id DESC")
    quizzes_list = cur.fetchall()
    conn.close()
    return render_template("quizzes.html", quizzes=quizzes_list, user=session['user'])

@app.route("/take_quiz/<int:quiz_id>", methods=['GET','POST'])
@login_required
def take_quiz(quiz_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if request.method == 'POST':
        # Simple scoring: teacher may provide scoring mechanism; here we accept numeric score field
        score = int(request.form.get('score', 0))
        cur.execute("SELECT COUNT(*) FROM quiz_attempts WHERE quiz_id = ? AND student_email = ?", (quiz_id, session['user']['email']))
        if cur.fetchone()[0] > 0:
            flash("You have already attempted this quiz.", "warning")
        else:
            cur.execute("INSERT INTO quiz_attempts (quiz_id,student_email,score,attempted_at) VALUES (?,?,?,?)",
                        (quiz_id, session['user']['email'], score, datetime.utcnow().isoformat()))
            conn.commit()
            flash("Quiz submitted.", "success")
        conn.close()
        return redirect(url_for('quizzes'))
    cur.execute("SELECT id,title,data FROM quizzes WHERE id = ?", (quiz_id,))
    quiz = cur.fetchone()
    conn.close()
    if not quiz:
        flash("Quiz not found.", "danger")
        return redirect(url_for('quizzes'))
    return render_template("take_quiz.html", quiz=quiz)

# AI Chat endpoint
@app.route("/api/ai_chat", methods=['POST'])
@login_required
def ai_chat():
    question = request.json.get('q','')
    if not OPENAI_KEY:
        return jsonify({"answer":"AI not configured. Set OPENAI_API_KEY environment variable to enable real AI."})
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":"You are a helpful student assistant for a college app."},
                      {"role":"user","content":question}],
            temperature=0.2
        )
        answer = resp.choices[0].message.content
    except Exception as e:
        answer = f"(AI error: {e})"
    return jsonify({"answer": answer})

# Timetable page with university exam timetable
@app.route("/timetable")
@login_required
def timetable():
    uni_tt = [
        ("2025-11-13", "Theoretical Computer Science (TCS)"),
        ("2025-11-14", "Software Engineering (SE)"),
        ("2025-11-17", "Computer Networks (CN)"),
        ("2025-11-19", "Data Warehousing & Mining (DWM)"),
        ("2025-11-21", "Internet Programming (IP)"),
    ]
    return render_template("timetable.html", uni_tt=uni_tt)

# Seed initial notice/event if none
def seed_notice_event():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM notices")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO notices (title,subtitle,created_at) VALUES (?,?,?)",
                    ("Sunday working", "21st September, that is Sunday, is working as a compensation of the holiday given on 6th September for Anand Chaturthi.", datetime.utcnow().isoformat()))
    cur.execute("SELECT COUNT(*) FROM events")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO events (title,subtitle,date,created_at) VALUES (?,?,?,?)",
                    ("Freshers Party", "Coming soon", "", datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
seed_notice_event()

# ---------- AUTO-OPEN BROWSER ----------
def open_browser():
    webbrowser.open("http://127.0.0.1:5000")

# ---------- RUN ----------
if __name__ == "__main__":
    # Open browser after 1 second
    Timer(1, open_browser).start()
    # Run Flask server with no console window in PyInstaller
    app.run(debug=True)

