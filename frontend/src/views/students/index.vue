<template>
  <div class="students-page">
    <!-- 顶部统计卡片 -->
    <el-row :gutter="12" style="margin-bottom:12px">
      <el-col :span="4" v-for="c in statCards" :key="c.label">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-left" :style="{ background: c.color }">
            <el-icon :size="22"><component :is="c.icon" /></el-icon>
          </div>
          <div class="stat-right">
            <div class="stat-value">{{ c.value }}</div>
            <div class="stat-label">{{ c.label }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="12" style="height:calc(100vh - 230px)">
      <!-- 左：学生列表（占 9 列 → 保持，但内部列宽重分配让薄弱率更突出） -->
      <el-col :span="9" style="height:100%">
        <el-card shadow="hover" class="list-card">
          <template #header>
            <div class="card-header-title">
              <el-icon><UserFilled /></el-icon>
              <span>学生列表</span>
              <el-tag v-if="stats.has_data" style="margin-left:6px" size="small" effect="plain" type="info">
                共 {{ stats.total }} 人 · 点行查看画像
              </el-tag>
            </div>
          </template>
          <div class="filters">
            <el-input v-model="keyword" size="small" placeholder="姓名/学号/班级/年份搜索" clearable style="width:200px" @keyup.enter="loadList(1)">
              <template #prefix><el-icon><Search /></el-icon></template>
              <template #append>
                <el-button @click="loadList(1)"><el-icon><Search /></el-icon></el-button>
              </template>
            </el-input>
            <el-checkbox v-model="weakOnly" size="small" @change="loadList(1)">仅看薄弱</el-checkbox>
            <el-select v-model="sort" size="small" style="width:135px" @change="loadList(1)">
              <el-option label="薄弱率 高→低" value="weakness_desc" />
              <el-option label="薄弱率 低→高" value="weakness_asc" />
              <el-option label="按姓名排序" value="name" />
            </el-select>
          </div>
          <el-table
            ref="listTableRef"
            :data="listItems"
            v-loading="listLoading"
            size="small"
            height="calc(100% - 110px)"
            highlight-current-row
            :current-row-key="currentKey"
            row-key="_key"
            @row-click="onRowClick"
            empty-text="暂无学生"
            stripe
          >
            <el-table-column label="#" type="index" width="46" />
            <el-table-column label="姓名" width="84">
              <template #default="{ row }">
                <div class="name-cell">
                  <b>{{ row.name }}</b>
                  <el-tag
                    size="small"
                    :type="row.weak_count ? (row.weakness_rate >= 0.5 ? 'danger' : 'warning') : 'success'"
                    effect="light"
                    class="level-tag"
                  >{{ weakLevel(row.weakness_rate) }}</el-tag>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="学号" width="112" prop="student_id" show-overflow-tooltip />
            <!-- 薄弱率列：加宽 + 百分比数字 + 进度条，保证一眼可见 -->
            <el-table-column label="薄弱率" min-width="180">
              <template #default="{ row }">
                <div class="weakness-cell">
                  <span class="weakness-num" :style="{color: progressColor(row.weakness_rate)}">
                    {{ (Math.round(row.weakness_rate * 1000) / 10).toFixed(1) }}%
                  </span>
                  <el-progress
                    :percentage="Math.round(row.weakness_rate * 100)"
                    :color="progressColor(row.weakness_rate)"
                    :stroke-width="10"
                    :show-text="false"
                    style="flex:1"
                  />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="薄弱点" width="86" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.weak_knowledge_count > 0" size="small" type="warning" effect="plain">
                  {{ row.weak_knowledge_count }} 个
                </el-tag>
                <el-tag v-else size="small" type="success" effect="plain">
                  0 个
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
          <div class="pagination-row">
            <el-pagination
              background
              size="small"
              layout="prev, pager, next"
              :current-page="page"
              :page-size="pageSize"
              :total="total"
              @current-change="loadList"
            />
          </div>
        </el-card>
      </el-col>

      <!-- 右：学生画像详情（占 15 列） -->
      <el-col :span="15" style="height:100%">
        <el-card shadow="hover" class="detail-card" v-loading="detailLoading">
          <template #header>
            <div class="card-header-title">
              <el-icon><UserFilled /></el-icon>
              <span v-if="current">
                {{ current.name }}（{{ current.student_id }}）
                <el-tag size="small" style="margin-left:10px" :type="levelType(current.level)">{{ current.level }}</el-tag>
              </span>
              <span v-else>学生画像详情</span>
              <el-tag v-if="stats.has_data" size="small" effect="plain" type="info" style="margin-left:auto">
                覆盖 {{ stats.experiment_count }} 实验 / {{ stats.quiz_count }} 测验
              </el-tag>
            </div>
          </template>

          <el-empty v-if="!current" description="请从左侧点击学生行查看画像" :image-size="100" />

          <div v-else class="detail-body">
            <!-- 画像核心数据（去掉平均分，薄弱率更突出） -->
            <el-row :gutter="12">
              <el-col :span="5">
                <div class="portrait-hero" :class="'level-' + current.level">
                  <div class="hero-avatar">{{ current.name.slice(-2) }}</div>
                  <div class="hero-name">{{ current.name }}</div>
                  <div class="hero-sid">{{ current.student_id }}</div>
                </div>
              </el-col>
              <el-col :span="19">
                <el-descriptions :column="3" border size="small">
                  <el-descriptions-item label="画像等级">
                    <el-tag :type="levelType(current.level)" effect="dark">{{ current.level }}</el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="薄弱率" :span="2">
                    <div class="big-weakness">
                      <span class="bw-num" :style="{color: progressColor(current.weakness_rate)}">
                        {{ (Math.round(current.weakness_rate * 1000) / 10).toFixed(1) }}%
                      </span>
                      <el-progress
                        :percentage="Math.round(current.weakness_rate * 100)"
                        :color="progressColor(current.weakness_rate)"
                        :stroke-width="12"
                        :show-text="false"
                        style="flex:1;max-width:340px;margin-left:14px"
                      />
                    </div>
                  </el-descriptions-item>
                  <el-descriptions-item label="薄弱知识点">{{ current.weak_knowledge_count }} 个</el-descriptions-item>
                  <el-descriptions-item label="薄弱子任务">{{ current.weak_subtask_count }} 个</el-descriptions-item>
                  <el-descriptions-item label="参与实验">{{ current.experiment_count }} 次</el-descriptions-item>
                </el-descriptions>
              </el-col>
            </el-row>

            <el-tabs v-model="tab" style="margin-top:12px">
              <!-- Tab1: 薄弱点分析 -->
              <el-tab-pane label="🎯 薄弱点分析" name="weak">
                <el-row :gutter="12">
                  <el-col :span="14">
                    <div class="section-title">按知识单元</div>
                    <div class="unit-list">
                      <div v-if="!current.weak_units.length" class="hint-success">✅ 暂无薄弱知识点，掌握良好</div>
                      <div v-for="u in current.weak_units" :key="u.unit" class="unit-row">
                        <div class="unit-head">
                          <b>{{ u.unit }}</b>
                          <el-tag size="small" type="danger" effect="plain">{{ u.count }} 个薄弱点</el-tag>
                        </div>
                        <div class="unit-kws">
                          <el-tag v-for="k in u.knowledge" :key="k" size="small" effect="light" type="warning" style="margin:2px 4px 2px 0">
                            {{ k }}
                          </el-tag>
                        </div>
                      </div>
                    </div>
                  </el-col>
                  <el-col :span="10">
                    <div class="section-title">薄弱学生 Top 单元（年级对比）</div>
                    <div class="unit-rank">
                      <div v-for="(u, i) in stats.top_units.slice(0, 8)" :key="u.unit" class="ur-row">
                        <span class="ur-idx" :class="'i' + (i + 1)">{{ i + 1 }}</span>
                        <span class="ur-unit" :title="u.unit">{{ u.unit }}</span>
                        <el-progress :percentage="Math.min(100, Math.round(u.weak_student_count / (stats.total || 1) * 100))" :stroke-width="10" :show-text="false" style="width:100px" />
                        <span class="ur-count" style="width:52px;text-align:right">{{ u.weak_student_count }}人</span>
                      </div>
                    </div>
                  </el-col>
                </el-row>
              </el-tab-pane>

              <!-- Tab2: 各实验成绩 -->
              <el-tab-pane label="📊 各实验表现" name="exp">
                <el-table :data="current.experiments" size="small" stripe empty-text="暂无实验数据">
                  <el-table-column label="#" type="index" width="50" />
                  <el-table-column label="实验名称" prop="name" min-width="220" show-overflow-tooltip />
                  <el-table-column label="成绩" width="100" align="right">
                    <template #default="{ row }">
                      <b :style="{color: row.score >= 80 ? '#67C23A' : (row.score >= 60 ? '#E6A23C' : '#F56C6C')}">{{ row.score }}</b>
                    </template>
                  </el-table-column>
                  <el-table-column label="薄弱子任务" width="110" align="right" prop="weak_count" />
                  <el-table-column label="实验内薄弱率" width="170">
                    <template #default="{ row }">
                      <el-progress :percentage="Math.round(row.weakness_rate * 100)" :stroke-width="8"
                        :color="progressColor(row.weakness_rate)" />
                    </template>
                  </el-table-column>
                  <el-table-column label="提交状态" width="100">
                    <template #default="{ row }">
                      <el-tag size="small" :type="row.submitted ? 'success' : 'info'" effect="plain">
                        {{ row.submitted ? '已提交' : '未提交' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </el-tab-pane>

              <!-- Tab3: 建议 -->
              <el-tab-pane label="💡 学习建议" name="adv">
                <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
                  title="以下建议由系统基于薄弱点自动生成，老师可结合学生实际情况参考使用" />
                <el-steps direction="vertical" :active="current.suggestions.length" finish-status="success">
                  <el-step v-for="(s, i) in current.suggestions" :key="i" :title="'建议 ' + (i + 1)" :description="s" />
                </el-steps>
                <div v-if="current.prediction" style="margin-top:14px">
                  <div class="section-title">成绩预测</div>
                  <el-card shadow="never" class="pred-card"><pre style="margin:0;white-space:pre-wrap;font-size:13px">{{ current.prediction }}</pre></el-card>
                </div>
              </el-tab-pane>

              <!-- Tab4: 完整报告 -->
              <el-tab-pane label="📄 完整报告" name="rep">
                <div class="report-actions">
                  <el-button type="primary" plain size="small" @click="downloadCurrentReport">
                    <el-icon><Download /></el-icon>下载报告
                  </el-button>
                </div>
                <div class="report-content" v-if="current.report_content"><pre>{{ current.report_content }}</pre></div>
                <el-empty v-else description="暂无生成好的报告文件" :image-size="90" />
              </el-tab-pane>
            </el-tabs>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import * as $students from '@/api/students'
import * as $reports from '@/api/reports'
import type { StudentStats, StudentListItem, StudentDetail } from '@/api/students'
import { ElMessage } from 'element-plus'
import {
  Search, UserFilled, DataLine, Download,
} from '@element-plus/icons-vue'

// ====== 统计 ======
const stats = reactive<StudentStats>({
  has_data: false,
  total: 0, weak_count: 0, healthy_count: 0, avg_weakness_rate: 0,
  segmentation: { rate_0: 0, rate_0_20: 0, rate_20_40: 0, rate_40_60: 0, rate_60_100: 0 },
  top_units: [],
  experiment_count: 0, quiz_count: 0, unit_count: 0, attendance_count: 0,
})

const statCards = computed(() => ([
  { label: '学生总数', value: stats.total, icon: UserFilled, color: 'linear-gradient(135deg,#1a73e8,#4285f4)' },
  { label: '薄弱学生', value: stats.weak_count, icon: DataLine, color: 'linear-gradient(135deg,#f56c6c,#ff8a80)' },
  { label: '健康掌握', value: stats.healthy_count, icon: DataLine, color: 'linear-gradient(135deg,#67c23a,#95d475)' },
  { label: '平均薄弱率', value: Math.round(stats.avg_weakness_rate * 100) + '%', icon: DataLine, color: 'linear-gradient(135deg,#e6a23c,#f5c16c)' },
]))

// ====== 列表 ======
const listTableRef = ref()
const keyword = ref('')
const weakOnly = ref(false)
const sort = ref('weakness_desc')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const listItems = ref<StudentListItem[]>([])
const listLoading = ref(false)

const loadStats = async () => {
  try {
    Object.assign(stats, await $students.getStudentStats())
  } catch {}
}

const loadList = async (p?: number) => {
  if (typeof p === 'number') page.value = p
  listLoading.value = true
  try {
    const data = await $students.listStudents({
      keyword: keyword.value,
      weak_only: weakOnly.value,
      sort: sort.value,
      page: page.value,
      page_size: pageSize.value,
    })
    total.value = data.total || 0
    listItems.value = data.items || []
  } catch (e: any) {
    ElMessage.error(e?.message || '列表加载失败')
  } finally {
    listLoading.value = false
  }
}

// ====== 详情 ======
const currentKey = ref('')
const current = ref<StudentDetail | null>(null)
const detailLoading = ref(false)
const tab = ref('weak')

const selectStudent = async (key: string) => {
  if (!key) return
  currentKey.value = key
  detailLoading.value = true
  tab.value = 'weak'
  try {
    current.value = await $students.getStudentDetail(key)
  } catch (e: any) {
    ElMessage.error(e?.message || '画像加载失败')
    current.value = null
  } finally {
    detailLoading.value = false
  }
}

const onRowClick = (row: StudentListItem) => {
  if (row && row._key && currentKey.value !== row._key) {
    selectStudent(row._key)
  }
}

const downloadCurrentReport = () => {
  if (current.value?.report_filename) {
    $reports.downloadReport(current.value.report_filename)
  } else {
    ElMessage.warning('当前学生暂无生成好的报告文件')
  }
}

watch(listItems, (arr) => {
  if (!currentKey.value && arr.length) {
    selectStudent(arr[0]._key)
  }
})

// ====== 工具函数 ======
const progressColor = (r: number) =>
  r < 0.05 ? '#67C23A' : r < 0.15 ? '#909399' : r < 0.3 ? '#E6A23C' : r < 0.5 ? '#F56C6C' : '#C0392B'

const weakLevel = (r: number) =>
  r < 0.05 ? '优秀' : r < 0.15 ? '良好' : r < 0.3 ? '中等' : r < 0.5 ? '薄弱' : '重点预警'

const levelType = (l: string) =>
  (l === '优秀' ? 'success' : (l === '良好' ? '' : (l === '中等' ? 'warning' : (l === '薄弱' ? 'danger' : 'danger')))) as any

onMounted(async () => {
  await Promise.all([loadStats(), loadList(1)])
})
</script>

<style lang="scss" scoped>
.students-page { display: flex; flex-direction: column; height: 100%; }

.stat-card :deep(.el-card__body) { display:flex; gap:12px; align-items:center; padding:14px 16px; }
.stat-left { width:46px; height:46px; border-radius:10px; color:#fff; display:flex; align-items:center; justify-content:center; box-shadow:0 4px 12px rgba(0,0,0,0.08); }
.stat-right { flex:1; }
.stat-value { font-size:22px; font-weight:700; color:#303133; line-height:1.1; }
.stat-label { font-size:12px; color:#909399; margin-top:4px; }

.list-card { height:100%; display:flex; flex-direction:column; }
.list-card :deep(.el-card__body) { flex:1; display:flex; flex-direction:column; padding:14px; overflow:hidden; }
.card-header-title { display: flex; align-items: center; gap: 8px; font-weight: 600; color: #303133; }
.filters { display:flex; gap:8px; align-items:center; margin-bottom:10px; flex-wrap:wrap; }
.pagination-row { padding-top:8px; display:flex; justify-content:center; flex-shrink:0; }

.name-cell { display:flex; flex-direction:column; gap:2px; align-items:flex-start; }
.level-tag { margin-top:2px !important; }
.weakness-cell { display:flex; align-items:center; gap:8px; }
.weakness-num { font-weight:700; font-size:14px; min-width:52px; }

.detail-card { height:100%; }
.detail-card :deep(.el-card__body) { height:calc(100% - 56px); overflow:auto; padding:14px 16px; }
.detail-body { padding: 0; }

.portrait-hero {
  padding: 14px 10px; border-radius: 10px; display:flex; flex-direction:column; align-items:center; justify-content:center;
  background:#fafcff; color:#303133; border:1px solid #ebeef5;
  &.level-优秀 { background:linear-gradient(135deg,#f0faef,#e7f9e3); }
  &.level-良好 { background:linear-gradient(135deg,#eef6ff,#e3f0ff); }
  &.level-中等 { background:linear-gradient(135deg,#fff8e6,#fff1cf); }
  &.level-薄弱 { background:linear-gradient(135deg,#ffecec,#ffd8d8); }
  &.level-重点预警 { background:linear-gradient(135deg,#ffe0dd,#ffbfb8); }
}
.hero-avatar {
  width:56px; height:56px; border-radius:50%;
  background:rgba(255,255,255,0.85); border:2px solid rgba(255,255,255,0.9);
  display:flex; align-items:center; justify-content:center;
  font-weight:700; font-size:18px; margin-bottom:6px;
  box-shadow:0 2px 8px rgba(0,0,0,0.08);
}
.hero-name { font-size:17px; font-weight:700; }
.hero-sid { font-size:12px; color:#606266; margin-top:2px; }

.big-weakness { display:flex; align-items:center; }
.bw-num { font-size:22px; font-weight:800; letter-spacing:0.3px; }

.section-title { font-size:13px; font-weight:600; color:#606266; margin:6px 0 8px; display:flex; align-items:center; gap:6px; }
.section-title::before { content:""; width:3px; height:14px; background:#1a73e8; border-radius:2px; }

.unit-list { display:flex; flex-direction:column; gap:8px; max-height:440px; overflow:auto; padding-right:6px; }
.unit-row { border:1px solid #ebeef5; border-radius:8px; padding:8px 10px; background:#fff; }
.unit-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }
.unit-kws { display:flex; flex-wrap:wrap; }
.hint-success { padding:18px; text-align:center; color:#67C23A; background:#f0faef; border-radius:8px; }

.unit-rank { display:flex; flex-direction:column; gap:7px; max-height:440px; overflow:auto; padding-right:4px; }
.ur-row { display:flex; align-items:center; gap:8px; font-size:12.5px; }
.ur-idx { width:22px; height:22px; border-radius:50%; background:#f0f2f5; display:flex; align-items:center; justify-content:center; color:#606266; font-weight:600; flex-shrink:0; font-size:12px;
  &.i1 { background:#ff7a73; color:#fff; }
  &.i2 { background:#ffa940; color:#fff; }
  &.i3 { background:#ffc53d; color:#fff; }
}
.ur-unit { flex:1; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }

.report-actions { margin-bottom:8px; display:flex; justify-content:flex-end; }
.report-content {
  border:1px solid #ebeef5; border-radius:8px; padding:12px 14px; background:#fff;
  max-height:480px; overflow:auto;
  pre { font-family: Consolas, 'Courier New', monospace; font-size:12.5px; line-height:1.6; margin:0; white-space:pre-wrap; color:#303133; }
}
.pred-card { background:#fffbeb; border:1px dashed #f5c16c; pre { font-family:inherit; color:#606266; } }
</style>
