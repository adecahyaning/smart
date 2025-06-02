from flask import Flask, request, jsonify
import os
import logging
from werkzeug.utils import secure_filename
import fitz
import pytesseract
import requests
import cv2
import numpy as np
import re

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ------------------ PDF Utilities ------------------

def remove_illegal_chars(text):
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', "", text)

def extract_text_with_fitz(pdf_path):
    with fitz.open(pdf_path) as doc:
        return "\n".join(page.get_text("text") for page in doc)

def extract_text_from_image(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return pytesseract.image_to_string(binary, lang="eng")

def extract_text_with_ocr(pdf_path):
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        pix = page.get_pixmap()
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        text += extract_text_from_image(img) + "\n"
    return text

def extract_text_from_pdf(pdf_path):
    text = extract_text_with_fitz(pdf_path)
    cleaned = remove_illegal_chars(text)
    if len(cleaned.strip()) < 500:
        cleaned = remove_illegal_chars(extract_text_with_ocr(pdf_path))
    return cleaned

def extract_abstract(text):
    match = re.search(r"(?i)\bA\s*B\s*S\s*T\s*R\s*A\s*C\s*T\b", text)
    stop_pattern = r"(?im)^((Keywords|Kata\s*Kunci|Introduction|Latar\s*Belakang|Chapter\s*1|Bab\s*1)[^\n]*)$"
    if match:
        start = match.end()
        stop = re.search(stop_pattern, text[start:])
        if stop:
            return text[start:start+stop.start()].strip()
        return " ".join(text[start:].split()[:300])
    else:
        stop = re.search(stop_pattern, text)
        if stop:
            before = text[:stop.start()]
            paras = list(re.finditer(r'\n\s*\n', before))
            return before[paras[-1].end():].strip() if paras else " ".join(before.split()[-300:])
        return " ".join(text.split()[:300])

def process_single_pdf(path):
    try:
        full_text = extract_text_from_pdf(path)
        abstract = extract_abstract(full_text)
        return {"status": "success", "abstract": abstract}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ------------------ Routes ------------------

@app.route("/", methods=["GET"])
def index():
    return "✅ API is running.========="

@app.route("/extract-abstract", methods=["POST"])
def extract_abstract_api():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"status": "error", "message": "Empty filename"}), 400

    path = os.path.join(UPLOAD_FOLDER, secure_filename(file.filename))
    file.save(path)
    result = process_single_pdf(path)
    os.remove(path)
    return jsonify(result)

@app.route("/forminator-webhook", methods=["POST"])
def forminator_webhook():
    data = request.json
    logging.debug("Received data from Forminator: %s", data)

    file_url = data.get("upload_1")  # ✅ Langsung ambil string URL


    if not file_url:
        return jsonify({"status": "error", "message": "No file_url found."}), 400

    try:
        r = requests.get(file_url)
        if r.status_code != 200:
            return jsonify({"status": "error", "message": "Failed to download file"}), 400

        filename = "uploaded.pdf"
        path = os.path.join(UPLOAD_FOLDER, filename)
        with open(path, "wb") as f:
            f.write(r.content)

        result = process_single_pdf(path)
        os.remove(path)
        logging.debug("Abstract result: %s", result)
        return jsonify(result)

    except Exception as e:
        logging.error("Error: %s", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------ Run ------------------

if __name__ == "__main__":
    app.run(debug=True)
