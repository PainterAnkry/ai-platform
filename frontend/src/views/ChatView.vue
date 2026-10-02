<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Promotion, Position, ChatDotRound, Cpu, UploadFilled } from '@element-plus/icons-vue'

import { agentApi } from '@/api/agent'
import { chatApi, type ChatStreamEvent } from '@/api/chat'
import { knowledgeApi } from '@/api/knowledge'
import type {
  Agent,
  AgentAttachment,
  AgentStep,
  ChatMessage,
  Conversation,
  DataFileRecord,
  KnowledgeBase,
  RagSource,
} from '@/types/api'
import { renderMarkdown } from '@/utils/markdown'

const conversations = ref<Conversation[]>([])
const knowledgeBases = ref<KnowledgeBase[]>([])
const agents = ref<Agent[]>([])
const dataFiles = ref<DataFileRecord[]>([])
const activeConvId = ref<number | null>(null)
const messages = ref<ChatMessage[]>([])
const selectedKbId = ref<number | null>(null)
const selectedAgentId = ref<number | null>(null)
const inputValue = ref('')
const pendingImages = ref<string[]>([])
const loadingList = ref(false)
const loadingDetail = ref(false)
const streaming = ref(false)
const streamText = ref('')
const streamSources = ref<RagSource[] | null>(null)
const streamSteps = ref<AgentStep[]>([])
const streamAttachments = ref<AgentAttachment[]>([])

let abortController: AbortController | null = null

const activeConv = computed(
  () => conversations.value.find((c) => c.id === activeConvId.value) ?? null,
)

const boundKbName = computed(() => {
  const kb = knowledgeBases.value.find((k) => k.id === selectedKbId.value)
  return kb ? kb.name : ''
})

function messageSteps(msg: ChatMessage): AgentStep[] {
  return msg.tool_calls?.steps ?? []
}

function messageAttachments(msg: ChatMessage): AgentAttachment[] {
  return msg.tool_calls?.attachments ?? []
}

const stepTypeLabel: Record<string, string> = {
  thought: '思考',
  tool_call: '调用工具',
  tool_result: '工具结果',
}

// ---------- markdown ----------
const rendered = new Map<string, string>()
function md(content: string): string {
  let html = rendered.get(content)
  if (html === undefined) {
    html = renderMarkdown(content)
    rendered.set(content, html)
  }
  return html
}

// ---------- data ----------
onMounted(async () => {
  loadingList.value = true
  try {
    const [convs, kbs, agentList, files] = await Promise.all([
      chatApi.list(),
      knowledgeApi.list().catch(() => [] as KnowledgeBase[]),
      agentApi.list().catch(() => [] as Agent[]),
      agentApi.listDataFiles().catch(() => [] as DataFileRecord[]),
    ])
    conversations.value = convs
    knowledgeBases.value = kbs
    agents.value = agentList
    dataFiles.value = files
    if (convs.length > 0) await selectConv(convs[0].id)
  } finally {
    loadingList.value = false
  }
})

onBeforeUnmount(stopStream)

async function loadConversations() {
  conversations.value = await chatApi.list()
}

async function selectConv(id: number) {
  if (streaming.value) stopStream()
  activeConvId.value = id
  loadingDetail.value = true
  try {
    const detail = await chatApi.detail(id)
    messages.value = detail.messages
    selectedKbId.value = detail.knowledge_base_id
    selectedAgentId.value = detail.agent_id
  } finally {
    loadingDetail.value = false
  }
  scrollToBottom()
}

async function newConversation() {
  const conv = await chatApi.create({
    knowledge_base_id: selectedKbId.value ?? undefined,
    agent_id: selectedAgentId.value ?? undefined,
  })
  conversations.value = [conv, ...conversations.value]
  activeConvId.value = conv.id
  messages.value = []
  streamText.value = ''
  streamSources.value = null
}

async function removeConv(conv: Conversation) {
  try {
    await ElMessageBox.confirm(`删除会话「${conv.title}」及其全部消息？`, '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await chatApi.remove(conv.id)
  await loadConversations()
  if (activeConvId.value === conv.id) {
    activeConvId.value = null
    messages.value = []
    if (conversations.value.length > 0) await selectConv(conversations.value[0].id)
  }
}

// ---------- data file upload (for the analysis agent) ----------
async function handleDataUpload(file: File) {
  try {
    const record = await agentApi.uploadDataFile(file as unknown as File)
    ElMessage.success(
      `已上传 ${record.filename}（${record.row_count} 行），Agent 可直接分析`,
    )
    dataFiles.value = [record, ...dataFiles.value]
  } catch {
    // surfaced by the interceptor
  }
  return false
}

// ---------- streaming send ----------
async function send() {
  const content = inputValue.value.trim()
  if (!content || streaming.value) return
  const images = pendingImages.value.length ? [...pendingImages.value] : undefined

  // First message may need to create the conversation on the fly.
  let convId = activeConvId.value
  if (convId === null) {
    const conv = await chatApi.create({
      knowledge_base_id: selectedKbId.value ?? undefined,
      agent_id: selectedAgentId.value ?? undefined,
    })
    conversations.value = [conv, ...conversations.value]
    activeConvId.value = conv.id
    convId = conv.id
  }

  messages.value.push({
    id: -Date.now(), // temporary; the real id arrives in the meta event
    role: 'user',
    content,
    sources: null,
    tool_calls: null,
    model: null,
    created_at: new Date().toISOString(),
    images,
  })
  inputValue.value = ''
  pendingImages.value = []
  streaming.value = true
  streamText.value = ''
  streamSources.value = null
  streamSteps.value = []
  streamAttachments.value = []
  scrollToBottom()

  abortController = new AbortController()
  try {
    await chatApi.stream(
      convId,
      content,
      (event: ChatStreamEvent) => handleStreamEvent(event),
      abortController.signal,
      images,
    )
  } catch (error) {
    const detail = error instanceof Error ? error.message : '请求失败'
    if (!abortController.signal.aborted) {
      ElMessage.error(detail)
      streamText.value += `\n\n> ⚠️ ${detail}`
    }
  } finally {
    finishStream()
    abortController = null
  }
}

function handleStreamEvent(event: ChatStreamEvent) {
  if (event.event === 'delta') {
    streamText.value += String(event.data.text ?? '')
  } else if (event.event === 'sources') {
    streamSources.value = (event.data.sources ?? []) as RagSource[]
  } else if (event.event === 'step') {
    streamSteps.value.push(event.data as unknown as AgentStep)
    const attachments = (event.data.attachments ?? []) as AgentAttachment[]
    if (attachments.length) streamAttachments.value.push(...attachments)
  } else if (event.event === 'done') {
    messages.value.push({
      id: Number(event.data.assistant_message_id ?? -Date.now()),
      role: 'assistant',
      content: streamText.value,
      sources: (event.data.sources ?? streamSources.value) as RagSource[] | null,
      tool_calls:
        streamSteps.value.length || streamAttachments.value.length
          ? {
              steps: [...streamSteps.value],
              attachments: [...streamAttachments.value],
            }
          : null,
      model: (event.data.model as string) ?? null,
      created_at: new Date().toISOString(),
    })
  } else if (event.event === 'error') {
    streamText.value += `\n\n> ⚠️ ${String(event.data.detail ?? '生成失败')}`
  }
  scrollToBottom()
}

function finishStream() {
  // If the stream ended without a done event (abort / error before done),
  // keep whatever text arrived so nothing silently disappears.
  if (streamText.value && !messages.value.some((m) => m.content === streamText.value)) {
    if (streaming.value) {
      messages.value.push({
        id: -Date.now(),
        role: 'assistant',
        content: streamText.value,
        sources: streamSources.value,
        tool_calls:
          streamSteps.value.length || streamAttachments.value.length
            ? { steps: [...streamSteps.value], attachments: [...streamAttachments.value] }
            : null,
        model: null,
        created_at: new Date().toISOString(),
      })
    }
  }
  streaming.value = false
  streamText.value = ''
  streamSources.value = null
  streamSteps.value = []
  streamAttachments.value = []
  scrollToBottom()
}

function stopStream() {
  abortController?.abort()
  abortController = null
}

// ---------- image attachments (vision) ----------
const MAX_IMAGES = 4
const MAX_IMAGE_BYTES = 3.5 * 1024 * 1024

function handleImageUpload(file: File) {
  if (pendingImages.value.length >= MAX_IMAGES) {
    ElMessage.warning(`最多附带 ${MAX_IMAGES} 张图片`)
    return false
  }
  if (file.size > MAX_IMAGE_BYTES) {
    ElMessage.warning('单张图片不超过 3.5 MB')
    return false
  }
  const reader = new FileReader()
  reader.onload = () => {
    pendingImages.value.push(String(reader.result))
  }
  reader.readAsDataURL(file)
  return false
}

function removeImage(index: number) {
  pendingImages.value.splice(index, 1)
}

function onKeydown(event: Event) {
  const keyEvent = event as KeyboardEvent
  if (keyEvent.key === 'Enter' && !keyEvent.shiftKey) {
    keyEvent.preventDefault()
    send()
  }
}

// ---------- scroll ----------
const scrollRef = ref<HTMLElement | null>(null)
function scrollToBottom() {
  nextTick(() => {
    const el = scrollRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}
watch([() => messages.value.length, streamText], scrollToBottom)

function sourcePage(source: RagSource): string {
  return source.page_number === null ? '' : ` · 第 ${source.page_number} 页`
}
</script>

<template>
  <div class="chat-root">
    <el-row :gutter="16" class="chat-row">
      <!-- conversations -->
      <el-col :xs="24" :md="7">
        <div class="page-card conv-panel">
          <div class="panel-head">
            <span class="panel-title">历史对话</span>
            <el-button
              type="primary"
              size="small"
              :icon="Position"
              :disabled="streaming"
              @click="newConversation"
            >
              新建对话
            </el-button>
          </div>

          <el-skeleton v-if="loadingList" :rows="4" animated />
          <el-empty
            v-else-if="conversations.length === 0"
            description="还没有对话，发送第一条消息即可自动创建"
            :image-size="70"
          />
          <ul v-else class="conv-list">
            <li
              v-for="conv in conversations"
              :key="conv.id"
              :class="['conv-item', { active: conv.id === activeConvId }]"
              @click="selectConv(conv.id)"
            >
              <div class="conv-main">
                <div class="conv-title">{{ conv.title }}</div>
                <div class="conv-meta muted">
                  {{ conv.knowledge_base_id ? '已绑定知识库' : '自由对话' }}
                </div>
              </div>
              <el-button
                text
                :icon="Delete"
                size="small"
                @click.stop="removeConv(conv)"
              />
            </li>
          </ul>
        </div>
      </el-col>

      <!-- chat area -->
      <el-col :xs="24" :md="17">
        <div class="page-card chat-panel">
          <div class="panel-head chat-head">
            <span class="panel-title">
              <el-icon class="chat-title-icon"><ChatDotRound /></el-icon>
              {{ activeConv ? activeConv.title : 'AI 对话' }}
            </span>
            <div class="head-controls">
              <div class="kb-bind">
                <span class="muted">Agent：</span>
                <el-select
                  v-model="selectedAgentId"
                  placeholder="不使用"
                  size="small"
                  clearable
                  style="width: 150px"
                >
                  <el-option
                    v-for="agent in agents"
                    :key="agent.id"
                    :label="agent.name"
                    :value="agent.id"
                  />
                </el-select>
              </div>
              <div class="kb-bind">
                <span class="muted">知识库：</span>
                <el-select
                  v-model="selectedKbId"
                  placeholder="不使用（自由对话）"
                  size="small"
                  clearable
                  style="width: 160px"
                >
                  <el-option
                    v-for="kb in knowledgeBases"
                    :key="kb.id"
                    :label="kb.name"
                    :value="kb.id"
                  />
                </el-select>
              </div>
              <el-tooltip content="上传 Excel/CSV 供数据分析 Agent 使用" placement="top">
                <el-upload
                  :show-file-list="false"
                  :before-upload="handleDataUpload"
                  accept=".xlsx,.xls,.csv"
                >
                  <el-button size="small" :icon="UploadFilled">数据</el-button>
                </el-upload>
              </el-tooltip>
            </div>
          </div>

          <div v-if="dataFiles.length > 0" class="data-files muted">
            可分析数据：
            <el-tag
              v-for="file in dataFiles.slice(0, 4)"
              :key="file.id"
              size="small"
              effect="plain"
              class="data-tag"
            >
              {{ file.filename }}（{{ file.row_count }} 行）
            </el-tag>
          </div>

          <div ref="scrollRef" class="chat-scroll">
            <el-empty
              v-if="messages.length === 0 && !streaming"
              description="向 AI 提问；绑定知识库后将基于文档内容作答并展示来源"
            />

            <div
              v-for="msg in messages"
              :key="msg.id"
              :class="['msg-row', msg.role === 'user' ? 'user' : 'assistant']"
            >
              <!-- agent step timeline -->
              <div v-if="messageSteps(msg).length" class="steps">
                <div v-for="step in messageSteps(msg)" :key="step.index" class="step">
                  <el-icon class="step-icon"><Cpu /></el-icon>
                  <span class="step-type">{{ stepTypeLabel[step.type] || step.type }}</span>
                  <el-tag v-if="step.tool" size="small" effect="plain">{{ step.tool }}</el-tag>
                  <span class="step-summary">{{ step.summary }}</span>
                </div>
              </div>

              <div class="bubble" :class="msg.role">
                <div v-if="msg.images && msg.images.length" class="bubble-images">
                  <img
                    v-for="(img, idx) in msg.images"
                    :key="idx"
                    :src="img"
                    class="bubble-img"
                    alt="附带图片"
                  />
                </div>
                <div v-if="msg.role === 'assistant'" class="md-body" v-html="md(msg.content)" />
                <template v-else>{{ msg.content }}</template>
              </div>

              <div v-if="messageAttachments(msg).length" class="charts">
                <img
                  v-for="att in messageAttachments(msg)"
                  :key="att.url"
                  :src="att.url"
                  class="chart-img"
                  alt="分析图表"
                />
              </div>

              <div v-if="msg.sources && msg.sources.length" class="sources">
                <el-tag
                  v-for="source in msg.sources"
                  :key="source.index"
                  size="small"
                  type="info"
                  effect="plain"
                  class="source-tag"
                >
                  {{ source.index }}·{{ source.filename }}{{ sourcePage(source) }}
                </el-tag>
              </div>
            </div>

            <!-- in-flight assistant message -->
            <div v-if="streaming" class="msg-row assistant">
              <div v-if="streamSteps.length" class="steps">
                <div v-for="step in streamSteps" :key="step.index" class="step">
                  <el-icon class="step-icon"><Cpu /></el-icon>
                  <span class="step-type">{{ stepTypeLabel[step.type] || step.type }}</span>
                  <el-tag v-if="step.tool" size="small" effect="plain">{{ step.tool }}</el-tag>
                  <span class="step-summary">{{ step.summary }}</span>
                </div>
              </div>

              <div v-if="streamSources && streamSources.length" class="sources">
                <el-tag
                  v-for="source in streamSources"
                  :key="source.index"
                  size="small"
                  type="info"
                  effect="plain"
                  class="source-tag"
                >
                  检索到 {{ source.filename }}{{ sourcePage(source) }}
                </el-tag>
              </div>

              <div v-if="streamAttachments.length" class="charts">
                <img
                  v-for="att in streamAttachments"
                  :key="att.url"
                  :src="att.url"
                  class="chart-img"
                  alt="分析图表"
                />
              </div>

              <div class="bubble assistant">
                <div v-if="streamText" class="md-body" v-html="md(streamText)" />
                <span v-else class="typing">
                  正在思考<span class="dots" />
                </span>
              </div>
            </div>
          </div>

          <div class="chat-input">
            <div v-if="pendingImages.length" class="image-previews">
              <div v-for="(img, idx) in pendingImages" :key="idx" class="image-preview">
                <img :src="img" alt="预览" />
                <el-button
                  class="img-remove"
                  size="small"
                  circle
                  type="danger"
                  @click="removeImage(idx)"
                >
                  ×
                </el-button>
              </div>
            </div>
            <el-input
              v-model="inputValue"
              type="textarea"
              :rows="2"
              resize="none"
              :placeholder="
                boundKbName
                  ? `基于「${boundKbName}」提问…（Enter 发送，Shift+Enter 换行）`
                  : '输入消息…（Enter 发送，Shift+Enter 换行；可附带图片）'
              "
              :disabled="streaming"
              @keydown="onKeydown"
            />
            <div class="input-actions">
              <el-upload
                :show-file-list="false"
                :before-upload="handleImageUpload"
                accept="image/*"
              >
                <el-button :disabled="streaming">图片</el-button>
              </el-upload>
              <el-button
                v-if="streaming"
                @click="stopStream"
              >
                停止
              </el-button>
              <el-button
                v-else
                type="primary"
                :icon="Promotion"
                :disabled="!inputValue.trim()"
                @click="send"
              >
                发送
              </el-button>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.chat-root,
.chat-row {
  height: 100%;
}

.conv-panel {
  min-height: 560px;
  max-height: calc(100vh - 160px);
  display: flex;
  flex-direction: column;
}

.chat-panel {
  min-height: 560px;
  height: calc(100vh - 160px);
  display: flex;
  flex-direction: column;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
}

.chat-title-icon {
  margin-right: 4px;
  color: var(--app-primary);
  vertical-align: -2px;
}

.conv-list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
}

.conv-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: 10px;
  cursor: pointer;
  transition: background-color 0.18s ease;
}

.conv-item:hover {
  background: var(--app-bg);
}

.conv-item.active {
  background: rgba(31, 111, 235, 0.1);
}

.conv-title {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 180px;
}

.conv-meta {
  font-size: 12px;
  margin-top: 2px;
}

.chat-head {
  border-bottom: 1px solid var(--el-border-color-lighter);
  padding-bottom: 10px;
}

.head-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.kb-bind {
  display: flex;
  align-items: center;
  font-size: 13px;
}

.data-files {
  font-size: 12px;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.data-tag {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.steps {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
  padding: 8px 12px;
  border-left: 3px solid var(--app-primary);
  background: rgba(31, 111, 235, 0.05);
  border-radius: 0 8px 8px 0;
}

.step {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--app-text-muted);
}

.step-icon {
  font-size: 13px;
  color: var(--app-primary);
}

.step-type {
  font-weight: 500;
  color: var(--app-text);
}

.step-summary {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 380px;
}

.charts {
  margin-top: 8px;
}

.chart-img {
  max-width: 100%;
  max-height: 320px;
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
}

.chat-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 4px 4px 12px;
}

.msg-row {
  display: flex;
  flex-direction: column;
  margin: 12px 0;
}

.msg-row.user {
  align-items: flex-end;
}

.msg-row.assistant {
  align-items: flex-start;
}

.bubble {
  max-width: 82%;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

.bubble.user {
  background: var(--app-primary, #1f6feb);
  color: #fff;
  white-space: pre-wrap;
}

.bubble-images {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.bubble-img {
  max-width: 180px;
  max-height: 140px;
  border-radius: 8px;
  display: block;
}

.image-previews {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.image-preview {
  position: relative;
}

.image-preview img {
  width: 72px;
  height: 72px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
  display: block;
}

.img-remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 20px !important;
  height: 20px !important;
  padding: 0 !important;
}

.bubble.assistant {
  background: var(--app-bg);
}

.sources {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.source-tag {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.typing {
  color: var(--app-text-muted);
}

.dots::after {
  content: '…';
  animation: dots 1.2s steps(3, end) infinite;
}

@keyframes dots {
  0% { content: ''; }
  50% { content: '··'; }
  100% { content: '…'; }
}

.chat-input {
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 12px;
}

.input-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}

/* markdown inside assistant bubbles */
.md-body :deep(p) {
  margin: 0 0 8px;
}

.md-body :deep(p:last-child) {
  margin-bottom: 0;
}

.md-body :deep(pre) {
  background: #f6f8fa;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 13px;
}

.md-body :deep(code) {
  font-family: 'JetBrains Mono', Consolas, monospace;
}

.md-body :deep(code:not(.hljs)) {
  background: rgba(31, 111, 235, 0.08);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 13px;
}

.md-body :deep(ul),
.md-body :deep(ol) {
  padding-left: 20px;
  margin: 6px 0;
}

.md-body :deep(blockquote) {
  margin: 8px 0;
  padding: 4px 12px;
  border-left: 3px solid var(--app-primary);
  color: var(--app-text-muted);
  background: rgba(31, 111, 235, 0.04);
}

.md-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
}

.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid var(--el-border-color-lighter);
  padding: 4px 10px;
  font-size: 13px;
}
</style>
