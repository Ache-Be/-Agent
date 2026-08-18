<template>
  <div class="dashboard-page">
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6" v-for="(card, i) in statCards" :key="i">
        <el-card shadow="hover" class="stat-card">
          <div class="card-left">
            <div class="card-label">{{ card.label }}</div>
            <div class="card-value" :style="{ color: card.color }">
              {{ card.value }} <span class="card-unit">{{ card.unit }}</span>
            </div>
            <div class="card-desc">{{ card.desc }}</div>
          </div>
          <div class="card-icon" :style="{ background: card.bgColor }">
            <el-icon :size="26" :color="card.color"><component :is="card.icon" /></el-icon>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="panel-row">
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header-title">
              <el-icon><Cpu /></el-icon>
              <span>系统状态</span>
            </div>
          </template>
          <el-empty v-if="!healthz?.has_analysis" description="暂无分析数据，请先上传数据文件" :image-size="110">
            <el-button type="primary" @click="$router.push('/upload')">
              <el-icon><Upload /></el-icon>立即上传
            </el-button>
          </el-empty>
          <div v-else class="state-preview">
            <el-descriptions :column="1" border size="default">
              <el-descriptions-item label="数据文件数">
                <b>{{ healthz?.files }}</b> 个
              </el-descriptions-item>
              <el-descriptions-item label="AI 对话会话数">
                <b>{{ healthz?.conversations }}</b> 个
              </el-descriptions-item>
              <el-descriptions-item label="学生分析状态">
                <el-tag :type="healthz?.has_analysis ? 'success' : 'warning'" effect="light">
                  {{ healthz?.has_analysis ? '已就绪 · 可直接查看学生画像' : '待上传数据' }}
                </el-tag>
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header-title">
              <el-icon><DataLine /></el-icon>
              <span>快速操作</span>
            </div>
          </template>
          <div class="quick-actions">
            <el-row :gutter="12">
              <el-col :span="12" style="margin-bottom:12px">
                <el-button type="primary" size="large" block @click="$router.push('/upload')">
                  <el-icon><Upload /></el-icon>上传数据文件
                </el-button>
              </el-col>
              <el-col :span="12" style="margin-bottom:12px">
                <el-button type="success" size="large" block @click="$router.push('/reports')">
                  <el-icon><Document /></el-icon>查看预警报告
                </el-button>
              </el-col>
              <el-col :span="12">
                <el-button type="warning" size="large" block @click="$router.push('/students')">
                  <el-icon><User /></el-icon>查看学生画像
                </el-button>
              </el-col>
              <el-col :span="12">
                <el-button type="danger" size="large" block @click="$router.push('/chat')">
                  <el-icon><ChatDotRound /></el-icon>AI 教学助手
                </el-button>
              </el-col>
            </el-row>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/store/app'
import { getHealthz } from '@/api/health'
import { Folder, ChatDotRound, DataLine, User, Upload, Document, Cpu } from '@element-plus/icons-vue'

const route = useRoute()
const appStore = useAppStore()

const healthz = ref<any>(null)

const statCards = computed(() => [
  {
    label: '已上传数据文件',
    value: healthz.value?.files ?? 0,
    unit: '个',
    desc: 'CSV / XLSX / DOCX',
    icon: Folder,
    color: '#1a73e8',
    bgColor: 'rgba(26,115,232,0.1)'
  },
  {
    label: 'AI 对话会话',
    value: healthz.value?.conversations ?? 0,
    unit: '个',
    desc: '累计历史对话数',
    icon: ChatDotRound,
    color: '#722ed1',
    bgColor: 'rgba(114,46,209,0.1)'
  },
  {
    label: '分析状态',
    value: healthz.value?.has_analysis ? '已就绪' : '待上传',
    unit: '',
    desc: '学生画像可用性',
    icon: DataLine,
    color: healthz.value?.has_analysis ? '#52c41a' : '#faad14',
    bgColor: healthz.value?.has_analysis ? 'rgba(82,196,26,0.1)' : 'rgba(250,173,20,0.1)'
  },
  {
    label: '学生总数',
    value: healthz.value?.total_students ?? 0,
    unit: '人',
    desc: '头歌/课堂报告识别到',
    icon: User,
    color: '#eb2f96',
    bgColor: 'rgba(235,47,150,0.1)'
  }
])

const fetchData = async () => {
  try {
    const data = await getHealthz()
    healthz.value = data as any
    appStore.healthzInfo = data as any
  } catch {
    healthz.value = { status: 'error', files: 0, conversations: 0, has_analysis: false, time: '-', total_students: 0 }
  }
}

onMounted(fetchData)
watch(() => route.fullPath, fetchData)
</script>

<style lang="scss" scoped>
.dashboard-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.stat-row .stat-card :deep(.el-card__body) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 20px;
}
.card-label { font-size: 13px; color: #909399; margin-bottom: 8px; }
.card-value { font-size: 24px; font-weight: 700; line-height: 1.2; .card-unit { font-size: 13px; font-weight: 400; opacity: 0.75; } }
.card-desc { font-size: 12px; color: #c0c4cc; margin-top: 6px; }
.card-icon {
  width: 50px; height: 50px; border-radius: 12px;
  display: flex; align-items: center; justify-content: center;
}

.panel-row { margin-top: 4px; }
.card-header-title { display: flex; align-items: center; gap: 8px; font-weight: 600; color: #303133; }
.state-preview { padding: 4px 0; }
.quick-actions { display: flex; flex-direction: column; }
.quick-actions :deep(.el-button) {
  justify-content: center;
  height: 46px;
  font-size: 14.5px;
  gap: 6px;
}
</style>
