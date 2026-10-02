// Type declarations shared by API modules and stores.

export interface UserInfo {
  id: number
  username: string
  email: string
  is_active: boolean
  is_superuser: boolean
  created_at: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: UserInfo
}

export interface DashboardStats {
  knowledge_base_count: number
  document_count: number
  conversation_count: number
  ai_call_count: number
  running_task_count: number
  completed_task_count: number
}

export interface TrendPoint {
  date: string
  value: number
}

export interface DashboardTrend {
  metric: string
  points: TrendPoint[]
}

export interface DashboardResponse {
  stats: DashboardStats
  ai_call_trend: DashboardTrend
  task_status_breakdown: TrendPoint[]
  recent_tasks: TaskRecord[]
}

export interface ApiErrorBody {
  detail: string | { msg: string; type: string }[]
}

// ---------- Phase 2: knowledge base / document / task ----------

export type DocumentStatus = 'pending' | 'processing' | 'completed' | 'failed'
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed'
export type FileType = 'pdf' | 'docx' | 'txt'

export interface KnowledgeBase {
  id: number
  name: string
  description: string | null
  document_count: number
  created_at: string
  updated_at: string
}

export interface KnowledgeBasePayload {
  name: string
  description?: string
}

export interface KnowledgeDocument {
  id: number
  knowledge_base_id: number
  filename: string
  file_type: FileType
  file_size: number
  status: DocumentStatus
  progress: number
  error_message: string | null
  page_count: number | null
  chunk_count: number
  character_count: number
  // Provenance of the chunk vectors, e.g. "mock:1024". null = not embedded yet.
  embedding_model: string | null
  created_at: string
  updated_at: string
}

export interface TaskRecord {
  id: number
  document_id: number | null
  type: string
  status: TaskStatus
  progress: number
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  result: Record<string, unknown> | null
}

export interface DocumentUploadResponse {
  document: KnowledgeDocument
  task: TaskRecord
  dispatch: 'queued' | 'inline'
}

// ---------- Phase 3: RAG search / query ----------

export interface RagSource {
  /** 1-based citation number (rank for search-only results). */
  index: number
  document_id: number
  chunk_id: number
  filename: string
  page_number: number | null
  chunk_index: number
  vector_score: number
  rerank_score: number | null
  final_score: number
  snippet: string
}

export interface SearchResponse {
  query: string
  embedding_model: string
  reranker: string
  hits: RagSource[]
  duration_ms: number
}

export interface RagQueryResponse {
  question: string
  answer: string
  llm_provider: string
  embedding_model: string
  reranker: string
  sources: RagSource[]
  vector_candidates: number
  duration_ms: number
}

export interface DocumentEmbedResponse {
  document: KnowledgeDocument
  task: TaskRecord
  dispatch: 'queued' | 'inline'
}

// ---------- Phase 4: conversations / chat ----------

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool'

export interface Conversation {
  id: number
  title: string
  knowledge_base_id: number | null
  agent_id: number | null
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: number
  role: MessageRole
  content: string
  sources: RagSource[] | null
  tool_calls: { steps?: AgentStep[]; attachments?: AgentAttachment[] } | null
  model: string | null
  created_at: string
  /** Vision attachments (data URLs) — only on freshly sent local messages. */
  images?: string[] | null
}

// ---------- Phase 5: agents / tools / data files ----------

export interface AgentStep {
  index: number
  type: 'thought' | 'tool_call' | 'tool_result'
  tool: string | null
  summary: string
  attachments?: AgentAttachment[]
}

export interface AgentAttachment {
  type: 'chart'
  url: string
}

export interface Agent {
  id: number
  name: string
  system_prompt: string
  model: string | null
  temperature: number | null
  tools: string[] | null
  created_at: string
  updated_at: string
}

export interface AgentPayload {
  name: string
  system_prompt?: string
  model?: string | null
  temperature?: number | null
  tools?: string[] | null
}

export interface ToolRecord {
  id: number
  name: string
  description: string
  type: string
  enabled: boolean
}

export interface DataFileRecord {
  id: number
  filename: string
  file_type: string
  file_size: number
  row_count: number
  column_names: string[] | null
  created_at: string
}

// ---------- Phase 6: MCP ----------

export interface McpStatus {
  state: 'connected' | 'unreachable' | 'not_discovered' | 'disabled'
  tools: number
  error: string | null
  server: { name?: string; version?: string } | null
}

export interface McpToolInfo {
  name: string
  remote_name: string
  description: string
  input_schema: Record<string, unknown>
}

export interface McpToolsResponse {
  status: McpStatus
  tools: McpToolInfo[]
}

export interface McpCallResponse {
  tool: string
  text: string
  is_error: boolean
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[]
}

export interface ConversationPayload {
  title?: string
  knowledge_base_id?: number
  agent_id?: number
}