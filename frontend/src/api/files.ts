import request from '@/utils/request'

export interface DataFile {
  name: string
  size: number
  size_str: string
  mtime: string
}

export const listDataFiles = (): Promise<{ files: DataFile[] }> => {
  return request.get('/api/data_files') as any
}

export const deleteDataFile = (name: string) => {
  return request.delete('/api/data_files', { data: { name } })
}

/**
 * 批量删除数据文件
 */
export const deleteBatchDataFiles = (names: string[]) => {
  return request.post('/api/data_files/batch-delete', { names })
}

/**
 * 清空所有数据文件
 */
export const deleteAllDataFiles = () => {
  return request.delete('/api/data_files/all')
}
