// 新架构 · 数据聚合 API（pgvector 聚合视图 + 混合检索）
import request from '@/utils/request'

export interface OverviewStats {
  file_count: number
  student_count: number
  class_count: number
  experiment_count: number
  avg_score: number
  weak_rate_percent: number
  fallback_to_state?: boolean
}

export interface StudentSummaryRow {
  student_id: string
  name: string
  class_name: string
  experiment_count: number
  avg_score: number
  weak_rate_percent: number
  weak_count: number
  total_score?: number
}

export interface ClassSummaryRow {
  class_name: string
  experiment_name: string
  source_type: string
  student_count: number
  avg_score: number
  weak_rate_percent: number
  min_score: number
  max_score: number
  median_score: number
}

export interface HybridSearchRow {
  id: number
  file_id: number
  line_no: number
  student_id: string
  name: string
  class_name: string
  experiment_name: string
  source_type: string
  final_score: number | null
  weak_count: number
  task_count: number
  row_text: string
  extra_cols?: Record<string, any>
  similarity: number
}

export interface PaginatedResp<T> {
  ok: boolean
  total: number
  data: T[]
  message?: string
  detail?: string
}

// =============== API 封装 ===============

/** 仪表盘大盘统计（pgvector） */
export const getOverview = (): Promise<{ ok: boolean; data: OverviewStats }> => {
  return request.get('/api/analytics/overview') as any
}

/** 学生聚合视图（班级×学生维度） */
export const listStudentSummary = (params: {
  class_name?: string
  student_id?: string
  name?: string
  min_weak_rate?: number
  max_avg_score?: number
  sort_by?: 'avg_score' | 'weak_rate_percent' | 'experiment_count' | 'weak_count'
  sort_desc?: boolean
  limit?: number
}): Promise<PaginatedResp<StudentSummaryRow>> => {
  return request.get('/api/analytics/student_summary', { params }) as any
}

/** 班级×实验聚合视图 */
export const listClassSummary = (params: {
  class_name?: string
  experiment_name?: string
  source_type?: string
  limit?: number
}): Promise<PaginatedResp<ClassSummaryRow>> => {
  return request.get('/api/analytics/class_summary', { params }) as any
}

/** RAG 混合检索（调试用，先 embedding query 再 WHERE + 余弦排序） */
export const hybridSearch = (body: {
  query: string
  top_k?: number
  student_id?: string
  name?: string
  class_name?: string
  experiment_name?: string
  source_type?: string
  min_score?: number
  max_score?: number
  vector_only?: boolean
}): Promise<PaginatedResp<HybridSearchRow>> => {
  return request.post('/api/analytics/hybrid_search', body) as any
}
