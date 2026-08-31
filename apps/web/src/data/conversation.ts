import { useCallback, useRef, useState } from 'react'
import { apiBaseUrl, identityHeaders } from '../api/client'

/** The block vocabulary the API streams. Mirrors conversations/blocks.py. */
export type BlockType =
  | 'answer_text' | 'record_summary' | 'forecast_summary' | 'recommendation'
  | 'citation' | 'action_proposal' | 'clarification' | 'warning' | 'error' | 'completed'

export interface CitationBlock {
  type: 'citation'
  record_type: string
  record_id: string
  label: string
  version: number | null
  observed_at: string
}

export interface RecordSummaryBlock {
  type: 'record_summary'
  title: string
  columns: string[]
  rows: Record<string, string | number | null>[]
  row_count: number
  tool: string | null
}

export interface CompletedBlock {
  type: 'completed'
  model_profile: string
  model_revision: string
  route: string
  prompt_version: string
  citation_count: number
  abstained: boolean
  latency_ms: number
}

export type Block =
  | { type: 'answer_text'; text: string }
  | { type: 'clarification'; question: string; options: string[] }
  | { type: 'recommendation'; text: string; rationale: string | null }
  | { type: 'warning'; message: string; code: string | null }
  | { type: 'error'; message: string; code: string }
  | CitationBlock
  | RecordSummaryBlock
  | CompletedBlock
  | { type: BlockType; [key: string]: unknown }

/** Parse an SSE byte stream into blocks as they arrive. */
async function* readBlocks(response: Response): AsyncGenerator<Block> {
  const body = response.body
  if (!body) return
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data: ')) continue
        try {
          yield JSON.parse(line.slice(6)) as Block
        } catch {
          // A malformed frame is dropped rather than breaking the stream.
        }
      }
      boundary = buffer.indexOf('\n\n')
    }
  }
}

export interface ConversationState {
  blocks: Block[]
  streaming: boolean
  error: string | null
}

export function useConversation() {
  const [state, setState] = useState<ConversationState>({
    blocks: [], streaming: false, error: null,
  })
  const conversationId = useRef<string | null>(null)
  const abort = useRef<AbortController | null>(null)

  const ensureConversation = useCallback(async () => {
    if (conversationId.current) return conversationId.current
    const response = await fetch(`${apiBaseUrl}/v1/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...identityHeaders() },
    })
    if (!response.ok) throw new Error(`could not start a conversation (${response.status})`)
    const created = (await response.json()) as { id: string }
    conversationId.current = created.id
    return created.id
  }, [])

  const ask = useCallback(async (content: string) => {
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    setState({ blocks: [], streaming: true, error: null })

    try {
      const id = await ensureConversation()
      const response = await fetch(`${apiBaseUrl}/v1/conversations/${id}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...identityHeaders() },
        body: JSON.stringify({ content }),
        signal: controller.signal,
      })
      if (!response.ok) {
        throw new Error(`the assistant returned ${response.status}`)
      }
      for await (const block of readBlocks(response)) {
        setState((current) => ({ ...current, blocks: [...current.blocks, block] }))
      }
      setState((current) => ({ ...current, streaming: false }))
    } catch (cause) {
      if (controller.signal.aborted) return
      setState({
        blocks: [],
        streaming: false,
        error: cause instanceof Error ? cause.message : 'the assistant is unavailable',
      })
    }
  }, [ensureConversation])

  const reset = useCallback(() => {
    abort.current?.abort()
    conversationId.current = null
    setState({ blocks: [], streaming: false, error: null })
  }, [])

  return { ...state, ask, reset }
}

export function blocksOf<T extends Block>(blocks: Block[], type: BlockType): T[] {
  return blocks.filter((block) => block.type === type) as T[]
}
