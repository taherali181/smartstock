import {
  AlertTriangle,
  ArrowLeft,
  Box,
  Camera,
  Check,
  ChevronRight,
  CloudOff,
  Download,
  PackageCheck,
  Play,
  RefreshCw,
  RotateCcw,
  ScanLine,
  Search,
  Signal,
  TriangleAlert,
  WifiOff,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRegisterSW } from 'virtual:pwa-register/react'
import {
  discardWarehouseCommand,
  enqueueWarehouseCommand,
  readCachedTasks,
  readQueuedCommands,
  refreshWarehouseTasks,
  retryWarehouseCommand,
  scanMatchesWarehouseTask,
  syncWarehouseQueue,
  warehouseCacheEvent,
  type QueuedWarehouseCommand,
  type WarehouseTask,
  type WarehouseTaskCommand,
} from '../data/warehouseOffline'

interface WarehouseWorkspaceProps {
  onExit: () => void
}

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

interface BarcodeResult {
  rawValue: string
}

interface BarcodeDetectorInstance {
  detect: (source: ImageBitmapSource) => Promise<BarcodeResult[]>
}

interface BarcodeDetectorConstructor {
  new (options?: { formats?: string[] }): BarcodeDetectorInstance
}

const taskTypes: Array<WarehouseTask['task_type'] | 'all'> = [
  'all', 'receive', 'putaway', 'pick', 'pack', 'transfer', 'count', 'replenish',
]

const activeStates = new Set<WarehouseTask['state']>(['open', 'assigned', 'in_progress', 'exception'])

function typeLabel(value: string) {
  return value.replace('-', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function shortId(value: string | null) {
  return value ? value.slice(0, 8).toUpperCase() : '—'
}

function commandLabel(command: WarehouseTaskCommand) {
  return {
    start: 'Start task',
    complete: 'Complete task',
    'report-exception': 'Report exception',
    reopen: 'Reopen task',
  }[command]
}

export function WarehouseWorkspace({ onExit }: WarehouseWorkspaceProps) {
  const [tasks, setTasks] = useState<WarehouseTask[]>([])
  const [queue, setQueue] = useState<QueuedWarehouseCommand[]>([])
  const [online, setOnline] = useState(navigator.onLine)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filter, setFilter] = useState<(typeof taskTypes)[number]>('all')
  const [query, setQuery] = useState('')
  const [scannerOpen, setScannerOpen] = useState(false)
  const [verifiedTaskId, setVerifiedTaskId] = useState<string | null>(null)
  const [installPrompt, setInstallPrompt] = useState<InstallPromptEvent | null>(null)

  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW()

  const loadCache = useCallback(async () => {
    const [cachedTasks, queuedCommands] = await Promise.all([
      readCachedTasks(),
      readQueuedCommands(),
    ])
    setTasks(cachedTasks)
    setQueue(queuedCommands)
    setLoading(false)
  }, [])

  const synchronize = useCallback(async (showResult = false) => {
    if (!navigator.onLine) return
    setSyncing(true)
    try {
      const result = await syncWarehouseQueue()
      if (result.remaining === 0) await refreshWarehouseTasks()
      if (showResult) {
        setMessage(result.blocked
          ? `${result.blocked} task command needs review.`
          : result.completed
            ? `${result.completed} task command${result.completed === 1 ? '' : 's'} synchronized.`
            : 'Warehouse tasks are up to date.')
      }
    } catch (error) {
      setMessage(error instanceof Error
        ? `Synchronization paused: ${error.message}`
        : 'The server is unavailable. Cached tasks remain ready offline.')
    } finally {
      await loadCache()
      setSyncing(false)
    }
  }, [loadCache])

  useEffect(() => {
    let active = true
    const initialize = async () => {
      await loadCache()
      if (!active || !navigator.onLine) return
      await synchronize()
    }
    void initialize()

    const cacheChanged = () => void loadCache()
    const wentOnline = () => {
      setOnline(true)
      void synchronize(true)
    }
    const wentOffline = () => setOnline(false)
    const captureInstall = (event: Event) => {
      event.preventDefault()
      setInstallPrompt(event as InstallPromptEvent)
    }
    window.addEventListener(warehouseCacheEvent(), cacheChanged)
    window.addEventListener('online', wentOnline)
    window.addEventListener('offline', wentOffline)
    window.addEventListener('beforeinstallprompt', captureInstall)
    return () => {
      active = false
      window.removeEventListener(warehouseCacheEvent(), cacheChanged)
      window.removeEventListener('online', wentOnline)
      window.removeEventListener('offline', wentOffline)
      window.removeEventListener('beforeinstallprompt', captureInstall)
    }
  }, [loadCache, synchronize])

  const selectedTask = tasks.find((task) => task.id === selectedId) ?? null
  const visibleTasks = useMemo(() => tasks.filter((task) => {
    if (filter !== 'all' && task.task_type !== filter) return false
    const needle = query.trim().toLowerCase()
    if (!needle) return true
    return [task.task_number, task.task_type, task.state, task.product_id, task.reference_type]
      .some((value) => value?.toLowerCase().includes(needle))
  }), [filter, query, tasks])
  const activeCount = tasks.filter((task) => activeStates.has(task.state)).length
  const blockedCount = queue.filter((item) => item.status === 'conflict' || item.status === 'failed').length

  async function runCommand(
    task: WarehouseTask,
    command: WarehouseTaskCommand,
    countedQuantity?: string,
  ) {
    if (command === 'complete' && verifiedTaskId !== task.id) {
      setScannerOpen(true)
      setMessage('Scan the task, product, or location before completing this task.')
      return
    }
    try {
      const optimistic = await enqueueWarehouseCommand(task, command, { countedQuantity })
      setSelectedId(optimistic.id)
      setVerifiedTaskId(null)
      setMessage(online ? `${commandLabel(command)} queued for safe synchronization.` : `${commandLabel(command)} saved offline.`)
      await loadCache()
      if (online) await synchronize()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'The task action could not be saved.')
    }
  }

  function acceptScan(rawValue: string) {
    const value = rawValue.trim().toLowerCase()
    if (!value) return false
    const matchingTask = selectedTask ?? tasks.find((task) =>
      task.task_number.toLowerCase() === value || task.id.toLowerCase() === value,
    ) ?? null
    if (!matchingTask) {
      setMessage('This barcode does not match a cached warehouse task.')
      return false
    }
    if (!scanMatchesWarehouseTask(matchingTask, value)) {
      setMessage('The scan does not match the selected task, product, or location.')
      return false
    }
    setSelectedId(matchingTask.id)
    setVerifiedTaskId(matchingTask.id)
    setMessage(`${matchingTask.task_number} verified.`)
    setScannerOpen(false)
    return true
  }

  return <main className="warehouse-app">
    <header className="warehouse-topbar">
      <button className="warehouse-back" type="button" onClick={onExit} aria-label="Back to conversational workspace">
        <ArrowLeft size={20} />
      </button>
      <div className="warehouse-brand">
        <span>WMS</span>
        <strong>Warehouse execution</strong>
      </div>
      <div className={`network-state ${online ? 'online' : 'offline'}`}>
        {online ? <Signal size={16} /> : <WifiOff size={16} />}
        <span>{online ? 'Online' : 'Offline'}</span>
      </div>
    </header>

    {!online && <div className="offline-banner"><CloudOff size={16} /> Working from the local task cache. Actions will synchronize when the connection returns.</div>}
    {needRefresh && <div className="update-banner">A warehouse app update is ready.<button type="button" onClick={() => void updateServiceWorker(true)}>Reload update</button><button type="button" aria-label="Dismiss update" onClick={() => setNeedRefresh(false)}><X size={15} /></button></div>}
    {message && <div className="warehouse-toast" role="status">{message}<button type="button" onClick={() => setMessage(null)} aria-label="Dismiss message"><X size={15} /></button></div>}

    <section className="warehouse-summary">
      <div><span>ACTIVE</span><strong>{activeCount}</strong></div>
      <div><span>QUEUED</span><strong>{queue.length}</strong></div>
      <div data-alert={blockedCount > 0}><span>BLOCKED</span><strong>{blockedCount}</strong></div>
      <button type="button" onClick={() => void synchronize(true)} disabled={!online || syncing}>
        <RefreshCw size={17} className={syncing ? 'spin' : ''} /> {syncing ? 'Syncing' : 'Sync now'}
      </button>
    </section>

    <section className="warehouse-toolbar">
      <label className="warehouse-search"><Search size={18} /><span className="visually-hidden">Search tasks</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Task, product, or status" /></label>
      <button className="scan-trigger" type="button" onClick={() => setScannerOpen(true)}><ScanLine size={19} /> Scan</button>
    </section>

    <nav className="warehouse-filters" aria-label="Task types">
      {taskTypes.map((item) => <button key={item} className={filter === item ? 'active' : ''} type="button" onClick={() => setFilter(item)}>{typeLabel(item)}</button>)}
    </nav>

    <section className="warehouse-content">
      <div className={`task-list ${selectedTask ? 'has-selection' : ''}`}>
        {loading ? <WarehouseEmpty icon={<RefreshCw className="spin" />} title="Loading task cache" detail="Preparing the offline workspace…" />
          : visibleTasks.length === 0 ? <WarehouseEmpty icon={<PackageCheck />} title="No matching tasks" detail={tasks.length ? 'Change the task type or search.' : online ? 'There are no warehouse tasks assigned to this workspace.' : 'Connect once to cache warehouse tasks on this device.'} />
            : visibleTasks.map((task) => {
              const queued = queue.filter((item) => item.taskId === task.id)
              const blocked = queued.some((item) => item.status === 'conflict' || item.status === 'failed')
              return <button key={task.id} className={`task-card ${selectedId === task.id ? 'selected' : ''}`} type="button" onClick={() => setSelectedId(task.id)}>
                <span className="task-type-icon"><Box size={19} /></span>
                <span className="task-card-copy">
                  <span className="task-card-heading"><strong>{task.task_number}</strong><small>PRIORITY {task.priority}</small></span>
                  <span>{typeLabel(task.task_type)} · {task.quantity ? `${task.quantity} ${task.uom ?? ''}` : 'Quantity on reference'}</span>
                  <span className={`task-state state-${task.state}`}>{typeLabel(task.state)}</span>
                </span>
                {blocked ? <TriangleAlert className="task-alert" size={18} /> : queued.length ? <span className="queued-dot" title="Queued offline" /> : <ChevronRight size={18} />}
              </button>
            })}
      </div>

      {selectedTask && <TaskDetail
        task={selectedTask}
        queue={queue.filter((item) => item.taskId === selectedTask.id)}
        verified={verifiedTaskId === selectedTask.id}
        onClose={() => setSelectedId(null)}
        onScan={() => setScannerOpen(true)}
        onCommand={(command, countedQuantity) => void runCommand(selectedTask, command, countedQuantity)}
        onDiscard={async (id) => { await discardWarehouseCommand(id); await loadCache() }}
        onRetry={async (id) => { setSyncing(true); await retryWarehouseCommand(id); await loadCache(); setSyncing(false) }}
      />}
    </section>

    {installPrompt && <button className="install-app" type="button" onClick={async () => {
      await installPrompt.prompt()
      await installPrompt.userChoice
      setInstallPrompt(null)
    }}><Download size={17} /> Install warehouse app</button>}

    {scannerOpen && <Scanner onClose={() => setScannerOpen(false)} onScan={acceptScan} />}
  </main>
}

function TaskDetail({ task, queue, verified, onClose, onScan, onCommand, onDiscard, onRetry }: {
  task: WarehouseTask
  queue: QueuedWarehouseCommand[]
  verified: boolean
  onClose: () => void
  onScan: () => void
  onCommand: (command: WarehouseTaskCommand, countedQuantity?: string) => void
  onDiscard: (id: string) => void
  onRetry: (id: string) => void
}) {
  const [countedQuantity, setCountedQuantity] = useState('')
  const validCount = /^\d+(\.\d+)?$/.test(countedQuantity.trim())

  return <aside className="task-detail">
    <div className="task-detail-header">
      <div><span>{typeLabel(task.task_type)}</span><h1>{task.task_number}</h1></div>
      <button type="button" onClick={onClose} aria-label="Close task"><X size={20} /></button>
    </div>
    <div className={`verification-card ${verified ? 'verified' : ''}`}>
      {verified ? <Check size={21} /> : <ScanLine size={21} />}
      <span><strong>{verified ? 'Task verified' : 'Verification required'}</strong><small>{verified ? 'The scanned identifier matches this task.' : 'Scan the task, product, or location before completion.'}</small></span>
      {!verified && <button type="button" onClick={onScan}>Scan</button>}
    </div>
    <dl className="task-facts">
      <div><dt>Status</dt><dd className={`task-state state-${task.state}`}>{typeLabel(task.state)}</dd></div>
      <div><dt>Quantity</dt><dd>{task.quantity ?? 'From reference'} {task.uom ?? ''}</dd></div>
      <div><dt>Product</dt><dd>{shortId(task.product_id)}</dd></div>
      <div><dt>From</dt><dd>{shortId(task.source_location_id)}</dd></div>
      <div><dt>To</dt><dd>{shortId(task.destination_location_id)}</dd></div>
      <div><dt>Reference</dt><dd>{task.reference_type ? `${task.reference_type} / ${shortId(task.reference_id)}` : '—'}</dd></div>
      <div><dt>Record version</dt><dd>v{task.version}</dd></div>
    </dl>

    {task.task_type === 'count' && task.state === 'in_progress' && <section className="count-entry">
      <label htmlFor={`counted-quantity-${task.id}`}>Counted quantity</label>
      <div>
        <input
          id={`counted-quantity-${task.id}`}
          type="number"
          inputMode="decimal"
          min="0"
          step="any"
          value={countedQuantity}
          onChange={(event) => setCountedQuantity(event.target.value)}
          placeholder="0"
          autoComplete="off"
        />
        <span>{task.uom}</span>
      </div>
      <small>Blind count: the expected quantity stays hidden until this task is posted.</small>
    </section>}

    {queue.length > 0 && <section className="task-sync-log">
      <h2>Synchronization</h2>
      {queue.map((item) => <div key={item.id} className={`sync-command sync-${item.status}`}>
        {item.status === 'conflict' || item.status === 'failed' ? <AlertTriangle size={17} /> : <RefreshCw size={17} className={item.status === 'syncing' ? 'spin' : ''} />}
        <span><strong>{commandLabel(item.command)}</strong><small>{item.error ?? typeLabel(item.status)}</small></span>
        {(item.status === 'conflict' || item.status === 'failed') && <span className="sync-actions">
          {item.status === 'failed' && <button type="button" onClick={() => onRetry(item.id)}>Retry</button>}
          <button type="button" onClick={() => onDiscard(item.id)}>{item.status === 'conflict' ? 'Discard & refresh' : 'Discard'}</button>
        </span>}
      </div>)}
    </section>}

    <div className="task-actions">
      {(task.state === 'open' || task.state === 'assigned' || task.state === 'exception') && <button className="task-primary" type="button" onClick={() => onCommand('start')}><Play size={18} /> Start task</button>}
      {task.state === 'in_progress' && <>
        <button
          className="task-primary"
          type="button"
          onClick={() => onCommand('complete', task.task_type === 'count' ? countedQuantity : undefined)}
          disabled={!verified || (task.task_type === 'count' && !validCount)}
        ><Check size={18} /> {task.task_type === 'count' ? 'Post count' : 'Complete task'}</button>
        <button className="task-danger" type="button" onClick={() => onCommand('report-exception')}><TriangleAlert size={18} /> Report exception</button>
      </>}
      {task.state === 'exception' && <button className="task-secondary" type="button" onClick={() => onCommand('reopen')}><RotateCcw size={18} /> Return to queue</button>}
      {(task.state === 'completed' || task.state === 'cancelled') && <p>This task is read-only. Its execution history remains in the audit log.</p>}
    </div>
  </aside>
}

function WarehouseEmpty({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) {
  return <div className="warehouse-empty">{icon}<strong>{title}</strong><p>{detail}</p></div>
}

function Scanner({ onClose, onScan }: { onClose: () => void; onScan: (value: string) => boolean }) {
  const [value, setValue] = useState('')
  const [cameraActive, setCameraActive] = useState(false)
  const [cameraError, setCameraError] = useState<string | null>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    setCameraActive(false)
  }, [])

  useEffect(() => stopCamera, [stopCamera])

  useEffect(() => {
    if (!cameraActive || !videoRef.current) return
    const Detector = (window as typeof window & { BarcodeDetector?: BarcodeDetectorConstructor }).BarcodeDetector
    if (!Detector) return
    const detector = new Detector({ formats: ['code_128', 'code_39', 'ean_13', 'ean_8', 'qr_code', 'data_matrix'] })
    let stopped = false
    let timer = 0
    const detect = async () => {
      if (stopped || !videoRef.current) return
      try {
        const results = await detector.detect(videoRef.current)
        if (results[0]?.rawValue && onScan(results[0].rawValue)) return
      } catch {
        // Individual camera frames can fail while autofocus settles.
      }
      timer = window.setTimeout(detect, 350)
    }
    timer = window.setTimeout(detect, 500)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [cameraActive, onScan])

  async function startCamera() {
    const Detector = (window as typeof window & { BarcodeDetector?: BarcodeDetectorConstructor }).BarcodeDetector
    if (!Detector) {
      setCameraError('Camera barcode detection is not supported by this browser. Use the scanner keyboard or enter the code below.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      streamRef.current = stream
      setCameraActive(true)
      setCameraError(null)
      window.setTimeout(() => {
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          void videoRef.current.play()
        }
      }, 0)
    } catch {
      setCameraError('Camera access was unavailable. You can still use a paired scanner or type the code.')
    }
  }

  return <div className="scanner-backdrop" role="dialog" aria-modal="true" aria-labelledby="scanner-title">
    <section className="scanner-sheet">
      <div className="scanner-header"><div><span>BARCODE VERIFICATION</span><h1 id="scanner-title">Scan warehouse label</h1></div><button type="button" onClick={() => { stopCamera(); onClose() }} aria-label="Close scanner"><X size={20} /></button></div>
      <div className={`camera-viewport ${cameraActive ? 'active' : ''}`}>
        {cameraActive ? <video ref={videoRef} muted playsInline /> : <><Camera size={34} /><p>Use the device camera, a paired hardware scanner, or manual entry.</p><button type="button" onClick={() => void startCamera()}><Camera size={17} /> Start camera</button></>}
        {cameraActive && <span className="camera-target" />}
      </div>
      {cameraError && <p className="camera-error"><AlertTriangle size={16} /> {cameraError}</p>}
      <form onSubmit={(event) => { event.preventDefault(); if (onScan(value)) stopCamera() }}>
        <label htmlFor="barcode-entry">Scanner or label value</label>
        <div><ScanLine size={19} /><input id="barcode-entry" autoFocus value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" placeholder="Scan or enter task / product / location" /><button type="submit" disabled={!value.trim()}>Verify</button></div>
      </form>
    </section>
  </div>
}
