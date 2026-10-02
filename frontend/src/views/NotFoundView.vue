<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

function goHome() {
  router.replace('/dashboard')
}
</script>

<template>
  <div class="not-found">
    <div class="card">
      <div class="code">404</div>
      <div class="title">页面不存在</div>
      <div class="muted">请检查链接，或返回 Dashboard</div>
      <el-button type="primary" @click="goHome">返回 Dashboard</el-button>
    </div>
  </div>
</template>

<style scoped>
.not-found {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-bg);
}

.card {
  background: var(--app-card-bg);
  padding: 48px;
  border-radius: 16px;
  box-shadow: var(--app-shadow);
  text-align: center;
  max-width: 420px;
}

.code {
  font-size: 72px;
  font-weight: 700;
  background: linear-gradient(135deg, #1f6feb 0%, #7c3aed 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.title {
  font-size: 22px;
  margin-bottom: 6px;
}
</style>