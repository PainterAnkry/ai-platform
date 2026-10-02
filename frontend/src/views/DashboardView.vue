<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import type { Component } from 'vue'
import * as echarts from 'echarts'
import type { EChartsType } from 'echarts'
import {
  ChatLineRound,
  CircleCheck,
  DataAnalysis,
  Document,
  Folder,
  Loading,
} from '@element-plus/icons-vue'

import { dashboardApi } from '@/api/dashboard'
import type { DashboardResponse, DashboardStats, TaskRecord, TaskStatus, TrendPoint } from '@/types/api'

const overview_ = ref<DashboardResponse | null>(null)
const loading = ref(false)

const stats = computed(() => overview_.value?.stats)
const trend = computed(() => overview_.value?.ai_call_trend)
const taskBreakdown = computed<TrendPoint[]>(() => overview_.value?.task_status_breakdown ?? [])
const recentTasks = computed<TaskRecord[]>(() => overview_.value?.recent_tasks ?? [])

const taskStatusMeta: Record<
  TaskStatus,
  { label: string; type: 'info' | 'warning' | 'success' | 'danger' }
> = {
  pending: { label: '排队中', type: 'info' },
  running: { label: '执行中', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false })
}

function taskResult(task: TaskRecord): string {
  if (task.status === 'failed') return task.error_message || '执行失败'
  const r = (task.result ?? {}) as Record<string, unknown>
  const bits: string[] = []
  if (r.chunk_count != null) bits.push(`${r.chunk_count} 切片`)
  if (r.character_count != null) bits.push(`${r.character_count} 字符`)
  if (r.page_count != null) bits.push(`${r.page_count} 页`)
  return bits.length ? bits.join(' · ') : '—'
}

const trendChartEl = ref<HTMLDivElement | null>(null)
const taskChartEl = ref<HTMLDivElement | null>(null)
const trendChart = shallowRef<EChartsType | null>(null)
const taskChart = shallowRef<EChartsType | null>(null)

function renderCharts() {
  if (trendChartEl.value && trend.value) {
    trendChart.value = echarts.init(trendChartEl.value)
    trendChart.value.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 36, right: 16, top: 28, bottom: 32 },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: trend.value.points.map((p) => p.date.slice(5)),
        axisLine: { lineStyle: { color: '#cbd5e1' } },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#e5e7eb' } },
      },
      series: [
        {
          name: 'AI 调用',
          type: 'line',
          smooth: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { width: 3, color: '#1f6feb' },
          itemStyle: { color: '#1f6feb' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(31,111,235,0.35)' },
              { offset: 1, color: 'rgba(31,111,235,0.02)' },
            ]),
          },
          data: trend.value.points.map((p) => p.value),
        },
      ],
    })
  }

  if (taskChartEl.value) {
    const total = taskBreakdown.value.reduce((sum, p) => sum + p.value, 0)
    taskChart.value = echarts.init(taskChartEl.value)
    taskChart.value.setOption(
      total === 0
        ? {
            // ECharts draws equal slices for an all-zero dataset, which looks
            // like real data. Show an explicit empty state instead.
            title: {
              text: '暂无任务数据',
              left: 'center',
              top: 'middle',
              textStyle: { color: '#9ca3af', fontSize: 13, fontWeight: 'normal' },
            },
            series: [],
          }
        : {
            tooltip: { trigger: 'item' },
            legend: { bottom: 0 },
            series: [
              {
                name: '任务状态',
                type: 'pie',
                radius: ['45%', '70%'],
                avoidLabelOverlap: false,
                itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
                label: { show: false },
                data: taskBreakdown.value.map((p) => ({ name: p.date, value: p.value })),
              },
            ],
          },
    )
  }
}

function handleResize() {
  trendChart.value?.resize()
  taskChart.value?.resize()
}

async function load() {
  loading.value = true
  try {
    overview_.value = await dashboardApi.overview()
    await new Promise(requestAnimationFrame)
    renderCharts()
  } finally {
    loading.value = false
  }
}

// `value` is written by the watcher below, so the cards cannot be `as const`.
interface StatCard {
  key: keyof DashboardStats
  label: string
  value: number
  icon: Component
  color: string
}

const statCards = reactive<StatCard[]>([
  { key: 'knowledge_base_count', label: '知识库', value: 0, icon: Folder, color: '#1f6feb' },
  { key: 'document_count', label: '文档', value: 0, icon: Document, color: '#10b981' },
  { key: 'conversation_count', label: '对话', value: 0, icon: ChatLineRound, color: '#7c3aed' },
  { key: 'ai_call_count', label: 'AI 调用', value: 0, icon: DataAnalysis, color: '#f59e0b' },
  { key: 'running_task_count', label: '运行中任务', value: 0, icon: Loading, color: '#0ea5e9' },
  { key: 'completed_task_count', label: '已完成任务', value: 0, icon: CircleCheck, color: '#16a34a' },
])

watch(
  () => stats.value,
  (next) => {
    if (!next) return
    statCards.forEach((card) => {
      card.value = next[card.key] ?? 0
    })
  },
  { immediate: true },
)

onMounted(async () => {
  await load()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  trendChart.value?.dispose()
  taskChart.value?.dispose()
})

// Re-render charts when overview data changes (e.g. after a refresh).
watch(overview_, () => {
  trendChart.value?.dispose()
  taskChart.value?.dispose()
  renderCharts()
})
</script>

<template>
  <div v-loading="loading" class="dashboard-root">
    <el-row :gutter="16">
      <el-col v-for="card in statCards" :key="card.key" :xs="12" :sm="8" :md="4">
        <div class="stat-card page-card">
          <div class="stat-icon" :style="{ backgroundColor: `${card.color}1a`, color: card.color }">
            <el-icon :size="20"><component :is="card.icon" /></el-icon>
          </div>
          <div class="stat-meta">
            <div class="stat-label">{{ card.label }}</div>
            <div class="stat-value">{{ card.value }}</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <el-col :xs="24" :md="16">
        <div class="page-card chart-card">
          <div class="chart-title">近 7 日 AI 调用趋势</div>
          <div ref="trendChartEl" class="chart-canvas"></div>
        </div>
      </el-col>
      <el-col :xs="24" :md="8">
        <div class="page-card chart-card">
          <div class="chart-title">任务状态分布</div>
          <div ref="taskChartEl" class="chart-canvas chart-canvas--small"></div>
        </div>
      </el-col>
    </el-row>

    <div class="page-card recent-tasks">
      <div class="chart-title">
        最近任务
        <span class="muted chart-hint">异步解析任务的最新进度</span>
      </div>
      <el-table :data="recentTasks" empty-text="还没有异步任务" style="width: 100%">
        <el-table-column label="任务" min-width="140">
          <template #default="{ row }">
            <span class="task-type">{{ row.type }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="taskStatusMeta[row.status as TaskStatus].type" size="small">
              {{ taskStatusMeta[row.status as TaskStatus].label }}
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
        <el-table-column label="结果 / 错误" min-width="200">
          <template #default="{ row }">
            <span :class="row.status === 'failed' ? 'err-cell' : 'muted'">
              {{ taskResult(row as TaskRecord) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">
            <span class="muted">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>


  </div>
</template>

<style scoped>
.dashboard-root {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
}

.stat-meta {
  display: flex;
  flex-direction: column;
}

.stat-label {
  font-size: 13px;
  color: var(--app-text-muted);
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
}

.stat-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
}

.chart-row {
  margin-top: 0;
}

.chart-card {
  display: flex;
  flex-direction: column;
}

.chart-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
}

.chart-canvas {
  width: 100%;
  height: 280px;
}

.chart-canvas--small {
  height: 280px;
}

.recent-tasks {
  display: flex;
  flex-direction: column;
}

.chart-hint {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
}

.task-type {
  font-size: 13px;
}

.err-cell {
  color: #d03050;
  font-size: 12px;
  word-break: break-all;
}

</style>