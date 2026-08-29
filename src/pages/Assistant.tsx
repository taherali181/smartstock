import { ArrowUp, BrainCircuit, Check, ChevronRight, Database, FileText, Plus, RotateCcw, ShieldCheck } from 'lucide-react'
import { useState } from 'react'

const prompts = [
  'Which products are likely to stock out next month?',
  'Why did inventory value increase this week?',
  'Build a reorder plan for the Austin warehouse.',
]

export function Assistant() {
  const [input, setInput] = useState('')
  const [question, setQuestion] = useState('Which products need attention this week?')
  const [sent, setSent] = useState(true)

  function ask(value?: string) {
    const next = value ?? input
    if (!next.trim()) return
    setQuestion(next)
    setSent(true)
    setInput('')
  }

  return (
    <div className="assistant-page">
      <header className="assistant-hero">
        <div><h1>Ask SmartStock</h1><p>Ask questions about inventory, orders, suppliers, or demand.</p></div>
      </header>

      <div className="assistant-layout">
        <aside className="conversation-list panel">
          <button className="new-thread"><Plus size={15} /> New conversation</button>
          <span className="section-kicker">Recent</span>
          <button className="conversation active"><strong>Weekly stock risks</strong><small>Just now</small></button>
          <button className="conversation"><strong>Supplier lead times</strong><small>Yesterday</small></button>
          <button className="conversation"><strong>Q3 margin review</strong><small>Aug 26</small></button>
          <div className="privacy-note"><ShieldCheck size={16} /><span><strong>Private by design</strong><small>Your operational data stays within your deployment.</small></span></div>
        </aside>

        <main className="chat-panel panel">
          <div className="chat-scroll">
            {sent && <>
              <div className="user-message"><span>TA</span><p>{question}</p></div>
              <div className="assistant-message">
                <span className="mini-ai"><BrainCircuit size={16} /></span>
                <div className="answer-copy">
                  <div className="answer-meta"><strong>SMARTSTOCK</strong><span>Analyzed 1,248 SKUs across 3 locations</span></div>
                  <p>Three products need action this week. The most urgent is <strong>Volt Travel Adapter</strong>, which is already out of stock with 12 committed units. The inbound shipment is due in 6 days, leaving an estimated $2,940 in demand at risk.</p>
                  <div className="insight-stack">
                    <div><span className="risk-index">01</span><span><strong>Volt Travel Adapter</strong><small>0 available · 12 committed · Austin Central</small></span><em>Critical</em></div>
                    <div><span className="risk-index">02</span><span><strong>Nexus Cable Kit</strong><small>8 usable · 9 days of cover · Brooklyn Hub</small></span><em>Reorder</em></div>
                    <div><span className="risk-index">03</span><span><strong>Arc Monitor Stand</strong><small>29 usable · demand trending +16%</small></span><em>Watch</em></div>
                  </div>
                  <p>I recommend expediting the Volt shipment, releasing a 96-unit PO for Nexus, and moving 20 Arc stands from Reno to Austin.</p>
                  <button className="proposed-action"><Check size={15} /><span><strong>Draft replenishment plan</strong><small>3 actions · requires approval</small></span><ChevronRight size={16} /></button>
                  <div className="sources"><span><Database size={13} /> 4 live records</span><span><FileText size={13} /> 2 supplier documents</span><button><RotateCcw size={13} /> Regenerate</button></div>
                </div>
              </div>
            </>}
          </div>
          <div className="prompt-suggestions">{prompts.map((prompt) => <button key={prompt} onClick={() => ask(prompt)}>{prompt}</button>)}</div>
          <div className="composer"><textarea rows={1} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() } }} placeholder="Ask about stock, orders, demand, or suppliers…" /><button onClick={() => ask()} aria-label="Send"><ArrowUp size={18} /></button><span>Uses live workspace data · Sources included</span></div>
        </main>
      </div>
    </div>
  )
}
