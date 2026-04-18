import sqlite3
import os
DB_NAME = "/tmp/resume_screening.db"
def init_db():
    con = sqlite3.connect(DB_NAME)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()
    #1. Admin & Roles
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'admin',
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    head_password = os.getenv("HEAD_ADMIN_PASSWORD")
    cur.execute("""
    INSERT OR IGNORE INTO admins (name, email, password, role, is_active)
    VALUES (?, ?, ?, ?, ?)
    """, (
        "Main Head",
        "head@hirestream.ai",
        head_password,
        "head",
        1
    ))

    #2. Users
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    #3.Jobs
    cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        title TEXT NOT NULL,
        description TEXT,
        skills TEXT,
        experience TEXT,
        openings INTEGER DEFAULT 1,
        status TEXT DEFAULT 'Open',
        locked INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (admin_id) REFERENCES admins(id)
    )
    """)

    #4. Resumes (Current Active Applications)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS resumes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        job_id INTEGER,
        match_score REAL,
        skill_score REAL,
        found_skills TEXT,
        format_status TEXT,
        format_sections TEXT,
        result TEXT DEFAULT 'Pending',
        resume_path TEXT,
        resume_hash TEXT,
        mcq_score INTEGER DEFAULT 0,
        mcq_completed INTEGER DEFAULT 0,
        final_score REAL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (job_id) REFERENCES jobs(id)
    )
    """)
    
    #5.Resume History and Analytics
    cur.execute("""
        CREATE TABLE IF NOT EXISTS resume_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        job_id INTEGER,
        quiz_score REAL,
        found_skills TEXT,
        time_taken INTEGER,
        resume_path TEXT,
        replaced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            resume_path TEXT NOT NULL,
            extracted_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            job_id INTEGER,
            status TEXT DEFAULT 'Applied',
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (job_id) REFERENCES jobs (id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            question TEXT NOT NULL,
            options TEXT, 
            correct_answer TEXT,
            FOREIGN KEY (job_id) REFERENCES jobs (id)
        )
    """)
    con.commit()
    con.close()
if __name__ == "__main__":
    init_db()

