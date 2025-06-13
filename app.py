from insight_db import init_db, log_upload, get_insight
from flask_cors import CORS
from flask import Flask, request, jsonify, request, send_file, render_template_string
import os
import psycopg2
import fitz
import re
import logging
import requests
import json
from werkzeug.utils import secure_filename
from fpdf import FPDF
import io
from io import BytesIO
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch
from reportlab.pdfbase import ttfonts
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics

pdfmetrics.registerFont(TTFont("Cambria", "static/fonts/cambria.ttf"))

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
        </style>
    </head>
    <body>
        <div class="section">
            <h1>📊 Platform Insight</h1>
            <p><strong>Total uploads:</strong> {total}</p>
            <p><strong>Last upload:</strong> {last_upload}</p>
        </div>

        <div class="section">
            <h2>🕒 Last 10 uploads:</h2>
            <ul>
                {''.join(f'<li>{t} — {f} ({ip}) - {loc}</li>' for f, t, ip, loc in recent)}
            </ul>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)


# @app.route('/download-result', methods=['POST'])
# def download_result():
#     data = request.get_json()
#     abstract = data.get('abstract', '')
#     sdg = data.get('sdg', {})

#     pdf = FPDF()
#     pdf.add_page()
#     pdf.set_font("Arial", size=12)

#     pdf.multi_cell(0, 10, f"Abstract:\n{abstract}\n")
#     pdf.ln(5)
#     pdf.cell(0, 10, "SDG Classification Results:", ln=True)

#     for label, score in sdg.items():
#         pdf.cell(0, 10, f"{label}: {score}%", ln=True)

#     # Save PDF to memory
#     # Save PDF to memory (fix with 'S' mode)
#     buffer = io.BytesIO()
#     pdf_output = pdf.output(dest='S').encode('latin1')
#     buffer.write(pdf_output)
#     buffer.seek(0)


#     return send_file(buffer, as_attachment=True, download_name="sdg_result.pdf", mimetype='application/pdf')

@app.route('/download_result', methods=['POST'])
def download_result():
    data = request.get_json()
    abstract = data.get("abstract", "")
    sdg_scores = data.get("sdg", {})

    # Prepare PDF in memory
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()


    title_style = ParagraphStyle(
        name="Title",
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        textColor=HexColor("#31572C"),
        spaceAfter=20
    )

    normal_style = styles["Normal"]
    normal_style.spaceAfter = 12
    justified_style = ParagraphStyle(
        name="Justified",
        parent=normal_style,
        alignment=TA_JUSTIFY,
        fontSize=10,
        fontName="Cambria"
    )
    normal_style.fontName = "Cambria"

    elements = []

    # Title
    elements.append(Paragraph("SDG Mapping and Assessment Report", title_style))
    elements.append(Spacer(1, 12))

    # General Notes
    notes = """
    This application performs Sustainable Development Goal (SDG) classification based on the abstract extracted from a PDF document.
    The document is parsed using the fitz library (PyMuPDF), which allows structured reading and text extraction.<br/><br/>
    The application first attempts to detect and extract the abstract section from the PDF. If an abstract is not detected, 
    the fallback mechanism extracts the first 500 words from the document as a proxy for the abstract.<br/><br/>
    The extracted text is then analyzed using the Aurora SDG multi-label mBERT model (https://aurora-sdg.labs.vu.nl/sdg-classifier/text). 
    This model performs multi-label classification across all 17 Sustainable Development Goals (SDGs).<br/><br/>
    The output consists of percentage scores (ranging from 0% to 100%) for each SDG, indicating the degree of relevance between the input text and each goal. 
    Multiple SDGs can be associated with a single document depending on the model’s confidence levels.<br/><br/>
    This abstract-based analysis enables efficient and scalable SDG classification.
    """
    elements.append(Paragraph(notes, justified_style))
    elements.append(Spacer(1, 18))

    # Abstract
    elements.append(Paragraph("<b>Detected Abstract</b>", normal_style))
    elements.append(Paragraph(abstract, justified_style))
    elements.append(Spacer(1, 18))

    # SDG Classification Results
    elements.append(Paragraph("<b>SDG Classification Results</b>", normal_style))

    sorted_scores = sorted(sdg_scores.items(), key=lambda x: x[1], reverse=True)
    table_data = [["SDG", "Relevance (%)"]] + [[k, f"{v:.2f}%"] for k, v in sorted_scores]

    table = Table(table_data, colWidths=[3*inch, 2*inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#31572C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#F5F5F5"), HexColor("#FFFFFF")]),
        ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#CCCCCC"))
    ]))

    elements.append(table)

    # Build and send PDF
    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="sdg_report.pdf",
        mimetype="application/pdf"
    )

# ------------------ RUN ------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
