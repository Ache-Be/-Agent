<template>
  <div class="reports-page">
    <!-- 顶部 Tab + 统计 -->
    <el-row :gutter="12" class="top-row">
      <el-col :span="18">
        <el-card shadow="hover">
          <div class="card-header-title" style="margin-bottom:12px">
            <el-icon><Document /></el-icon>
            <span>预警报告中心</span>
            <el-button-group style="margin-left:auto">
              <el-button
                v-for="tab in tabs" :key="tab.key"
                :type="currentTab === tab.key ? 'primary' : 'default'"
                @click="currentTab = tab.key"
              >{{ tab.label }}</el-button>
            </el-button-group>
            <el-input
              v-model="keyword"
              size="small"
              placeholder="搜索姓名 / 学号 / 实验名"
              style="width:260px;margin-left:10px"
              clearable
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-button size="small" :icon="Refresh" @click="loadList" style="margin-left:8px">刷新</el-button>
          </div>

          <el-table
            :data="filteredReports"
            v-loading="loading"
            stripe
            height="520"
            empty-text="暂无报告文件"
            row-key="name"
          >
            <el-table-column label="#" type="index" width="60" />
            <el-table-column label="报告名称" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="report-name-cell">
                  <b>{{ row.display_name || row.name }}</b>
                  <div v-if="row.student_name || row.student_id" class="sub-name">
                    <template v-if="row.student_name">
                      <el-tag size="small" type="primary" effect="plain" style="margin-right:6px">
                        {{ row.student_name }}
                      </el-tag>
                    </template>
                    <template v-if="row.student_id">
                      <el-tag size="small" type="info" effect="plain">
                        {{ row.student_id }}
                      </el-tag>
                    </template>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="120">
              <template #default="{ row }">
                <el-tag v-if="row.category==='summary'" type="success" effect="light">综合汇总</el-tag>
                <el-tag v-else-if="row.category==='student'" type="warning" effect="light">学生个人</el-tag>
                <el-tag v-else-if="row.category==='single'" type="primary" effect="light">单文件报告</el-tag>
                <el-tag v-else-if="row.category==='question'" type="danger" effect="light">题目报告</el-tag>
                <el-tag v-else type="info" effect="plain">其他</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="110" align="right">
              <template #default="{ row }">{{ row.size_str }}</template>
            </el-table-column>
            <el-table-column label="时间" width="160">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.mtime }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="180" fixed="right" align="center">
              <template #default="{ row }">
                <el-button size="small" type="primary" link @click="preview(row)">
                  <el-icon><Document /></el-icon>预览
                </el-button>
                <el-button size="small" type="success" link @click="api.downloadReport(row.name)">
                  <el-icon><Download /></el-icon>下载
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="pagination">
            共 {{ total }} 份报告 · 当前显示 {{ filteredReports.length }} 条
          </div>
        </el-card>
      </el-col>

      <!-- 右侧：批量报告总览 + 导入工具 -->
      <el-col :span="6">
        <el-card shadow="hover" style="margin-bottom:12px">
          <div class="card-header-title">
            <el-icon><DataLine /></el-icon>
            <span>批量分析总览</span>
          </div>
          <div v-if="!overview.has_data" style="padding:16px 0;text-align:center;color:#909399;font-size:13px">
            暂无聚合分析数据，请先上传数据文件。
          </div>
          <div v-else class="overview">
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="成功/失败">
                <el-tag type="success">{{ overview.success_count }}</el-tag>
                <el-tag type="danger" style="margin-left:4px">{{ overview.fail_count }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="学生数">
                <b style="color:#1a73e8">{{ overview.agg?.total_students ?? 0 }}</b>
                <span style="color:#909399;margin-left:4px">人</span>
              </el-descriptions-item>
              <el-descriptions-item label="薄弱学生">
                <b style="color:#f56c6c">{{ overview.agg?.weak_student_count_live ?? 0 }}</b>
              </el-descriptions-item>
              <el-descriptions-item label="实验/测验/单元/课堂">
                {{ overview.agg?.experiment_count ?? 0 }} /
                {{ overview.agg?.quiz_count ?? 0 }} /
                {{ overview.agg?.unit_count ?? 0 }} /
                {{ overview.agg?.attendance_count ?? 0 }}
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </el-card>

        <el-card shadow="hover">
          <div class="card-header-title">
            <el-icon><Folder /></el-icon>
            <span>导入工具</span>
          </div>
          <div class="imports">
            <el-upload
              action=""
              :auto-upload="false"
              :show-file-list="false"
              :on-change="handleQuestionUpload"
              accept=".docx"
            >
              <el-button type="primary" style="width:100%;margin-bottom:8px">
                <el-icon><Upload /></el-icon>Word 题目分析
              </el-button>
            </el-upload>
            <el-upload
              action=""
              :auto-upload="false"
              :show-file-list="false"
              :on-change="handleKnowledgeUpload"
              accept=".pdf,.docx"
            >
              <el-button type="success" style="width:100%">
                <el-icon><Document /></el-icon>知识点文档导入
              </el-button>
            </el-upload>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 预览弹窗 -->
    <el-dialog v-model="previewVisible" :title="previewing.display_name || previewing.name" width="960px" top="6vh" destroy-on-close>
      <div style="max-height:70vh;overflow:auto">
        <div class="report-meta">
          <el-tag size="small">{{ previewing.size_str }}</el-tag>
          <el-tag size="small" type="info" effect="plain" style="margin-left:6px">{{ previewing.mtime }}</el-tag>
          <el-button size="small" style="float:right" type="primary" plain
            @click="api.downloadReport(previewing.name)">
            <el-icon><Download /></el-icon>下载
          </el-button>
        </div>
        <div class="report-content" v-html="renderMarkdown(previewContent)"></div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search, Download, Document, DataLine, Folder, Upload } from '@element-plus/icons-vue'
import * as api from '@/api/reports'
import type { ReportItem, ReportOverview } from '@/api/reports'
import { marked } from 'marked'

const tabs = [
  { label: '全部', key: 'all' },
  { label: '综合汇总', key: 'summary' },
  { label: '单文件报告', key: 'single' },
  { label: '学生个人', key: 'student' },
  { label: '题目报告', key: 'question' },
]

const loading = ref(false)
const currentTab = ref('all')
const keyword = ref('')
const reports = ref<ReportItem[]>([])
const total = ref(0)
const overview = ref<ReportOverview>({ has_data: false })

// 前端本地过滤：tab切换+关键字搜索都在内存里算，切 tab 不再发请求扫盘
const filteredReports = computed(() => {
  let list = reports.value
  if (currentTab.value !== 'all') list = list.filter(r => r.category === currentTab.value)
  if (keyword.value) {
    const k = keyword.value.trim().toLowerCase()
    if (k) {
      list = list.filter(r => {
        const hay = [
          r.display_name, r.name,
          (r as any).student_name, (r as any).student_id,
        ].filter(Boolean).join(' | ').toLowerCase()
        return hay.includes(k)
      })
    }
  }
  return list
})

const renderMarkdown = (c: string) => {
  if (!c) return ''
  try {
    return marked.parse(c) as string
  } catch {
    return c.replace(/\n/g, '<br>')
  }
}

const loadList = async () => {
  loading.value = true
  try {
    // 永远只拉 type=all（后端30s单entry缓存），前端本地按 type 过滤
    const data = await api.listReports('all')
    total.value = data.total
    reports.value = (data.items || []).map((it: any) => ({
      ...it,
      display_name: it.display_name || it.name,
    })) as ReportItem[]
  } finally {
    loading.value = false
  }
}

const loadOverview = async () => {
  try {
    overview.value = await api.getReportOverview()
  } catch {}
}

// 预览
const previewVisible = ref(false)
const previewing = ref<any>({ name: '', display_name: '', size_str: '', mtime: '' })
const previewContent = ref('')
const preview = async (row: ReportItem) => {
  previewing.value = row
  previewVisible.value = true
  previewContent.value = ''
  try {
    const data = await api.viewReportContent(row.name)
    previewContent.value = data.content
  } catch (e: any) {
    previewContent.value = `加载失败：${e?.message || '未知错误'}`
  }
}

// 工具上传
const handleQuestionUpload = async (file: any) => {
  try {
    ElMessage.info(`开始解析题目文档：${file.name}…`)
    const data = await api.analyzeQuestions(file.raw)
    ElMessage.success(
      `解析完成：共 ${data.total_questions} 题，`
      + `匹配知识点 ${data.matched_count}，未匹配 ${data.unmatched_count}。`
      + `报告已保存：${data.report_filename}`
    )
    loadList()
  } catch (e: any) {
    ElMessage.error(e?.message || '题目解析失败')
  }
}

const handleKnowledgeUpload = async (file: any) => {
  try {
    ElMessage.info(`开始导入知识点文档：${file.name}…`)
    const data = await api.importKnowledge(file.raw)
    ElMessage.success(data.message || '导入完成')
    loadList()
  } catch (e: any) {
    ElMessage.error(e?.message || '导入失败')
  }
}

// currentTab 不再 watch 触发请求 → 前端自己 computed 过滤
// 只有用户手动点「刷新」按钮才会重新请求

onMounted(async () => {
  await Promise.all([loadList(), loadOverview()])
})
</script>

<style lang="scss" scoped>
.reports-page { display:flex; flex-direction:column; gap:16px; }
.card-header-title { display: flex; align-items: center; gap: 8px; font-weight: 600; color: #303133; }
.pagination { padding-top: 10px; font-size: 12px; color: #909399; text-align: right; }
.report-name-cell { line-height: 1.35; b { font-weight: 600; color: #1f2937; } }
.sub-name { margin-top: 4px; }
.report-meta {
  padding: 8px 12px; margin-bottom: 12px;
  background: #f5f7fa; border-radius: 6px; font-size: 12px; color: #606266;
}
.report-content {
  font-size: 13.5px; line-height: 1.75;
  :deep(h1), :deep(h2), :deep(h3), :deep(h4) { margin: 14px 0 8px; color: #1f2937; }
  :deep(h1) { font-size: 20px; }
  :deep(h2) { font-size: 17px; }
  :deep(h3) { font-size: 15px; }
  :deep(p) { margin: 6px 0; }
  :deep(ul), :deep(ol) { padding-left: 24px; }
  :deep(table) { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 12.5px; }
  :deep(th), :deep(td) { border: 1px solid #e5e7eb; padding: 4px 8px; }
  :deep(th) { background: #f0f4f8; }
  :deep(code) { background: #f1f5f9; padding: 1px 6px; border-radius: 4px; font-family: monospace; }
  :deep(hr) { border: none; border-top: 1px dashed #e5e7eb; margin: 14px 0; }
}
.imports { padding: 6px 0 2px; display:flex; flex-direction: column; }
</style>
