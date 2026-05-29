# Optus Bill AI Converter

The **Optus Bill AI Converter** is an intelligent web application designed to parse, extract, and clean phone call data from Optus PDF bills using Google's Gemini AI. It converts large, messy PDF invoices into clean, structured CSV files ready for spreadsheet analysis.

## 🏗️ Architecture

The application has been decoupled into two distinct modules:

1. **Frontend (`/frontend`)**
   - Built with **React** and **Vite**.
   - Provides a clean, responsive UI to upload PDFs, view extraction progress, and download the resulting CSV.
   - Communicates with the backend API via proxy in local development.

2. **Backend (`/backend`)**
   - Built with **Python** and **Flask**.
   - Handles the file uploads securely in temporary storage.
   - Extracts PDF metadata (like total pages) using `pypdf`.
   - Sends chunked pages of the PDF to the **Gemini AI API** (`gemini-flash-latest`) for precise tabular data extraction.
   - Uses `pandas` to clean, standardize, and format the extracted JSON data (e.g., standardizing dates, calculating duration in seconds, creating unique row IDs).

## 🧠 How it Works

1. **Upload:** You upload a PDF bill via the React frontend.
2. **Metadata:** The Flask API reads the PDF, counts the pages, and returns the metadata.
3. **Chunking:** The React app asks the backend to process the PDF in chunks (e.g., 5 pages at a time). This bypasses AI context limits and prevents timeouts on massive documents.
4. **AI Extraction:** The Flask backend uploads the chunk to Gemini and prompts it to extract table data for specific call types (Mobile Calls, Roaming, International, etc.).
5. **Cleaning:** The raw JSON output from Gemini is immediately fed into a Pandas dataframe where dates are normalized, call durations are converted to seconds, and a unique `Row External Id` is generated to prevent duplicate entries (especially due to the 1-page overlap during chunking).
6. **Export:** Once all chunks are processed, the React frontend deduplicates the data and constructs a downloadable CSV file.

## 🚀 Local Development

To run this application locally, you will need to start both the backend and frontend servers.

### 1. Backend Setup
Open a terminal and run the following:
```bash
cd backend
# Install Python dependencies
pip install -r requirements.txt

# Set your Gemini API key
export GEMINI_API_KEY="your-api-key-here"  # macOS/Linux
# OR
set GEMINI_API_KEY="your-api-key-here"     # Windows CMD
# OR
$env:GEMINI_API_KEY="your-api-key-here"    # Windows PowerShell

# Start the Flask API
python app.py
```
*The API will run on http://localhost:5000*

### 2. Frontend Setup
Open a second terminal and run:
```bash
cd frontend
# Install Node dependencies
npm install

# Start the Vite development server
npm run dev
```
*The frontend will be available at http://localhost:5173*

## 🐳 Production Deployment (VPS)

The project includes a multi-stage `Dockerfile`. In production, the React frontend is compiled into static files which are then served directly by the Python container using Gunicorn. This means you only need to run **one** container.

### Deploying via Docker

1. **Clone the repository on your server:**
   ```bash
   git clone https://github.com/your-username/your-repo-name.git
   cd your-repo-name
   ```

2. **Build the image:**
   ```bash
   sudo docker build -t optus-bill-converter .
   ```

3. **Run the container:**
   Map the container's port 5000 to the server's port 80 (HTTP). You must pass your Gemini API key as an environment variable using the `-e` flag.
   ```bash
   sudo docker run -d \
     -p 80:5000 \
     -e GEMINI_API_KEY="your_actual_gemini_api_key_here" \
     --name bill-converter \
     optus-bill-converter
   ```

Your app will now be live on your server's IP address!
