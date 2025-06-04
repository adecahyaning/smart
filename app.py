from insight_db import init_db, log_upload, get_insight
from flask_cors import CORS
from flask import Flask, request, jsonify
from flask import render_template_string
import os
import psycopg2
import fitz
import re
import logging
import requests
import json
from werkzeug.utils import secure_filename

DB_CONFIG = {
    "host": os.getenv("PGHOST"),
    "port": os.getenv("PGPORT"),
    "dbname": os.getenv("PGDATABASE"),
    "user": os.getenv("PGUSER"),
    "password": os.getenv("PGPASSWORD"),
}

# Konfigurasi logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s:%(name)s:%(message)s"
)
log = logging.getLogger('werkzeug')
log.setLevel(logging.DEBUG)

# Inisialisasi Flask
app = Flask(__name__)
CORS(app)
UPLOAD_FOLDER = "uploads"
init_db()
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ------------------ UTILITAS PDF ------------------

def remove_illegal_chars(text):
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', "", text)

def extract_text_with_fitz(pdf_path):
    with fitz.open(pdf_path) as doc:
        return "\n".join(page.get_text("text") for page in doc)

def extract_text_from_pdf(pdf_path):
    text = extract_text_with_fitz(pdf_path)
    return remove_illegal_chars(text)

def extract_abstract(text):
    abstract_match = re.search(r"(?i)\bA\s*B\s*S\s*T\s*R\s*A\s*C\s*T\b", text)
    stop_heading_pattern = (
        r"(?im)^("
        r"(Keywords|Kata\s*Kunci)\s*[:\-]?\s*(.*)?$|"
        r"(Introduction|Latar\s*Belakang|Chapter\s*1|Bab\s*1|"
        r"(?:Chapter|Bab)?\s*(?:1|I)\.?\s+(?:Introduction|Latar\s*Belakang)|"
        r"Notation|Background)"
        r")\s*[:\-]?\s*$"
    )

    if abstract_match:
        abstract_start = abstract_match.end()
        stop_after_abstract = re.search(stop_heading_pattern, text[abstract_start:])
        if stop_after_abstract:
            abstract_end = abstract_start + stop_after_abstract.start()
            return text[abstract_start:abstract_end].strip()
        else:
            return " ".join(text[abstract_start:].split()[:300])
    else:
        stop_match = re.search(stop_heading_pattern, text)
        if stop_match:
            pre = text[:stop_match.start()].rstrip()
            paras = list(re.finditer(r'\n\s*\n', pre))
            if paras:
                return pre[paras[-1].end():].strip()
            else:
                return " ".join(pre.split()[-300:])
        else:
            return " ".join(text.split()[:300])

# ------------------ PROSES PDF + API AURORA ------------------

# def classify_with_aurora(abstract):
#     url = "https://aurora-sdg.labs.vu.nl/classifier/classify/aurora-sdg-multi"
#     headers = {"Content-Type": "application/json"}
#     payload = json.dumps({"text": abstract})

#     try:
#         response = requests.post(url, headers=headers, data=payload)
#         if response.status_code == 200:
#             predictions = response.json().get("predictions", [])
#             filtered = [
#                 {
#                     "label": p["sdg"]["label"],
#                     "score": round(p["prediction"] * 100, 2)
#                 }
#                 for p in predictions if p["prediction"] >= 0.15
#             ]
#             logging.info("✅ SDG Classification Result:")
#             for item in filtered:
#                 logging.info(f"- {item['label']}: {item['score']}%")
#             return filtered
#         else:
#             logging.error(f"❌ Gagal panggil API Aurora: {response.status_code}")
#             return []
#     except Exception as e:
#         logging.error(f"❌ Error saat memanggil API Aurora: {str(e)}")
#         return []

def classify_with_aurora(abstract):
    url = "https://aurora-sdg.labs.vu.nl/classifier/classify/aurora-sdg-multi"
    headers = {"Content-Type": "application/json"}
    payload = json.dumps({"text": abstract})

    try:
        response = requests.post(url, headers=headers, data=payload)
        if response.status_code == 200:
            predictions = response.json().get("predictions", [])

            all_sdg_scores = {
                p["sdg"]["label"]: round(p["prediction"] * 100, 2)
                for p in predictions
            }

            # Logging top N atau semua
            logging.info("✅ SDG Classification (All):")
            for label, score in sorted(all_sdg_scores.items(), key=lambda x: x[1], reverse=True):
                logging.info(f"- {label}: {score}%")

            return all_sdg_scores  # ← dikembalikan dalam format dict langsung
        else:
            logging.error(f"❌ Gagal panggil API Aurora: {response.status_code}")
            return {}
    except Exception as e:
        logging.error(f"❌ Error saat memanggil API Aurora: {str(e)}")
        return {}


def process_single_pdf(pdf_path):
    try:
        full_text = extract_text_from_pdf(pdf_path)
        abstract = extract_abstract(full_text)
        sdg_result = classify_with_aurora(abstract)
        return {
            "status": "success",
            "abstract": abstract,
            "sdg": sdg_result
        }
    except Exception as e:
        logging.error(f"❌ Error di process_single_pdf: {str(e)}")
        return {"status": "error", "message": str(e)}

# ------------------ ROUTES ------------------

@app.route("/", methods=["GET"])
def index():
    return "✅ API is running. Use /extract-abstract or /forminator-webhook."

@app.route("/extract-abstract", methods=["POST"])
def extract_abstract_api():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"status": "error", "message": "Filename is empty."}), 400

    filename = secure_filename(file.filename)
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(file_path)

    # 🔴 Log upload setelah file disimpan
    log_upload(filename, request.remote_addr)

    result = process_single_pdf(file_path)
    os.remove(file_path)
    return jsonify(result)

@app.route("/forminator-webhook", methods=["POST"])
def forminator_webhook():
    data = request.json
    logging.debug("📥 Received data from Forminator: %s", data)

    upload_data = data.get("upload_1")
    if isinstance(upload_data, dict):
        file_url = upload_data.get("file_url")
    elif isinstance(upload_data, str):
        file_url = upload_data
    else:
        file_url = None

    if not file_url:
        return jsonify({"status": "error", "message": "No valid file URL provided."}), 400

    file_url = file_url.replace("http://", "https://")

    try:
        response = requests.get(file_url)
        if response.status_code != 200:
            return jsonify({"status": "error", "message": "Failed to download file."}), 400

        filename = "uploaded.pdf"
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        with open(file_path, "wb") as f:
            f.write(response.content)

        # 🔴 Log upload setelah file disimpan
        log_upload(filename, request.remote_addr)

        result = process_single_pdf(file_path)
        os.remove(file_path)

        return jsonify(result)
    except Exception as e:
        logging.error(f"❌ Error in webhook: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# @app.route("/admin")
# def admin_page():
#     total, latest, recent = get_insight()
#     html = f"""
#     <h2>📊 Platform Insight</h2>
#     <p><strong>Total uploads:</strong> {total}</p>
#     <p><strong>Last upload:</strong> {latest}</p>
#     <h3>🕘 Last 10 uploads:</h3>
#     <ul>
#     """
#     for filename, time, ip in recent:
#         html += f"<li>{time} — {filename} ({ip})</li>"
#     html += "</ul>"
#     return html

@app.route("/admin", methods=["GET"])
def admin_dashboard():
    total, last_upload, recent = get_insight()

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Platform Insight</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 40px;
                background-color: #f9f9f9;
                color: #333;
            }}
            h1 {{
                color: #4A148C;
            }}
            .section {{
                background-color: #fff;
                padding: 20px;
                margin-bottom: 30px;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.05);
            }}
            ul {{
                padding-left: 20px;
            }}
            li {{
                margin-bottom: 10px;
            }}
            .icon {{
                font-size: 1.3em;
                margin-right: 5px;
            }}
        </style>
    </head>
    <body>
        <div class="section">
            <h1>📊 Platform Insight</h1>
            <p><strong>Total uploads:</strong> {total}</p>
            <p><strong>Last upload:</strong> {last_upload}</p>
        </div>

        <div class="section">
            <h2 class="icon">🕒 Last 10 uploads:</h2>
            <ul>
                {''.join(f'<li>{t} — {f} ({ip})</li>' for t, f, ip in recent)}
            </ul>

        </div>
    </body>
    </html>
    """
    return render_template_string(html)

# ------------------ RUN ------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
