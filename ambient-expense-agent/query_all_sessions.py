import sqlite3

db_path = "./expense_agent/.adk/session.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT id, user_id, app_name FROM sessions ORDER BY create_time DESC;")
print("All sessions in DB (newest first):")
for row in cursor.fetchall():
    print(row)
conn.close()
