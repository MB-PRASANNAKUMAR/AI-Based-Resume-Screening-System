from flask import Flask, render_template, request, redirect, session, send_file,flash,make_response,Response, url_for, Response
import sqlite3
import re
import pandas as pd
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import json
import os
import io
import time
from PyPDF2 import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import hashlib
import csv
from io import StringIO
from werkzeug.utils import secure_filename
from database import init_db
DB_NAME = "/tmp/resume_screening.db"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")
# ---------- FOLDERS ----------
RESUME_FOLDER = "/tmp/uploads"
os.makedirs(RESUME_FOLDER, exist_ok=True)

    
# ---------- DATABASE ----------
def db():
    con = sqlite3.connect(DB_NAME)
    con.row_factory = sqlite3.Row
    return con

# ---------- PDF TEXT EXTRACTION ----------
def extract_text(path):
    try:
        reader = PdfReader(path)
        text = ""
        for page in reader.pages:
            content = page.extract_text()
            if content:
                text += content + " "
        return text
    except Exception as e:
        return ""
    
# ---------- NORMALIZATION ----------
def normalize_text(text):
    if not text:
        return ""
    
    text = text.lower()
    text = re.sub(r'\S+@\S+', '', text)   # remove emails
    text = re.sub(r'http\S+', '', text)   # remove URLs
    text = re.sub(r'[^a-z0-9\s]', ' ', text)  # remove symbols
    text = " ".join(text.split())  # remove extra spaces
    
    return text

# ---------- JD MATCH ----------
def jd_match(resume, jd):
    resume = normalize_text(resume)
    jd = normalize_text(jd)

    if not resume or not jd:
        return 0.0

    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    
    try:
        vectors = vectorizer.fit_transform([resume, jd])
        score = cosine_similarity(vectors)[0][1] * 100
        return round(min(score, 100.0), 2)
    except Exception as e:
        return 0.0

def extract_skills(resume_text, skills_list):
    resume_text = normalize_text(resume_text)
    found = []

    for skill in skills_list:
        pattern = r'\b' + re.escape(skill.lower()) + r'\b'
        if re.search(pattern, resume_text):
            found.append(skill)

    return found

@app.route('/assessment/pass/<int:job_id>')
def assessment_pass(job_id):
    score = request.args.get('score',0)
    return render_template('pass_result.html', job_id=job_id, score=score)

@app.route('/assessment/fail/<int:job_id>')
def assessment_fail(job_id):
    score = request.args.get('score')
    return render_template('fail_result.html', job_id=job_id, score=score)

# ---------- SKILL MATCH ----------
def skill_score(resume, skills_str):
    resume = normalize_text(resume)

    if not skills_str:
        return 0.0

    required_skills = [s.strip().lower() for s in skills_str.split(",") if s.strip()]
    
    if not required_skills:
        return 0.0
    
    matched_count = 0
    
    for skill in required_skills:
        pattern = r'\b' + re.escape(skill) + r'\b'
        if re.search(pattern, resume):
            matched_count += 1
    
    score = (matched_count / len(required_skills)) * 100   
    return round(score, 2)

SAMPLES_FOLDER = "uploads/samples"

def load_sample_resumes():
    texts = []
    
    if not os.path.exists(SAMPLES_FOLDER):
        return texts
    
    for file in os.listdir(SAMPLES_FOLDER):
        if file.endswith(".pdf"):
            path = os.path.join(SAMPLES_FOLDER, file)
            text = extract_text(path)
            texts.append(normalize_text(text))
    
    return texts

# Load once when app starts
samples = load_sample_resumes()

# ---------- FORMAT CHECK ----------
def format_check(resume_text):
    resume_text = normalize_text(resume_text)

    patterns = {
        "education": r"(education|academic|qualification)",
        "experience": r"(experience|work history|employment|internship)",
        "skills": r"(skills|technologies|technical)",
        "projects": r"(projects|portfolio)",
        "contact": r"(contact|email|phone|linkedin)"
    }
    
    found = [
        section.capitalize()
        for section, regex in patterns.items()
        if re.search(regex, resume_text)
    ]

    # ---------- SIMILARITY ----------
    similarity_score = 0
    
    if samples:
        try:
            vectorizer = TfidfVectorizer(stop_words="english")
            vectors = vectorizer.fit_transform([resume_text] + samples)
            similarities = cosine_similarity(vectors[0:1], vectors[1:])
            similarity_score = similarities.max() * 100
        except Exception as e:
            print("Format similarity error:", e)

    # ---------- DECISION ----------
    if len(found) >= 4 and similarity_score >= 60:
        status = "Professional"
    elif len(found) >= 2 and similarity_score >= 40:
        status = "Basic"
    else:
        status = "Poor/Incomplete"

    return status, found, round(similarity_score, 2)

def analyze_resume(resume_text, jd_text, skills_required):
    resume_text = normalize_text(resume_text)
    jd_text = normalize_text(jd_text)

    jd = jd_match(resume_text, jd_text)
    skill = skill_score(resume_text, skills_required)
    format_status, sections, format_sim = format_check(resume_text)

    # 🎯 FINAL AI SCORE (weighted)
    final = (0.5 * jd) + (0.3 * skill) + (0.2 * format_sim)

    return {
        "jd_score": jd,
        "skill_score": skill,
        "format_score": format_sim,
        "format_status": format_status,
        "sections": sections,
        "final_score": round(final, 2)
    }

# --Main Program-- #
@app.route("/")
def home():
    return render_template("home.html")
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        role = session.get('role')
        if not role or role not in ['admin', 'head']:
            flash("Unauthorized Access.")
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    return render_template('admin.html')

@app.route('/admin-secure-gate')
def admin_login_gate():
    return render_template('login_admin.html')

@app.route("/register/admin", methods=["GET", "POST"])
def register_admin():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = hash_password(request.form["password"])
        con = db()
        cur = con.cursor()
        try:
            # Note: We set role to 'admin' here
            cur.execute(
                "INSERT INTO admins (name, email, password, role) VALUES (?, ?, ?, ?)",
                (name, email, password, "admin")
            )
            con.commit()
            flash("Admin Account created! Redirecting to Admin Login...", "success")
            return render_template("register_admin.html", success=True)
            
        except sqlite3.IntegrityError:
            flash("That email is already registered.", "danger")
        finally:
            con.close()
    return render_template("register_admin.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = generate_password_hash(request.form["password"])
        
        con = db()
        cur = con.cursor()
        try:
            cur.execute(
                "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
                (name, email, password, "user")
            )
            con.commit()
            flash("Account created! Redirecting to login...", "success")
            return render_template("register_user.html", success=True)
            
        except sqlite3.IntegrityError:
            flash("That email is already registered.", "danger")
        finally:
            con.close()
    return render_template("register_user.html")

@app.route("/login", methods=["GET", "POST"])
def user_login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        con = db()
        cur = con.cursor()
        user = cur.execute("""
            SELECT * FROM users 
            WHERE email=? AND role='user'
        """, (email,)).fetchone()
        con.close()
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["name"]
            session["role"] = user["role"]
            return redirect("/user")

        flash("❌ Invalid user credentials", "error")

    return render_template("login_user.html")

def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()

@app.route("/login/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form["password"]
        
        con = db()
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        
        # Fetch by email ONLY
        admin = cur.execute("""
            SELECT * FROM admins 
            WHERE email=? AND is_active=1
        """, (email,)).fetchone()
        con.close()

        # Verify password using hash
        if admin and admin["password"]==hash_password(password):
            # Login Success
            session.clear() 
            session["admin_id"] = admin["id"]
            session["role"] = admin["role"]
            
            if admin["role"] == "head":
                return redirect("/head/dashboard")
            return redirect(url_for("admin"))

        else:
            flash("❌ Authentication failed: Invalid email or password", "danger")

    return render_template("login_admin.html")

# ---------- ADMIN DASHBOARD ----------
@app.route("/admin", methods=["GET", "POST"])
def admin():
    if session.get("role") != "admin":
        return redirect("/")

    con = db()
    cur = con.cursor()

    if request.method == "POST":
        cur.execute(
            "INSERT INTO jobs (title,description,skills,openings,experience,status,admin_id) VALUES (?,?,?,?,?,?,?)",
            (
                request.form["title"],
                request.form["desc"],
                request.form["skills"],
                request.form["openings"],
                request.form["experience"],
                "Open",
                session['admin_id']
            )
        )
        con.commit()
        new_job_id = cur.lastrowid
        mcq_file = request.files.get('mcq_file')
        if mcq_file:
            stream = io.StringIO(mcq_file.stream.read().decode("UTF8"))
            reader = csv.DictReader(stream)

            for row in reader:
                options = json.dumps({
                    "A": row['option_a'],
                    "B": row['option_b'],
                    "C": row['option_c'],
                    "D": row['option_d']
                })

                cur.execute("""
                    INSERT INTO questions (job_id, question, options, correct_answer)
                    VALUES (?, ?, ?, ?)
                """, (new_job_id, row['question'], options, row['correct']))

        con.commit()
    jobs = cur.execute("""
    SELECT 
        jobs.id,
        jobs.title,
        jobs.description,
        jobs.skills,
        jobs.experience,
        jobs.openings,
        jobs.status,
        jobs.locked,
        COUNT(resumes.id) AS resume_count,
        COUNT(CASE WHEN resumes.result='Selected' THEN 1 END) AS selected_count,
        (jobs.openings - COUNT(CASE WHEN resumes.result='Selected' THEN 1 END)) AS remaining
    FROM jobs
    LEFT JOIN resumes ON jobs.id = resumes.job_id
    WHERE jobs.admin_id = ?
    GROUP BY jobs.id
    """, (session["admin_id"],)).fetchall()

    users = cur.execute("""
        SELECT 
            users.name,
            users.email,
            users.created_at,
        MAX(resumes.result) AS last_result
        FROM users
        LEFT JOIN resumes ON users.id = resumes.user_id
        LEFT JOIN jobs ON resumes.job_id = jobs.id
        WHERE users.role = 'user'
        AND jobs.admin_id = ?
        GROUP BY users.id
        ORDER BY users.created_at DESC
    """, (session["admin_id"],)).fetchall()

    total_resumes = cur.execute("SELECT COUNT(*) FROM resumes JOIN jobs ON resumes.job_id = jobs.id WHERE jobs.admin_id = ?",(session["admin_id"],)).fetchone()[0]

    today_uploads = cur.execute("""
        SELECT COUNT(*) FROM resumes
        JOIN jobs ON resumes.job_id = jobs.id
        WHERE DATE(resumes.created_at, 'localtime') = DATE('now', 'localtime')
        AND jobs.admin_id = ?
    """,(session["admin_id"],)).fetchone()[0]
    # ---------- ANALYTICS DATA ----------

    # Total selected / rejected
    status_counts = cur.execute("""
        SELECT result, COUNT(*) as count
        FROM resumes
        JOIN jobs ON resumes.job_id = jobs.id
        WHERE jobs.admin_id = ?
        GROUP BY result
    """,(session["admin_id"],)).fetchall()

    selected = sum(r["count"] for r in status_counts if r["result"] == "Selected")
    rejected = sum(r["count"] for r in status_counts if r["result"] == "Rejected")

    # Avg scores
    avg_scores = cur.execute("""
        SELECT 
            AVG(match_score) AS avg_match,
            AVG(skill_score) AS avg_skill
        FROM resumes
        JOIN jobs ON resumes.job_id = jobs.id
        WHERE jobs.admin_id = ?
    """,(session["admin_id"],)).fetchone()

    # Job-wise resume count
    job_resume_data = cur.execute("""
        SELECT j.title, COUNT(r.id) as count
        FROM jobs j
        LEFT JOIN resumes r ON j.id = r.job_id
        WHERE j.admin_id=?
        GROUP BY j.id
    """,(session["admin_id"],)).fetchall()

    job_labels = [j["title"] for j in job_resume_data]
    job_resume_counts = [j["count"] for j in job_resume_data]

    con.close()
    return render_template("admin.html", jobs=jobs, users=users, total=total_resumes, today_uploads=today_uploads, selected=selected, rejected=rejected, avg_match=round(avg_scores["avg_match"] or 0, 1), avg_skill=round(avg_scores["avg_skill"] or 0, 1), job_labels=job_labels, job_resume_counts=job_resume_counts)

def get_resume_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

@app.route('/assessment/<int:job_id>')
def assessment(job_id):
    con = db()
    # Fetch questions linked ONLY to this job_id
    mcqs_raw = con.execute("SELECT id, question, options FROM questions WHERE job_id = ?", (job_id,)).fetchall()
    
    questions = []
    for q in mcqs_raw:
        questions.append({
            "id": q["id"],
            "question": q["question"],
            "options": json.loads(q["options"]) # Converts string back to Dictionary
        })
    
    return render_template("assessment.html", questions=questions, job_id=job_id)

@app.route('/admin/bulk_upload/<int:job_id>', methods=['POST'])
def bulk_upload(job_id):
    file = request.files.get('assessment_csv')
    if not file:
        flash("No file uploaded", "danger")
        return redirect(url_for('admin'))

    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    reader = csv.DictReader(stream)
    
    con = db()
    cur = con.cursor()

    for row in reader:
        options = json.dumps({
            "A": row['option_a'], "B": row['option_b'], 
            "C": row['option_c'], "D": row['option_d']
        })
        cur.execute("""
            INSERT INTO questions (job_id, question, options, correct_answer)
            VALUES (?, ?, ?, ?)
        """, (job_id, row['question_or_title'], options, row['correct']))

    con.commit()
    con.close()
    flash("Assessment uploaded successfully!", "success")
    return redirect(url_for('admin'))

@app.route('/submit_assessment/<int:job_id>', methods=['POST'])
def submit_assessment(job_id):
    user_id = session.get("user_id")
    con = db()
    
    # 1. Fetch correct answers for this job from DB
    questions = con.execute("SELECT id, correct_answer FROM questions WHERE job_id = ?", (job_id,)).fetchall()
    
    score = 0
    total = len(questions)
    
    # 2. Compare user input to DB answers
    for q in questions:
        user_ans = request.form.get(f"mcq_{q['id']}") 
        if user_ans == q['correct_answer']:
            score += 1
    PASS_MARK = 80
    # Calculate percentage
    final_percentage = (score / total * 100) if total > 0 else 0

    # 3. Save result to history
    con.execute("""
        INSERT INTO resume_history (user_id, job_id, quiz_score, resume_path) 
        VALUES (?, ?, ?, ?)
    """, (user_id, job_id, final_percentage, "assessment_done.log"))
    final_percentage = (score / total * 100) if total > 0 else 0

    session['temp_quiz_score'] = final_percentage
    session['temp_job_id'] = job_id
    con.execute("""
        UPDATE resumes 
        SET mcq_completed = 1,
            mcq_score = ?,
            result = CASE 
                WHEN ? >= ? THEN 'Selected'
                ELSE 'Rejected'
            END
        WHERE user_id = ? AND job_id = ?
    """, (final_percentage, final_percentage, PASS_MARK, user_id, job_id))

    con.commit()
    con.close()

    if final_percentage >= PASS_MARK:
        return render_template(
            'thank_you.html',
            score=final_percentage,
            job_id=job_id,
            passed=True
        )
    else:
        return render_template(
            'assessment_failed.html',
            score=final_percentage,
            job_id=job_id,
            passed=False
        )
    
@app.route("/job_uploads_left/<int:job_id>")
def job_uploads_left(job_id):
    # Standardize: check both common session keys
    user_id = session.get("user_id")
    
    if not user_id:
        return {"remaining": 0}

    con = db()
    # Use Row factory so we can access by column name
    con.row_factory = sqlite3.Row 
    cur = con.cursor()

    MAX_UPLOADS = 5

    # Logic: Count how many rows exist for THIS user and THIS job
    cur.execute("""
        SELECT COUNT(*) as total
        FROM resumes
        WHERE user_id=? AND job_id=?
    """, (user_id, job_id))
    
    row = cur.fetchone()
    used = row['total'] if row else 0
    con.close()

    return {
        "remaining": max(0, MAX_UPLOADS - used)
    }

@app.route("/head/admin/<int:admin_id>")
def head_view_admin_jobs(admin_id):
    if session.get("role") != "head":
        return redirect("/")

    con = db()
    cur = con.cursor()

    admin = cur.execute("""
        SELECT name, email
        FROM admins
        WHERE id=? AND role='admin'
    """, (admin_id,)).fetchone()

    jobs = cur.execute("""
        SELECT
            jobs.title,
            jobs.experience,
            jobs.openings,
            jobs.status,
            COUNT(resumes.id) AS applicants,
            COUNT(CASE WHEN resumes.result='Selected' THEN 1 END) AS selected
        FROM jobs
        LEFT JOIN resumes ON jobs.id = resumes.job_id
        WHERE jobs.admin_id=?
        GROUP BY jobs.id
    """, (admin_id,)).fetchall()

    con.close()

    return render_template(
        "head_admin_jobs.html",
        admin=admin,
        jobs=jobs
    )

@app.route("/head/dashboard")
def head_dashboard():
    con=db()
    cur=con.cursor()
    if session.get("role") != "head":
        return redirect("/")

    stats = cur.execute("""
        SELECT
            admins.id,
            admins.name,
            admins.email,
            admins.is_active,
            COUNT(DISTINCT jobs.id) AS total_jobs,
            COUNT(resumes.id) AS applicants,
            COUNT(CASE WHEN resumes.result='Selected' THEN 1 END) AS selected
        FROM admins
        LEFT JOIN jobs ON jobs.admin_id = admins.id
        LEFT JOIN resumes ON resumes.job_id = jobs.id
        WHERE admins.role = 'admin'
        GROUP BY admins.id
    """).fetchall()
    labels = [a["name"] for a in stats]
    applicants = [a["applicants"] for a in stats]
    con.close()
    return render_template("head.html", stats = stats, labels = labels, applicants = applicants)



@app.route("/head/toggle_admin/<int:admin_id>")
def toggle_admin(admin_id):
    if session.get("role") != "head":
        return redirect("/")

    con = db()
    cur = con.cursor()

    # Toggle admin status
    cur.execute("""
        UPDATE admins
        SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END
        WHERE id=?
    """, (admin_id,))

    # Get new status
    is_active = cur.execute(
        "SELECT is_active FROM admins WHERE id=?",
        (admin_id,)
    ).fetchone()["is_active"]

    if is_active == 0:
        # 🔒 Disable admin → lock and close jobs
        cur.execute("""
            UPDATE jobs
            SET status='Closed', locked=1
            WHERE admin_id=?
        """, (admin_id,))
    else:
        # 🔓 Enable admin → unlock jobs (keep closed)
        cur.execute("""
            UPDATE jobs
            SET locked=0
            WHERE admin_id=?
        """, (admin_id,))

    con.commit()
    con.close()
    return redirect("/head/dashboard")


@app.route("/head/export")
def export_platform():
    if session.get("role") != "head":
        return redirect("/")

    con = db()
    cur = con.cursor()

    rows = cur.execute("""
        SELECT admins.name, jobs.title, resumes.result
        FROM resumes
        JOIN jobs ON resumes.job_id = jobs.id
        JOIN admins ON jobs.admin_id = admins.id
    """).fetchall()

    con.close()

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Admin","Job","Result"])
    for r in rows:
        writer.writerow(r)

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=platform.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route("/admin/edit_job/<int:job_id>", methods=["GET", "POST"])
def edit_job(job_id):
    if session.get("role") != "admin":
        return redirect("/")

    con = db()
    cur = con.cursor()

    if request.method == "POST":
        title = request.form["title"]
        desc = request.form["desc"]
        skills = request.form["skills"]
        openings = int(request.form["openings"])
        experience = request.form["experience"]

        # 🔍 Get already selected count
        selected_count = cur.execute("""
            SELECT COUNT(*)
            FROM resumes
            WHERE job_id=? AND result='Selected'
        """, (job_id,)).fetchone()[0]
        openings = abs(int(openings))
        # 🚫 BLOCK invalid update
        if openings < selected_count:
            flash(
                f"❌ Cannot set openings to {openings}. "
                f"{selected_count} candidates already selected.",
                "error"
            )
            return redirect(f"/admin/edit_job/{job_id}")

        # ✅ Auto-close if exactly filled
        status = "Closed" if openings == selected_count else "Open"

        cur.execute("""
            UPDATE jobs
            SET title=?, description=?, skills=?, openings=?, experience=?, status=?
            WHERE id=?
        """, (title, desc, skills, openings, experience, status, job_id))

        con.commit()
        con.close()
        return redirect("/admin")

    job = cur.execute("SELECT * FROM jobs WHERE id=? AND admin_id=?", (job_id,session["admin_id"])).fetchone()
    con.close()
    return render_template("edit_job.html", job=job)

@app.route("/admin/toggle_job/<int:job_id>")
def toggle_job(job_id):
    if session.get("role") != "admin":
        return redirect("/")

    con = db()
    cur = con.cursor()

    job = cur.execute("""
        SELECT 
            jobs.status,
            jobs.openings,
            jobs.locked,
            COUNT(CASE WHEN resumes.result='Selected' THEN 1 END) AS selected_count
        FROM jobs
        LEFT JOIN resumes ON jobs.id = resumes.job_id
        WHERE jobs.id=? AND jobs.admin_id=?
        GROUP BY jobs.id
    """, (job_id, session["admin_id"])).fetchone()

    # ❌ Job not found or not owned by admin
    if not job:
        con.close()
        flash("❌ Unauthorized job access", "error")
        return redirect("/admin")

    # 🔒 Job locked by Head Admin
    if job["locked"] == 1:
        con.close()
        flash("🔒 Job disabled by Head Admin", "error")
        return redirect("/admin")

    # 🚫 Job already filled
    if job["selected_count"] >= job["openings"]:
        con.close()
        flash("❌ Job already filled", "error")
        return redirect("/admin")

    # 🔁 Toggle status
    new_status = "Closed" if job["status"] == "Open" else "Open"

    cur.execute(
        "UPDATE jobs SET status=? WHERE id=?",
        (new_status, job_id)
    )
    con.commit()
    con.close()

    flash(f"✅ Job {new_status}", "success")
    return redirect("/admin")

# Route to show instructions
@app.route('/instructions/<int:job_id>')
def instructions(job_id):
    # This matches the <a href="/instructions/{{ job_id }}"> from your Success page
    return render_template('instructions.html', job_id=job_id)

# The updated Assessment route
# ---------- DELETE JOB ----------
@app.route("/admin/delete_job/<int:job_id>")
def delete_job(job_id):
    if session.get("role") != "admin":
        return redirect("/")
    admin_id = session.get("admin_id")
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM resumes WHERE job_id=? AND job_id IN ( Select id From jobs where admin_id = ?)", (job_id,admin_id))
    cur.execute("DELETE FROM jobs WHERE id=? and admin_id = ?", (job_id,admin_id))
    con.commit()
    con.close()
    return redirect("/admin")
# ---------- USER DASHBOARD ----------
@app.route("/user", methods=["GET", "POST"])
def user():
    if session.get("role") != "user":
        return redirect("/")

    u_id = session.get("user_id")
    if not u_id:
        return redirect("/login")

    u_name = session.get("username", "Candidate")
    con = db()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        if request.method == "POST":
            job_id = request.form.get("job")
            
            # --- NEW: Capture additional fields from application.html ---
            # (Note: Ensure your 'resumes' table has these columns, or skip if only analyzing)
            phone = request.form.get("phone")
            exp_years = request.form.get("experience")

            # Check for existing application
            existing_app = cur.execute("""
                SELECT id FROM resumes WHERE user_id=? AND job_id=?
            """, (u_id, job_id)).fetchone()

            if existing_app:
                flash("You have already applied for this position.", "info")
                return redirect(url_for('job_status', job_id=job_id))

            file = request.files.get("resume")
            if not file or file.filename == "":
                flash("❌ Please select a PDF resume.", "error")
                return redirect(url_for('apply_page', job_id=job_id))

            if not file.filename.lower().endswith('.pdf'):
                flash("❌ Only PDF files are allowed.", "error")
                return redirect(url_for('apply_page', job_id=job_id))

            # Save File
            safe_user_name = secure_filename(u_name)
            filename = f"{safe_user_name}_{u_id}_{job_id}_{int(time.time())}.pdf"
            path = os.path.join(RESUME_FOLDER, filename)
            file_path = os.path.join(RESUME_FOLDER, filename)
            file.save(file_path)

            # Analyze Resume
            resume_text = normalize_text(extract_text(path))
            resume_hash = get_resume_hash(resume_text)
            
            job = cur.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            jd_score = jd_match(resume_text, normalize_text(job["description"]))
            skill = skill_score(resume_text, job["skills"])
            f_status, f_sections, _ = format_check(resume_text)
            # 🔥 AI SCREENING FILTER
            if jd_score < 80 or skill < 80 or f_status.lower() != "professional":
                
                return render_template(
                    "result.html",
                    result="Rejected",
                    match=round(jd_score, 2),
                    skill=round(skill, 2),
                    format=f_status,
                    sections=f_sections,
                    job=job
                )
            # Initial Insert
            cur.execute("""
                INSERT INTO resumes 
                (user_id, job_id, match_score, skill_score, format_status, 
                format_sections, result, resume_path, resume_hash, 
                mcq_completed)
                VALUES (?,?,?,?,?,?,?,?,?,0)
            """, (u_id, job_id, jd_score, skill, f_status, 
                  json.dumps(f_sections), "Selected",path, resume_hash))

            con.commit()
            flash("✅ Resume submitted successfully!", "success")
            return render_template("success.html", job_id=job_id)

        # --- GET: PREPARE DASHBOARD DATA ---
        jobs = cur.execute("""
    SELECT j.*,
           (
               SELECT COUNT(*) 
               FROM resumes r 
               WHERE r.job_id = j.id 
               AND LOWER(r.result) = 'selected'
           ) AS selected_count
    FROM jobs j
    WHERE j.status = 'Open'
      AND j.locked = 0
      AND (
          SELECT COUNT(*) 
          FROM resumes r 
          WHERE r.job_id = j.id 
          AND LOWER(r.result) = 'selected'
      ) < j.openings
""").fetchall()
        applied_jobs = cur.execute("""
    SELECT r.job_id, r.mcq_completed,r.mcq_score
    FROM resumes r
    INNER JOIN (
        SELECT job_id, MAX(id) as max_id
        FROM resumes
        WHERE user_id = ?
        GROUP BY job_id
    ) latest
    ON r.id = latest.max_id
""", (u_id,)).fetchall()
        job_statuses = {row['job_id']: dict(row) for row in applied_jobs}

        return render_template("user.html", 
                               jobs=jobs, 
                               user_name=u_name, 
                               job_statuses=job_statuses)
    
    finally:
        con.close()
        
@app.route("/admin/compare", methods=["POST"])
def compare_resumes():
    if session.get("role") != "admin":
        return redirect("/")

    selected = request.form.getlist("resume_ids")

    if len(selected) != 2:
        return "Please select exactly 2 resumes."

    resume1, resume2 = selected

    con = db()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # ✅ Fetch resume details
    r1_data = cur.execute("""
        SELECT users.name, resumes.match_score, resumes.skill_score, 
               resumes.mcq_score, resumes.format_status
        FROM resumes
        JOIN users ON resumes.user_id = users.id
        WHERE resumes.id=?
    """, (resume1,)).fetchone()

    r2_data = cur.execute("""
        SELECT users.name, resumes.match_score, resumes.skill_score, 
               resumes.mcq_score, resumes.format_status
        FROM resumes
        JOIN users ON resumes.user_id = users.id
        WHERE resumes.id=?
    """, (resume2,)).fetchone()

    # ✅ Extract text for similarity
    path1 = cur.execute("SELECT resume_path FROM resumes WHERE id=?", (resume1,)).fetchone()
    path2 = cur.execute("SELECT resume_path FROM resumes WHERE id=?", (resume2,)).fetchone()

    con.close()

    # Fix paths
    path1 = os.path.join(RESUME_FOLDER, os.path.basename(path1["resume_path"]))
    path2 = os.path.join(RESUME_FOLDER, os.path.basename(path2["resume_path"]))

    t1 = extract_text(path1)
    t2 = extract_text(path2)

    similarity = cosine_similarity(
        TfidfVectorizer().fit_transform([t1, t2])
    )[0][1] * 100

    # ✅ Decide winner
    score1 = r1_data["match_score"] + r1_data["skill_score"] + (r1_data["mcq_score"] or 0)
    score2 = r2_data["match_score"] + r2_data["skill_score"] + (r2_data["mcq_score"] or 0)

    winner = 1 if score1 > score2 else 2

    return render_template(
        "compare.html",
        score=round(similarity, 2),
        r1=resume1,
        r2=resume2,
        r1_data={
            "name": r1_data["name"],
            "match": r1_data["match_score"],
            "skill": r1_data["skill_score"],
            "mcq": r1_data["mcq_score"] or 0,
            "format": r1_data["format_status"]
        },
        r2_data={
            "name": r2_data["name"],
            "match": r2_data["match_score"],
            "skill": r2_data["skill_score"],
            "mcq": r2_data["mcq_score"] or 0,
            "format": r2_data["format_status"]
        },
        winner=winner
    )

@app.route("/apply/<int:job_id>",methods=["GET", "POST"])
def apply_page(job_id):
    # 1. Security check
    if session.get("role") != "user":
        return redirect("/login")

    # 2. Get the user name from the session (Crucial for the navbar)
    u_name = session.get("username")
    u_id = session.get("user_id")

    con = db()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 3. Fetch the specific job details
# Fetch ONLY the specific job the user clicked
    job = cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    try:
        # 2. Fetch ONLY the specific job details
        job = cur.execute("SELECT * FROM jobs WHERE id = ? AND status = 'Open'", (job_id,)).fetchone()
        all_jobs = cur.execute("SELECT * FROM jobs WHERE status = 'Open'").fetchall()

        if not job:
            flash("Job not found or no longer accepting applications!", "error")
            return redirect("/user")

        return render_template("application.html", job=job, user_name=u_name)
    finally:
        con.close()

@app.route('/admin/download_report')
def download_report():
    con = sqlite3.connect("/tmp/resume_screening.db")
    
    # SQL query to join user names, job titles, and their scores
    query = """
    SELECT 
        u.name AS Candidate_Name, 
        j.title AS Job_Role, 
        rh.quiz_score AS MCQ_Score_Percent,
        rh.time_taken AS Time_Taken_Seconds,
        rh.replaced_at AS Date_Submitted
    FROM resume_history rh
    JOIN users u ON rh.user_id = u.id
    JOIN jobs j ON rh.job_id = j.id
    """    
    # Load data into Pandas
    df = pd.read_sql_query(query, con)
    con.close()

    # Create an Excel file in memory (no need to save on server disk)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Applicant_Report')
    
    output.seek(0)

    return send_file(
        output, 
        download_name="Recruitment_Report.xlsx", 
        as_attachment=True
    )
@app.route("/user/download_resume/<int:job_id>")
def user_download_resume(job_id):
    if session.get("role") != "user":
        return redirect("/")

    con = db()
    cur = con.cursor()

    r = cur.execute("""
        SELECT resume_path
        FROM resumes
        WHERE user_id=? AND job_id=?
    """, (session["user_id"], job_id)).fetchone()

    con.close()

    if r and r["resume_path"] and os.path.exists(r["resume_path"]):
        return send_file(
            r["resume_path"],
            as_attachment=True,
            download_name=os.path.basename(r["resume_path"])
        )

    flash("❌ No resume found for this job", "error")
    return redirect("/user")

@app.route("/job_details/<int:job_id>")
def job_details(job_id):
    con = db()
    cur = con.cursor()

    job = cur.execute("""
        SELECT description, skills, experience, status
        FROM jobs WHERE id=?
    """, (job_id,)).fetchone()
    if job and job["status"] == "Closed":
        con.close()
        flash("❌ This job is closed. Resume upload is not allowed.", "error")
        return redirect("/user")
    con.close()

    if job:
        return {
            "description": job["description"],
            "skills": job["skills"],
            "experience": job["experience"]
        }

    return {}

# --- STEP 1: SHOW THE QUIZ ---
@app.route('/assessment/quiz/<int:job_id>')
def show_quiz(job_id):
    # Fetch your hardcoded or DB questions here
    mock_questions=""
    return render_template('quiz.html', job_id=job_id, questions=mock_questions)
def calculate_mcq_score(form_data, questions):
    """
    Compares form submitted data against the correct answers.
    """
    correct_count = 0
    total_questions = len(questions)

    for q in questions:
        # The form field name is 'mcq_' + the question ID
        field_name = f"mcq_{q['id']}"
        user_answer = form_data.get(field_name)
        
        if user_answer == q['correct_option']:
            correct_count += 1

    percentage = (correct_count / total_questions) * 100
    return percentage

# --- STEP 2: GRADE QUIZ & REDIRECT ---
@app.route('/grade_quiz/<int:job_id>', methods=['POST'])
def grade_quiz(job_id):
    u_id = session.get("user_id")
    if not u_id:
        return redirect("/login")

    con = db()
    con.row_factory = sqlite3.Row
    
    try:
        # 1. Fetch questions for this job
        cur = con.cursor()
        questions = cur.execute(
            "SELECT id, correct_answer FROM questions WHERE job_id = ?", 
            (job_id,)
        ).fetchall()
        
        if not questions:
            flash("No questions found for this job assessment.", "error")
            return redirect(url_for('user'))

        # 2. Grading logic
        score = 0
        for q in questions:
            user_answer = request.form.get(f"mcq_{q['id']}")
            if user_answer == q['correct_answer']:
                score += 1
        
        percentage = round((score / len(questions)) * 100, 2)

        # 3. Save to DB (This makes the 'Take Quiz' button disappear)
        cur.execute("""
        UPDATE resumes 
        SET mcq_completed = 1, 
            mcq_score = ?, 
            result = CASE 
                WHEN ? >= 80 THEN 'Selected' 
                ELSE 'Rejected' 
            END
        WHERE user_id = ? AND job_id = ?
        """, (percentage, percentage, u_id, job_id))
        con.commit()
        # 4. Routing based on performance
        if percentage >= 80:
            flash(f"Congratulations! You scored {percentage}%.", "success")
            return redirect(url_for('assessment_pass', job_id=job_id, score=percentage))
        
    except Exception as e:
        flash("An error occurred while grading your quiz. Please contact support.", "error")
        return redirect(url_for('user'))
    finally:
        con.close()
        
@app.route("/logout")
def logout():
    session.clear()
    return render_template("logout.html")

@app.route("/admin/export_csv/<int:job_id>")
def export_selected_csv(job_id):
    if session.get("role") != "admin":
        return redirect("/")

    con = db()
    cur = con.cursor()

    rows = cur.execute("""
        SELECT 
            users.name,
            users.email,
            jobs.title AS job_title,
            resumes.match_score,
            resumes.skill_score,
            resumes.format_status
        FROM resumes
        JOIN users ON resumes.user_id = users.id
        JOIN jobs ON resumes.job_id = jobs.id
        WHERE resumes.result='Selected' AND jobs.id=? AND jobs.admin_id=?
    """, (job_id,session["admin_id"])).fetchall()

    con.close()

    # Create CSV in memory
    output = StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Candidate Name",
        "Email",
        "Job Title",
        "JD Match %",
        "Skill Match %",
        "Format Status"
    ])

    # Data rows
    for r in rows:
        writer.writerow([
            r["name"],
            r["email"],
            r["job_title"],
            round(r["match_score"], 2),
            round(r["skill_score"], 2),
            r["format_status"]
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            f"attachment;filename=selected_candidates_job_{job_id}.csv"
        }
    )


# ---------- RESUME RANKING ----------

@app.route("/admin/ranking")
def ranking():
    if session.get("role") != "admin":
        return redirect("/")

    con = db()
    cur = con.cursor()

    data = cur.execute("""
        SELECT 
            resumes.id AS resume_id,
            users.name AS candidate,
            jobs.title AS job_title,
            resumes.match_score,
            resumes.skill_score,
            resumes.mcq_score,
            resumes.format_status,
            (resumes.match_score + resumes.skill_score + COALESCE(resumes.mcq_score,0)) AS total_score,
            resumes.result,
            resumes.resume_path
        FROM resumes
        JOIN users ON resumes.user_id = users.id
        JOIN jobs ON resumes.job_id = jobs.id
        WHERE jobs.admin_id=? AND resumes.result='Selected'
        AND LOWER(TRIM(resumes.result)) = 'selected'
        AND resumes.match_score >= 80
        AND resumes.skill_score >= 80
        AND LOWER(resumes.format_status) IN ('professional', 'basic')
        ORDER BY total_score DESC
    """, (session["admin_id"],)).fetchall()

    con.close()
    return render_template("ranking.html", data=data)

# ---------- VIEW RESUME ----------
@app.route("/view/<int:resume_id>")
def view_resume(resume_id):
    if session.get("role") != "admin":
        return redirect("/")

    con = db()
    cur = con.cursor()
    r = cur.execute(
        "SELECT resume_path FROM resumes JOIN jobs ON resumes.job_id = jobs.id WHERE resumes.id=? AND jobs.admin_id=?",
        (resume_id,session["admin_id"])
    ).fetchone()
    con.close()

    if r and r["resume_path"]:
        # Ensure full path
        full_path = os.path.join(RESUME_FOLDER, os.path.basename(r["resume_path"]))
        if os.path.exists(full_path):
            return send_file(full_path, mimetype="application/pdf", as_attachment=False)
    
    return "Resume not found or not selected.", 404

@app.route('/job/status/<int:job_id>')
def job_status(job_id):
    user_id = session.get('user_id')
    if not user_id:
        return redirect("/")
    con = db()
    con.row_factory = sqlite3.Row
    
    # Check if application exists
    app_data = con.execute("""
        SELECT mcq_score, mcq_completed
        FROM resumes 
        WHERE user_id = ? AND job_id = ?
    """, (user_id, job_id)).fetchone()
    job = con.execute("SELECT title FROM jobs WHERE id=?", (job_id,)).fetchone()
    con.close()
    if not app_data:
        return redirect("/user")
    status = {
        "mcq_completed": bool(app_data['mcq_completed']),
        "mcq_score": app_data['mcq_score'] or 0,
        "job_title": job['title'] if job else "Unknown Role"
    }
    return render_template('job_status.html', job_id=job_id, status=status)

@app.route("/download/<int:resume_id>")
def download_resume(resume_id):
    if session.get("role") != "admin":
        return redirect("/")

    con = db()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    r = cur.execute(
        "SELECT resumes.resume_path FROM resumes JOIN jobs ON resumes.job_id=jobs.id  WHERE resumes.id=? AND result='Selected' AND jobs.admin_id=?",
        (resume_id,session["admin_id"])
    ).fetchone()
    con.close()

    if r and r["resume_path"] and os.path.exists(r["resume_path"]):
        full_path = os.path.join(RESUME_FOLDER, os.path.basename(r["resume_path"]))

        if os.path.exists(full_path):
            return send_file(
                full_path,
                as_attachment=True,
                download_name=os.path.basename(full_path)
            )

    return redirect("/admin/ranking")

# ---------- DELETE RESUME ----------
@app.route("/admin/delete_resume/<int:resume_id>")
def delete_resume(resume_id):
    if session.get("role") != "admin":
        return redirect("/")
    admin_id =session.get("admin_id")
    con = db()
    cur = con.cursor()
    r = cur.execute("SELECT resume_path FROM resumes JOIN jobs on resumes.job_id=jobs.id WHERE resumes.id=? AND jobs.admin_id=?", (resume_id,admin_id)).fetchone()
    if not r:
        con.close()
        flash("Unauthorized access","error")
        return redirect("/admin/ranking")
    cur.execute("DELETE FROM resumes WHERE id=? ", (resume_id,))
    con.commit()
    con.close()
    if os.path.exists(r["resume_path"]):
        os.remove(r["resume_path"])
    flash("Resume deleted successfully","success")
    return redirect("/admin/ranking")

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))