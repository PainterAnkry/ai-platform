import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

import { authApi, type LoginPayload, type RegisterPayload } from '@/api/auth'
import { clearToken, getToken, setToken } from '@/utils/token'
import type { UserInfo } from '@/types/api'

export const useUserStore = defineStore('user', () => {
  // Seed from localStorage so `isAuthenticated` is correct immediately after a
  // page reload, instead of flickering to the signed-out header while the
  // router guard is still fetching the profile.
  const token = ref<string | null>(getToken())
  const user = ref<UserInfo | null>(null)
  const isLoading = ref(false)

  const isAuthenticated = computed(() => Boolean(token.value && user.value))

  async function login(payload: LoginPayload) {
    isLoading.value = true
    try {
      const res = await authApi.login(payload)
      token.value = res.access_token
      user.value = res.user
      setToken(res.access_token)
      return res
    } finally {
      isLoading.value = false
    }
  }

  async function register(payload: RegisterPayload) {
    isLoading.value = true
    try {
      return await authApi.register(payload)
    } finally {
      isLoading.value = false
    }
  }

  async function fetchMe() {
    const res = await authApi.me()
    user.value = res
    return res
  }

  async function logout() {
    try {
      await authApi.logout()
    } catch {
      // Ignore network errors — we're discarding local state anyway.
    }
    token.value = null
    user.value = null
    clearToken()
  }

  function reset() {
    token.value = null
    user.value = null
    clearToken()
  }

  return {
    token,
    user,
    isLoading,
    isAuthenticated,
    login,
    register,
    fetchMe,
    logout,
    reset,
  }
})