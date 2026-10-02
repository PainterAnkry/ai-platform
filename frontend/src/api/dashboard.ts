import request from './request'
import type { DashboardResponse } from '@/types/api'

export const dashboardApi = {
  overview() {
    return request.get<null, DashboardResponse>('/dashboard')
  },
}