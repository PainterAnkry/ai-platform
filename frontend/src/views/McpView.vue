<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Connection, RefreshRight, Promotion } from '@element-plus/icons-vue'

import { mcpApi } from '@/api/mcp'
import type { McpToolInfo } from '@/types/api'

const tools = ref<McpToolInfo[]>([])
const state = ref<string>('not_discovered')
const errorText = ref<string | null>(null)
const serverName = ref('')
const loading = ref(false)
const refreshing = ref(false)

const selectedTool = ref<string | null>(null)
const argsText = ref('{}')
const calling = ref(false)
const callResult = ref<{ text: string; is_error: boolean } | null>(null)

const stateMeta: Record<string, { label: string; type: 'success' | 'warning' | 'info' | 'danger' }> = {
  connected: { label: '已连接', type: 'success' },
  unreachable: { label: '不可达', type: 'danger' },
  not_discovered: { label: '未发现', type: 'info' },
  disabled: { label: '已停用', type: 'info' },
}

const selectedDescriptor = computed(
  () => tools.value.find((t) => t.name === selectedTool.value) ?? null,
)

onMounted(load)

async function load() {
  loading.value = true
  try {
    const res = await mcpApi.listTools()
    tools.value = res.tools
    state.value = res.status.state
    errorText.value = res.status.error
    serverName.value = res.status.server?.name ?? ''
    if (!selectedTool.value && res.tools.length > 0) {
      selectedTool.value = res.tools[0].name
    }
  } finally {
    loading.value = false
  }
}

async function refresh() {
  refreshing.value = true
  try {
    const res = await mcpApi.refresh()
    tools.value = res.tools
    state.value = res.status.state
    errorText.value = res.status.error
    serverName.value = res.status.server?.name ?? ''
    ElMessage.success(`发现 ${res.tools.length} 个 MCP 工具`)
    if (!tools.value.some((t) => t.name === selectedTool.value)) {
      selectedTool.value = res.tools[0]?.name ?? null
    }
  } finally {
    refreshing.value = false
  }
}

async function invoke() {
  if (!selectedTool.value) return
  let args: Record<string, unknown>
  try {
    args = argsText.value.trim() ? JSON.parse(argsText.value) : {}
  } catch {
    ElMessage.error('参数不是合法 JSON')
    return
  }
  calling.value = true
  callResult.value = null
  try {
    const res = await mcpApi.call(selectedTool.value, args)
    callResult.value = { text: res.text, is_error: res.is_error }
  } catch {
    // surfaced by the interceptor
  } finally {
    calling.value = false
  }
}

function schemaHint(tool: McpToolInfo): string {
  const props = (tool.input_schema as { properties?: Record<string, unknown> }).properties
  if (!props) return ''
  return Object.keys(props).join(', ')
}
</script>

<template>
  <div class="mcp-root">
    <el-row :gutter="16">
      <el-col :xs="24" :md="14">
        <div class="page-card tool-panel">
          <div class="panel-head">
            <span class="panel-title">
              <el-icon class="title-icon"><Connection /></el-icon>
              MCP 工具
              <el-tag :type="stateMeta[state]?.type ?? 'info'" size="small" class="state-tag">
                {{ stateMeta[state]?.label ?? state }}
              </el-tag>
              <span v-if="serverName" class="muted server-name">{{ serverName }}</span>
            </span>
            <el-button size="small" :icon="RefreshRight" :loading="refreshing" @click="refresh">
              重新发现
            </el-button>
          </div>

          <el-alert
            v-if="errorText"
            type="warning"
            :closable="false"
            show-icon
            class="err-alert"
            :title="`MCP 服务器不可达：${errorText}`"
          />

          <el-skeleton v-if="loading" :rows="4" animated />
          <el-empty
            v-else-if="tools.length === 0"
            description="没有发现 MCP 工具；确认 MCP_ENABLED=true 后点击「重新发现」"
            :image-size="70"
          />
          <ul v-else class="tool-list">
            <li
              v-for="tool in tools"
              :key="tool.name"
              :class="['tool-item', { active: tool.name === selectedTool }]"
              @click="selectedTool = tool.name"
            >
              <div class="tool-head">
                <el-tag size="small" effect="dark">{{ tool.remote_name }}</el-tag>
                <span class="muted tool-id">{{ tool.name }}</span>
              </div>
              <div class="tool-desc muted">{{ tool.description }}</div>
            </li>
          </ul>
        </div>
      </el-col>

      <el-col :xs="24" :md="10">
        <div class="page-card call-panel">
          <div class="panel-head">
            <span class="panel-title">工具测试调用</span>
          </div>

          <el-select
            v-model="selectedTool"
            placeholder="选择工具"
            style="width: 100%; margin-bottom: 12px"
          >
            <el-option v-for="tool in tools" :key="tool.name" :label="tool.name" :value="tool.name" />
          </el-select>

          <div v-if="selectedDescriptor" class="muted schema-hint">
            参数：{{ schemaHint(selectedDescriptor) || '（无）' }}
          </div>

          <el-input
            v-model="argsText"
            type="textarea"
            :rows="6"
            placeholder='JSON 参数，例如 {"expression": "1+2*3"}'
            class="args-input"
          />

          <div class="call-actions">
            <el-button
              type="primary"
              :icon="Promotion"
              :loading="calling"
              :disabled="!selectedTool"
              @click="invoke"
            >
              调用
            </el-button>
          </div>

          <el-empty v-if="!callResult" description="调用结果会显示在这里" :image-size="60" />
          <div v-else class="call-result" :class="{ error: callResult.is_error }">
            <pre>{{ callResult.text }}</pre>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.tool-panel,
.call-panel {
  min-height: 520px;
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

.state-tag {
  margin-left: 8px;
}

.server-name {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
}

.err-alert {
  margin-bottom: 12px;
}

.tool-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tool-item {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.tool-item:hover,
.tool-item.active {
  border-color: var(--app-primary);
}

.tool-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tool-id {
  font-size: 12px;
}

.tool-desc {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
}

.schema-hint {
  font-size: 12px;
  margin-bottom: 8px;
}

.args-input {
  font-family: Consolas, monospace;
}

.call-actions {
  display: flex;
  justify-content: flex-end;
  margin: 10px 0 16px;
}

.call-result {
  border-radius: 10px;
  border: 1px solid var(--el-border-color-lighter);
  padding: 12px;
  max-height: 320px;
  overflow: auto;
}

.call-result.error {
  border-color: var(--el-color-danger);
}

.call-result pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
}
</style>
