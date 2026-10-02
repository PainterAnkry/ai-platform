import request from './request'
import type {
  DocumentEmbedResponse,
  DocumentUploadResponse,
  KnowledgeBase,
  KnowledgeBasePayload,
  KnowledgeDocument,
  RagQueryResponse,
  SearchResponse,
  TaskRecord,
  TaskStatus,
} from '@/types/api'

// RAG generation can exceed the default 15s budget when a real LLM API is
// configured, so query calls get their own (larger) timeout.
const RAG_TIMEOUT_MS = 60_000

export const knowledgeApi = {
  list() {
    return request.get<null, KnowledgeBase[]>('/knowledge-bases')
  },
  get(kbId: number) {
    return request.get<null, KnowledgeBase>(`/knowledge-bases/${kbId}`)
  },
  create(payload: KnowledgeBasePayload) {
    return request.post<KnowledgeBasePayload, KnowledgeBase>('/knowledge-bases', payload)
  },
  update(kbId: number, payload: Partial<KnowledgeBasePayload>) {
    return request.patch<Partial<KnowledgeBasePayload>, KnowledgeBase>(
      `/knowledge-bases/${kbId}`,
      payload,
    )
  },
  remove(kbId: number) {
    return request.delete<null, void>(`/knowledge-bases/${kbId}`)
  },

  // ---- documents ----
  listDocuments(kbId: number) {
    return request.get<null, KnowledgeDocument[]>(`/knowledge-bases/${kbId}/documents`)
  },
  uploadDocument(kbId: number, file: File) {
    const form = new FormData()
    form.append('file', file)
    // The request interceptor strips the JSON content type for FormData so the
    // browser can supply the multipart boundary.
    return request.post<FormData, DocumentUploadResponse>(
      `/knowledge-bases/${kbId}/documents`,
      form,
    )
  },
  removeDocument(kbId: number, docId: number) {
    return request.delete<null, void>(`/knowledge-bases/${kbId}/documents/${docId}`)
  },
  reembedDocument(kbId: number, docId: number) {
    return request.post<null, DocumentEmbedResponse>(
      `/knowledge-bases/${kbId}/documents/${docId}/reembed`,
    )
  },

  // ---- Phase 3: RAG ----
  search(kbId: number, query: string, topK?: number) {
    return request.post<{ query: string; top_k?: number }, SearchResponse>(
      `/knowledge-bases/${kbId}/search`,
      { query, top_k: topK },
    )
  },
  query(kbId: number, question: string, topK?: number) {
    return request.post<{ query: string; top_k?: number }, RagQueryResponse>(
      `/knowledge-bases/${kbId}/query`,
      { query: question, top_k: topK },
      { timeout: RAG_TIMEOUT_MS },
    )
  },
}

export const taskApi = {
  list(status?: TaskStatus) {
    const params = status ? { status } : {}
    return request.get<null, TaskRecord[]>('/tasks', { params })
  },
  get(taskId: number) {
    return request.get<null, TaskRecord>(`/tasks/${taskId}`)
  },
}
