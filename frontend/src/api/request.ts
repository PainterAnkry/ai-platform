import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { ElMessage } from 'element-plus'

import { clearToken, getToken } from '@/utils/token'
import type { ApiErrorBody } from '@/types/api'

const baseURL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

const instance: AxiosInstance = axios.create({
  baseURL,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

// Attach JWT to every request when available.
instance.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getToken()
  if (token && config.headers) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  // FormData uploads must keep the boundary the browser generates, so drop the
  // default JSON content type for them.
  if (typeof FormData !== 'undefined' && config.data instanceof FormData) {
    config.headers.set('Content-Type', undefined as unknown as string)
  }
  return config
})

let isRedirecting = false

function extractDetail(data: unknown): string {
  if (!data) return '请求失败'
  if (typeof data === 'string') return data
  const body = data as ApiErrorBody
  if (typeof body.detail === 'string') return body.detail
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join('; ')
  return '请求失败'
}

instance.interceptors.response.use(
  (response: AxiosResponse) => response.data,
  (error: AxiosError) => {
    const status = error.response?.status
    const detail = extractDetail(error.response?.data)

    if (status === 401) {
      clearToken()
      if (!isRedirecting && window.location.pathname !== '/login') {
        isRedirecting = true
        const redirect = encodeURIComponent(window.location.pathname + window.location.search)
        window.location.replace(`/login?redirect=${redirect}`)
        setTimeout(() => (isRedirecting = false), 1000)
      }
      ElMessage.error(detail || '请重新登录')
    } else if (status && status >= 500) {
      ElMessage.error(`服务器错误 (${status})`)
    } else if (status) {
      ElMessage.error(detail)
    } else {
      ElMessage.error('网络异常，请稍后重试')
    }

    return Promise.reject(error)
  },
)

export default instance