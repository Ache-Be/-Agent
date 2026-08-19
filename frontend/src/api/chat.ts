import request from '@/utils/request'

export interface Conversation {
  id: string
  title: string
  created_at: string
  pinned: boolean
  msg_count: number
  messages?: ChatMessage[]
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
}

export const listConversations = (): Promise<{ conversations: Conversation[] }> => {
  return request.get('/api/conversations') as any
}

export const getConversation = (id: string): Promise<Conversation> => {
  return request.get(`/api/conversations/${id}`) as any
}

export const createConversation = (): Promise<Conversation> => {
  return request.post('/api/conversations', {}) as any
}

export const updateConversation = (id: string, data: { title?: string; pinned?: boolean }) => {
  return request.patch(`/api/conversations/${id}`, data)
}

export const deleteConversation = (id: string) => {
  return request.delete(`/api/conversations/${id}`)
}

export const chat = (data: { message: string; conversation_id?: string }) => {
  return request.post('/api/chat', data)
}

export const getQaSedimentCount = (): Promise<{ jsonl: number; pgvector: number }> => {
  return request.get('/api/chat/qa-sediment-count') as any
}

export const clearQaSediment = (): Promise<{ cleared: { jsonl: number; pgvector: number } }> => {
  return request.delete('/api/chat/qa-sediment') as any
}

export interface QaSedimentItem {
  source: 'jsonl' | 'pgvector'
  id: string
  question: string
  answer: string
  time: string
}

export const listQaSediment = (limit = 100): Promise<{ total: number; logs: QaSedimentItem[] }> => {
  return request.get('/api/qa-sediment', { params: { limit } }) as any
}

export const deleteQaSediment = (items: { source: string; id: string }[]) => {
  return request.delete('/api/chat/qa-sediment', { data: { items } }) as any
}
