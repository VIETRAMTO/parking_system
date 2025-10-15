import sqlite3
import hashlib
import re
from datetime import datetime

def adapt_datetime(dt):
    return dt.isoformat()

def convert_datetime(s):
    return datetime.fromisoformat(s.decode())

sqlite3.register_adapter(datetime, adapt_datetime)
sqlite3.register_converter("datetime", convert_datetime)

def get_db_connection():
    conn = sqlite3.connect('parking.db', detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(password, hashed):
    return hashlib.sha256(password.encode()).hexdigest() == hashed

def validate_license_plate(plate):
    pattern = r'^\d{2}[A-Z][-]\d{3}\.\d{2}$'
    return re.match(pattern, plate) is not None