import os
import time
import json
import pandas as pd
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
import uuid
import google.generativeai as genai
from pypdf import PdfReader

# --- CONFIG ---
app = Flask(__name__, template_folder='templates')
app.config['UPLOAD_FOLDER'] = '/tmp'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

API_KEY = os.environ.get("GEMINI_API_KEY")
if API_KEY:
    os.environ["GOOGLE_API_USE_MTLS"] = "never" 
    genai.configure(api_key=API_KEY)

# --- HELPER FUNCTIONS ---
def clean_chunk_dataframe(data, source_filename):
    if not data: return []
    df = pd.DataFrame(data)
    
    # --- ADDED LOGIC: Remove summary rows mistakenly captured by the AI ---
    if not df.empty and 'Duration' in df.columns:
        df = df[~df['Duration'].astype(str).str.contains('minutes &|hours', case=False, na=False)]
    
    if df.empty: return [] # Return early if filtering removed all rows
    
    required_cols = ['Duration', 'Bill Period', 'Date', 'Time', 'Service Mobile', 'Number Called']
    for col in required_cols:
        if col not in df.columns: df[col] = ""

    # 1. Duration Seconds
    def safe_calc_seconds(val):
        try:
            parts = str(val).split(':')
            return int(parts[0])*60 + int(parts[1])
        except: return 0
    df['Duration Seconds'] = df['Duration'].apply(safe_calc_seconds)

    # 2. Date Format
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

    # 3. Time Format
    df['Time of Call'] = df['Time'].apply(lambda x: pd.to_datetime(x, format='%I:%M%p').strftime('%H:%M:%S') if 'm' in str(x).lower() else x)

    # 4. Bill Period
    if 'Bill Period' in df.columns:
        df[['Bill Period Start', 'Bill Period End']] = df['Bill Period'].str.split(' to ', expand=True)

    # 5. Exact ID Generation for deduplication
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
        reader = PdfReader(filepath)
        total_pages = len(reader.pages)
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

    try:
        sample_file = genai.upload_file(path=filepath)
        
        while sample_file.state.name == "PROCESSING":
            time.sleep(1)
            sample_file = genai.get_file(sample_file.name)

        prompt = f"""
        Analyze pages {start+1} to {end}.
        Extract EVERY row that represents a phone call, voicemail, or connection.
        
        Look for data in ANY table with columns for Date, Time, and Duration.
        Include sections titled: "Mobile Calls", "Other Mobile Calls", "International Calls", "Roaming", "Premium Services".
        
        For "Number Called":
        - If it is a phone number, extract it.
        - If it is text (e.g., "Div-VoiceMailDeposit", "Weather"), extract that text.
        
        IMPORTANT: Use only standard JSON values. NEVER use 'NaN', 'Infinity', or 'null'. 
        If a field is missing, use an empty string "".
        
        Output strictly as a JSON list of objects with these keys: 
        "Service Mobile", "Date", "Time", "Number Called", "Duration", "Bill Period", "Invoice Number", "Page Number".
        """

        model = genai.GenerativeModel(model_name="models/gemini-flash-latest")
        
        response = model.generate_content(
            [sample_file, prompt], 
            generation_config={"response_mime_type": "application/json"},
            request_options={"timeout": 600}
        )
        
        genai.delete_file(sample_file.name)
        
        # --- THE SAFETY NET ---
        response_text = response.text
        # Replace invalid NaN tokens with empty strings before parsing
        safe_text = response_text.replace(": NaN", ': ""').replace(":NaN", ': ""')
        
        raw_data = json.loads(safe_text)
        clean_data = clean_chunk_dataframe(raw_data, original_name)
        
        return jsonify({"data": clean_data})

    except Exception as e:
        if 'sample_file' in locals():
            try: genai.delete_file(sample_file.name)
            except: pass
        print(f"Error processing chunk {start}-{end}: {e}")
        return jsonify({"data": [], "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
