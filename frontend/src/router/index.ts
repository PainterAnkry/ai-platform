import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useUserStore } from '@/stores/user'
import { getToken } from '@/utils/token'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/RegisterView.vue'),
    meta: { public: true, title: '注册' },
  },
  {
    path: '/',
    component: () => import('@/views/AppLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: 'Dashboard' },
      },
      {
        path: 'knowledge',
        name: 'knowledge',
        component: () => import('@/views/KnowledgeView.vue'),
        meta: { title: '知识库' },
      },
      {
        path: 'chat',
        name: 'chat',
        component: () => import('@/views/ChatView.vue'),
        meta: { title: 'AI 对话' },
      },
      {
        path: 'agent',
        name: 'agent',
        component: () => import('@/views/AgentView.vue'),
        meta: { title: 'Agent' },
      },
      {
        path: 'mcp',
        name: 'mcp',
        component: () => import('@/views/McpView.vue'),
        meta: { title: 'MCP 工具' },
      },
      {
        path: 'tasks',
        name: 'tasks',
        component: () => import('@/views/TasksView.vue'),
        meta: { title: '异步任务' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { public: true, title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  if (to.meta.title) {
    document.title = `${to.meta.title as string} · AI Platform`
  }

  if (to.meta.public) {
    return true
  }

  const userStore = useUserStore()
  if (!getToken()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  // Hydrate the user record on first navigation after page reload.
  if (!userStore.user) {
    try {
      await userStore.fetchMe()
    } catch {
      userStore.reset()
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }

  return true
})

export default router