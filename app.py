import os
import time
import json
import base64
import pandas as pd
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
import uuid
import fitz  # PyMuPDF
from openai import AzureOpenAI

# --- CONFIG ---
app = Flask(__name__, template_folder='templates')
app.config['UPLOAD_FOLDER'] = '/tmp'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# --- AZURE OPENAI SETUP ---
AZURE_KEY = os.environ.get("AZURE_OPENAI_KEY")
ENDPOINT = "https://crhr-model-testing.openai.azure.com/"
DEPLOYMENT_NAME = "gpt-4.1-nano"

client = None
if AZURE_KEY:
    client = AzureOpenAI(
        api_key=AZURE_KEY,
        api_version="2024-05-01-preview", 
        azure_endpoint=ENDPOINT
    )

# --- HELPER FUNCTIONS ---
def clean_chunk_dataframe(data, source_filename):
    if not data: return []
    df = pd.DataFrame(data)
    
    required_cols = ['Duration', 'Bill Period', 'Date', 'Time', 'Service Mobile', 'Number Called']
    for col in required_cols:
        if col not in df.columns: df[col] = ""

    def safe_calc_seconds(val):
        try:
            parts = str(val).split(':')
            return int(parts[0])*60 + int(parts[1])
        except: return 0
    df['Duration Seconds'] = df['Duration'].apply(safe_calc_seconds)

    month_map = {"Jan":"01", "Feb":"02", "Mar":"03", "Apr":"04", "May":"05", "Jun":"06", 
                 "Jul":"07", "Aug":"08", "Sep":"09", "Oct":"10", "Nov":"11", "Dec":"12"}
    def fix_date(d):
        try:
            parts = str(d).split()
            if len(parts) < 2: return d
            day, mon = parts[0], parts[1]
            year = "2025" if mon == "Dec" else "2026"
            return f"{day.zfill(2)}/{month_map.get(mon, '01')}/{year}"
        except: return d
    df['Date of Call'] = df['Date'].apply(fix_date)

    df['Time of Call'] = df['Time'].apply(lambda x: pd.to_datetime(x, format='%I:%M%p').strftime('%H:%M:%S') if 'm' in str(x).lower() else x)

    if 'Bill Period' in df.columns:
        df[['Bill Period Start', 'Bill Period End']] = df['Bill Period'].str.split(' to ', expand=True)

    df['Row External Id'] = (
        df['Date'].astype(str).str.replace(" ", "") + "_" + 
        df['Time'].astype(str).str.replace(":", "").str.replace("am","").str.replace("pm","") + "_" + 
        df['Service Mobile'].astype(str) + "_" + 
        df['Number Called'].astype(str)
    )
    
    df['Source File Name'] = source_filename
    
    final_cols = [
        'Service Mobile', 'Date of Call', 'Time of Call', 'Number Called', 
        'Duration', 'Duration Seconds', 'Bill Period Start', 'Bill Period End', 
        'Source File Name', 'Row External Id', 'Invoice Number', 'Page Number'
    ]
    for col in final_cols:
        if col not in df.columns: df[col] = ""
        
    return df[final_cols].to_dict(orient='records')

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files: return jsonify({"error": "No file"}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({"error": "No filename"}), 400
    
    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4()}_{filename}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    file.save(filepath)
    
    try:
        doc = fitz.open(filepath)
        total_pages = len(doc)
        doc.close()
        return jsonify({
            "filename": unique_name, 
            "original_name": filename,
            "total_pages": total_pages
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/process_chunk', methods=['POST'])
def process_chunk_route():
    data = request.json
    filename = data.get('filename')
    original_name = data.get('original_name')
    start = int(data.get('start'))
    end = int(data.get('end'))
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    if not client:
        return jsonify({"error": "Azure OpenAI Client not initialized. Check API Key."}), 500

    try:
        doc = fitz.open(filepath)
        all_chunk_data = []
        
        # Process PAGE BY PAGE for maximum aggressive extraction
        for i in range(start, end):
            if i >= len(doc): break
            page = doc.load_page(i)
            
            # 1. Get raw text to prevent the AI from missing numbers
            raw_text = page.get_text("text") 
            
            # 2. Get high-res 3x image to preserve column alignment
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3)) 
            img_bytes = pix.tobytes("jpeg")
            base64_image = base64.b64encode(img_bytes).decode('utf-8')
            
            prompt = f"""
            SYSTEM: You are an aggressive data recovery engine. 
            USER: Extract EVERY SINGLE ROW from the call tables on this page.
            
            REFERENCE TEXT (Use this for exact numbers and dates):
            {raw_text}
            
            VISUAL IMAGE: (Use this to understand which column is which)
            [IMAGE PROVIDED BELOW]
            
            RULES:
            1. DO NOT SUMMARIZE. DO NOT SKIP ROWS.
            2. Extract "Mobile Calls", "Other Mobile Calls" (e.g. Div-VoiceMailDeposit), "International Calls", "Roaming", "Premium Services".
            3. If the 'Number Called' is text (like 'Weather'), extract the text.
            4. IMPORTANT: NEVER use 'NaN' or 'null' as a value. If a field is blank or missing, use an empty string "".
            
            You must return a JSON object with a single key "calls" containing a list of objects.
            Required keys per object: "Service Mobile" (from header), "Date", "Time", "Number Called", "Duration", "Bill Period", "Invoice Number", "Page Number".
            """

            response = client.chat.completions.create(
                model=DEPLOYMENT_NAME,
                messages=[
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]}
                ],
                response_format={ "type": "json_object" },
                temperature=0.0
            )
            
            # --- THE SAFETY NET: Fix invalid JSON tokens before parsing ---
            response_text = response.choices[0].message.content
            # Replace JavaScript NaN with empty strings to prevent DecodeError
            safe_text = response_text.replace(": NaN", ': ""').replace(":NaN", ':-""')
            
            try:
                page_data = json.loads(safe_text)
                all_chunk_data.extend(page_data.get("calls", []))
            except json.JSONDecodeError as e:
                print(f"JSON Decode Error on Page {i+1}: {e}")
                # We log it but continue so we don't lose the whole chunk
                continue
            
        doc.close()

        # Clean all data from this chunk
        clean_data = clean_chunk_dataframe(all_chunk_data, original_name)
        
        return jsonify({"data": clean_data})

    except Exception as e:
        print(f"Error processing chunk {start}-{end}: {e}")
        return jsonify({"data": [], "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
