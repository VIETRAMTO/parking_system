import hmac
import hashlib
import urllib.parse
from datetime import datetime, timedelta

VNP_TMNCODE = "VNR5SL3C"
VNP_HASH_SECRET = "LJHZSDIZURKRQDK4E73WWFZTJ4JY1RNQ"
VNP_URL = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
VNP_RETURN_URL = "https://mourningly-unstructural-adonis.ngrok-free.app"

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

def handle_vnpay_return(params):
    response_code = params.get("vnp_ResponseCode")
    secure_hash = params.get("vnp_SecureHash")
    txn_ref = params.get("vnp_TxnRef")
    amount = int(params.get("vnp_Amount", 0)) / 100 if params.get("vnp_Amount") else 0

    params_dict = {k: v for k, v in params.items()}
    if 'vnp_SecureHash' in params_dict:
        del params_dict['vnp_SecureHash']
    sorted_params = sorted(params_dict.items())
    query = urllib.parse.urlencode(sorted_params)
    h = hmac.new(VNP_HASH_SECRET.encode(), query.encode(), hashlib.sha512)
    calculated_hash = h.hexdigest()

    conn = get_db_connection()
    trans = conn.execute("SELECT user_id, amount FROM PaymentTransaction WHERE transaction_code = ?", (txn_ref,)).fetchone()

    result = {'status': 'failed', 'response_code': response_code, 'amount': amount}
    if trans and secure_hash == calculated_hash and response_code == '00':
        user_id = trans['user_id']
        user = conn.execute("SELECT balance FROM User WHERE user_id = ?", (user_id,)).fetchone()
        new_balance = user['balance'] + amount
        conn.execute("UPDATE PaymentTransaction SET status = 'completed', transaction_time = ? WHERE transaction_code = ?",
                     (datetime.now(), txn_ref))
        conn.execute("UPDATE User SET balance = ? WHERE user_id = ?", (new_balance, user_id))
        conn.commit()
        result['status'] = 'success'
    elif trans:
        conn.execute("UPDATE PaymentTransaction SET status = 'failed' WHERE transaction_code = ?", (txn_ref,))
        conn.commit()
    conn.close()
    return result