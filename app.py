import os
import json
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, jsonify, render_template, send_file
from pypdf import PdfReader
from werkzeug.utils import secure_filename
import uuid

# --- CONFIG ---
app = Flask(__name__, template_folder='templates')
app.config['UPLOAD_FOLDER'] = '/tmp'  # Render's temporary storage
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB limit

# Get API Key from Render Environment Variable
API_KEY = os.environ.get("GEMINI_API_KEY")
if API_KEY:
    os.environ["GOOGLE_API_USE_MTLS"] = "never"
    genai.configure(api_key=API_KEY)

# --- HELPER FUNCTIONS (From your script) ---
def clean_chunk_dataframe(data, source_filename):
    if not data: return []
    
    df = pd.DataFrame(data)
    
    # 1. Clean Columns
    required_cols = ['Duration', 'Bill Period', 'Date', 'Time', 'Service Mobile', 'Number Called']
    for col in required_cols:
        if col not in df.columns: df[col] = ""

    # 2. Duration Seconds
    df['Duration Seconds'] = df['Duration'].apply(lambda x: int(str(x).split(':')[0])*60 + int(str(x).split(':')[1]) if ':' in str(x) else 0)

    # 3. Date Format (06 Jan -> 06/01/2026)
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

    # 4. Time Format
    df['Time of Call'] = df['Time'].apply(lambda x: pd.to_datetime(x, format='%I:%M%p').strftime('%H:%M:%S') if 'm' in str(x).lower() else x)

    # 5. Bill Period
    if 'Bill Period' in df.columns:
        df[['Bill Period Start', 'Bill Period End']] = df['Bill Period'].str.split(' to ', expand=True)

    # 6. ID Generation
    df['Row External Id'] = (
        df['Date'].astype(str).str.replace(" ", "") + "_" + 
        df['Time'].astype(str).str.replace(":", "").str.replace("am","").str.replace("pm","") + "_" + 
        df['Service Mobile'].astype(str) + "_" + 
        df['Number Called'].astype(str)
    )
    
    df['Source File Name'] = source_filename
    
    # Return as list of dicts for JSON response
    final_cols = [
        'Service Mobile', 'Date of Call', 'Time of Call', 'Number Called', 
        'Duration', 'Duration Seconds', 'Bill Period Start', 'Bill Period End', 
        'Source File Name', 'Row External Id', 'Invoice Number', 'Page Number'
    ]
    # Ensure all cols exist
    for col in final_cols:
        if col not in df.columns: df[col] = ""
        
    return df[final_cols].to_dict(orient='records')

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4()}_{filename}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    file.save(filepath)
    
    # Get total pages
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
        return jsonify({"error": "File not found (session expired)"}), 404

    # --- GEMINI PROCESSING ---
    try:
        sample_file = genai.upload_file(path=filepath)
        while sample_file.state.name == "PROCESSING":
            pass # Fast loop for cloud functions

        prompt = f"""
        Analyze pages {start+1} to {end}.
        Extract EVERY row that represents a phone call, voicemail, or connection.
        Look for data in ANY table with columns for Date, Time, and Duration.
        Include sections titled: "Mobile Calls", "Other Mobile Calls", "International Calls", "Roaming", "Premium Services".
        For "Number Called": If text (e.g. Div-VoiceMailDeposit), extract text.
        Output strictly as a JSON list of objects: "Service Mobile", "Date", "Time", "Number Called", "Duration", "Bill Period", "Invoice Number", "Page Number".
        """

        model = genai.GenerativeModel(model_name="models/gemini-flash-latest")
        
        response = model.generate_content(
            [sample_file, prompt], 
            generation_config={"response_mime_type": "application/json"},
            request_options={"timeout": 600}
        )
        genai.delete_file(sample_file.name)
        
        raw_data = json.loads(response.text)
        
        # Clean data immediately
        clean_data = clean_chunk_dataframe(raw_data, original_name)
        
        return jsonify({"data": clean_data})

    except Exception as e:
        print(f"Error: {e}")
        # Return empty list on error to allow frontend to continue
        return jsonify({"data": [], "error": str(e)})

if __name__ == '__main__':
    if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
