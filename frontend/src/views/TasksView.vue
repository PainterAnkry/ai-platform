<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, Close, Loading, Refresh, VideoPlay } from '@element-plus/icons-vue'

import { taskApi } from '@/api/knowledge'
import type { TaskRecord, TaskStatus } from '@/types/api'

const tasks = ref<TaskRecord[]>([])
const loading = ref(false)
const filter = ref<TaskStatus | ''>('')

let pollTimer: number | null = null

const isBusy = computed(() => tasks.value.some((t) => t.status === 'pending' || t.status === 'running'))

const statusMeta: Record<TaskStatus, { label: string; type: 'info' | 'warning' | 'success' | 'danger' }> = {
  pending: { label: '排队中', type: 'info' },
  running: { label: '执行中', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('zh-CN', { hour12: false })
}

function duration(task: TaskRecord): string {
  if (!task.started_at) return '—'
  const start = new Date(task.started_at).getTime()
  const end = task.finished_at ? new Date(task.finished_at).getTime() : Date.now()
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—'
  const ms = end - start
  if (ms < 1000) return `${ms} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
}

function resultSummary(task: TaskRecord): string {
  if (!task.result) return '—'
  const r = task.result as Record<string, unknown>
  const bits: string[] = []
  if (r.file_type) bits.push(String(r.file_type).toUpperCase())
  if (r.page_count != null) bits.push(`${r.page_count} 页`)
  if (r.chunk_count != null) bits.push(`${r.chunk_count} 切片`)
  if (r.character_count != null) bits.push(`${r.character_count} 字符`)
  return bits.length ? bits.join(' · ') : '—'
}

async function load() {
  loading.value = true
  try {
    tasks.value = await taskApi.list(filter.value || undefined)
  } finally {
    loading.value = false
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    await load()
    if (!isBusy.value) stopPolling()
  }, 1500)
}

async function manualRefresh() {
  await load()
  if (isBusy.value) startPolling()
  ElMessage.success('已刷新')
}

onMounted(async () => {
  await load()
  if (isBusy.value) startPolling()
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="tasks-root page-card">
    <div class="panel-head">
      <div class="flex-row gap-8">
        <span class="panel-title">异步任务</span>
        <el-tag v-if="isBusy" type="warning" size="small" effect="plain">
          <el-icon style="vertical-align: -2px"><VideoPlay /></el-icon>
          有任务在执行
        </el-tag>
      </div>
      <div class="flex-row gap-8">
        <el-select
          v-model="filter"
          placeholder="全部状态"
          clearable
          size="small"
          style="width: 140px"
          @change="load"
        >
          <el-option label="排队中" value="pending" />
          <el-option label="执行中" value="running" />
          <el-option label="已完成" value="completed" />
          <el-option label="失败" value="failed" />
        </el-select>
        <el-button size="small" :icon="Refresh" @click="manualRefresh">刷新</el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="tasks" empty-text="还没有异步任务" style="width: 100%">
      <el-table-column label="#" width="60">
        <template #default="{ $index }">
          <span class="muted">{{ $index + 1 }}</span>
        </template>
      </el-table-column>

      <el-table-column label="任务" min-width="150">
        <template #default="{ row }">
          <div class="type-cell">
            <el-icon
              :class="{
                'icon-done': row.status === 'completed',
                'icon-fail': row.status === 'failed',
                'icon-busy': row.status === 'running' || row.status === 'pending',
              }"
            >
              <CircleCheck v-if="row.status === 'completed'" />
              <Close v-else-if="row.status === 'failed'" />
              <Loading v-else />
            </el-icon>
            <span>{{ row.type }}</span>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusMeta[row.status as TaskStatus].type" size="small">
            {{ statusMeta[row.status as TaskStatus].label }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="进度" width="160">
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

      <el-table-column label="结果 / 错误" min-width="200">
        <template #default="{ row }">
          <span v-if="row.status === 'failed'" class="err-cell">
            {{ row.error_message || '执行失败' }}
          </span>
          <span v-else class="muted">{{ resultSummary(row as TaskRecord) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="耗时" width="100">
        <template #default="{ row }">
          <span class="muted">{{ duration(row as TaskRecord) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">
          <span class="muted">{{ formatTime(row.created_at) }}</span>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.tasks-root {
  min-height: 480px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
}

.type-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.icon-done {
  color: #16a34a;
}

.icon-fail {
  color: #d03050;
}

.icon-busy {
  color: #f59e0b;
}

.err-cell {
  color: #d03050;
  font-size: 12px;
  word-break: break-all;
}
</style>