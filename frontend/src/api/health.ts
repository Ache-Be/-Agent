import request from '@/utils/request'

export interface HealthzInfo {
  status: string
  time: string
  files: number
  has_analysis: boolean
  conversations: number
}

export const getHealthz = (): Promise<HealthzInfo> => {
  return request.get('/healthz') as any
}
