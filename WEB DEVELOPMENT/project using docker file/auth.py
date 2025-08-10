import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

def init_db():
    conn = sqlite3.connect('transport.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  username TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
                  
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  source TEXT NOT NULL,
                  destination TEXT NOT NULL,
                  date TEXT NOT NULL,
                  mode TEXT NOT NULL,
                  results TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(user_id) REFERENCES users(id))''')
    conn.commit()
    conn.close()

def register_user(name, username, password, confirm_password):
    if password != confirm_password:
        return 'Passwords do not match!'
    
    conn = sqlite3.connect('transport.db')
    c = conn.cursor()
    
    # Check if username exists
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    if c.fetchone():
        conn.close()
        return 'Username already exists!'
    
    # Create new user
    hashed_pw = generate_password_hash(password)
    c.execute("INSERT INTO users (name, username, password) VALUES (?, ?, ?)", 
             (name, username, hashed_pw))
    conn.commit()
    conn.close()
    return "success"

def login_user(username, password):
    conn = sqlite3.connect('transport.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = c.fetchone()
    conn.close()
    
    if user and check_password_hash(user[3], password):
        return user
    return None

def get_user_history(user_id):
    conn = sqlite3.connect('transport.db')
    c = conn.cursor()
    c.execute("SELECT * FROM history WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    history_items = c.fetchall()
    conn.close()
    return history_items

def get_user_profile(user_id):
    conn = sqlite3.connect('transport.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user