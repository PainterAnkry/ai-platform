<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'

import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

const formRef = ref<FormInstance>()
const form = reactive({
  username: '',
  email: '',
  password: '',
  confirm: '',
})

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 64, message: '长度 3-64', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule, value, callback) => {
        if (value !== form.password) callback(new Error('两次密码不一致'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
}

const submitting = ref(false)

async function onSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    await userStore.register({
      username: form.username,
      email: form.email,
      password: form.password,
    })
    ElMessage.success('注册成功，请登录')
    router.push({ name: 'login' })
  } catch {
    /* surfaced by interceptor */
  } finally {
    submitting.value = false
  }
}

function goLogin() {
  router.push({ name: 'login' })
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

      <h2 class="title">注册账号</h2>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="onSubmit">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="3-64 个字符" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="you@example.com" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm">
          <el-input v-model="form.confirm" type="password" show-password placeholder="再次输入密码" />
        </el-form-item>
        <el-button type="primary" class="submit" :loading="submitting" @click="onSubmit">
          创建账号
        </el-button>
      </el-form>

      <div class="footer-row">
        <span class="muted">已有账号？</span>
        <el-link type="primary" @click="goLogin">返回登录</el-link>
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
  max-width: 460px;
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