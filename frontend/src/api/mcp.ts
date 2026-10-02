import request from './request'
import type {
  McpCallResponse,
  McpToolsResponse,
} from '@/types/api'

export const mcpApi = {
  listTools() {
    return request.get<null, McpToolsResponse>('/mcp/tools')
  },
  refresh() {
    return request.post<null, McpToolsResponse>('/mcp/refresh')
  },
  call(tool: string, args: Record<string, unknown>) {
    return request.post<{ tool: string; arguments: Record<string, unknown> }, McpCallResponse>(
      '/mcp/call',
      { tool, arguments: args },
    )
  },
}
