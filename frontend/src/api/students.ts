import request from '@/utils/request'

export interface StudentListItem {
  _key: string
  name: string
  student_id: string
  weak_count: number
  weak_subtask_count: number
  weak_knowledge_count: number
  weakness_rate: number
  avg_score: number
  experiment_count: number
}

export interface StudentStats {
  has_data: boolean
  total: number
  weak_count: number
  healthy_count: number
  avg_weakness_rate: number
  segmentation: {
    rate_0: number
    rate_0_20: number
    rate_20_40: number
    rate_40_60: number
    rate_60_100: number
  }
  top_units: { unit: string; weak_student_count: number }[]
  experiment_count: number
  quiz_count: number
  unit_count: number
  attendance_count: number
}

export interface StudentDetail {
  _key: string
  name: string
  student_id: string
  level: '优秀' | '良好' | '中等' | '薄弱' | '重点预警'
  weakness_rate: number
  weak_count: number
  weak_subtask_count: number
  weak_knowledge_count: number
  avg_score: number
  experiment_count: number
  experiments: { name: string; score: number; weak_count: number; weakness_rate: number; submitted: boolean }[]
  weak_units: { unit: string; knowledge: string[]; count: number }[]
  weak_knowledge_list: any[]
  top_weak_knowledge: any[]
  prediction: string
  suggestions: string[]
  report_filename: string
  report_content: string
}

export const getStudentStats = (): Promise<StudentStats> => {
  return request.get('/api/students/stats') as any
}

export const listStudents = (params: {
  keyword?: string
  weak_only?: boolean
  min_weakness_rate?: number
  sort?: string
  page?: number
  page_size?: number
}): Promise<{ has_data: boolean; total: number; page: number; page_size: number; items: StudentListItem[] }> => {
  return request.get('/api/students', { params }) as any
}

export const getStudentDetail = (key: string): Promise<StudentDetail> => {
  return request.get(`/api/students/${encodeURIComponent(key)}`) as any
}

export const downloadStudentReport = (filename: string) => {
  window.location.href = `/api/reports/download/${encodeURIComponent(filename)}`
}
