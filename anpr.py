import easyocr
import cv2
import numpy as np
from PIL import Image
import re

# Khởi tạo EasyOCR
reader = easyocr.Reader(['vi', 'en'], gpu=False)

def validate_license_plate(plate):
    pattern = r'^\d{2}[A-Z]\d{3}\.\d{2}$|^\d{2}[A-Z][-]\d{3}\.\d{2}$'
    return re.match(pattern, plate) is not None

def recognize_license_plate(image):
    if isinstance(image, Image.Image):
        image = image.resize((640, 480))
        image_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    else:
        image_cv = image
    
    # Tiền xử lý ảnh
    gray = cv2.cvtColor(image_cv, cv2.COLOR_BGR2GRAY)
    alpha = 1.25
    beta = 80
    gray = cv2.convertScaleAbs(gray, alpha=alpha, beta=beta)
    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharp = cv2.filter2D(thresh, -1, kernel)
    
    # Nhận diện với EasyOCR
    results = reader.readtext(sharp)
    license_plate = ""
    for (bbox, text, confidence) in results:
        if confidence > 0.1:
            text = text.replace(' ', '').upper()
            text = text.replace(',', '.')
            if len(text) >= 3 and text[2].isdigit() and text[2] == '4':
                text = text[:2] + 'A' + text[3:]
            if len(text) >= 4 and text[3] == '.':
                text = text[:3] + '-' + text[4:]
            if re.match(r'^\d{2}[A-Z]\d{3}\.\d{2}$', text):
                prefix = text[:3]
                numbers = text[3:]
                text = prefix + '-' + numbers
            elif re.match(r'^\d{2}[A-Z]{2}\d{3}\.\d{2}$', text):
                prefix = text[:4]
                numbers = text[4:]
                text = prefix + '-' + numbers
            
            if validate_license_plate(text):
                license_plate = text
                break
    
    return license_plate if license_plate else "Không nhận diện được"