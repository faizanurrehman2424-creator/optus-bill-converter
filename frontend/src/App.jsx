import { useState, useRef } from 'react'
import './App.css'

function App() {
  const [selectedFile, setSelectedFile] = useState(null)
  const [isProcessing, setIsProcessing] = useState(false)
  const [progress, setProgress] = useState(0)
  const [statusText, setStatusText] = useState('Preparing...')
  const [logs, setLogs] = useState([])
  const [downloadUrl, setDownloadUrl] = useState(null)
  const [originalFilename, setOriginalFilename] = useState("")

  const fileInputRef = useRef(null)
  const logBoxRef = useRef(null)

  const addLog = (msg) => {
    setLogs((prev) => [...prev, msg])
    setTimeout(() => {
      if (logBoxRef.current) {
        logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight
      }
    }, 10)
  }

  const handleFileChange = (e) => {
    if (e.target.files[0]) {
      setSelectedFile(e.target.files[0])
      setDownloadUrl(null)
      setProgress(0)
      setLogs([])
    }
  }

  const fetchChunkWithRetry = async (payload, maxRetries = 3) => {
    let lastError = null
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        const res = await fetch('/process_chunk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        })

        const rawText = await res.text()
        if (!rawText || !rawText.trim()) {
          throw new Error("Server returned empty response")
        }

        let json
        try {
          json = JSON.parse(rawText)
        } catch (parseErr) {
          throw new Error(`Invalid JSON received: ${rawText.substring(0, 80)}`)
        }

        if (!res.ok || json.error) {
          throw new Error(json.error || `HTTP ${res.status}: ${res.statusText}`)
        }

        return json
      } catch (err) {
        lastError = err
        if (attempt < maxRetries) {
          const waitSeconds = attempt * 5
          addLog(`Pages ${payload.start + 1}-${payload.end} failed (${err.message}). Retrying (${attempt}/${maxRetries}) in ${waitSeconds}s...`)
          await new Promise(r => setTimeout(r, waitSeconds * 1000))
        }
      }
    }
    throw lastError
  }

  const startProcess = async () => {
    if (!selectedFile) return
    setIsProcessing(true)
    setDownloadUrl(null)
    setLogs([])
    setProgress(0)
    setStatusText('Preparing...')

    const allRecords = []
    const failedChunks = []
    addLog("Uploading PDF to server...")
    
    const formData = new FormData()
    formData.append('file', selectedFile)

    try {
      const uploadRes = await fetch('/upload', { method: 'POST', body: formData })
      const rawUploadText = await uploadRes.text()
      if (!rawUploadText || !rawUploadText.trim()) {
        throw new Error("Server returned empty response during upload")
      }
      const uploadData = JSON.parse(rawUploadText)
      
      if (!uploadRes.ok || uploadData.error) {
        throw new Error(uploadData.error || `Upload failed with status ${uploadRes.status}`)
      }
      
      const serverFilename = uploadData.filename
      const origName = uploadData.original_name
      setOriginalFilename(origName)
      const totalPages = uploadData.total_pages
      
      addLog(`Upload complete. Total pages: ${totalPages}`)
      
      const chunkSize = 4
      const overlap = 1
      const step = chunkSize - overlap
      
      for (let i = 0; i < totalPages; i += step) {
        let start = i
        let end = Math.min(i + chunkSize, totalPages)
        
        if (start >= totalPages) break
        if (i > 0 && end === totalPages && (end - start) < 2) continue

        setStatusText(`Scanning Pages ${start + 1}-${end}`)
        addLog(`Extracting data from pages ${start + 1} to ${end}...`)
        
        try {
          const chunkJson = await fetchChunkWithRetry({
            filename: serverFilename,
            original_name: origName,
            start: start,
            end: end
          }, 3)
          
          if (chunkJson && chunkJson.data) {
            allRecords.push(...chunkJson.data)
            addLog(`Found ${chunkJson.data.length} records in pages ${start + 1}-${end}`)
          }
        } catch (chunkErr) {
          failedChunks.push({ start: start + 1, end: end, reason: chunkErr.message })
          addLog(`Warning: Failed pages ${start + 1}-${end} (${chunkErr.message}). Continuing with remaining pages...`)
        }
        
        if (end < totalPages) {
          addLog("Pausing for 5s to maintain stable connection...")
          await new Promise(r => setTimeout(r, 5000))
        }

        const currentProgress = Math.min(100, Math.round((end / totalPages) * 100))
        setProgress(currentProgress)
      }

      if (failedChunks.length > 0) {
        const failedSummary = failedChunks.map(f => `Pages ${f.start}-${f.end}`).join(", ")
        addLog(`Processing completed with skipped sections: ${failedSummary}`)
      } else {
        addLog("Processing complete. Deduplicating...")
      }

      finishProcess(allRecords, origName, failedChunks)

    } catch (err) {
      addLog(`ERROR: ${err.message}`)
      alert("An error occurred. Check logs.")
      if (allRecords.length > 0) {
        finishProcess(allRecords, origName, [{ start: 0, end: 0, reason: err.message }])
      }
    } finally {
      setIsProcessing(false)
    }
  }

  const finishProcess = (records, origName, failedChunks = []) => {
    const unique = new Map()
    records.forEach(r => {
      if (r['Row External Id']) unique.set(r['Row External Id'], r)
    })
    const finalData = Array.from(unique.values())
    
    addLog(`Final clean count: ${finalData.length} records`)
    setStatusText(failedChunks.length > 0 ? "Completed with warnings" : "Done!")
    
    if (finalData.length === 0) {
      alert("No data found!")
      return
    }
    
    const headers = Object.keys(finalData[0])
    const csvContent = [
      headers.join(','),
      ...finalData.map(row => headers.map(h => `"${row[h] || ''}"`).join(','))
    ].join('\n')
    
    const blob = new Blob([csvContent], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    setDownloadUrl(url)
  }

  return (
    <div className="container">
      <h1>Optus Bill AI Converter</h1>
      <div className="warning">Large bills (50+ pages) will process in chunks. Please keep this tab open.</div>
      
      <input 
        type="file" 
        ref={fileInputRef} 
        accept=".pdf" 
        style={{ display: 'none' }} 
        onChange={handleFileChange}
      />
      
      <div className="upload-box" onClick={() => fileInputRef.current?.click()}>
        <span>{selectedFile ? selectedFile.name : 'Click to select PDF'}</span>
      </div>

      {(isProcessing || logs.length > 0) && (
        <div className="progress-container">
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span>{statusText}</span>
            <span>{progress}%</span>
          </div>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${progress}%` }}></div>
          </div>
          <div className="log-box" ref={logBoxRef}>
            {logs.map((log, index) => (
              <div key={index}>{log}</div>
            ))}
          </div>
        </div>
      )}

      {!downloadUrl ? (
        <button 
          onClick={startProcess} 
          disabled={!selectedFile || isProcessing}
        >
          {isProcessing ? 'Extracting...' : 'Start Extraction'}
        </button>
      ) : (
        <a href={downloadUrl} download={originalFilename.replace('.pdf', '_final.csv')}>
          <button style={{ background: '#007bff' }}>
            Download CSV
          </button>
        </a>
      )}
    </div>
  )
}

export default App
