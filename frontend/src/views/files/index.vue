<template>
  <el-card shadow="hover">
    <template #header>
      <div class="card-header-title">
        <el-icon><Folder /></el-icon>
        <span>已上传数据文件</span>
        <el-input
          v-model="keyword"
          size="small"
          placeholder="搜索文件名..."
          style="width:200px;margin-left:auto"
          clearable
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button 
          size="small" 
          type="danger" 
          plain 
          :disabled="!selectedFiles.length" 
          :icon="Delete" 
          @click="handleBatchDelete"
        >
          批量删除 ({{ selectedFiles.length }})
        </el-button>
        <el-button 
          size="small" 
          type="danger" 
          :disabled="!files.length" 
          :icon="Delete" 
          @click="handleDeleteAll"
        >
          全部清空
        </el-button>
        <el-button size="small" :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </template>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px">
      <template #title>
        共 {{ filteredFiles.length }} 个文件，总大小 {{ totalSizeStr }}。删除文件将同步清理关联的分析报告与学生结果。
      </template>
    </el-alert>

    <el-table 
      :data="filteredFiles" 
      v-loading="loading" 
      stripe 
      empty-text="暂无上传的文件"
      @selection-change="handleSelectionChange"
    >
      <el-table-column type="selection" width="50" align="center" />
      <el-table-column prop="name" label="文件名" min-width="260" show-overflow-tooltip>
        <template #default="{ row }">
          <el-icon style="vertical-align:-2px;margin-right:6px;color:#1a73e8">
            <component :is="iconOf(row.name)" />
          </el-icon>
          {{ row.name }}
        </template>
      </el-table-column>
      <el-table-column label="大小" width="120" align="right">
        <template #default="{ row }">{{ row.size_str }}</template>
      </el-table-column>
      <el-table-column label="上传时间" width="180">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ row.mtime }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right" align="center">
        <template #default="{ row }">
          <el-button type="danger" size="small" link @click="handleDelete(row.name)">
            <el-icon><Delete /></el-icon>删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listDataFiles, deleteDataFile, deleteBatchDataFiles, deleteAllDataFiles, type DataFile } from '@/api/files'
import { Refresh, Delete, Search, Folder } from '@element-plus/icons-vue'

const loading = ref(false)
const keyword = ref('')
const files = ref<DataFile[]>([])
const selectedFiles = ref<DataFile[]>([])

const filteredFiles = computed(() => {
  if (!keyword.value) return files.value
  const k = keyword.value.toLowerCase()
  return files.value.filter(f => f.name.toLowerCase().includes(k))
})

const totalSizeStr = computed(() => {
  const bytes = files.value.reduce((s, f) => s + f.size, 0)
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return bytes + ' B'
})

const iconOf = (name: string) => {
  if (name.endsWith('.csv')) return 'Cpu'
  if (name.endsWith('.xlsx')) return 'Grid'
  if (name.endsWith('.docx')) return 'Document'
  return 'Document'
}

const loadData = async () => {
  loading.value = true
  try {
    const data = await listDataFiles()
    files.value = data.files || []
  } finally {
    loading.value = false
  }
}

const handleSelectionChange = (val: DataFile[]) => {
  selectedFiles.value = val
}

const handleDelete = async (name: string) => {
  try {
    await ElMessageBox.confirm(
      `确认删除文件「${name}」？关联的分析结果与学生报告也会同步清理。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
    await deleteDataFile(name)
    ElMessage.success('已删除')
    loadData()
  } catch {}
}

const handleBatchDelete = async () => {
  if (!selectedFiles.value.length) return
  const names = selectedFiles.value.map(f => f.name)
  try {
    await ElMessageBox.confirm(
      `确认批量删除选中的 ${names.length} 个文件？关联的分析结果也会同步清理。`,
      '批量删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
    await deleteBatchDataFiles(names)
    ElMessage.success('批量删除成功')
    loadData()
  } catch {}
}

const handleDeleteAll = async () => {
  try {
    await ElMessageBox.confirm(
      '确认清空所有已上传的数据文件？这将重置系统所有分析状态及报告，操作不可恢复！',
      '全部清空确认',
      { type: 'warning', confirmButtonText: '确认清空', cancelButtonText: '取消' }
    )
    await deleteAllDataFiles()
    ElMessage.success('已清空所有数据')
    loadData()
  } catch {}
}

onMounted(loadData)
</script>

<style lang="scss" scoped>
.card-header-title { display: flex; align-items: center; gap: 8px; font-weight: 600; color: #303133; }

// 优化复选框颜色，使其更清晰可见
:deep(.el-checkbox__inner) {
  border-color: #dcdfe6; // 默认边框深一点
  background-color: #fff;
  transition: all 0.2s;
  
  &:hover {
    border-color: #409eff;
  }
}

:deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background-color: #409eff;
  border-color: #409eff;
}

:deep(.el-table__header .el-checkbox__inner) {
  border-color: #c0c4cc; // 表头复选框再深一点
}
</style>
