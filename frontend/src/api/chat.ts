import request from './request'
import { getToken } from '@/utils/token'
import type {
  ChatMessage,
  Conversation,
  ConversationDetail,
  ConversationPayload,
} from '@/types/api'

/** One parsed Server-Sent Event from the chat stream. */
export interface ChatStreamEvent {
  event: 'meta' | 'sources' | 'step' | 'delta' | 'done' | 'error'
  data: Record<string, any>
}

// The axios instance's response interceptor unwraps `response.data`, which is
// what we want for JSON APIs but NOT for streaming — chat uses raw fetch so
// we can read the body incrementally (axios has no SSE support).
export const chatApi = {
  list() {
    return request.get<null, Conversation[]>('/conversations')
  },
  create(payload: ConversationPayload) {
    return request.post<ConversationPayload, Conversation>('/conversations', payload)
  },
  detail(convId: number) {
    return request.get<null, ConversationDetail>(`/conversations/${convId}`)
  },
  rename(convId: number, title: string) {
    return request.patch<{ title: string }, Conversation>(
      `/conversations/${convId}`,
      { title },
    )
  },
  remove(convId: number) {
    return request.delete<null, void>(`/conversations/${convId}`)
  },

  /**
   * POST one message and stream the answer back as parsed SSE events.
   * `onEvent` is called for every event as soon as it arrives.
   * `images` are base64 data URLs for vision-capable models (plain chat).
   */
  async stream(
    convId: number,
    content: string,
    onEvent: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
    images?: string[],
  ): Promise<void> {
    const token = getToken()
    const response = await fetch(`/api/v1/conversations/${convId}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(images?.length ? { content, images } : { content }),
      signal,
    })
    if (!response.ok || !response.body) {
      let detail = `请求失败 (${response.status})`
      try {
        const body = await response.json()
        if (typeof body?.detail === 'string') detail = body.detail
      } catch {
        /* keep the generic message */
      }
      throw new Error(detail)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    // SSE frames are separated by a blank line; a frame can also be split
    // across TCP chunks, so buffer until we see the separator.
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let sep = buffer.indexOf('\n\n')
      while (sep !== -1) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        const event = parseFrame(frame)
        if (event) onEvent(event)
        sep = buffer.indexOf('\n\n')
      }
    }
    const tail = parseFrame(buffer)
    if (tail) onEvent(tail)
  },
}

function parseFrame(frame: string): ChatStreamEvent | null {
  let event = ''
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!event) return null
  let parsed: Record<string, any> = {}
  if (data) {
    try {
      parsed = JSON.parse(data)
    } catch {
      parsed = {}
    }
  }
  return { event: event as ChatStreamEvent['event'], data: parsed }
}

export type { ChatMessage }
