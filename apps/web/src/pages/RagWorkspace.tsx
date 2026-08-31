import {
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Boxes,
  ChevronDown,
  CircleDot,
  Clock3,
  Database,
  FileText,
  History,
  Moon,
  PackageCheck,
  Paperclip,
  Plus,
  Search,
  ShieldCheck,
  Sun,
  Truck,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useOperationalProducts } from '../data/operationalData'
import { useConversation, type Block, type CitationBlock, type CompletedBlock, type RecordSummaryBlock } from '../data/conversation'
import { useOperationalQueues } from '../data/operationalQueues'
import type { components } from '../api/schema'
import type { Product } from '../types'

export type PanelKind = 'inventory' | 'operations' | 'item' | 'order' | 'forecast' | 'sources' | 'plan' | 'history'

export type PanelEntry =
  | { kind: 'inventory'; title: string; payload: { query?: string } }
  | { kind: 'operations'; title: string; payload: Record<string, never> }
  | { kind: 'item'; title: string; payload: { product: Product } }
  | { kind: 'order'; title: string; payload: { orderId: string; product: Product } }
  | { kind: 'forecast'; title: string; payload: { product: Product } }
  | { kind: 'sources'; title: string; payload: { focus?: string } }
  | { kind: 'plan'; title: string; payload: { planId: string } }
  | { kind: 'history'; title: string; payload: Record<string, never> }

const PANEL_STORAGE_KEY = 'smartstock-panel-width'
const PANEL_MIN = 320
const PANEL_DEFAULT = 480
const PANEL_PRESETS = [360, 480, 640]
const EMPTY_PRODUCTS: Product[] = []

type QueryScope = 'All data' | 'Inventory' | 'Orders' | 'Documents'

interface SubmittedContext {
  scope: QueryScope
  attachment: string | null
}

const starterPrompts = [
  'What needs my attention today?',
  'Show products at risk of stocking out',
  'Build a replenishment plan for this week',
  'What changed in inventory value?',
]

const conversationHistory = [
  ['Weekly stock risks', 'Today, 9:24 AM'],
  ['Austin replenishment plan', 'Yesterday'],
  ['Supplier lead-time review', 'Aug 27'],
  ['Inventory value variance', 'Aug 26'],
]

interface RagWorkspaceProps {
  theme: 'dark' | 'light'
  onThemeToggle: () => void
  onOpenWarehouse: () => void
}

function maximumPanelWidth() {
  if (typeof window === 'undefined') return 720
  return Math.min(720, window.innerWidth * 0.55)
}

function clampPanelWidth(width: number) {
  return Math.round(Math.max(PANEL_MIN, Math.min(maximumPanelWidth(), width)))
}

function initialPanelWidth() {
  const saved = Number(localStorage.getItem(PANEL_STORAGE_KEY))
  return Number.isFinite(saved) && saved >= PANEL_MIN ? saved : PANEL_DEFAULT
}

export function RagWorkspace({ theme, onThemeToggle, onOpenWarehouse }: RagWorkspaceProps) {
  const productQuery = useOperationalProducts()
  const conversation = useConversation()
  const queueQuery = useOperationalQueues()
  const products = productQuery.data ?? EMPTY_PRODUCTS
  const [panelStack, setPanelStack] = useState<PanelEntry[]>([])
  const [panelWidth, setPanelWidth] = useState(initialPanelWidth)
  const [desktopLayout, setDesktopLayout] = useState(() => window.innerWidth >= 1024)
  const [question, setQuestion] = useState('')
  const [input, setInput] = useState('')
  const [hasConversation, setHasConversation] = useState(false)
  const [queryScope, setQueryScope] = useState<QueryScope>('All data')
  const [attachment, setAttachment] = useState<string | null>(null)
  const [submittedContext, setSubmittedContext] = useState<SubmittedContext>({ scope: 'All data', attachment: null })
  const [inventoryQuery, setInventoryQuery] = useState('')
  const dragState = useRef<{ x: number; width: number } | null>(null)

  const activePanel = panelStack.at(-1)
  const filteredProducts = useMemo(() => products.filter((product) =>
    `${product.name} ${product.sku}`.toLowerCase().includes(inventoryQuery.toLowerCase()),
  ), [inventoryQuery, products])

  useEffect(() => {
    const onResize = () => {
      setDesktopLayout(window.innerWidth >= 1024)
      setPanelWidth((width) => clampPanelWidth(width))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    localStorage.setItem(PANEL_STORAGE_KEY, String(panelWidth))
  }, [panelWidth])

  useEffect(() => {
    if (!activePanel) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPanelStack([])
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [activePanel])

  function ask(nextQuestion?: string) {
    const next = nextQuestion ?? input
    if (!next.trim()) return
    setQuestion(next.trim())
    setSubmittedContext({ scope: queryScope, attachment })
    setInput('')
    setAttachment(null)
    setHasConversation(true)
    void conversation.ask(next.trim())
  }

  function openPanel(entry: PanelEntry) {
    setPanelStack([entry])
  }

  function pushPanel(entry: PanelEntry) {
    setPanelStack((stack) => [...stack, entry])
  }

  function openItem(product: Product, nested = false) {
    const entry: PanelEntry = { kind: 'item', title: product.name, payload: { product } }
    if (nested) pushPanel(entry)
    else openPanel(entry)
  }

  function newConversation() {
    setHasConversation(false)
    setQuestion('')
    setInput('')
    setQueryScope('All data')
    setAttachment(null)
    setSubmittedContext({ scope: 'All data', attachment: null })
    setPanelStack([])
    conversation.reset()
  }

  function setWidth(nextWidth: number) {
    setPanelWidth(clampPanelWidth(nextWidth))
  }

  function resizeFromKeyboard(event: React.KeyboardEvent<HTMLDivElement>) {
    if (!desktopLayout) return
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      setWidth(panelWidth + 16)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      setWidth(panelWidth - 16)
    } else if (event.key === 'Home') {
      event.preventDefault()
      setWidth(PANEL_MIN)
    } else if (event.key === 'End') {
      event.preventDefault()
      setWidth(maximumPanelWidth())
    }
  }

  return (
    <div className={`rag-app ${activePanel ? 'panel-open' : ''}`}>
      <header className="rag-topbar">
        <div className="rag-brand" aria-label="SmartStock home">
          <span aria-hidden="true">S</span>
          <strong>SMARTSTOCK</strong>
        </div>

        <button className="workspace-name" type="button">
          Nova Supply Co. <ChevronDown size={14} />
        </button>

        <nav className="rag-top-actions" aria-label="Workspace controls">
          <button className="top-action new-chat-action" type="button" onClick={newConversation}><Plus size={16} /> <span>New chat</span></button>
          <button className="top-icon" type="button" onClick={onOpenWarehouse} aria-label="Open warehouse workspace"><PackageCheck size={18} /></button>
          <button className="top-icon" type="button" onClick={() => openPanel({ kind: 'history', title: 'Conversation history', payload: {} })} aria-label="Conversation history"><History size={18} /></button>
          <button className="top-icon" type="button" onClick={onThemeToggle} aria-label={`Use ${theme === 'dark' ? 'light' : 'dark'} mode`}>
            {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <button className="account-dot" type="button" aria-label="Account menu">TA</button>
        </nav>
      </header>

      <main className="rag-body">
        <section className="conversation-area" aria-label="SmartStock conversation">
          {!hasConversation ? (
            <LandingComposer input={input} onInput={setInput} onAsk={ask} scope={queryScope} onScope={setQueryScope} attachment={attachment} onAttachment={setAttachment} />
          ) : (
            <>
              <div className="thread" aria-live="polite">
                <Conversation question={question} submittedContext={submittedContext} openPanel={openPanel} blocks={conversation.blocks} streaming={conversation.streaming} error={conversation.error} recordCount={products.length} />
              </div>
              <Composer input={input} onInput={setInput} onAsk={ask} scope={queryScope} onScope={setQueryScope} attachment={attachment} onAttachment={setAttachment} />
            </>
          )}
        </section>

        {activePanel && (
          <>
            <div className="drawer-scrim" onClick={() => setPanelStack([])} aria-hidden="true" />
            <div
              className="panel-resizer"
              role="separator"
              aria-label="Resize context panel"
              aria-orientation="vertical"
              aria-valuemin={PANEL_MIN}
              aria-valuemax={Math.round(maximumPanelWidth())}
              aria-valuenow={Math.round(panelWidth)}
              tabIndex={desktopLayout ? 0 : -1}
              onKeyDown={resizeFromKeyboard}
              onPointerDown={(event) => {
                if (!desktopLayout) return
                dragState.current = { x: event.clientX, width: panelWidth }
                event.currentTarget.setPointerCapture(event.pointerId)
              }}
              onPointerMove={(event) => {
                if (!dragState.current || !desktopLayout) return
                setWidth(dragState.current.width - (event.clientX - dragState.current.x))
              }}
              onPointerUp={(event) => {
                dragState.current = null
                if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
              }}
            >
              <span />
            </div>
            <aside className="context-panel" style={{ '--panel-width': `${panelWidth}px` } as React.CSSProperties}>
              <div className="context-header">
                <div className="context-title">
                  <span>CONTEXT / {activePanel.kind}</span>
                  <h2>{activePanel.title}</h2>
                </div>
                <div className="panel-header-actions">
                  {desktopLayout && <div className="width-presets" aria-label="Panel width presets">
                    {PANEL_PRESETS.map((width) => (
                      <button key={width} type="button" className={panelWidth === clampPanelWidth(width) ? 'active' : ''} onClick={() => setWidth(width)} aria-label={`Set panel width to ${width} pixels`}>{width / 10}</button>
                    ))}
                  </div>}
                  {panelStack.length > 1 && <button type="button" onClick={() => setPanelStack((stack) => stack.slice(0, -1))} aria-label="Back"><ArrowLeft size={18} /></button>}
                  <button type="button" onClick={() => setPanelStack([])} aria-label="Close panel"><X size={18} /></button>
                </div>
              </div>

              <PanelContent
                entry={activePanel}
                products={products}
                queues={queueQuery.data}
                queuesLoading={queueQuery.isLoading}
                queuesError={queueQuery.error}
                filteredProducts={filteredProducts}
                isLoading={productQuery.isLoading}
                error={productQuery.error}
                inventoryQuery={inventoryQuery}
                setInventoryQuery={setInventoryQuery}
                pushPanel={pushPanel}
                openItem={(product) => openItem(product, true)}
                selectHistory={(title) => {
                  setQuestion(title)
                  setHasConversation(true)
                  setPanelStack([])
                }}
              />
            </aside>
          </>
        )}
      </main>
    </div>
  )
}

interface ComposerProps {
  input: string
  onInput: (value: string) => void
  onAsk: (question?: string) => void
  scope: QueryScope
  onScope: (scope: QueryScope) => void
  attachment: string | null
  onAttachment: (name: string | null) => void
  landing?: boolean
}

function LandingComposer(props: Omit<ComposerProps, 'landing'>) {
  return <div className="landing-canvas">
    <div className="landing-inner">
      <div className="landing-kicker"><CircleDot size={13} /> LIVE OPERATIONS / 6 SOURCES CONNECTED</div>
      <h1>What do you want to know?</h1>
      <p>Ask across inventory, purchase orders, suppliers, and demand.</p>
      <Composer {...props} landing />
      <div className="starter-grid" aria-label="Starter prompts">
        {starterPrompts.map((prompt, index) => <button type="button" key={prompt} onClick={() => props.onAsk(prompt)}><span>0{index + 1}</span>{prompt}<ArrowRight size={16} /></button>)}
      </div>
      <div className="landing-provenance"><ShieldCheck size={14} /><span>Answers respect your workspace permissions and cite live records and approved documents.</span></div>
    </div>
  </div>
}

function Composer({ input, onInput, onAsk, scope, onScope, attachment, onAttachment, landing = false }: ComposerProps) {
  const fileInput = useRef<HTMLInputElement>(null)
  const scopes: { label: QueryScope; icon: typeof Database }[] = [
    { label: 'All data', icon: Database },
    { label: 'Inventory', icon: Boxes },
    { label: 'Orders', icon: Truck },
    { label: 'Documents', icon: FileText },
  ]
  const submit = () => onAsk(input.trim() || (attachment ? `Review the attached file: ${attachment}` : undefined))

  return <div className={landing ? 'composer-shell landing-composer' : 'composer-shell'}>
    <div className="rag-composer">
      <div className="composer-input-row">
        <textarea
          rows={1}
          value={input}
          onChange={(event) => onInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
          placeholder={scope === 'All data' ? 'Ask SmartStock anything…' : `Ask about ${scope.toLowerCase()}…`}
          aria-label="Ask SmartStock"
        />
        <button className="composer-send" type="button" onClick={submit} disabled={!input.trim() && !attachment} aria-label="Send message"><ArrowUp size={19} /></button>
      </div>
      <div className="composer-toolbar">
        <input
          ref={fileInput}
          className="visually-hidden"
          type="file"
          accept=".csv,.xlsx,.xls,.pdf,.doc,.docx,.txt,.png,.jpg,.jpeg"
          onChange={(event) => onAttachment(event.target.files?.[0]?.name ?? null)}
        />
        <button className="attach-button" type="button" onClick={() => fileInput.current?.click()} aria-label="Attach a file"><Paperclip size={16} /><span>Attach</span></button>
        {attachment && <button className="attachment-chip" type="button" onClick={() => onAttachment(null)} title={attachment} aria-label={`Remove ${attachment}`}><FileText size={14} /><span>{attachment}</span><X size={14} /></button>}
        <div className="scope-select" aria-label="Select data scope">
          {scopes.map(({ label, icon: Icon }) => <button type="button" key={label} className={scope === label ? 'active' : ''} onClick={() => onScope(label)} aria-pressed={scope === label}><Icon size={14} /><span>{label}</span></button>)}
        </div>
      </div>
    </div>
  </div>
}

function ViewAction({ label = 'View in panel' }: { label?: string }) {
  return <span className="view-action">{label}<ArrowRight size={14} /></span>
}

function Conversation({ question, submittedContext, openPanel, blocks, streaming, error, recordCount }: { question: string; submittedContext: SubmittedContext; openPanel: (entry: PanelEntry) => void; blocks: Block[]; streaming: boolean; error: string | null; recordCount: number }) {
  const completed = blocks.find((block) => block.type === 'completed') as CompletedBlock | undefined
  const citations = blocks.filter((block) => block.type === 'citation') as CitationBlock[]
  const narrative = blocks.filter((block) => block.type !== 'citation' && block.type !== 'completed')

  return <div className="messages">
    <div className="user-message"><span>YOU</span><p>{question}</p>{(submittedContext.scope !== 'All data' || submittedContext.attachment) && <div className="user-context">{submittedContext.scope !== 'All data' && <span>{submittedContext.scope}</span>}{submittedContext.attachment && <span><Paperclip size={12} />{submittedContext.attachment}</span>}</div>}</div>
    <article className="assistant-message">
      <div className="answer-meta">
        <span className="assistant-mark">S</span><span>SMARTSTOCK</span>
        <small>
          {completed
            ? `${completed.route.toUpperCase()} · ${completed.model_profile} · ${completed.latency_ms} ms`
            : streaming ? 'READING AUTHORIZED RECORDS…' : 'LIVE OPERATIONAL DATA'}
        </small>
      </div>

      {error && <p className="answer-lead">The assistant is unavailable: {error}</p>}
      {!error && streaming && narrative.length === 0 && <p className="answer-lead">Selecting a tool and reading records…</p>}

      {narrative.map((block, index) => <BlockView key={index} block={block} />)}

      {citations.length > 0 && (
        <section className="answer-section">
          <div className="section-heading"><span>SOURCES</span><h2>{citations.length} authorized record{citations.length === 1 ? '' : 's'}</h2></div>
          <div className="inline-records">
            {citations.slice(0, 8).map((citation) => (
              <button type="button" key={`${citation.record_type}:${citation.record_id}`} onClick={() => openPanel({ kind: 'sources', title: citation.label, payload: { focus: citation.record_id } })}>
                <span className="record-state" data-status="Healthy" />
                <span className="record-copy"><strong>{citation.label}</strong><small>{citation.record_type}{citation.version != null ? ` · v${citation.version}` : ''} · {new Date(citation.observed_at).toLocaleTimeString()}</small></span>
                <ViewAction />
              </button>
            ))}
          </div>
        </section>
      )}

      {completed?.abstained && (
        <p className="answer-lead"><ShieldCheck size={14} /> No answer was generated. Nothing was inferred from records that were not read.</p>
      )}

      <div className="answer-sources">
        <span><ShieldCheck size={14} /> AUTHORIZED OPERATIONAL RECORDS</span>
        <button type="button" onClick={() => openPanel({ kind: 'inventory', title: 'Inventory overview', payload: {} })}><Database size={14} /> {recordCount} LIVE RECORDS <ViewAction /></button>
      </div>
    </article>
  </div>
}

function BlockView({ block }: { block: Block }) {
  if (block.type === 'answer_text') {
    return <p className="answer-lead">{String((block as { text: string }).text)}</p>
  }
  if (block.type === 'recommendation') {
    const item = block as { text: string; rationale: string | null }
    return <p className="answer-lead"><strong>{item.text}</strong>{item.rationale ? ` — ${item.rationale}` : ''}</p>
  }
  if (block.type === 'clarification') {
    const item = block as { question: string; options: string[] }
    return <section className="answer-section">
      <p className="answer-lead">{item.question}</p>
      <ul className="clarification-options">{item.options.map((option) => <li key={option}>{option}</li>)}</ul>
    </section>
  }
  if (block.type === 'warning') {
    const item = block as { message: string; code: string | null }
    return <p className="answer-warning" data-code={item.code ?? undefined}>{item.message}</p>
  }
  if (block.type === 'error') {
    return <p className="answer-warning">{String((block as { message: string }).message)}</p>
  }
  if (block.type === 'record_summary') {
    const table = block as RecordSummaryBlock
    return <section className="answer-section">
      <div className="section-heading"><span>RECORDS</span><h2>{table.title}</h2></div>
      <div className="record-table-scroll">
        <table className="record-table">
          <thead><tr>{table.columns.map((column) => <th key={column}>{column.replace(/_/g, ' ')}</th>)}</tr></thead>
          <tbody>
            {table.rows.slice(0, 25).map((row, index) => (
              <tr key={index}>{table.columns.map((column) => <td key={column}>{row[column] == null ? '—' : String(row[column])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.row_count > 25 && <small className="record-more">Showing 25 of {table.row_count}</small>}
    </section>
  }
  return null
}

interface PanelContentProps {
  entry: PanelEntry
  products: Product[]
  queues: ReturnType<typeof useOperationalQueues>['data']
  queuesLoading: boolean
  queuesError: Error | null
  filteredProducts: Product[]
  isLoading: boolean
  error: Error | null
  inventoryQuery: string
  setInventoryQuery: (query: string) => void
  pushPanel: (entry: PanelEntry) => void
  openItem: (product: Product) => void
  selectHistory: (title: string) => void
}

function PanelContent(props: PanelContentProps) {
  const { entry } = props
  switch (entry.kind) {
    case 'inventory': return <InventoryPanel products={props.filteredProducts} query={props.inventoryQuery} onQuery={props.setInventoryQuery} onOpenItem={props.openItem} isLoading={props.isLoading} error={props.error} />
    case 'operations': return <OperationsPanel queues={props.queues} isLoading={props.queuesLoading} error={props.queuesError} />
    case 'item': return <ItemPanel product={entry.payload.product} pushPanel={props.pushPanel} />
    case 'order': return <OrderPanel orderId={entry.payload.orderId} product={entry.payload.product} pushPanel={props.pushPanel} />
    case 'forecast': return <ForecastPanel product={entry.payload.product} pushPanel={props.pushPanel} />
    case 'sources': return <SourcesPanel pushPanel={props.pushPanel} focus={entry.payload.focus} products={props.products} />
    case 'plan': return <PlanPanel pushPanel={props.pushPanel} products={props.products} />
    case 'history': return <HistoryPanel onSelect={props.selectHistory} />
  }
}

type OrderRecord = components['schemas']['OrderResponse']
type TaskRecord = components['schemas']['WarehouseTaskResponse']

function OperationsPanel({ queues, isLoading, error }: { queues: ReturnType<typeof useOperationalQueues>['data']; isLoading: boolean; error: Error | null }) {
  if (isLoading) return <div className="context-content"><p className="context-intro">Loading operational queues…</p></div>
  if (error) return <div className="context-content"><p className="context-intro">{error.message}</p></div>
  const purchaseOrders: OrderRecord[] = queues?.purchaseOrders ?? []
  const salesOrders: OrderRecord[] = queues?.salesOrders ?? []
  const tasks: TaskRecord[] = queues?.tasks ?? []
  return <div className="context-content">
    <div className="detail-grid"><div><span>PURCHASE ORDERS</span><strong>{purchaseOrders.length}</strong></div><div><span>SALES ORDERS</span><strong>{salesOrders.length}</strong></div><div><span>OPEN TASKS</span><strong>{tasks.filter((task) => !['completed', 'cancelled'].includes(task.state)).length}</strong></div><div><span>EXCEPTIONS</span><strong>{tasks.filter((task) => task.state === 'exception').length}</strong></div></div>
    <section className="context-section"><h3>WAREHOUSE TASKS <span>{tasks.length}</span></h3>{tasks.length === 0 && <p className="context-intro">No warehouse tasks are queued.</p>}{tasks.map((task) => <div className="timeline-row" key={task.id}><PackageCheck size={15} /><span><strong>{task.task_number}</strong><small>{task.task_type.toUpperCase()} · {task.state.replace('_', ' ')} · priority {task.priority}</small></span></div>)}</section>
    <section className="context-section"><h3>PURCHASING <span>{purchaseOrders.length}</span></h3>{purchaseOrders.map((order) => <div className="timeline-row" key={order.id}><Truck size={15} /><span><strong>{order.order_number}</strong><small>{order.state.replaceAll('_', ' ')} · {order.lines.length} line{order.lines.length === 1 ? '' : 's'} · {order.currency} {order.total}</small></span></div>)}</section>
    <section className="context-section"><h3>SALES <span>{salesOrders.length}</span></h3>{salesOrders.map((order) => <div className="timeline-row" key={order.id}><PackageCheck size={15} /><span><strong>{order.order_number}</strong><small>{order.state.replaceAll('_', ' ')} · {order.lines.length} line{order.lines.length === 1 ? '' : 's'} · {order.currency} {order.total}</small></span></div>)}</section>
  </div>
}

function SourcesPanel({ pushPanel, focus, products }: { pushPanel: (entry: PanelEntry) => void; focus?: string; products: Product[] }) {
  const primary = products[0]
  return <div className="context-content">
    <p className="context-intro">This answer combines current operational records with approved supplier documents. Each claim can be inspected at its source.</p>
    <section className="context-section"><h3>LIVE RECORDS <span>4</span></h3>
      {primary ? <button className="source-row" type="button" onClick={() => pushPanel({ kind: 'item', title: primary.name, payload: { product: primary } })}><Database size={16} /><span><strong>Inventory position</strong><small>{primary.name} · {primary.warehouse}</small></span><ViewAction /></button> : <p className="context-intro">No operational records are available.</p>}
    </section>
    <section className={`context-section ${focus === 'documents' ? 'focused-section' : ''}`}><h3>DOCUMENTS <span>2</span></h3>
      <div className="document-row"><FileText size={16} /><span><strong>Nova Manufacturing terms</strong><small>PDF · Updated Aug 12</small></span><span>VERIFIED</span></div>
      <div className="document-row"><FileText size={16} /><span><strong>Inbound receiving policy</strong><small>DOCX · Updated Jul 28</small></span><span>VERIFIED</span></div>
    </section>
    <div className="grounding-note"><ShieldCheck size={17} /><span><strong>PERMISSION-AWARE RETRIEVAL</strong><small>Only sources available to your Nova Supply workspace were used.</small></span></div>
  </div>
}

function ItemPanel({ product, pushPanel }: { product: Product; pushPanel: (entry: PanelEntry) => void }) {
  return <div className="context-content">
    <div className="item-heading"><span className="item-monogram">{product.name.slice(0, 2).toUpperCase()}</span><div><h3>{product.name}</h3><p>{product.sku} · {product.category}</p></div><span className="status-tag" data-status={product.status}>{product.status}</span></div>
    <div className="detail-grid"><div><span>AVAILABLE</span><strong>{product.available}</strong></div><div><span>COMMITTED</span><strong>{product.committed}</strong></div><div><span>INCOMING</span><strong>{product.incoming || '—'}</strong></div><div><span>REORDER AT</span><strong>{product.reorderAt}</strong></div></div>
    <section className="context-section detail-list"><h3>STOCK POSITION</h3><div><span>Location</span><strong>{product.warehouse}</strong></div><div><span>Unit price</span><strong>${product.price.toFixed(2)}</strong></div><div><span>Days of cover</span><strong>{product.available === 0 ? '0 days' : '9 days'}</strong></div></section>
    <section className="context-section"><h3>RECENT ACTIVITY</h3><div className="timeline-row"><Clock3 size={15} /><span><strong>12 units allocated</strong><small>Sales order SO-8402 · 2h ago</small></span></div><div className="timeline-row"><Clock3 size={15} /><span><strong>Inbound date updated</strong><small>Purchase order PO-2051 · Yesterday</small></span></div></section>
    <button className="panel-secondary" type="button" onClick={() => pushPanel({ kind: 'forecast', title: `${product.name} forecast`, payload: { product } })}>View demand forecast <ArrowRight size={15} /></button>
    <button className="panel-secondary" type="button" onClick={() => pushPanel({ kind: 'sources', title: 'Supporting sources', payload: { focus: product.sku } })}>View supporting sources <ArrowRight size={15} /></button>
  </div>
}

function InventoryPanel({ products: list, query, onQuery, onOpenItem, isLoading, error }: { products: Product[]; query: string; onQuery: (query: string) => void; onOpenItem: (product: Product) => void; isLoading: boolean; error: Error | null }) {
  const attention = list.filter((product) => product.status !== 'Healthy').length
  return <div className="context-content"><label className="panel-search"><Search size={17} /><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search item or SKU" /></label><div className="panel-filter-row"><button type="button" className="active">All / {list.length}</button><button type="button">Attention / {attention}</button></div>{isLoading && <p className="context-intro">Loading authorized inventory…</p>}{error && <p className="context-intro">{error.message}</p>}{!isLoading && !error && list.length === 0 && <p className="context-intro">No matching products.</p>}<section className="inventory-panel-list">{list.map((product) => <button type="button" key={product.id} onClick={() => onOpenItem(product)}><span className="item-monogram small">{product.name.slice(0, 2).toUpperCase()}</span><span><strong>{product.name}</strong><small>{product.sku} · {product.warehouse}</small></span><span className="quantity">{product.available}<small>AVAILABLE</small></span><ViewAction /></button>)}</section></div>
}

function OrderPanel({ orderId, product, pushPanel }: { orderId: string; product: Product; pushPanel: (entry: PanelEntry) => void }) {
  return <div className="context-content"><div className="order-hero"><Truck size={22} /><span>IN TRANSIT</span><strong>200 units</strong><small>Expected September 4, 2026</small></div><section className="context-section detail-list"><h3>ORDER DETAILS</h3><div><span>Purchase order</span><strong>{orderId}</strong></div><div><span>Supplier</span><strong>Nova Manufacturing</strong></div><div><span>Destination</span><strong>Austin Central</strong></div><div><span>Tracking</span><strong>Confirmation pending</strong></div></section><section className="context-section"><h3>LINE ITEM</h3><button className="source-row" type="button" onClick={() => pushPanel({ kind: 'item', title: product.name, payload: { product } })}><PackageCheck size={16} /><span><strong>{product.name}</strong><small>{product.sku} · 200 units</small></span><ViewAction /></button></section><button className="panel-primary" type="button">Request carrier confirmation</button><button className="panel-secondary" type="button" onClick={() => pushPanel({ kind: 'sources', title: 'Order evidence', payload: { focus: orderId } })}>View order evidence <ArrowRight size={15} /></button></div>
}

function ForecastPanel({ product, pushPanel }: { product: Product; pushPanel: (entry: PanelEntry) => void }) {
  const bars = [72, 60, 66, 48, 39, 29, 18, 9, 4]
  return <div className="context-content"><div className="forecast-heading"><span>14-DAY PROJECTION</span><strong>Stockout exposure</strong><p>Inventory is already below safety stock and projected demand remains elevated.</p></div><div className="forecast-chart" aria-label="Projected inventory decreasing over nine days">{bars.map((height, index) => <div key={index}><i style={{ height: `${height}%` }} data-risk={index > 5} /><span>{index === 0 ? 'NOW' : index === 4 ? 'SEP 02' : index === 8 ? 'SEP 06' : ''}</span></div>)}</div><div className="forecast-metrics"><div><span>DAILY DEMAND</span><strong>12.4</strong></div><div><span>SAFETY STOCK</span><strong>60</strong></div><div><span>CONFIDENCE</span><strong>87%</strong></div></div><section className="context-section detail-list"><h3>MODEL INPUTS</h3><div><span>Sales history</span><strong>90 days</strong></div><div><span>Open orders</span><strong>14 orders</strong></div><div><span>Inbound supply</span><strong>200 units</strong></div></section><button className="panel-secondary" type="button" onClick={() => pushPanel({ kind: 'item', title: product.name, payload: { product } })}>View item record <ArrowRight size={15} /></button><button className="panel-secondary" type="button" onClick={() => pushPanel({ kind: 'sources', title: 'Forecast evidence', payload: { focus: 'forecast' } })}>Inspect model sources <ArrowRight size={15} /></button></div>
}

function PlanPanel({ pushPanel, products }: { pushPanel: (entry: PanelEntry) => void; products: Product[] }) {
  const planProducts = products.filter((product) => product.status !== 'Healthy').slice(0, 3)
  return <div className="context-content"><p className="context-intro">These actions are drafts. Nothing changes until you review and approve them.</p><div className="plan-summary"><span>ESTIMATED IMPACT / 30 DAYS</span><strong>$8,420</strong><small>REVENUE PROTECTED</small></div><section className="plan-list">{planProducts.map((product, index) => <button type="button" key={product.sku} onClick={() => pushPanel({ kind: 'item', title: product.name, payload: { product } })}><span className="plan-number">0{index + 1}</span><span><strong>{index === 0 ? 'Expedite inbound shipment' : index === 1 ? 'Create purchase order' : 'Transfer between locations'}</strong><small>{product.name} · {index === 0 ? '200 units' : index === 1 ? '96 units' : '20 units'}</small></span><ViewAction /></button>)}</section><div className="approval-note"><ShieldCheck size={15} /> REQUIRES PURCHASING APPROVAL</div><button className="panel-primary" type="button">Review and approve</button><button className="panel-secondary" type="button" onClick={() => pushPanel({ kind: 'sources', title: 'Plan evidence', payload: { focus: 'plan' } })}>Inspect supporting evidence <ArrowRight size={15} /></button></div>
}

function HistoryPanel({ onSelect }: { onSelect: (title: string) => void }) {
  return <div className="context-content"><label className="panel-search"><Search size={17} /><input placeholder="Search conversations" /></label><section className="history-list">{conversationHistory.map(([title, date]) => <button type="button" key={title} onClick={() => onSelect(title)}><span><strong>{title}</strong><small>{date}</small></span><ArrowRight size={16} /></button>)}</section></div>
}
