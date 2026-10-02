<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadRawFile } from 'element-plus'
import {
  ChatDotRound,
  Delete,
  Document as DocumentIcon,
  Plus,
  RefreshRight,
  Search,
  UploadFilled,
} from '@element-plus/icons-vue'

import { knowledgeApi } from '@/api/knowledge'
import type {
  DocumentStatus,
  KnowledgeBase,
  KnowledgeDocument,
  RagSource,
} from '@/types/api'

const knowledgeBases = ref<KnowledgeBase[]>([])
const documents = ref<KnowledgeDocument[]>([])
const activeKbId = ref<number | null>(null)
const loading = ref(false)
const docLoading = ref(false)
const uploading = ref(false)
const keyword = ref('')

let pollTimer: number | null = null

// ---------- Phase 3: RAG ----------
const ragMode = ref<'qa' | 'search'>('qa')
const ragQuestion = ref('')
const ragLoading = ref(false)
const ragAsked = ref(false)
const ragAnswer = ref('')
const ragSources = ref<RagSource[]>([])
const ragMeta = ref('')

const activeKb = computed(
  () => knowledgeBases.value.find((kb) => kb.id === activeKbId.value) ?? null,
)

const filteredDocuments = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return documents.value
  return documents.value.filter((d) => d.filename.toLowerCase().includes(kw))
})

// ---------- helpers ----------
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

const statusMeta: Record<DocumentStatus, { label: string; type: 'info' | 'warning' | 'success' | 'danger' }> = {
  pending: { label: '等待中', type: 'info' },
  processing: { label: '解析中', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
}

function fileTypeLabel(t: string): string {
  return { pdf: 'PDF', docx: 'Word', txt: 'TXT' }[t] ?? t.toUpperCase()
}

// ---------- data ----------
async function loadKnowledgeBases() {
  loading.value = true
  try {
    knowledgeBases.value = await knowledgeApi.list()
    if (activeKbId.value === null && knowledgeBases.value.length > 0) {
      await selectKb(knowledgeBases.value[0].id)
    } else if (activeKbId.value !== null) {
      const still = knowledgeBases.value.some((kb) => kb.id === activeKbId.value)
      if (!still) activeKbId.value = knowledgeBases.value[0]?.id ?? null
      if (activeKbId.value !== null) await loadDocuments()
    } else {
      documents.value = []
    }
  } finally {
    loading.value = false
  }
}

async function loadDocuments() {
  if (activeKbId.value === null) {
    documents.value = []
    return
  }
  docLoading.value = true
  try {
    documents.value = await knowledgeApi.listDocuments(activeKbId.value)
  } finally {
    docLoading.value = false
  }
}

async function selectKb(id: number) {
  activeKbId.value = id
  ragAsked.value = false
  ragAnswer.value = ''
  ragSources.value = []
  ragMeta.value = ''
  await loadDocuments()
}

async function createKb() {
  try {
    const { value } = await ElMessageBox.prompt('知识库名称', '新建知识库', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (v: string) => (v && v.trim().length > 0 ? true : '名称不能为空'),
    })
    const created = await knowledgeApi.create({ name: value.trim() })
    ElMessage.success('知识库已创建')
    activeKbId.value = created.id
    await loadKnowledgeBases()
  } catch {
    // cancelled by the user
  }
}

async function removeKb(kb: KnowledgeBase) {
  try {
    await ElMessageBox.confirm(
      `删除「${kb.name}」会同时移除其中的 ${kb.document_count} 个文档及其切片，且不可恢复。`,
      '确认删除知识库',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await knowledgeApi.remove(kb.id)
  ElMessage.success('已删除')
  if (activeKbId.value === kb.id) activeKbId.value = null
  await loadKnowledgeBases()
}

async function removeDoc(doc: KnowledgeDocument) {
  try {
    await ElMessageBox.confirm(`确认删除文档「${doc.filename}」？`, '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await knowledgeApi.removeDocument(doc.knowledge_base_id, doc.id)
  ElMessage.success('已删除')
  await loadDocuments()
  await loadKnowledgeBases()
}

// ---------- Phase 3: RAG ----------
async function runRag() {
  if (activeKbId.value === null) {
    ElMessage.warning('请先选择一个知识库')
    return
  }
  const question = ragQuestion.value.trim()
  if (!question) {
    ElMessage.warning('请输入问题')
    return
  }
  ragLoading.value = true
  ragAsked.value = true
  try {
    if (ragMode.value === 'search') {
      const res = await knowledgeApi.search(activeKbId.value, question)
      ragAnswer.value = ''
      ragSources.value = res.hits
      ragMeta.value = `向量 ${res.embedding_model} · 重排 ${res.reranker} · ${res.duration_ms}ms`
    } else {
      const res = await knowledgeApi.query(activeKbId.value, question)
      ragAnswer.value = res.answer
      ragSources.value = res.sources
      ragMeta.value = `模型 ${res.llm_provider} · 向量 ${res.embedding_model} · 重排 ${res.reranker} · ${res.duration_ms}ms`
    }
  } catch {
    // The axios interceptor already surfaced the error message.
  } finally {
    ragLoading.value = false
  }
}

function scorePercent(source: RagSource): string {
  const score = source.rerank_score ?? source.vector_score
  return `${(Math.max(0, Math.min(1, score)) * 100).toFixed(1)}%`
}

function sourcePage(source: RagSource): string {
  return source.page_number === null ? '未知页' : `第 ${source.page_number} 页`
}

function canReembed(doc: KnowledgeDocument): boolean {
  return doc.chunk_count > 0 && (doc.status === 'completed' || doc.status === 'failed')
}

async function reembedDoc(doc: KnowledgeDocument) {
  try {
    const res = await knowledgeApi.reembedDocument(doc.knowledge_base_id, doc.id)
    ElMessage.success(
      res.dispatch === 'inline' ? '本地模式：已同步完成向量化' : '已加入向量化队列',
    )
    await loadDocuments()
    startPolling()
  } catch {
    // surfaced by the interceptor
  }
}

// ---------- upload ----------
async function handleUpload(file: UploadRawFile) {
  if (activeKbId.value === null) {
    ElMessage.warning('请先创建或选择一个知识库')
    return
  }
  uploading.value = true
  try {
    const res = await knowledgeApi.uploadDocument(activeKbId.value, file as unknown as File)
    ElMessage.success(
      res.dispatch === 'inline'
        ? '上传成功（本地模式：已同步完成解析）'
        : '上传成功，已加入解析队列',
    )
    await loadDocuments()
    await loadKnowledgeBases()
    startPolling()
  } catch {
    // interceptor surfaced the error
  } finally {
    uploading.value = false
  }
  return false
}

// ---------- progress polling ----------
function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling() {
  stopPolling()
  // Poll while any document is still queued/running, then stop on its own.
  pollTimer = window.setInterval(async () => {
    await loadDocuments()
    const busy = documents.value.some(
      (d) => d.status === 'pending' || d.status === 'processing',
    )
    if (!busy) {
      stopPolling()
      await loadKnowledgeBases()
    }
  }, 1500)
}

onMounted(async () => {
  await loadKnowledgeBases()
  if (documents.value.some((d) => d.status === 'pending' || d.status === 'processing')) {
    startPolling()
  }
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="knowledge-root">
    <el-row :gutter="16">
      <!-- Knowledge base column -->
      <el-col :xs="24" :md="7">
        <div class="page-card kb-panel">
          <div class="panel-head">
            <span class="panel-title">知识库</span>
            <el-button type="primary" size="small" :icon="Plus" @click="createKb">
              新建
            </el-button>
          </div>

          <el-skeleton v-if="loading" :rows="3" animated />
          <el-empty v-else-if="knowledgeBases.length === 0" description="还没有知识库" :image-size="70" />

          <ul v-else class="kb-list">
            <li
              v-for="kb in knowledgeBases"
              :key="kb.id"
              :class="['kb-item', { active: kb.id === activeKbId }]"
              @click="selectKb(kb.id)"
            >
              <div class="kb-item-main">
                <div class="kb-name">{{ kb.name }}</div>
                <div class="kb-meta">{{ kb.document_count }} 个文档</div>
              </div>
              <el-button
                class="kb-del"
                text
                :icon="Delete"
                size="small"
                @click.stop="removeKb(kb)"
              />
            </li>
          </ul>
        </div>
      </el-col>

      <!-- Documents column -->
      <el-col :xs="24" :md="17">
        <div class="page-card doc-panel">
          <template v-if="activeKb">
            <div class="panel-head">
              <div>
                <span class="panel-title">{{ activeKb.name }}</span>
                <span v-if="activeKb.description" class="muted kb-desc">
                  {{ activeKb.description }}
                </span>
              </div>
              <div class="panel-actions">
                <el-input
                  v-model="keyword"
                  placeholder="按文件名筛选"
                  :prefix-icon="Search"
                  clearable
                  size="small"
                  class="kb-search"
                />
                <el-upload
                  :show-file-list="false"
                  :before-upload="handleUpload"
                  :disabled="uploading"
                  accept=".pdf,.docx,.txt"
                >
                  <el-button type="primary" size="small" :icon="UploadFilled" :loading="uploading">
                    上传文档
                  </el-button>
                </el-upload>
              </div>
            </div>

            <el-alert
              v-if="documents.some((d) => d.status === 'pending' || d.status === 'processing')"
              type="info"
              :closable="false"
              show-icon
              class="poll-hint"
              title="正在解析文档，进度每 1.5 秒自动刷新"
            />

            <el-table
              v-loading="docLoading"
              :data="filteredDocuments"
              empty-text="该知识库还没有文档"
              style="width: 100%"
            >
              <el-table-column label="文件" min-width="220">
                <template #default="{ row }">
                  <div class="file-cell">
                    <el-icon class="file-icon"><DocumentIcon /></el-icon>
                    <div>
                      <div class="file-name">{{ row.filename }}</div>
                      <div class="muted file-meta">
                        {{ fileTypeLabel(row.file_type) }} · {{ formatSize(row.file_size) }}
                      </div>
                    </div>
                  </div>
                </template>
              </el-table-column>

              <el-table-column label="状态" width="150">
                <template #default="{ row }">
                  <el-tag :type="statusMeta[row.status as DocumentStatus].type" size="small">
                    {{ statusMeta[row.status as DocumentStatus].label }}
                  </el-tag>
                </template>
              </el-table-column>

              <el-table-column label="进度" width="170">
                <template #default="{ row }">
                  <el-progress
                    :percentage="row.progress"
                    :status="
                      row.status === 'failed'
                        ? 'exception'
                        : row.status === 'completed'
                          ? 'success'
                          : undefined
                    "
                    :stroke-width="10"
                  />
                </template>
              </el-table-column>

              <el-table-column label="解析结果" min-width="220">
                <template #default="{ row }">
                  <div v-if="row.status === 'completed' || canReembed(row as KnowledgeDocument)" class="result-cell">
                    <span>{{ row.chunk_count }} 切片</span>
                    <span class="muted">{{ row.character_count }} 字符</span>
                    <span v-if="row.page_count" class="muted">{{ row.page_count }} 页</span>
                    <el-tag
                      v-if="row.embedding_model"
                      size="small"
                      type="success"
                      effect="plain"
                    >
                      已向量化
                    </el-tag>
                    <el-tag v-else size="small" type="info" effect="plain">未向量化</el-tag>
                  </div>
                  <div v-else-if="row.status === 'failed'" class="err-cell">
                    {{ row.error_message || '解析失败' }}
                  </div>
                  <span v-else class="muted">—</span>
                </template>
              </el-table-column>

              <el-table-column label="操作" width="110" align="center">
                <template #default="{ row }">
                  <el-tooltip content="重建向量（更换 Embedding 模型后使用）" placement="top">
                    <el-button
                      v-if="canReembed(row as KnowledgeDocument)"
                      text
                      type="primary"
                      :icon="RefreshRight"
                      @click="reembedDoc(row as KnowledgeDocument)"
                    />
                  </el-tooltip>
                  <el-button
                    text
                    type="danger"
                    :icon="Delete"
                    @click="removeDoc(row as KnowledgeDocument)"
                  />
                </template>
              </el-table-column>
            </el-table>

            <!-- ---------- Phase 3: RAG 问答 / 向量检索 ---------- -->
            <div class="rag-section">
              <div class="panel-head rag-head">
                <span class="panel-title">
                  <el-icon class="rag-title-icon"><ChatDotRound /></el-icon>
                  AI 问答（RAG）
                </span>
                <el-radio-group v-model="ragMode" size="small">
                  <el-radio-button value="qa">问答模式</el-radio-button>
                  <el-radio-button value="search">仅检索</el-radio-button>
                </el-radio-group>
              </div>

              <div class="rag-input-row">
                <el-input
                  v-model="ragQuestion"
                  :placeholder="
                    ragMode === 'qa'
                      ? '基于当前知识库提问，例如：本季度销售趋势如何？'
                      : '输入要检索的关键词或句子'
                  "
                  clearable
                  @keyup.enter="runRag"
                />
                <el-button
                  type="primary"
                  :icon="Search"
                  :loading="ragLoading"
                  @click="runRag"
                >
                  {{ ragMode === 'qa' ? '提问' : '检索' }}
                </el-button>
              </div>

              <div v-if="ragMeta" class="rag-meta muted">{{ ragMeta }}</div>

              <div v-if="ragMode === 'qa' && ragAnswer" class="rag-answer">
                <pre class="rag-answer-text">{{ ragAnswer }}</pre>
              </div>

              <el-empty
                v-if="!ragLoading && ragAsked && ragSources.length === 0"
                :description="
                  ragMode === 'qa'
                    ? '知识库中没有检索到相关内容（或文档尚未向量化）'
                    : '没有检索到相关内容'
                "
                :image-size="60"
              />

              <ul v-if="ragSources.length > 0" class="rag-sources">
                <li v-for="source in ragSources" :key="source.chunk_id" class="rag-source">
                  <div class="rag-source-head">
                    <el-tag size="small" round>{{ source.index }}</el-tag>
                    <span class="rag-source-file">{{ source.filename }}</span>
                    <span class="muted rag-source-loc">
                      {{ sourcePage(source) }} · 切片 {{ source.chunk_index }} ·
                      相似度 {{ scorePercent(source) }}
                    </span>
                  </div>
                  <div class="rag-snippet">{{ source.snippet }}</div>
                </li>
              </ul>
            </div>
          </template>

          <el-empty v-else description="选择或新建一个知识库开始" />
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.knowledge-root {
  display: flex;
  flex-direction: column;
}

.kb-panel,
.doc-panel {
  min-height: 560px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
}

.panel-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.kb-search {
  width: 180px;
}

.kb-desc {
  margin-left: 10px;
  font-size: 13px;
}

.kb-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.kb-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-radius: 10px;
  cursor: pointer;
  transition: background-color 0.18s ease;
}

.kb-item:hover {
  background: var(--app-bg);
}

.kb-item.active {
  background: rgba(31, 111, 235, 0.1);
}

.kb-name {
  font-size: 14px;
  font-weight: 500;
}

.kb-meta {
  font-size: 12px;
  color: var(--app-text-muted);
  margin-top: 2px;
}

.poll-hint {
  margin-bottom: 12px;
}

.file-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.file-icon {
  font-size: 20px;
  color: var(--app-primary);
}

.file-name {
  font-size: 14px;
  word-break: break-all;
}

.file-meta {
  font-size: 12px;
}

.result-cell {
  display: flex;
  gap: 10px;
  font-size: 13px;
}

.err-cell {
  color: #d03050;
  font-size: 12px;
  word-break: break-all;
}

/* ---------- Phase 3: RAG ---------- */
.rag-section {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.rag-head {
  margin-bottom: 12px;
}

.rag-title-icon {
  margin-right: 4px;
  color: var(--app-primary);
  vertical-align: -2px;
}

.rag-input-row {
  display: flex;
  gap: 8px;
}

.rag-meta {
  margin-top: 6px;
  font-size: 12px;
}

.rag-answer {
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: 10px;
  background: rgba(31, 111, 235, 0.06);
}

.rag-answer-text {
  margin: 0;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.rag-sources {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rag-source {
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--el-border-color-lighter);
}

.rag-source-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.rag-source-file {
  font-size: 13px;
  font-weight: 500;
  word-break: break-all;
}

.rag-source-loc {
  font-size: 12px;
}

.rag-snippet {
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--app-text-muted);
  word-break: break-word;
}
</style>