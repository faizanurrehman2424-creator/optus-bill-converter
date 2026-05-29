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

  const startProcess = async () => {
    if (!selectedFile) return
    setIsProcessing(true)
    setDownloadUrl(null)
    setLogs([])
    setProgress(0)
    setStatusText('Preparing...')

    const allRecords = []
    addLog("Uploading PDF to server...")
    
    const formData = new FormData()
    formData.append('file', selectedFile)

    try {
      const uploadRes = await fetch('/upload', { method: 'POST', body: formData })
      const uploadData = await uploadRes.json()
      
      if (uploadData.error) throw new Error(uploadData.error)
      
      const serverFilename = uploadData.filename
      const origName = uploadData.original_name
      setOriginalFilename(origName)
      const totalPages = uploadData.total_pages
      
      addLog(`Upload complete. Total pages: ${totalPages}`)
      
      const chunkSize = 5
      const overlap = 1
      const step = chunkSize - overlap
      
      for (let i = 0; i < totalPages; i += step) {
        let start = i
        let end = Math.min(i + chunkSize, totalPages)
        
        if (start >= totalPages) break
        if (i > 0 && end === totalPages && (end - start) < 2) continue

        addLog(`Processing pages ${start+1} to ${end}...`)
        
        const chunkRes = await fetch('/process_chunk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: serverFilename,
            original_name: origName,
            start: start,
            end: end
          })
        })
        
        const chunkJson = await chunkRes.json()
        if (chunkJson.data) {
          allRecords.push(...chunkJson.data)
          addLog(`✓ Found ${chunkJson.data.length} rows`)
        }
        
        const currentProgress = Math.min(100, Math.round((end / totalPages) * 100))
        setProgress(currentProgress)
      }

      addLog("Processing complete. Deduplicating...")
      finishProcess(allRecords, origName)

    } catch (err) {
      addLog(`ERROR: ${err.message}`)
      alert("An error occurred. Check logs.")
    } finally {
      setIsProcessing(false)
    }
  }

  const finishProcess = (records, origName) => {
    const unique = new Map()
    records.forEach(r => unique.set(r['Row External Id'], r))
    const finalData = Array.from(unique.values())
    
    addLog(`Final clean count: ${finalData.length} rows`)
    setStatusText("Done!")
    
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
      <div className="warning">⚠️ Large bills (50+ pages) may take 10-15 minutes. Please do not close this tab.</div>
      
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
            📥 Download CSV
          </button>
        </a>
      )}
    </div>
  )
}

export default App
