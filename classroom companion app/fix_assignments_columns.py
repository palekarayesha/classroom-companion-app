import sqlite3, os

db_path = os.path.join("data", "notes_metadata.db")

if not os.path.exists(db_path):
    print("❌ Database not found at:", db_path)
    exit()

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Check if table exists
cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = [t[0] for t in cur.fetchall()]
if "assignments" not in tables:
    print("⚠️ Table 'assignments' not found. Creating it now...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            details TEXT,
            due TEXT,
            filename TEXT,
            uploaded_by TEXT,
            uploaded_at TEXT
        );
    """)
    conn.commit()
    print("✅ Table 'assignments' created successfully.")
else:
    # Get existing columns
    cur.execute("PRAGMA table_info(assignments);")
    cols = [c[1] for c in cur.fetchall()]
    print("📋 Existing columns:", cols)

    # Add missing columns safely
    for col, coltype in [
        ("filename", "TEXT"),
        ("uploaded_by", "TEXT"),
        ("uploaded_at", "TEXT"),
    ]:
        if col not in cols:
            try:
                cur.execute(f"ALTER TABLE assignments ADD COLUMN {col} {coltype};")
                print(f"✅ Added missing column: {col}")
            except Exception as e:
                print(f"⚠️ Could not add column {col}: {e}")
        else:
            print(f"✔️ Column '{col}' already exists")

    conn.commit()

cur.execute("PRAGMA table_info(assignments);")
print("✅ Final columns in 'assignments':", [c[1] for c in cur.fetchall()])

conn.close()
print("\n🎯 Database structure fixed successfully!")
