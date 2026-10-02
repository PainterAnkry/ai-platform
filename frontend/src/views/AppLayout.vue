<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  ArrowDown,
  Bell,
  ChatLineRound,
  Connection,
  DataAnalysis,
  Document,
  House,
  Setting,
} from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const activeMenu = computed(() => route.path)

const menuItems = [
  { path: '/dashboard', title: 'Dashboard', icon: House, disabled: false },
  { path: '/knowledge', title: '知识库', icon: Document, disabled: false },
  { path: '/chat', title: 'AI 对话', icon: ChatLineRound, disabled: false },
  { path: '/agent', title: 'Agent', icon: DataAnalysis, disabled: false },
  { path: '/mcp', title: 'MCP 工具', icon: Connection, disabled: false },
  { path: '/tasks', title: '异步任务', icon: Bell, disabled: false },
  { path: '/settings', title: '设置', icon: Setting, disabled: true },
]

const userMenuVisible = ref(false)

// A valid token can exist before the profile request resolves, so fall back to
// a neutral label instead of rendering an empty user chip.
const displayName = computed(
  () => userStore.user?.username || (userStore.token ? '加载中' : ''),
)
const initial = computed(() => displayName.value.slice(0, 1).toUpperCase() || '?')

function goLogin() {
  router.push({ name: 'login' })
}

async function handleLogout() {
  userMenuVisible.value = false
  await userStore.logout()
  router.push({ name: 'login' })
}

function goRegister() {
  router.push({ name: 'register' })
}
</script>

<template>
  <el-container class="layout-root">
    <el-aside width="220px" class="layout-aside">
      <div class="brand">
        <span class="brand-mark">AI</span>
        <span class="brand-text">Platform</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        class="layout-menu"
        background-color="transparent"
        text-color="#cbd5e1"
        active-text-color="#ffffff"
        router
      >
        <el-menu-item
          v-for="item in menuItems"
          :key="item.path"
          :index="item.path"
          :disabled="item.disabled"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
      <div class="aside-footer muted">智能知识库 · Agent 工作流平台</div>
    </el-aside>

    <el-container>
      <el-header class="layout-header">
        <div class="header-title">
          {{ (route.meta.title as string) || 'AI Platform' }}
        </div>
        <div class="header-actions">
          <template v-if="userStore.isAuthenticated">
            <el-dropdown v-model:visible="userMenuVisible" trigger="click">
              <span class="user-chip">
                <el-avatar :size="28">{{ initial }}</el-avatar>
                <span class="user-name">{{ displayName }}</span>
                <el-icon><ArrowDown /></el-icon>
              </span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item disabled>
                    {{ userStore.user?.email || '—' }}
                  </el-dropdown-item>
                  <el-dropdown-item divided @click="handleLogout">
                    退出登录
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
          <template v-else>
            <el-button text @click="goLogin">登录</el-button>
            <el-button type="primary" @click="goRegister">注册</el-button>
          </template>
        </div>
      </el-header>

      <el-main class="layout-main">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout-root {
  min-height: 100vh;
}

.layout-aside {
  background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
  color: #e2e8f0;
  display: flex;
  flex-direction: column;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 22px 16px;
  font-weight: 600;
  font-size: 18px;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: linear-gradient(135deg, #1f6feb 0%, #7c3aed 100%);
  color: #fff;
  font-weight: 700;
}

.brand-text {
  letter-spacing: 0.5px;
}

.layout-menu {
  border: none;
  flex: 1;
}

.layout-menu :deep(.el-menu-item) {
  margin: 4px 12px;
  border-radius: 8px;
}

.layout-menu :deep(.el-menu-item.is-active) {
  background: rgba(31, 111, 235, 0.25);
}

.aside-footer {
  padding: 16px 22px 24px;
  font-size: 12px;
}

.layout-header {
  background: #ffffff;
  border-bottom: 1px solid var(--app-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 999px;
  transition: background-color 0.2s ease;
}

.user-chip:hover {
  background: var(--app-bg);
}

.user-name {
  font-size: 14px;
  color: var(--app-text);
}

.layout-main {
  background: var(--app-bg);
  padding: 24px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>