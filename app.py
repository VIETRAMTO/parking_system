from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
import sqlite3
import pandas as pd
import uuid
from datetime import datetime, timedelta
import hashlib
from reportlab.pdfgen import canvas
from io import BytesIO
import easyocr
import cv2
from PIL import Image
import numpy as np
import re
import os
from pyzbar.pyzbar import decode
import qrcode
from io import BytesIO
import hmac
import requests
import urllib.parse
from anpr import recognize_license_plate, validate_license_plate
import json
import csv
from io import StringIO, BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch


def parse_datetime_safe(dt_value):
    """
    Safely parse datetime from various formats
    """
    if isinstance(dt_value, datetime):
        return dt_value
    elif isinstance(dt_value, str):
        try:
            # Try ISO format first
            if 'T' in dt_value:
                return datetime.fromisoformat(dt_value.replace('Z', '+00:00'))
            else:
                # Try SQLite datetime format
                return datetime.strptime(dt_value, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                # Try with microseconds
                return datetime.strptime(dt_value, '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                # Return current time if parsing fails
                return datetime.now()
    return datetime.now()  # Default fallback

app = Flask(__name__)
app.secret_key = 'parking_system_secret_key_2024'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)

# Cấu hình VNPAY
VNP_TMNCODE = "VNR5SL3C"
VNP_HASH_SECRET = "LJHZSDIZURKRQDK4E73WWFZTJ4JY1RNQ"
VNP_URL = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
VNP_RETURN_URL = "http://localhost:5000/vnpay_return"

# Database setup
def get_db_connection():
    conn = sqlite3.connect('parking.db', detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def login_required(role=None):
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if role and session.get('role') not in role:
                flash('Bạn không có quyền truy cập trang này.', 'error')
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.before_request
def make_session_permanent():
    session.permanent = True

# Routes
@app.route('/')
def home():
    conn = get_db_connection()
    available_slots = conn.execute("SELECT COUNT(*) FROM ParkingSlot WHERE status = 'available'").fetchone()[0]
    occupied_slots = conn.execute("SELECT COUNT(*) FROM ParkingSession WHERE status = 'in_progress'").fetchone()[0]
    config = conn.execute("SELECT name, address, managing_agency, total_slots, price_per_hour FROM SystemConfig WHERE id = 1").fetchone()
    
    name = config['name'] if config and config['name'] else "Bãi xe Trung tâm"
    address = config['address'] if config and config['address'] else "123 Đường ABC, Quận 1, TP.HCM"
    managing_agency = config['managing_agency'] if config and config['managing_agency'] else "Sở Giao thông Vận tải TP.HCM"
    total_slots = config['total_slots'] if config else 100
    price_per_hour = config['price_per_hour'] if config else 5000
    conn.close()
    
    return render_template('home.html', 
                         available_slots=available_slots,
                         occupied_slots=occupied_slots,
                         total_slots=total_slots,
                         name=name,
                         address=address,
                         managing_agency=managing_agency,
                         price_per_hour=price_per_hour)
@app.context_processor
def utility_processor():
    def calculate_duration(start, end):
        """Calculate duration between two datetimes"""
        try:
            if isinstance(start, str):
                start = datetime.fromisoformat(start.replace('Z', '+00:00'))
            if isinstance(end, str):
                end = datetime.fromisoformat(end.replace('Z', '+00:00'))
            
            duration = end - start
            hours = int(duration.total_seconds() // 3600)
            minutes = int((duration.total_seconds() % 3600) // 60)
            
            if hours > 0:
                return f"{hours}h {minutes}p"
            else:
                return f"{minutes} phút"
        except:
            return "N/A"
    
    return dict(calculate_duration=calculate_duration)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if not username or not password:
            flash('Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu.', 'error')
            return render_template('login.html')
        
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM User WHERE username = ? AND password = ?", 
                          (username, hash_password(password))).fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['user_id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash('Đăng nhập thành công!', 'success')
            return redirect(url_for('home'))
        else:
            flash('Sai tên đăng nhập hoặc mật khẩu', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        admin_code = request.form.get('admin_code', '')
        username = request.form['username']
        password = request.form['password']
        full_name = request.form['full_name']
        phone = request.form['phone']
        email = request.form.get('email', '')
        role = request.form['role']
        license_plate = request.form.get('license_plate', '')
        vehicle_type = request.form.get('vehicle_type', 'sedan')
        
        if role == 'admin':
            if 'role' in session and session['role'] == 'admin':
                is_authorized = True
            elif admin_code == "admin_secret":
                is_authorized = True
            else:
                is_authorized = False
            
            if not is_authorized:
                flash('Bạn cần mã admin hoặc tài khoản admin để đăng ký vai trò admin.', 'error')
                return render_template('register.html')
        
        user_id = str(uuid.uuid4())
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO User (user_id, username, password, role, full_name, phone, email, balance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, hash_password(password), role, full_name, phone, email or None, 0.0)
            )
            if role == 'customer' and license_plate and validate_license_plate(license_plate):
                vehicle_id = str(uuid.uuid4())
                conn.execute("INSERT INTO Vehicle (vehicle_id, license_plate, vehicle_type, owner_id) VALUES (?, ?, ?, ?)",
                            (vehicle_id, license_plate, vehicle_type, user_id))
            conn.commit()
            flash('Đăng ký thành công!', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Lỗi khi đăng ký, tài khoản hoặc biển số xe có thể đã tồn tại', 'error')
        finally:
            conn.close()
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Đã đăng xuất thành công.', 'success')
    return redirect(url_for('home'))

@app.route('/vehicle_entry', methods=['GET', 'POST'])
@login_required(role=['admin', 'operator'])
def vehicle_entry():
    if request.method == 'POST':
        license_plate = request.form['license_plate']
        vehicle_type = request.form['vehicle_type']
        owner_username = request.form.get('owner_username', '')
        
        if not license_plate:
            flash('Vui lòng nhập biển số xe.', 'error')
            return render_template('vehicle_entry.html')
        
        if not validate_license_plate(license_plate):
            flash('Biển số xe không hợp lệ.', 'error')
            return render_template('vehicle_entry.html')
        
        conn = get_db_connection()
        vehicle = conn.execute("SELECT vehicle_id FROM Vehicle WHERE license_plate = ?", (license_plate,)).fetchone()
        
        if not vehicle:
            vehicle_id = str(uuid.uuid4())
            owner_id = conn.execute("SELECT user_id FROM User WHERE username = ?", (owner_username,)).fetchone()
            owner_id = owner_id['user_id'] if owner_id else None
            conn.execute("INSERT INTO Vehicle (vehicle_id, license_plate, vehicle_type, owner_id) VALUES (?, ?, ?, ?)",
                         (vehicle_id, license_plate, vehicle_type, owner_id))
        else:
            vehicle_id = vehicle['vehicle_id']
        
        session_id = str(uuid.uuid4())
        entry_time = datetime.now()
        slot = conn.execute("SELECT slot_id, slot_number FROM ParkingSlot WHERE status = 'available' LIMIT 1").fetchone()
        slot_id = slot['slot_id'] if slot else None
        slot_number = slot['slot_number'] if slot else None
        
        conn.execute("INSERT INTO ParkingSession (session_id, vehicle_id, entry_time, status, slot_id) VALUES (?, ?, ?, ?, ?)",
                     (session_id, vehicle_id, entry_time, 'in_progress', slot_id))
        if slot_id:
            conn.execute("UPDATE ParkingSlot SET status = 'occupied' WHERE slot_id = ?", (slot_id,))
        
        conn.commit()
        conn.close()
        
        flash(f'Xe {license_plate} vào bãi lúc {entry_time}. (Chỗ đỗ: {slot_number if slot else "Chưa gán"})', 'success')
        return redirect(url_for('vehicle_entry'))
    
    return render_template('vehicle_entry.html')

@app.route('/vehicle_exit', methods=['GET', 'POST'])
@login_required(role=['admin', 'operator'])
def vehicle_exit():
    if request.method == 'POST':
        license_plate = request.form['license_plate']
        payment_method = request.form['payment_method']
        
        if not license_plate:
            flash('Vui lòng nhập biển số xe.', 'error')
            return render_template('vehicle_exit.html')
        
        if not validate_license_plate(license_plate):
            flash('Biển số xe không hợp lệ (định dạng: XXA-XXX.XX).', 'error')
            return render_template('vehicle_exit.html')
        
        conn = get_db_connection()
        config = conn.execute("SELECT price_per_hour FROM SystemConfig WHERE id = 1").fetchone()
        price_per_hour = config['price_per_hour'] if config else 5000
        
        session_data = conn.execute("""
            SELECT ps.session_id, ps.entry_time, ps.slot_id, ps.vehicle_id, v.owner_id 
            FROM ParkingSession ps 
            JOIN Vehicle v ON ps.vehicle_id = v.vehicle_id 
            WHERE v.license_plate = ? AND ps.status = 'in_progress'
        """, (license_plate,)).fetchone()
        
        if session_data:
            exit_time = datetime.now()
            try:
                # FIX: Use safe datetime parsing
                entry_time = parse_datetime_safe(session_data['entry_time'])
                
                duration = (exit_time - entry_time).total_seconds() / 3600
                fee = round(duration * price_per_hour, 2)
                
                user_id = session_data['owner_id']
                user = conn.execute("SELECT balance FROM User WHERE user_id = ?", (user_id,)).fetchone()
                
                if payment_method == 'balance':
                    if user and user['balance'] >= fee:
                        new_balance = user['balance'] - fee
                        conn.execute("UPDATE User SET balance = ? WHERE user_id = ?", (new_balance, user_id))
                        payment_method_db = 'balance'
                        payment_status = 'completed'
                    else:
                        flash(f'Số dư không đủ ({user["balance"] if user else 0:,.0f} VND). Cần {fee:,.0f} VND. Vui lòng chọn VNPAY hoặc nạp thêm tiền.', 'error')
                        conn.close()
                        return render_template('vehicle_exit.html')
                elif payment_method == 'vnpay':
                    transaction_id = str(uuid.uuid4())
                    payment_url = create_vnpay_payment(fee, transaction_id, user_id)
                    conn.execute(
                        "INSERT INTO PaymentTransaction (transaction_id, session_id, user_id, amount, payment_method, transaction_time, status, transaction_code) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (transaction_id, session_data['session_id'], user_id, fee, 'vnpay', datetime.now(), 'pending', transaction_id)
                    )
                    conn.commit()
                    conn.close()
                    return redirect(payment_url)
                else:
                    flash('Phương thức thanh toán không hỗ trợ.', 'error')
                    conn.close()
                    return render_template('vehicle_exit.html')

                conn.execute("UPDATE ParkingSession SET exit_time = ?, parking_fee = ?, status = 'completed' WHERE session_id = ?",
                             (exit_time, fee, session_data['session_id']))
                if session_data['slot_id']:
                    conn.execute("UPDATE ParkingSlot SET status = 'available' WHERE slot_id = ?", (session_data['slot_id'],))
                
                transaction_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO PaymentTransaction (transaction_id, session_id, user_id, amount, payment_method, transaction_time, status, transaction_code) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (transaction_id, session_data['session_id'], user_id, fee, payment_method_db, exit_time, payment_status, transaction_id)
                )
                conn.commit()
                flash(f'Xe {license_plate} ra bãi lúc {exit_time.strftime("%Y-%m-%d %H:%M:%S")}. Phí: {fee:,.0f} VND. Số dư mới: {new_balance:,.0f} VND', 'success')
            except ValueError as e:
                flash(f'Lỗi định dạng thời gian: {e}. Vui lòng kiểm tra dữ liệu entry_time.', 'error')
            except sqlite3.Error as e:
                flash(f'Lỗi cơ sở dữ liệu: {e}', 'error')
        else:
            flash('Không tìm thấy xe đang gửi.', 'error')
        conn.close()
    
    return render_template('vehicle_exit.html')

@app.route('/recharge', methods=['GET', 'POST'])
@login_required(role=['customer'])
def recharge():
    conn = get_db_connection()
    user = conn.execute("SELECT user_id, balance FROM User WHERE username = ?", (session['username'],)).fetchone()
    current_balance = user['balance'] if user else 0.0
    user_id = user['user_id']
    
    if request.method == 'POST':
        amount = float(request.form['amount'])
        
        if amount <= 0:
            flash('Số tiền nạp phải lớn hơn 0.', 'error')
            return render_template('recharge.html', current_balance=current_balance)
        
        transaction_id = str(uuid.uuid4())
        payment_url = create_vnpay_payment(amount, transaction_id, user_id)
        try:
            conn.execute(
                "INSERT INTO PaymentTransaction (transaction_id, user_id, amount, payment_method, transaction_time, status, transaction_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (transaction_id, user_id, amount, 'vnpay', datetime.now(), 'pending', transaction_id)
            )
            conn.commit()
            return redirect(payment_url)
        except sqlite3.Error as e:
            flash(f'Lỗi lưu giao dịch: {e}', 'error')
    
    transactions = conn.execute("""
        SELECT transaction_id, amount, payment_method, transaction_time, status, transaction_code 
        FROM PaymentTransaction 
        WHERE user_id = ? 
        ORDER BY transaction_time DESC
    """, (user_id,)).fetchall()
    
    conn.close()
    return render_template('recharge.html', current_balance=current_balance, transactions=transactions)

@app.route('/manage_vehicles')
@login_required(role=['admin', 'operator'])
def manage_vehicles():
    conn = get_db_connection()
    search_type = request.args.get('search_type', '')
    search_query = request.args.get('search_query', '')
    
    if search_type and search_query:
        if search_type == 'license_plate':
            vehicles = conn.execute(
                "SELECT v.vehicle_id, v.license_plate, v.vehicle_type, u.username "
                "FROM Vehicle v LEFT JOIN User u ON v.owner_id = u.user_id "
                "WHERE v.license_plate LIKE ?",
                (f"%{search_query}%",)
            ).fetchall()
        else:
            vehicles = conn.execute(
                "SELECT v.vehicle_id, v.license_plate, v.vehicle_type, u.username "
                "FROM Vehicle v LEFT JOIN User u ON v.owner_id = u.user_id "
                "WHERE u.username LIKE ?",
                (f"%{search_query}%",)
            ).fetchall()
    else:
        vehicles = conn.execute(
            "SELECT v.vehicle_id, v.license_plate, v.vehicle_type, u.username FROM Vehicle v LEFT JOIN User u ON v.owner_id = u.user_id"
        ).fetchall()
    
    conn.close()
    return render_template('manage_vehicles.html', vehicles=vehicles, search_type=search_type, search_query=search_query)

@app.route('/add_vehicle', methods=['GET', 'POST'])
@login_required(role=['admin', 'operator'])
def add_vehicle():
    if request.method == 'POST':
        license_plate = request.form['license_plate']
        vehicle_type = request.form['vehicle_type']
        owner_username = request.form.get('owner_username', '')
        
        if not license_plate or not validate_license_plate(license_plate):
            flash('Biển số xe không hợp lệ.', 'error')
            return render_template('add_vehicle.html')
        
        conn = get_db_connection()
        try:
            vehicle_id = str(uuid.uuid4())
            owner_id = None
            if owner_username:
                owner = conn.execute("SELECT user_id FROM User WHERE username = ?", (owner_username,)).fetchone()
                owner_id = owner['user_id'] if owner else None
            
            conn.execute("INSERT INTO Vehicle (vehicle_id, license_plate, vehicle_type, owner_id) VALUES (?, ?, ?, ?)",
                         (vehicle_id, license_plate, vehicle_type, owner_id))
            conn.commit()
            flash('Thêm phương tiện thành công!', 'success')
            return redirect(url_for('manage_vehicles'))
        except sqlite3.IntegrityError:
            flash('Biển số xe đã tồn tại.', 'error')
        finally:
            conn.close()
    
    return render_template('add_vehicle.html')

@app.route('/edit_vehicle/<vehicle_id>', methods=['GET', 'POST'])
@login_required(role=['admin', 'operator'])
def edit_vehicle(vehicle_id):
    conn = get_db_connection()
    
    if request.method == 'GET':
        vehicle = conn.execute("""
            SELECT v.*, u.username 
            FROM Vehicle v 
            LEFT JOIN User u ON v.owner_id = u.user_id 
            WHERE v.vehicle_id = ?
        """, (vehicle_id,)).fetchone()
        
        if not vehicle:
            flash('Không tìm thấy phương tiện.', 'error')
            return redirect(url_for('manage_vehicles'))
        
        conn.close()
        return render_template('edit_vehicle.html', vehicle=vehicle)
    
    elif request.method == 'POST':
        license_plate = request.form['license_plate']
        vehicle_type = request.form['vehicle_type']
        owner_username = request.form.get('owner_username', '')
        
        if not license_plate or not validate_license_plate(license_plate):
            flash('Biển số xe không hợp lệ.', 'error')
            return redirect(url_for('edit_vehicle', vehicle_id=vehicle_id))
        
        try:
            owner_id = None
            if owner_username:
                owner = conn.execute("SELECT user_id FROM User WHERE username = ?", (owner_username,)).fetchone()
                owner_id = owner['user_id'] if owner else None
            
            conn.execute("UPDATE Vehicle SET license_plate = ?, vehicle_type = ?, owner_id = ? WHERE vehicle_id = ?",
                         (license_plate, vehicle_type, owner_id, vehicle_id))
            conn.commit()
            flash('Cập nhật phương tiện thành công!', 'success')
        except sqlite3.IntegrityError:
            flash('Biển số xe đã tồn tại.', 'error')
        finally:
            conn.close()
        
        return redirect(url_for('manage_vehicles'))

@app.route('/delete_vehicle/<vehicle_id>', methods=['POST'])
@login_required(role=['admin', 'operator'])
def delete_vehicle(vehicle_id):
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM Vehicle WHERE vehicle_id = ?", (vehicle_id,))
        conn.commit()
        flash('Xóa phương tiện thành công!', 'success')
    except sqlite3.Error as e:
        flash(f'Lỗi khi xóa phương tiện: {e}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('manage_vehicles'))

@app.route('/handle_incidents', methods=['GET', 'POST'])
@login_required(role=['admin', 'operator'])
def handle_incidents():
    if request.method == 'POST':
        license_plate = request.form['license_plate']
        issue_type = request.form['issue_type']
        description = request.form.get('description', '')
        urgency_level = request.form.get('urgency_level', 'medium')
        action_taken = request.form.get('action_taken', '')
        
        if not license_plate:
            flash('Vui lòng nhập biển số xe.', 'error')
            return render_template('handle_incidents.html')
        
        if not validate_license_plate(license_plate):
            flash('Biển số xe không hợp lệ (định dạng: XXA-XXX.XX).', 'error')
            return render_template('handle_incidents.html')
        
        conn = get_db_connection()
        
        try:
            # Ghi nhận sự cố vào bảng incidents
            incident_id = str(uuid.uuid4())
            
            # Xử lý dựa trên loại sự cố
            if issue_type in ['Mất thẻ', 'Không nhận diện được', 'Mất vé']:
                config = conn.execute("SELECT price_per_hour FROM SystemConfig WHERE id = 1").fetchone()
                price_per_hour = config['price_per_hour'] if config else 5000
                vehicle = conn.execute("SELECT vehicle_id FROM Vehicle WHERE license_plate = ?", (license_plate,)).fetchone()
                
                if vehicle:
                    session_data = conn.execute(
                        "SELECT session_id, entry_time, slot_id FROM ParkingSession WHERE vehicle_id = ? AND status = 'in_progress'", 
                        (vehicle['vehicle_id'],)
                    ).fetchone()
                    
                    if session_data:
                        exit_time = datetime.now()
                        try:
                            # FIX: Ensure entry_time is a datetime object
                            entry_time = session_data['entry_time']
                            if isinstance(entry_time, str):
                                # Convert string to datetime if needed
                                entry_time = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                            
                            duration = (exit_time - entry_time).total_seconds() / 3600
                            fee = round(duration * price_per_hour, 2)
                            
                            # Update parking session
                            conn.execute("UPDATE ParkingSession SET exit_time = ?, parking_fee = ?, status = 'completed' WHERE session_id = ?",
                                         (exit_time, fee, session_data['session_id']))
                            
                            # Free up parking slot
                            if session_data['slot_id']:
                                conn.execute("UPDATE ParkingSlot SET status = 'available' WHERE slot_id = ?", (session_data['slot_id'],))
                            
                            # Insert incident record with resolution
                            conn.execute(
                                "INSERT INTO Incident (incident_id, license_plate, issue_type, description, urgency_level, action_taken, reported_by, status, resolved_by, resolution_notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (incident_id, license_plate, issue_type, description, urgency_level, action_taken, session['username'], 'resolved', session['username'], f'Đã xử lý tự động. Phí: {fee:,.0f} VND')
                            )
                            
                            flash(f'Đã xử lý sự cố {issue_type} cho xe {license_plate}. Phí: {fee:,.0f} VND', 'success')
                            
                        except ValueError as e:
                            flash(f'Lỗi định dạng thời gian: {e}. Vui lòng kiểm tra dữ liệu entry_time.', 'error')
                            # Insert incident record as open
                            conn.execute(
                                "INSERT INTO Incident (incident_id, license_plate, issue_type, description, urgency_level, action_taken, reported_by, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (incident_id, license_plate, issue_type, description, urgency_level, action_taken, session['username'], 'open')
                            )
                    else:
                        flash('Không tìm thấy xe đang gửi.', 'error')
                        # Insert incident record as open
                        conn.execute(
                            "INSERT INTO Incident (incident_id, license_plate, issue_type, description, urgency_level, action_taken, reported_by, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (incident_id, license_plate, issue_type, description, urgency_level, action_taken, session['username'], 'open')
                        )
                else:
                    flash('Không tìm thấy phương tiện.', 'error')
                    # Insert incident record as open
                    conn.execute(
                        "INSERT INTO Incident (incident_id, license_plate, issue_type, description, urgency_level, action_taken, reported_by, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (incident_id, license_plate, issue_type, description, urgency_level, action_taken, session['username'], 'open')
                    )
            else:
                # For other issue types, just record the incident
                conn.execute(
                    "INSERT INTO Incident (incident_id, license_plate, issue_type, description, urgency_level, action_taken, reported_by, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (incident_id, license_plate, issue_type, description, urgency_level, action_taken, session['username'], 'open')
                )
                flash(f'Đã ghi nhận sự cố {issue_type}. Đội kỹ thuật sẽ xử lý sớm.', 'success')
            
            conn.commit()
            
        except sqlite3.Error as e:
            flash(f'Lỗi khi ghi nhận sự cố: {e}', 'error')
        finally:
            conn.close()
    
    return render_template('handle_incidents.html')

@app.route('/api/recent_incidents')
@login_required(role=['admin', 'operator'])
def get_recent_incidents():
    conn = get_db_connection()
    
    try:
        # Lấy sự cố trong 24h gần đây
        incidents = conn.execute("""
            SELECT 
                incident_id,
                license_plate,
                issue_type,
                urgency_level,
                status,
                reported_by,
                resolved_by,
                created_at
            FROM Incident 
            WHERE datetime(created_at) >= datetime('now', '-1 day')
            ORDER BY created_at DESC
            LIMIT 20
        """).fetchall()
        
        incidents_list = []
        for incident in incidents:
            # Xử lý datetime
            created_at = incident['created_at']
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                except ValueError:
                    created_at = datetime.now()
            
            if isinstance(created_at, datetime):
                created_at_str = created_at.strftime('%H:%M %d/%m')
            else:
                created_at_str = 'N/A'
            
            incidents_list.append({
                'id': incident['incident_id'],
                'license_plate': incident['license_plate'],
                'issue_type': incident['issue_type'],
                'urgency_level': incident['urgency_level'],
                'status': incident['status'],
                'reported_by': incident['reported_by'],
                'created_at': created_at_str
            })
        
        return jsonify(incidents_list)
        
    except Exception as e:
        print(f"Error: {e}")
        return jsonify([])
    finally:
        conn.close()
@app.route('/api/update_incident_status/<incident_id>', methods=['POST'])
@login_required(role=['admin', 'operator'])
def update_incident_status(incident_id):
    data = request.get_json()
    new_status = data.get('status')
    
    if not new_status:
        return jsonify({'success': False, 'error': 'Trạng thái không được để trống'})
    
    conn = get_db_connection()
    try:
        if new_status == 'resolved':
            # Chuyển thành đã xử lý
            conn.execute("""
                UPDATE Incident 
                SET status = 'resolved', 
                    resolved_by = ?,
                    resolved_at = CURRENT_TIMESTAMP
                WHERE incident_id = ?
            """, (session['username'], incident_id))
        else:
            # Chuyển thành chưa xử lý
            conn.execute("""
                UPDATE Incident 
                SET status = 'open',
                    resolved_by = NULL,
                    resolved_at = NULL
                WHERE incident_id = ?
            """, (incident_id,))
        
        conn.commit()
        return jsonify({'success': True, 'message': 'Cập nhật trạng thái thành công'})
    except sqlite3.Error as e:
        return jsonify({'success': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/configure_system', methods=['GET', 'POST'])
@login_required(role=['admin'])
def configure_system():
    conn = get_db_connection()
    
    if request.method == 'POST':
        if 'general_config' in request.form:
            parking_lot_name = request.form['parking_lot_name']
            slots = int(request.form['slots'])
            
            if not parking_lot_name:
                flash('Vui lòng nhập tên bãi xe.', 'error')
                return redirect(url_for('configure_system'))
            
            try:
                current_config = conn.execute("SELECT price_per_hour FROM SystemConfig WHERE id = 1").fetchone()
                current_price = current_config['price_per_hour'] if current_config else 5000
                conn.execute("INSERT OR REPLACE INTO SystemConfig (id, name, total_slots, price_per_hour) VALUES (?, ?, ?, ?)",
                             (1, parking_lot_name, slots, current_price))
                conn.commit()
                flash('Cấu hình chung đã được lưu!', 'success')
            except sqlite3.Error:
                flash('Lỗi khi lưu cấu hình.', 'error')
        
        elif 'price_config' in request.form:
            price_per_hour = float(request.form['price_per_hour'])
            
            try:
                current_config = conn.execute("SELECT name, total_slots FROM SystemConfig WHERE id = 1").fetchone()
                current_name = current_config['name'] if current_config else "Bãi xe Trung tâm"
                current_slots = current_config['total_slots'] if current_config else 100
                conn.execute("INSERT OR REPLACE INTO SystemConfig (id, name, total_slots, price_per_hour) VALUES (?, ?, ?, ?)",
                             (1, current_name, current_slots, price_per_hour))
                conn.commit()
                flash('Giá phí đã được lưu!', 'success')
            except sqlite3.Error:
                flash('Lỗi khi lưu giá phí.', 'error')
    
    # Handle user search
    user_search = request.args.get('user_search', '')
    active_tab = request.args.get('tab', 'general')
    if user_search:
        users = conn.execute("""
            SELECT user_id, username, role, full_name, phone, email, balance 
            FROM User 
            WHERE username LIKE ? OR full_name LIKE ? OR email LIKE ? OR phone LIKE ?
            ORDER BY username
        """, (f"%{user_search}%", f"%{user_search}%", f"%{user_search}%", f"%{user_search}%")).fetchall()
    else:
        users = conn.execute("SELECT user_id, username, role, full_name, phone, email, balance FROM User ORDER BY username").fetchall()
    
    config = conn.execute("SELECT name, total_slots, price_per_hour FROM SystemConfig WHERE id = 1").fetchone()
    slots = conn.execute("SELECT slot_id, slot_number, status, location FROM ParkingSlot ORDER BY slot_number").fetchall()
    devices = conn.execute("SELECT device_id, device_type, device_status, location FROM Device").fetchall()
    
    conn.close()
    return render_template('configure_system.html', 
                         config=config, 
                         users=users, 
                         slots=slots, 
                         devices=devices,
                         user_search=user_search,
                        active_tab=active_tab)
@app.route('/dashboard')
@login_required(role=['admin', 'operator'])
def dashboard():
    conn = get_db_connection()
    
    active_sessions = conn.execute("SELECT COUNT(*) FROM ParkingSession WHERE status = 'in_progress'").fetchone()[0]
    total_revenue = conn.execute("SELECT SUM(parking_fee) FROM ParkingSession WHERE status = 'completed'").fetchone()[0] or 0.0
    
    from_date = request.args.get('from_date', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    to_date = request.args.get('to_date', datetime.now().strftime('%Y-%m-%d'))
    license_plate = request.args.get('license_plate', '')  # THÊM: tham số tìm kiếm biển số
    
    try:
        # Xây dựng query linh hoạt
        query = """
            SELECT ps.session_id, v.license_plate, ps.entry_time, ps.exit_time, ps.parking_fee, ps.status
            FROM ParkingSession ps 
            JOIN Vehicle v ON ps.vehicle_id = v.vehicle_id
            WHERE date(ps.entry_time) BETWEEN ? AND ?
        """
        params = [from_date, to_date]
        
        # THÊM: Điều kiện tìm kiếm biển số
        if license_plate:
            query += " AND v.license_plate LIKE ?"
            params.append(f"%{license_plate}%")
        
        query += " ORDER BY ps.entry_time DESC"
        
        sessions_data = conn.execute(query, params).fetchall()
        
        # Convert to list of dictionaries và xử lý datetime
        sessions_list = []
        for session in sessions_data:
            session_dict = dict(session)
            
            # Xử lý entry_time
            if session_dict['entry_time']:
                if isinstance(session_dict['entry_time'], str):
                    # Nếu là string, giữ nguyên
                    pass
                else:
                    # Nếu là datetime object, convert thành string
                    session_dict['entry_time'] = session_dict['entry_time'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Xử lý exit_time
            if session_dict['exit_time']:
                if isinstance(session_dict['exit_time'], str):
                    # Nếu là string, giữ nguyên
                    pass
                else:
                    # Nếu là datetime object, convert thành string
                    session_dict['exit_time'] = session_dict['exit_time'].strftime('%Y-%m-%d %H:%M:%S')
            
            sessions_list.append(session_dict)
        
        # Tính doanh thu
        filtered_revenue = sum(session['parking_fee'] or 0 for session in sessions_list if session['status'] == 'completed')
        
        # THÊM: Tính tổng lượt gửi trong khoảng thời gian
        total_sessions = len(sessions_list)
        
    except Exception as e:
        print(f"Error loading sessions data: {e}")
        filtered_revenue = 0.0
        sessions_list = []
        total_sessions = 0  # THÊM: Khởi tạo tổng lượt gửi
    
    conn.close()
    
    return render_template('dashboard.html', 
                         active_sessions=active_sessions,
                         total_revenue=total_revenue,
                         filtered_revenue=filtered_revenue,
                         sessions_data=sessions_list,
                         total_sessions=total_sessions,  # THÊM: truyền tổng lượt gửi
                         license_plate=license_plate,    # THÊM: truyền biển số tìm kiếm
                         from_date=from_date,
                         to_date=to_date)
@app.route('/account')
@login_required(role=['customer'])
def account():
    conn = get_db_connection()
    user_id = conn.execute("SELECT user_id FROM User WHERE username = ?", (session['username'],)).fetchone()['user_id']
    
    user_info = conn.execute("""
        SELECT full_name, phone, email, balance 
        FROM User 
        WHERE user_id = ?
    """, (user_id,)).fetchone()
    
    vehicle_info = conn.execute("""
        SELECT license_plate, vehicle_type 
        FROM Vehicle 
        WHERE owner_id = ?
    """, (user_id,)).fetchone()
    
    sessions_df = pd.read_sql_query("""
        SELECT ps.session_id, ps.entry_time, ps.exit_time, ps.parking_fee, ps.status, ps.slot_id
        FROM ParkingSession ps 
        JOIN Vehicle v ON ps.vehicle_id = v.vehicle_id 
        WHERE v.owner_id = ?
        ORDER BY ps.entry_time DESC
    """, conn, params=(user_id,))
    
    if not sessions_df.empty:
        total_sessions = len(sessions_df)
        total_spent = sessions_df[sessions_df['status'] == 'completed']['parking_fee'].sum() or 0.0
        sessions_data = sessions_df.to_dict('records')
    else:
        total_sessions = 0
        total_spent = 0.0
        sessions_data = []
    
    conn.close()
    
    return render_template('account.html',
                         user_info=user_info,
                         vehicle_info=vehicle_info,
                         sessions_data=sessions_data,
                         total_sessions=total_sessions,
                         total_spent=total_spent)

# VNPAY Functions
def create_vnpay_payment(amount, order_id, user_id, ip_addr="127.0.0.1"):
    data = {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": VNP_TMNCODE,
        "vnp_Amount": str(int(amount * 100)),
        "vnp_CurrCode": "VND",
        "vnp_TxnRef": order_id,
        "vnp_OrderInfo": f"Nap tien cho user {user_id}",
        "vnp_OrderType": "other",
        "vnp_Locale": "vn",
        "vnp_ReturnUrl": VNP_RETURN_URL,
        "vnp_IpAddr": ip_addr,
        "vnp_CreateDate": datetime.now().strftime("%Y%m%d%H%M%S"),
        "vnp_ExpireDate": (datetime.now() + timedelta(minutes=15)).strftime("%Y%m%d%H%M%S")
    }
    
    sorted_data = sorted(data.items())
    querystring = urllib.parse.urlencode(sorted_data)
    h = hmac.new(VNP_HASH_SECRET.encode(), querystring.encode(), hashlib.sha512)
    vnp_SecureHash = h.hexdigest()
    
    payment_url = f"{VNP_URL}?{querystring}&vnp_SecureHash={vnp_SecureHash}"
    return payment_url

@app.route('/vnpay_return')
def vnpay_return():
    response_code = request.args.get("vnp_ResponseCode")
    secure_hash = request.args.get("vnp_SecureHash")
    txn_ref = request.args.get("vnp_TxnRef")
    amount = int(request.args.get("vnp_Amount", 0)) / 100 if request.args.get("vnp_Amount") else 0
    
    params_dict = dict(request.args)
    if 'vnp_SecureHash' in params_dict:
        del params_dict['vnp_SecureHash']
    
    sorted_params = sorted(params_dict.items())
    query = urllib.parse.urlencode(sorted_params)
    h = hmac.new(VNP_HASH_SECRET.encode(), query.encode(), hashlib.sha512)
    calculated_hash = h.hexdigest()
    
    conn = get_db_connection()
    trans = conn.execute("SELECT user_id, amount FROM PaymentTransaction WHERE transaction_code = ?", (txn_ref,)).fetchone()
    
    if trans and secure_hash == calculated_hash and response_code == '00':
        user_id = trans['user_id']
        user = conn.execute("SELECT balance FROM User WHERE user_id = ?", (user_id,)).fetchone()
        new_balance = user['balance'] + amount
        conn.execute("UPDATE PaymentTransaction SET status = 'completed', transaction_time = ? WHERE transaction_code = ?",
                     (datetime.now(), txn_ref))
        conn.execute("UPDATE User SET balance = ? WHERE user_id = ?", (new_balance, user_id))
        conn.commit()
        flash(f'Thanh toán thành công! Đã nạp {amount:,.0f} VND. Số dư mới: {new_balance:,.0f} VND', 'success')
    else:
        if trans:
            conn.execute("UPDATE PaymentTransaction SET status = 'failed' WHERE transaction_code = ?", (txn_ref,))
            conn.commit()
        flash(f'Thanh toán thất bại. Mã lỗi: {response_code or "Không có mã lỗi"}', 'error')
    conn.close()
    
    return redirect(url_for('recharge'))


# ANPR endpoint
@app.route('/recognize_license_plate', methods=['POST'])
def recognize_license_plate_endpoint():
    if 'image' not in request.files:
        return jsonify({'error': 'No image file'}), 400
    
    image_file = request.files['image']
    if image_file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    try:
        image = Image.open(image_file)
        license_plate = recognize_license_plate(image)
        return jsonify({'license_plate': license_plate})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/add_parking_slot', methods=['POST'])
@login_required(role=['admin'])
def add_parking_slot():
    slot_number = request.form['slot_number']
    slot_status = request.form['slot_status']
    slot_location = request.form.get('slot_location', '')
    
    conn = get_db_connection()
    try:
        slot_id = str(uuid.uuid4())
        conn.execute("INSERT INTO ParkingSlot (slot_id, slot_number, status, location) VALUES (?, ?, ?, ?)",
                     (slot_id, slot_number, slot_status, slot_location))
        conn.commit()
        flash('Thêm chỗ đỗ thành công!', 'success')
    except sqlite3.IntegrityError:
        flash('Số chỗ đỗ đã tồn tại.', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('configure_system') + '#slot')

@app.route('/edit_parking_slot/<slot_id>', methods=['POST'])
@login_required(role=['admin'])
def edit_parking_slot(slot_id):
    slot_number = request.form['slot_number']
    slot_status = request.form['slot_status']
    slot_location = request.form.get('slot_location', '')
    
    conn = get_db_connection()
    try:
        conn.execute("UPDATE ParkingSlot SET slot_number = ?, status = ?, location = ? WHERE slot_id = ?",
                     (slot_number, slot_status, slot_location, slot_id))
        conn.commit()
        flash('Cập nhật chỗ đỗ thành công!', 'success')
    except sqlite3.IntegrityError:
        flash('Số chỗ đỗ đã tồn tại.', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('configure_system') + '#slot')

@app.route('/delete_parking_slot/<slot_id>', methods=['POST'])
@login_required(role=['admin'])
def delete_parking_slot(slot_id):
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM ParkingSlot WHERE slot_id = ?", (slot_id,))
        conn.commit()
        flash('Xóa chỗ đỗ thành công!', 'success')
    except sqlite3.Error as e:
        flash(f'Lỗi khi xóa chỗ đỗ: {e}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('configure_system') + '#slot')


@app.route('/get_vehicle_info/<license_plate>')
@login_required(role=['admin', 'operator'])
def get_vehicle_info(license_plate):
    conn = get_db_connection()
    
    try:
        # Get vehicle information
        vehicle_info = conn.execute("""
            SELECT v.vehicle_id, v.license_plate, v.vehicle_type, v.created_at
            FROM Vehicle v 
            WHERE v.license_plate = ?
        """, (license_plate,)).fetchone()
        
        if not vehicle_info:
            return jsonify({'error': 'Vehicle not found'}), 404
        
        # Get owner information
        owner_info = conn.execute("""
            SELECT u.username, u.full_name, u.phone, u.email, u.balance
            FROM User u
            JOIN Vehicle v ON u.user_id = v.owner_id
            WHERE v.license_plate = ?
        """, (license_plate,)).fetchone()
        
        # Get current parking session
        session_info = conn.execute("""
            SELECT ps.session_id, ps.entry_time, ps.status, ps.slot_id, pslot.slot_number
            FROM ParkingSession ps
            LEFT JOIN ParkingSlot pslot ON ps.slot_id = pslot.slot_id
            JOIN Vehicle v ON ps.vehicle_id = v.vehicle_id
            WHERE v.license_plate = ? AND ps.status = 'in_progress'
            ORDER BY ps.entry_time DESC
            LIMIT 1
        """, (license_plate,)).fetchone()
        
        # Convert datetime objects to ISO format strings for JSON serialization
        result = {}
        
        if vehicle_info:
            vehicle_dict = dict(vehicle_info)
            # Convert datetime to ISO string
            if vehicle_dict.get('created_at'):
                if isinstance(vehicle_dict['created_at'], datetime):
                    vehicle_dict['created_at'] = vehicle_dict['created_at'].isoformat()
                elif isinstance(vehicle_dict['created_at'], str):
                    # If it's already a string, ensure it's in ISO format
                    try:
                        dt = datetime.fromisoformat(vehicle_dict['created_at'].replace('Z', '+00:00'))
                        vehicle_dict['created_at'] = dt.isoformat()
                    except ValueError:
                        # If conversion fails, keep the original string
                        pass
            result['vehicle_info'] = vehicle_dict
        
        if owner_info:
            result['owner_info'] = dict(owner_info)
        
        if session_info:
            session_dict = dict(session_info)
            # Convert datetime to ISO string
            if session_dict.get('entry_time'):
                if isinstance(session_dict['entry_time'], datetime):
                    session_dict['entry_time'] = session_dict['entry_time'].isoformat()
                elif isinstance(session_dict['entry_time'], str):
                    # If it's already a string, ensure it's in ISO format
                    try:
                        dt = datetime.fromisoformat(session_dict['entry_time'].replace('Z', '+00:00'))
                        session_dict['entry_time'] = dt.isoformat()
                    except ValueError:
                        # If conversion fails, keep the original string
                        pass
            result['session_info'] = session_dict
        
        conn.close()
        
        return jsonify(result)
        
    except sqlite3.Error as e:
        conn.close()
        return jsonify({'error': str(e)}), 500

@app.route('/get_price_configuration')
def get_price_configuration():
    conn = get_db_connection()
    config = conn.execute("SELECT price_per_hour FROM SystemConfig WHERE id = 1").fetchone()
    conn.close()
    
    return jsonify({
        'price_per_hour': config['price_per_hour'] if config else 5000
    })

from jinja2 import Environment

# Thêm template filter để xử lý datetime
def format_datetime(value, format='%d/%m/%Y %H:%M'):
    """Template filter để format datetime từ string hoặc datetime object"""
    if not value:
        return ''
    
    if isinstance(value, datetime):
        return value.strftime(format)
    elif isinstance(value, str):
        try:
            # Try to parse the string as datetime
            dt = parse_datetime_safe(value)
            return dt.strftime(format)
        except:
            return value  # Return original string if parsing fails
    return str(value)

# Đăng ký template filter với Flask
@app.template_filter('format_datetime')
def format_datetime_filter(value, format='%d/%m/%Y %H:%M'):
    return format_datetime(value, format)
# User Management Routes
@app.route('/edit_user/<user_id>', methods=['POST'])
@login_required(role=['admin'])
def edit_user(user_id):
    full_name = request.form['full_name']
    phone = request.form['phone']
    email = request.form['email']
    role = request.form['role']
    balance = float(request.form['balance'])
    
    conn = get_db_connection()
    try:
        conn.execute("""
            UPDATE User SET full_name = ?, phone = ?, email = ?, role = ?, balance = ? 
            WHERE user_id = ?
        """, (full_name, phone, email, role, balance, user_id))
        conn.commit()
        flash('Cập nhật người dùng thành công!', 'success')
    except sqlite3.Error as e:
        flash(f'Lỗi khi cập nhật người dùng: {e}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('configure_system') + '#users')

@app.route('/delete_user/<user_id>', methods=['POST'])
@login_required(role=['admin'])
def delete_user(user_id):
    conn = get_db_connection()
    try:
        # Check if user is the last admin
        user = conn.execute("SELECT role FROM User WHERE user_id = ?", (user_id,)).fetchone()
        if user and user['role'] == 'admin':
            admin_count = conn.execute("SELECT COUNT(*) FROM User WHERE role = 'admin'").fetchone()[0]
            if admin_count <= 1:
                flash('Không thể xóa admin cuối cùng!', 'error')
                conn.close()
                return redirect(url_for('configure_system') + '#users')
        
        conn.execute("DELETE FROM User WHERE user_id = ?", (user_id,))
        conn.commit()
        flash('Xóa người dùng thành công!', 'success')
    except sqlite3.Error as e:
        flash(f'Lỗi khi xóa người dùng: {e}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('configure_system') + '#users')

@app.route('/add_device', methods=['POST'])
@login_required(role=['admin'])
def add_device():
    device_type = request.form['device_type'].strip().lower()
    device_status = request.form['device_status']
    device_location = request.form['device_location']
    
    print(f"DEBUG: Adding device - type: {device_type}, status: {device_status}, location: {device_location}")
    
    conn = get_db_connection()
    try:
        device_id = str(uuid.uuid4())
        
        # Validate device_type
        allowed_types = ['camera', 'barrier', 'rfid_reader']
        if device_type not in allowed_types:
            flash(f'Loại thiết bị không hợp lệ: "{device_type}". Chỉ chấp nhận: {", ".join(allowed_types)}', 'error')
            conn.close()
            return redirect(url_for('configure_system') + '#devices')
        
        # Sửa lỗi SQL syntax - đảm bảo đúng số lượng parameters
        conn.execute(
            "INSERT INTO Device (device_id, device_type, device_status, location, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (device_id, device_type, device_status, device_location, datetime.now(), datetime.now())
        )
        conn.commit()
        flash('Thêm thiết bị thành công!', 'success')
        print(f"DEBUG: Device added successfully - ID: {device_id}")
        
    except sqlite3.Error as e:
        print(f"DEBUG: SQL Error - {e}")
        flash(f'Lỗi khi thêm thiết bị: {e}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('configure_system') + '#devices')

@app.route('/edit_device/<device_id>', methods=['POST'])
@login_required(role=['admin'])
def edit_device(device_id):
    device_type = request.form['device_type'].strip().lower()
    device_status = request.form['device_status']
    device_location = request.form['device_location']
    
    print(f"DEBUG: Editing device {device_id} - type: {device_type}, status: {device_status}, location: {device_location}")
    
    conn = get_db_connection()
    try:
        # Validate device_type
        allowed_types = ['camera', 'barrier', 'rfid_reader']
        if device_type not in allowed_types:
            flash(f'Loại thiết bị không hợp lệ: "{device_type}". Chỉ chấp nhận: {", ".join(allowed_types)}', 'error')
            conn.close()
            return redirect(url_for('configure_system') + '#devices')
        
        # Sửa lỗi SQL syntax - đảm bảo đúng số lượng parameters
        conn.execute(
            "UPDATE Device SET device_type = ?, device_status = ?, location = ?, updated_at = ? WHERE device_id = ?",
            (device_type, device_status, device_location, datetime.now(), device_id)
        )
        conn.commit()
        flash('Cập nhật thiết bị thành công!', 'success')
        print(f"DEBUG: Device updated successfully - ID: {device_id}")
        
    except sqlite3.Error as e:
        print(f"DEBUG: SQL Error - {e}")
        flash(f'Lỗi khi cập nhật thiết bị: {e}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('configure_system') + '#devices')

@app.route('/delete_device/<device_id>', methods=['POST'])
@login_required(role=['admin'])
def delete_device(device_id):
    print(f"DEBUG: Deleting device - ID: {device_id}")
    
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM Device WHERE device_id = ?", (device_id,))
        conn.commit()
        flash('Xóa thiết bị thành công!', 'success')
        print(f"DEBUG: Device deleted successfully - ID: {device_id}")
    except sqlite3.Error as e:
        print(f"DEBUG: SQL Error - {e}")
        flash(f'Lỗi khi xóa thiết bị: {e}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('configure_system') + '#devices')

# Update the existing configure_system route to handle user search

@app.route('/debug_tabs')
@login_required(role=['admin'])
def debug_tabs():
    conn = get_db_connection()
    
    slots_count = conn.execute("SELECT COUNT(*) FROM ParkingSlot").fetchone()[0]
    users_count = conn.execute("SELECT COUNT(*) FROM User").fetchone()[0]
    devices_count = conn.execute("SELECT COUNT(*) FROM Device").fetchone()[0]
    
    conn.close()
    
    return jsonify({
        'slots_count': slots_count,
        'users_count': users_count,
        'devices_count': devices_count,
        'current_tab': request.args.get('tab', 'unknown')
    })
@app.route('/export_dashboard_csv')
@login_required(role=['admin', 'operator'])
def export_dashboard_csv():
    # Lấy tham số từ URL
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')
    
    conn = get_db_connection()
    
    try:
        # Lấy dữ liệu sessions
        if from_date and to_date:
            sessions_data = conn.execute("""
                SELECT 
                    v.license_plate,
                    ps.entry_time,
                    ps.exit_time,
                    ps.parking_fee,
                    ps.status,
                    pslot.slot_number
                FROM ParkingSession ps 
                JOIN Vehicle v ON ps.vehicle_id = v.vehicle_id
                LEFT JOIN ParkingSlot pslot ON ps.slot_id = pslot.slot_id
                WHERE date(ps.entry_time) BETWEEN ? AND ?
                ORDER BY ps.entry_time DESC
            """, (from_date, to_date)).fetchall()
        else:
            # Mặc định 30 ngày gần đây
            sessions_data = conn.execute("""
                SELECT 
                    v.license_plate,
                    ps.entry_time,
                    ps.exit_time,
                    ps.parking_fee,
                    ps.status,
                    pslot.slot_number
                FROM ParkingSession ps 
                JOIN Vehicle v ON ps.vehicle_id = v.vehicle_id
                LEFT JOIN ParkingSlot pslot ON ps.slot_id = pslot.slot_id
                WHERE date(ps.entry_time) >= date('now', '-30 days')
                ORDER BY ps.entry_time DESC
            """).fetchall()
        
        # Lấy thống kê
        active_sessions = conn.execute("SELECT COUNT(*) FROM ParkingSession WHERE status = 'in_progress'").fetchone()[0]
        total_revenue = conn.execute("SELECT SUM(parking_fee) FROM ParkingSession WHERE status = 'completed'").fetchone()[0] or 0.0
        
        if from_date and to_date:
            filtered_revenue = conn.execute("""
                SELECT SUM(parking_fee) FROM ParkingSession 
                WHERE status = 'completed' AND date(entry_time) BETWEEN ? AND ?
            """, (from_date, to_date)).fetchone()[0] or 0.0
        else:
            filtered_revenue = conn.execute("""
                SELECT SUM(parking_fee) FROM ParkingSession 
                WHERE status = 'completed' AND date(entry_time) >= date('now', '-30 days')
            """).fetchone()[0] or 0.0
        
        # Tạo CSV
        output = StringIO()
        writer = csv.writer(output)
        
        # Header thống kê
        writer.writerow(['BÁO CÁO DASHBOARD - HỆ THỐNG BÃI XE'])
        writer.writerow([f'Thời gian: {from_date} đến {to_date}' if from_date and to_date else 'Thời gian: 30 ngày gần đây'])
        writer.writerow([f'Ngày xuất báo cáo: {datetime.now().strftime("%d/%m/%Y %H:%M")}'])
        writer.writerow([])
        writer.writerow(['THỐNG KÊ TỔNG QUAN'])
        writer.writerow(['Xe đang trong bãi', active_sessions])
        writer.writerow(['Tổng doanh thu', f'{total_revenue:,.0f} VND'])
        writer.writerow(['Doanh thu khoảng thời gian', f'{filtered_revenue:,.0f} VND'])
        writer.writerow([])
        
        # Header chi tiết
        writer.writerow(['CHI TIẾT LỊCH SỬ GỬI XE'])
        writer.writerow([
            'Biển số xe', 'Thời gian vào', 'Thời gian ra', 
            'Phí gửi xe (VND)', 'Trạng thái', 'Chỗ đỗ'
        ])
        
        # Data chi tiết
        for session in sessions_data:
            # Xử lý thời gian
            entry_time = session['entry_time']
            if isinstance(entry_time, datetime):
                entry_time_str = entry_time.strftime('%d/%m/%Y %H:%M')
            elif isinstance(entry_time, str):
                entry_time_str = entry_time[:16].replace('T', ' ')
            else:
                entry_time_str = str(entry_time)
            
            exit_time = session['exit_time']
            if exit_time:
                if isinstance(exit_time, datetime):
                    exit_time_str = exit_time.strftime('%d/%m/%Y %H:%M')
                elif isinstance(exit_time, str):
                    exit_time_str = exit_time[:16].replace('T', ' ')
                else:
                    exit_time_str = str(exit_time)
            else:
                exit_time_str = 'Đang gửi'
            
            # Xử lý trạng thái
            status_map = {
                'completed': 'Hoàn thành',
                'in_progress': 'Đang gửi'
            }
            status = status_map.get(session['status'], session['status'])
            
            writer.writerow([
                session['license_plate'],
                entry_time_str,
                exit_time_str,
                f'{session["parking_fee"] or 0:,.0f}',
                status,
                session['slot_number'] or 'N/A'
            ])
        
        output.seek(0)
        
        # Tạo filename
        if from_date and to_date:
            filename = f'bao_cao_dashboard_{from_date}_den_{to_date}.csv'
        else:
            filename = 'bao_cao_dashboard_30_ngay.csv'
        
        return send_file(
            BytesIO(output.getvalue().encode('utf-8-sig')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        print(f"Error exporting dashboard CSV: {e}")
        return "Lỗi khi xuất báo cáo", 500
    finally:
        conn.close()

@app.route('/export_dashboard_pdf')
@login_required(role=['admin', 'operator'])
def export_dashboard_pdf():
    # Lấy tham số từ URL
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')
    
    conn = get_db_connection()
    
    try:
        # Lấy dữ liệu sessions (giống như CSV)
        if from_date and to_date:
            sessions_data = conn.execute("""
                SELECT 
                    v.license_plate,
                    ps.entry_time,
                    ps.exit_time,
                    ps.parking_fee,
                    ps.status,
                    pslot.slot_number
                FROM ParkingSession ps 
                JOIN Vehicle v ON ps.vehicle_id = v.vehicle_id
                LEFT JOIN ParkingSlot pslot ON ps.slot_id = pslot.slot_id
                WHERE date(ps.entry_time) BETWEEN ? AND ?
                ORDER BY ps.entry_time DESC
            """, (from_date, to_date)).fetchall()
        else:
            sessions_data = conn.execute("""
                SELECT 
                    v.license_plate,
                    ps.entry_time,
                    ps.exit_time,
                    ps.parking_fee,
                    ps.status,
                    pslot.slot_number
                FROM ParkingSession ps 
                JOIN Vehicle v ON ps.vehicle_id = v.vehicle_id
                LEFT JOIN ParkingSlot pslot ON ps.slot_id = pslot.slot_id
                WHERE date(ps.entry_time) >= date('now', '-30 days')
                ORDER BY ps.entry_time DESC
            """).fetchall()
        
        # Lấy thống kê (giống như CSV)
        active_sessions = conn.execute("SELECT COUNT(*) FROM ParkingSession WHERE status = 'in_progress'").fetchone()[0]
        total_revenue = conn.execute("SELECT SUM(parking_fee) FROM ParkingSession WHERE status = 'completed'").fetchone()[0] or 0.0
        
        if from_date and to_date:
            filtered_revenue = conn.execute("""
                SELECT SUM(parking_fee) FROM ParkingSession 
                WHERE status = 'completed' AND date(entry_time) BETWEEN ? AND ?
            """, (from_date, to_date)).fetchone()[0] or 0.0
        else:
            filtered_revenue = conn.execute("""
                SELECT SUM(parking_fee) FROM ParkingSession 
                WHERE status = 'completed' AND date(entry_time) >= date('now', '-30 days')
            """).fetchone()[0] or 0.0
        
        # Tạo PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, 
                              rightMargin=72, leftMargin=72, 
                              topMargin=72, bottomMargin=18)
        
        elements = []
        styles = getSampleStyleSheet()
        
        # Tiêu đề
        title_style = styles['Heading1']
        title_style.alignment = 1
        title = Paragraph("BÁO CÁO DASHBOARD - HỆ THỐNG BÃI XE", title_style)
        elements.append(title)
        elements.append(Spacer(1, 0.3*inch))
        
        # Thông tin thời gian
        time_range = f"{from_date} đến {to_date}" if from_date and to_date else "30 ngày gần đây"
        info_text = f"Thời gian: {time_range}<br/>Ngày xuất báo cáo: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        info = Paragraph(info_text, styles['Normal'])
        elements.append(info)
        elements.append(Spacer(1, 0.2*inch))
        
        # Thống kê tổng quan
        stats_style = styles['Heading2']
        stats_style.alignment = 0
        stats_title = Paragraph("THỐNG KÊ TỔNG QUAN", stats_style)
        elements.append(stats_title)
        elements.append(Spacer(1, 0.1*inch))
        
        # Bảng thống kê
        stats_data = [
            ['Chỉ số', 'Giá trị'],
            ['Xe đang trong bãi', str(active_sessions)],
            ['Tổng doanh thu', f'{total_revenue:,.0f} VND'],
            ['Doanh thu khoảng thời gian', f'{filtered_revenue:,.0f} VND']
        ]
        
        stats_table = Table(stats_data, colWidths=[3*inch, 2*inch])
        stats_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(stats_table)
        elements.append(Spacer(1, 0.3*inch))
        
        # Chi tiết lịch sử
        if sessions_data:
            details_title = Paragraph("CHI TIẾT LỊCH SỬ GỬI XE", stats_style)
            elements.append(details_title)
            elements.append(Spacer(1, 0.1*inch))
            
            # Header bảng chi tiết
            detail_data = [['Biển số', 'Thời gian vào', 'Thời gian ra', 'Phí (VND)', 'Trạng thái']]
            
            # Data chi tiết
            for session in sessions_data[:50]:  # Giới hạn 50 bản ghi cho PDF
                entry_time = session['entry_time']
                if isinstance(entry_time, datetime):
                    entry_time_str = entry_time.strftime('%d/%m %H:%M')
                elif isinstance(entry_time, str):
                    entry_time_str = entry_time[:16].replace('T', ' ')
                else:
                    entry_time_str = str(entry_time)
                
                exit_time = session['exit_time']
                if exit_time:
                    if isinstance(exit_time, datetime):
                        exit_time_str = exit_time.strftime('%d/%m %H:%M')
                    elif isinstance(exit_time, str):
                        exit_time_str = exit_time[:16].replace('T', ' ')
                    else:
                        exit_time_str = str(exit_time)
                else:
                    exit_time_str = 'Đang gửi'
                
                status_map = {
                    'completed': 'Hoàn thành',
                    'in_progress': 'Đang gửi'
                }
                status = status_map.get(session['status'], session['status'])
                
                detail_data.append([
                    session['license_plate'],
                    entry_time_str,
                    exit_time_str,
                    f'{session["parking_fee"] or 0:,.0f}',
                    status
                ])
            
            # Tạo bảng chi tiết
            detail_table = Table(detail_data, colWidths=[1.2*inch, 1.5*inch, 1.5*inch, 1.0*inch, 1.0*inch])
            detail_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black)
            ]))
            elements.append(detail_table)
            
            if len(sessions_data) > 50:
                elements.append(Spacer(1, 0.1*inch))
                note = Paragraph(f"<i>Hiển thị 50/{len(sessions_data)} bản ghi đầu tiên</i>", styles['Italic'])
                elements.append(note)
        else:
            no_data = Paragraph("Không có dữ liệu phiên đỗ xe trong khoảng thời gian này.", styles['Normal'])
            elements.append(no_data)
        
        # Build PDF
        doc.build(elements)
        buffer.seek(0)
        
        # Tạo filename
        if from_date and to_date:
            filename = f'bao_cao_dashboard_{from_date}_den_{to_date}.pdf'
        else:
            filename = 'bao_cao_dashboard_30_ngay.pdf'
        
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        print(f"Error exporting dashboard PDF: {e}")
        return "Lỗi khi xuất báo cáo", 500
    finally:
        conn.close()



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)