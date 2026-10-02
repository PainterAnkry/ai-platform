<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Cpu, Delete, Plus, UploadFilled } from '@element-plus/icons-vue'

import { agentApi } from '@/api/agent'
import type { Agent, AgentPayload, DataFileRecord, ToolRecord } from '@/types/api'

const agents = ref<Agent[]>([])
const tools = ref<ToolRecord[]>([])
const dataFiles = ref<DataFileRecord[]>([])
const loading = ref(false)

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)

const form = reactive<AgentPayload>({
  name: '',
  system_prompt: '',
  tools: [],
})
// Checkbox group needs a non-null string[] binding; synced to/from form.tools.
const selectedTools = ref<string[]>([])

onMounted(load)

async function load() {
  loading.value = true
  try {
    const [agentList, toolList, files] = await Promise.all([
      agentApi.list(),
      agentApi.listTools(),
      agentApi.listDataFiles().catch(() => [] as DataFileRecord[]),
    ])
    agents.value = agentList
    tools.value = toolList
    dataFiles.value = files
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  form.name = ''
  form.system_prompt = ''
  form.tools = []
  selectedTools.value = []
  dialogVisible.value = true
}

function openEdit(agent: Agent) {
  editingId.value = agent.id
  form.name = agent.name
  form.system_prompt = agent.system_prompt
  form.tools = [...(agent.tools ?? [])]
  selectedTools.value = [...(agent.tools ?? [])]
  dialogVisible.value = true
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写 Agent 名称')
    return
  }
  saving.value = true
  try {
    const payload: AgentPayload = {
      name: form.name.trim(),
      system_prompt: form.system_prompt,
      tools: selectedTools.value.length ? [...selectedTools.value] : null,
    }
    if (editingId.value === null) {
      await agentApi.create(payload)
      ElMessage.success('Agent 已创建')
    } else {
      await agentApi.update(editingId.value, payload)
      ElMessage.success('已保存')
    }
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function removeAgent(agent: Agent) {
  try {
    await ElMessageBox.confirm(`删除 Agent「${agent.name}」？`, '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await agentApi.remove(agent.id)
  ElMessage.success('已删除')
  await load()
}

async function handleDataUpload(file: File) {
  try {
    const record = await agentApi.uploadDataFile(file as unknown as File)
    ElMessage.success(`已上传 ${record.filename}（${record.row_count} 行）`)
    await load()
  } catch {
    // surfaced by the interceptor
  }
  return false
}

async function removeDataFile(file: DataFileRecord) {
  await agentApi.removeDataFile(file.id)
  ElMessage.success('已删除')
  await load()
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}
</script>

<template>
  <div class="agent-root">
    <el-row :gutter="16">
      <el-col :xs="24" :md="14">
        <div class="page-card agent-panel">
          <div class="panel-head">
            <span class="panel-title">
              <el-icon class="title-icon"><Cpu /></el-icon>
              Agent 管理
            </span>
            <el-button type="primary" size="small" :icon="Plus" @click="openCreate">
              新建 Agent
            </el-button>
          </div>

          <el-skeleton v-if="loading" :rows="4" animated />
          <el-empty v-else-if="agents.length === 0" description="还没有 Agent，创建一个开始自动化工作流" :image-size="70" />

          <ul v-else class="agent-list">
            <li v-for="agent in agents" :key="agent.id" class="agent-item">
              <div class="agent-main" @click="openEdit(agent)">
                <div class="agent-name">{{ agent.name }}</div>
                <div class="agent-prompt muted">
                  {{ agent.system_prompt || '（默认系统提示词）' }}
                </div>
                <div class="agent-tools">
                  <el-tag
                    v-for="tool in agent.tools ?? []"
                    :key="tool"
                    size="small"
                    effect="plain"
                  >
                    {{ tool }}
                  </el-tag>
                  <el-tag v-if="!(agent.tools ?? []).length" size="small" type="info" effect="plain">
                    全部内置工具
                  </el-tag>
                </div>
              </div>
              <el-button text type="danger" :icon="Delete" @click="removeAgent(agent)" />
            </li>
          </ul>
        </div>
      </el-col>

      <el-col :xs="24" :md="10">
        <div class="page-card tool-panel">
          <div class="panel-head">
            <span class="panel-title">内置工具</span>
          </div>
          <ul class="tool-list">
            <li v-for="tool in tools" :key="tool.id" class="tool-item">
              <div class="tool-head">
                <el-tag size="small" effect="dark">{{ tool.name }}</el-tag>
                <el-tag size="small" type="success" effect="plain">
                  {{ tool.enabled ? '可用' : '停用' }}
                </el-tag>
              </div>
              <div class="tool-desc muted">{{ tool.description }}</div>
            </li>
          </ul>

          <div class="panel-head data-head">
            <span class="panel-title">数据文件</span>
            <el-upload
              :show-file-list="false"
              :before-upload="handleDataUpload"
              accept=".xlsx,.xls,.csv"
            >
              <el-button size="small" :icon="UploadFilled">上传 Excel/CSV</el-button>
            </el-upload>
          </div>
          <el-empty
            v-if="dataFiles.length === 0"
            description="上传数据文件后，Agent 可自动完成 Pandas 分析并生成图表"
            :image-size="60"
          />
          <ul v-else class="tool-list">
            <li v-for="file in dataFiles" :key="file.id" class="tool-item">
              <div class="tool-head">
                <span class="file-name">{{ file.filename }}</span>
                <span class="muted file-meta">{{ file.row_count }} 行 · {{ formatSize(file.file_size) }}</span>
                <el-button text type="danger" :icon="Delete" size="small" @click="removeDataFile(file)" />
              </div>
              <div class="tool-desc muted">{{ (file.column_names ?? []).join(' · ') }}</div>
            </li>
          </ul>
        </div>
      </el-col>
    </el-row>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId === null ? '新建 Agent' : '编辑 Agent'"
      width="560px"
    >
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="例如：数据分析师" maxlength="128" />
        </el-form-item>
        <el-form-item label="系统提示词">
          <el-input
            v-model="form.system_prompt"
            type="textarea"
            :rows="4"
            placeholder="定义 Agent 的人设与工作方式（留空使用默认提示词）"
          />
        </el-form-item>
        <el-form-item label="启用工具（不选 = 全部内置工具）">
          <el-checkbox-group v-model="selectedTools">
            <el-checkbox v-for="tool in tools" :key="tool.name" :value="tool.name">
              {{ tool.name }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.agent-panel,
.tool-panel {
  min-height: 480px;
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

.title-icon {
  margin-right: 4px;
  color: var(--app-primary);
  vertical-align: -2px;
}

.agent-list,
.tool-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.agent-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  transition: box-shadow 0.18s ease;
}

.agent-item:hover {
  box-shadow: 0 2px 10px rgba(31, 111, 235, 0.1);
}

.agent-name {
  font-size: 14px;
  font-weight: 600;
}

.agent-prompt {
  font-size: 12px;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
}

.agent-tools {
  margin-top: 8px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.tool-item {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
}

.tool-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-name {
  font-size: 13px;
  font-weight: 500;
  word-break: break-all;
}

.file-meta {
  font-size: 12px;
}

.tool-desc {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
}

.data-head {
  margin-top: 20px;
}
</style>
