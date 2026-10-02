import request from './request'
import type {
  Agent,
  AgentPayload,
  DataFileRecord,
  ToolRecord,
} from '@/types/api'

export const agentApi = {
  list() {
    return request.get<null, Agent[]>('/agents')
  },
  create(payload: AgentPayload) {
    return request.post<AgentPayload, Agent>('/agents', payload)
  },
  get(agentId: number) {
    return request.get<null, Agent>(`/agents/${agentId}`)
  },
  update(agentId: number, payload: Partial<AgentPayload>) {
    return request.patch<Partial<AgentPayload>, Agent>(`/agents/${agentId}`, payload)
  },
  remove(agentId: number) {
    return request.delete<null, void>(`/agents/${agentId}`)
  },

  listTools() {
    return request.get<null, ToolRecord[]>('/agents/tools')
  },

  listDataFiles() {
    return request.get<null, DataFileRecord[]>('/agents/data-files')
  },
  uploadDataFile(file: File) {
    const form = new FormData()
    form.append('file', file)
    return request.post<FormData, DataFileRecord>('/agents/data-files', form)
  },
  removeDataFile(fileId: number) {
    return request.delete<null, void>(`/agents/data-files/${fileId}`)
  },
}
