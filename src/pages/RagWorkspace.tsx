import {
  ArrowRight,
  ArrowUp,
  Boxes,
  Check,
  ChevronDown,
  CircleDot,
  Clock3,
  Database,
  FileText,
  History,
  Menu,
  Moon,
  PanelRight,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Sun,
  X,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { products } from '../data/mockData'
import type { Product } from '../types'

type Panel = 'inventory' | 'item' | 'sources' | 'plan' | 'history' | null

const starterPrompts = [
  'What needs my attention today?',
  'Show products at risk of stocking out',
  'Build a replenishment plan for this week',
  'What changed in inventory value?',
]

const history = [
  ['Weekly stock risks', 'Today, 9:24 AM'],
  ['Austin replenishment plan', 'Yesterday'],
  ['Supplier lead-time review', 'Aug 27'],
  ['Inventory value variance', 'Aug 26'],
]

interface RagWorkspaceProps {
  theme: 'dark' | 'light'
  onThemeToggle: () => void
}

export function RagWorkspace({ theme, onThemeToggle }: RagWorkspaceProps) {
  const [panel, setPanel] = useState<Panel>(() => window.innerWidth > 760 ? 'sources' : null)
  const [selectedProduct, setSelectedProduct] = useState<Product>(products[2])
  const [question, setQuestion] = useState('Which products need attention this week?')
  const [input, setInput] = useState('')
  const [hasConversation, setHasConversation] = useState(true)
  const [inventoryQuery, setInventoryQuery] = useState('')
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  const filteredProducts = useMemo(() => products.filter((product) =>
    `${product.name} ${product.sku}`.toLowerCase().includes(inventoryQuery.toLowerCase()),
  ), [inventoryQuery])

  function ask(nextQuestion?: string) {
    const next = nextQuestion ?? input
    if (!next.trim()) return
    setQuestion(next)
    setInput('')
    setHasConversation(true)
  }

  function openItem(product: Product) {
    setSelectedProduct(product)
    setPanel('item')
  }

  function newConversation() {
    setHasConversation(false)
    setQuestion('')
    setInput('')
    setPanel(null)
  }

  return (
    <div className={`rag-app ${panel ? 'panel-open' : ''}`}>
      <header className="rag-topbar">
        <div className="rag-brand">
          <span>S</span>
          <strong>SmartStock</strong>
        </div>

        <button className="workspace-name">
          Nova Supply Co. <ChevronDown size={13} />
        </button>

        <div className="rag-top-actions">
          <button className="top-action desktop-only" onClick={newConversation}><Plus size={15} /> New chat</button>
          <button className="top-icon" onClick={() => setPanel('history')} aria-label="Conversation history"><History size={17} /></button>
          <button className="top-icon" onClick={onThemeToggle} aria-label={`Use ${theme === 'dark' ? 'light' : 'dark'} mode`}>
            {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
          </button>
          <button className="account-dot">TA</button>
          <button className="top-icon mobile-only" onClick={() => setMobileMenuOpen((open) => !open)} aria-label="Open menu"><Menu size={18} /></button>
        </div>
      </header>

      {mobileMenuOpen && (
        <div className="mobile-menu-card">
          <button onClick={() => { newConversation(); setMobileMenuOpen(false) }}><Plus size={15} /> New chat</button>
          <button onClick={() => { setPanel('inventory'); setMobileMenuOpen(false) }}><Boxes size={15} /> Browse inventory</button>
          <button onClick={() => { setPanel('history'); setMobileMenuOpen(false) }}><History size={15} /> History</button>
        </div>
      )}

      <main className="rag-body">
        <section className="conversation-area">
          <div className="conversation-header">
            <div>
              <span className="assistant-status"><CircleDot size={12} /> Live workspace data</span>
              <h1>Operations assistant</h1>
            </div>
            <button className="browse-button" onClick={() => setPanel('inventory')}><Boxes size={15} /> Browse inventory</button>
          </div>

          <div className={`thread ${hasConversation ? '' : 'empty-thread'}`}>
            {!hasConversation ? (
              <div className="empty-chat">
                <span className="empty-chat-mark"><Sparkles size={20} /></span>
                <h2>What would you like to know?</h2>
                <p>Ask about inventory, orders, suppliers, demand, or operating documents.</p>
                <div className="starter-grid">
                  {starterPrompts.map((prompt) => <button key={prompt} onClick={() => ask(prompt)}>{prompt}<ArrowRight size={14} /></button>)}
                </div>
              </div>
            ) : (
              <div className="messages">
                <div className="message user-question">
                  <span className="message-avatar user">TA</span>
                  <div><span>You</span><p>{question}</p></div>
                </div>

                <div className="message assistant-answer">
                  <span className="message-avatar assistant">S</span>
                  <div className="answer-body">
                    <div className="answer-author"><span>SmartStock</span><small>Based on live inventory and 6 sources</small></div>
                    <p>Three products need attention this week. <strong>Volt Travel Adapter</strong> is the most urgent: it is out of stock with 12 units committed, and its inbound shipment is still six days away.</p>

                    <div className="inline-records">
                      {products.slice(1, 3).concat(products[5]).map((product) => (
                        <button key={product.sku} onClick={() => openItem(product)}>
                          <span className="record-state" data-status={product.status} />
                          <span><strong>{product.name}</strong><small>{product.available} available · {product.committed} committed</small></span>
                          <span className="record-status">{product.status}</span>
                          <ArrowRight size={14} />
                        </button>
                      ))}
                    </div>

                    <p>I recommend expediting the Volt shipment, ordering 96 Nexus Cable Kits, and moving 20 Arc Monitor Stands from Reno to Austin.</p>

                    <button className="inline-plan" onClick={() => setPanel('plan')}>
                      <span><Check size={14} /></span>
                      <span><strong>Replenishment plan ready</strong><small>3 proposed actions · approval required</small></span>
                      <span>Review plan</span>
                      <ArrowRight size={14} />
                    </button>

                    <div className="answer-sources">
                      <button onClick={() => setPanel('sources')}><Database size={13} /> 4 live records</button>
                      <button onClick={() => setPanel('sources')}><FileText size={13} /> 2 documents</button>
                      <span>Updated 2 min ago</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="composer-wrap">
            {hasConversation && <div className="follow-ups">
              {starterPrompts.slice(1, 4).map((prompt) => <button key={prompt} onClick={() => ask(prompt)}>{prompt}</button>)}
            </div>}
            <div className="rag-composer">
              <textarea
                rows={1}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    ask()
                  }
                }}
                placeholder="Ask SmartStock anything…"
              />
              <button onClick={() => ask()} aria-label="Send message"><ArrowUp size={18} /></button>
              <span><ShieldCheck size={11} /> Answers include sources and respect workspace permissions</span>
            </div>
          </div>
        </section>

        {panel && (
          <aside className="context-panel">
            <div className="context-header">
              <div>
                <span>Context</span>
                <h2>{panel === 'item' ? 'Item details' : panel === 'inventory' ? 'Inventory' : panel === 'sources' ? 'Sources' : panel === 'plan' ? 'Proposed plan' : 'History'}</h2>
              </div>
              <button onClick={() => setPanel(null)} aria-label="Close panel"><X size={17} /></button>
            </div>

            {panel === 'sources' && <SourcesPanel onOpenItem={() => openItem(products[2])} />}
            {panel === 'item' && <ItemPanel product={selectedProduct} onOpenSources={() => setPanel('sources')} />}
            {panel === 'inventory' && <InventoryPanel products={filteredProducts} query={inventoryQuery} onQuery={setInventoryQuery} onOpenItem={openItem} />}
            {panel === 'plan' && <PlanPanel onOpenItem={openItem} />}
            {panel === 'history' && <HistoryPanel onSelect={(title) => { setQuestion(title); setHasConversation(true); setPanel(null) }} />}
          </aside>
        )}

        {!panel && <button className="open-context" onClick={() => setPanel('sources')}><PanelRight size={17} /><span>Open context</span></button>}
      </main>
    </div>
  )
}

function SourcesPanel({ onOpenItem }: { onOpenItem: () => void }) {
  return <div className="context-content">
    <p className="context-intro">This answer combines current operational records with your uploaded supplier documents.</p>
    <section className="context-section">
      <h3>Live records <span>4</span></h3>
      <button className="source-card" onClick={onOpenItem}><Database size={15} /><span><strong>Inventory position</strong><small>Volt Travel Adapter · Austin Central</small></span><ArrowRight size={14} /></button>
      <button className="source-card"><Database size={15} /><span><strong>Purchase order PO-2051</strong><small>Expected Sep 4 · 200 units</small></span><ArrowRight size={14} /></button>
      <button className="source-card"><Database size={15} /><span><strong>Demand history</strong><small>90-day sales and stockout events</small></span><ArrowRight size={14} /></button>
    </section>
    <section className="context-section">
      <h3>Documents <span>2</span></h3>
      <button className="source-card"><FileText size={15} /><span><strong>Nova Manufacturing terms</strong><small>PDF · Updated Aug 12</small></span><ArrowRight size={14} /></button>
      <button className="source-card"><FileText size={15} /><span><strong>Inbound receiving policy</strong><small>DOCX · Updated Jul 28</small></span><ArrowRight size={14} /></button>
    </section>
    <div className="grounding-note"><ShieldCheck size={15} /><span><strong>Grounded answer</strong><small>All claims are linked to records you can inspect.</small></span></div>
  </div>
}

function ItemPanel({ product, onOpenSources }: { product: Product; onOpenSources: () => void }) {
  return <div className="context-content">
    <div className="item-heading"><span className="item-monogram">{product.name.slice(0, 2).toUpperCase()}</span><div><h3>{product.name}</h3><p>{product.sku} · {product.category}</p></div></div>
    <div className="detail-grid">
      <div><span>Available</span><strong>{product.available}</strong></div>
      <div><span>Committed</span><strong>{product.committed}</strong></div>
      <div><span>Incoming</span><strong>{product.incoming || '—'}</strong></div>
      <div><span>Reorder at</span><strong>{product.reorderAt}</strong></div>
    </div>
    <section className="context-section detail-list">
      <h3>Stock position</h3>
      <div><span>Location</span><strong>{product.warehouse}</strong></div>
      <div><span>Status</span><strong>{product.status}</strong></div>
      <div><span>Unit price</span><strong>${product.price.toFixed(2)}</strong></div>
      <div><span>Days of cover</span><strong>{product.available === 0 ? '0 days' : '9 days'}</strong></div>
    </section>
    <section className="context-section">
      <h3>Recent activity</h3>
      <div className="timeline-row"><Clock3 size={14} /><span><strong>12 units allocated</strong><small>Sales order SO-8402 · 2h ago</small></span></div>
      <div className="timeline-row"><Clock3 size={14} /><span><strong>Inbound date updated</strong><small>Purchase order PO-2051 · Yesterday</small></span></div>
    </section>
    <button className="panel-secondary" onClick={onOpenSources}>View supporting sources</button>
  </div>
}

function InventoryPanel({ products: list, query, onQuery, onOpenItem }: { products: Product[]; query: string; onQuery: (query: string) => void; onOpenItem: (product: Product) => void }) {
  return <div className="context-content">
    <label className="panel-search"><Search size={15} /><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search item or SKU" /></label>
    <div className="panel-filter-row"><button className="active">All</button><button>Attention</button><button>Incoming</button></div>
    <section className="inventory-panel-list">
      {list.map((product) => <button key={product.sku} onClick={() => onOpenItem(product)}><span className="item-monogram small">{product.name.slice(0, 2).toUpperCase()}</span><span><strong>{product.name}</strong><small>{product.sku} · {product.warehouse}</small></span><span className="quantity">{product.available}<small>available</small></span><ArrowRight size={14} /></button>)}
    </section>
  </div>
}

function PlanPanel({ onOpenItem }: { onOpenItem: (product: Product) => void }) {
  const planProducts = [products[2], products[1], products[5]]
  return <div className="context-content">
    <p className="context-intro">These actions are drafts. Nothing changes until you review and approve them.</p>
    <div className="plan-summary"><span>Estimated impact</span><strong>$8,420</strong><small>revenue protected over 30 days</small></div>
    <section className="plan-list">
      {planProducts.map((product, index) => <button key={product.sku} onClick={() => onOpenItem(product)}><span className="plan-number">{index + 1}</span><span><strong>{index === 0 ? 'Expedite inbound shipment' : index === 1 ? 'Create purchase order' : 'Transfer between locations'}</strong><small>{product.name} · {index === 0 ? '200 units' : index === 1 ? '96 units' : '20 units'}</small></span><ArrowRight size={14} /></button>)}
    </section>
    <div className="approval-note"><ShieldCheck size={14} /> Requires purchasing approval</div>
    <button className="panel-primary">Review and approve</button>
    <button className="panel-secondary">Export plan</button>
  </div>
}

function HistoryPanel({ onSelect }: { onSelect: (title: string) => void }) {
  return <div className="context-content"><label className="panel-search"><Search size={15} /><input placeholder="Search conversations" /></label><section className="history-list">{history.map(([title, date]) => <button key={title} onClick={() => onSelect(title)}><span><strong>{title}</strong><small>{date}</small></span><ArrowRight size={14} /></button>)}</section></div>
}
