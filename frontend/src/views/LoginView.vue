<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'

import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const formRef = ref<FormInstance>()
const form = reactive({
  username: '',
  password: '',
})

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名或邮箱', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const submitting = ref(false)

async function onSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    await userStore.login({ username: form.username, password: form.password })
    ElMessage.success('登录成功')
    const redirect = (route.query.redirect as string) || '/dashboard'
    router.replace(redirect)
  } catch {
    // request interceptor already surfaced the error message
  } finally {
    submitting.value = false
  }
}

function goRegister() {
  router.push({ name: 'register' })
}
</script>

<template>
  <div class="auth-shell">
    <el-card class="auth-card" shadow="never">
      <div class="brand-row">
        <span class="brand-mark">AI</span>
        <div>
          <div class="brand-title">AI Platform</div>
          <div class="muted">智能知识库与 Agent 工作流平台</div>
        </div>
      </div>

      <h2 class="title">登录</h2>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="onSubmit">
        <el-form-item label="用户名 / 邮箱" prop="username">
          <el-input v-model="form.username" autocomplete="username" placeholder="username or email" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            autocomplete="current-password"
            show-password
            placeholder="••••••••"
          />
        </el-form-item>
        <el-button type="primary" class="submit" :loading="submitting" @click="onSubmit">
          登录
        </el-button>
      </el-form>

      <div class="footer-row">
        <span class="muted">还没有账号？</span>
        <el-link type="primary" @click="goRegister">立即注册</el-link>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.auth-shell {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1f6feb 0%, #7c3aed 100%);
  padding: 24px;
}

.auth-card {
  width: 100%;
  max-width: 420px;
  border-radius: 16px;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: linear-gradient(135deg, #1f6feb 0%, #7c3aed 100%);
  color: #fff;
  font-weight: 700;
  font-size: 18px;
}

.brand-title {
  font-size: 18px;
  font-weight: 600;
}

.title {
  margin: 0 0 12px;
  font-size: 22px;
}

.submit {
  width: 100%;
  margin-top: 8px;
}

.footer-row {
  margin-top: 16px;
  text-align: center;
  font-size: 14px;
}
</style>