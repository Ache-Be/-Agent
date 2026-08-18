import request from '@/utils/request'

export interface ReportItem {
  name: string
  category: 'summary' | 'student' | 'single' | 'question' | 'other'
  size: number
  size_str: string
  mtime: string
  display_name: string
  student_name: string
  student_id: string
}

export interface ReportOverview {
  has_data: boolean
  results?: any[]
  success_count?: number
  fail_count?: number
  agg?: any
  student_list?: any[]
}

export const listReports = (type: string = 'all'): Promise<{ total: number; items: ReportItem[] }> => {
  return request.get('/api/reports', { params: { type } }) as any
}

export const viewReportContent = (filename: string): Promise<{ name: string; content: string; size: number; mtime: string }> => {
  return request.get(`/api/reports/content/${filename}`) as any
}

export const downloadReport = (filename: string) => {
  // 直接用浏览器打开（会触发下载）
  window.location.href = `/api/reports/download/${encodeURIComponent(filename)}`
}

export const getReportOverview = (): Promise<ReportOverview> => {
  return request.get('/api/reports/overview') as any
}

export const getSingleFileReport = (stem: string): Promise<any> => {
  return request.get(`/api/reports/single/${stem}`) as any
}

export const getStudentReport = (studentKey: string, reportStem?: string): Promise<any> => {
  const url = reportStem
    ? `/api/reports/student/${reportStem}/${studentKey}`
    : `/api/reports/student/${studentKey}`
  return request.get(url) as any
}

export const analyzeQuestions = (file: File): Promise<any> => {
  const form = new FormData()
  form.append('file', file)
  return request.post('/api/reports/analyze-questions', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export const importKnowledge = (file: File): Promise<any> => {
  const form = new FormData()
  form.append('file', file)
  return request.post('/api/reports/knowledge-import', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
