import request from './request'
import type { LoginResponse, UserInfo } from '@/types/api'

export interface LoginPayload {
  username: string
  password: string
}

export interface RegisterPayload {
  username: string
  email: string
  password: string
}

export const authApi = {
  login(payload: LoginPayload) {
    return request.post<LoginPayload, LoginResponse>('/auth/login', payload)
  },
  register(payload: RegisterPayload) {
    return request.post<RegisterPayload, UserInfo>('/auth/register', payload)
  },
  me() {
    return request.get<null, UserInfo>('/auth/me')
  },
  logout() {
    return request.post<null, { detail: string }>('/auth/logout', {})
  },
}