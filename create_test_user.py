import sqlite3
import hashlib
import secrets
import time
import os

DB_PATH = os.path.abspath(os.path.join(os.getcwd(), '../backup/user.sql'))

def hash_password(password, salt):
    return hashlib.sha256(f"{password}{salt}".encode()).hexdigest()

def generate_salt():
    return secrets.token_hex(16)

def generate_unique_id():
    return hashlib.sha256(f"{time.time()}{secrets.token_hex(16)}".encode()).hexdigest()

def create_user(email, password):
    salt = generate_salt()
    password_hash = hash_password(password, salt)
    unique_id = generate_unique_id()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    try:
        c.execute("""
            INSERT INTO users (email, password_hash, salt, unique_id, is_admin, status)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (email, password_hash, salt, unique_id, False, "active"))
        conn.commit()
        print(f"User {email} created successfully.")
    except Exception as e:
        print(f"Error creating user: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    create_user("test@example.com", "password123")
