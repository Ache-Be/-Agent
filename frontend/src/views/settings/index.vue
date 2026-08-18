<template>
  <el-card shadow="hover">
    <template #header>
      <div class="card-header-title">
        <el-icon><Setting /></el-icon>
        <span>系统设置</span>
        <el-tag v-if="configStatus" size="small" effect="plain" :type="configStatusType" style="margin-left:8px">
          {{ configStatus }}
        </el-tag>
      </div>
    </template>

    <el-form :model="form" label-width="170px" style="max-width:760px">
      <el-form-item label="DeepSeek API Key">
        <el-input
          v-model="form.apiKey"
          type="password"
          show-password
          placeholder="sk-..."
          style="width:100%"
          autocomplete="off"
        />
        <div style="font-size:12px;color:#909399;margin-top:4px">
          配置后即可使用 AI 教学助手；Key 仅保存在服务器本地 <code>server/config/settings.json</code>，不会外传。
          当前状态：<b :style="{ color: form.apiKey ? '#67C23A' : '#F56C6C' }">{{ form.apiKey ? '✅ 已配置' : '⚠️ 未配置' }}</b>
        </div>
      </el-form-item>

      <el-divider content-position="left">分析阈值（保存后立即生效）</el-divider>

      <el-form-item label="薄弱得分率阈值"
        :tip="'低于该百分比的子任务/题目会被判定为薄弱，直接影响学生画像和 AI 助手判断'">
        <el-slider v-model="form.weakThreshold" :min="30" :max="95" style="width:440px" />
        <span style="margin-left:12px;color:#1a73e8;font-weight:600">{{ form.weakThreshold }}%</span>
      </el-form-item>

      <el-form-item label="低分线阈值"
        :tip="'头歌实验总分低于此分数判定为低分，纳入预警名单'">
        <el-slider v-model="form.lowScoreLine" :min="20" :max="90" style="width:440px" />
        <span style="margin-left:12px;color:#f56c6c;font-weight:600">{{ form.lowScoreLine }} 分</span>
      </el-form-item>

      <el-form-item label="查答案率关注线"
        :tip="'子任务查看答案次数占比超过此值时在报告中提示关注'">
        <el-slider v-model="form.answerLookupLine" :min="1" :max="80" style="width:440px" />
        <span style="margin-left:12px;color:#e6a23c;font-weight:600">{{ form.answerLookupLine }}%</span>
      </el-form-item>

      <el-alert
        title="保存后会自动重新计算所有学生的薄弱判断、画像等级、聚合报告，下次打开页面即时生效"
        type="warning"
        :closable="false"
        show-icon
        style="margin:0 0 16px 170px;max-width:560px"
      />

      <el-form-item>
        <el-button type="primary" @click="handleSave" :loading="saving">
          <el-icon><Check /></el-icon>保存设置
        </el-button>
        <el-button @click="handleReset">恢复默认值</el-button>
        <el-button @click="$router.push('/dashboard')">返回仪表盘</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Setting, Check } from '@element-plus/icons-vue'
import request from '@/utils/request'

const saving = ref(false)
const loaded = ref(false)
const configStatus = ref('')
const configStatusType = ref<'success' | 'warning' | 'info'>('info')

const form = reactive({
  apiKey: '',
  weakThreshold: 70,
  lowScoreLine: 60,
  answerLookupLine: 30,
})

const loadCurrent = async () => {
  try {
    const r: any = await request.get('/api/config')
    form.apiKey = r.api_configured ? '*****已配置*****' : ''
    form.weakThreshold = Math.round((r.weak_threshold ?? 0.7) * 100)
    form.lowScoreLine = Math.round(r.low_score_line ?? 60)
    form.answerLookupLine = Math.round((r.view_answer_alert_rate ?? 0.3) * 100)
    loaded.value = true
    configStatus.value = '已读取当前服务端配置'
    configStatusType.value = 'success'
  } catch (e: any) {
    configStatus.value = '读取失败（使用默认值）'
    configStatusType.value = 'warning'
    ElMessage.warning(e?.message || '读取配置失败，将使用默认值')
  }
}

const handleReset = () => {
  form.apiKey = ''
  form.weakThreshold = 70
  form.lowScoreLine = 60
  form.answerLookupLine = 30
  ElMessage.info('已恢复为默认值（需点击「保存设置」才会真正写入）')
}

const handleSave = async () => {
  // 如果 API Key 是占位字符串（没改），就不要真的把 ***** 发送给后端，让后端保留原来的 key
  const reallySendKey = (form.apiKey && !form.apiKey.startsWith('*****已配置'))
    ? form.apiKey
    : null

  saving.value = true
  configStatus.value = '保存中…'
  configStatusType.value = 'info'
  try {
    const resp: any = await request.post('/api/config', {
      api_key: reallySendKey,
      weak_threshold: form.weakThreshold / 100,
      low_score_line: form.lowScoreLine,
      view_answer_alert_rate: form.answerLookupLine / 100,
    })
    if (resp.ok) {
      ElMessage.success(resp.msg || '保存成功')
      const lines = (resp.msg || '').split('；')
      if (resp.analysis_rebuilt || lines.some((l: string) => String(l).includes('重新计算'))) {
        await ElMessageBox.alert(
          `${resp.msg}\n\n学生聚合、画像等级、报告已按新阈值重新计算完毕，现在返回并刷新任意页面即可看到最新结果。`,
          '保存成功',
          { confirmButtonText: '知道了', type: 'success' as const }
        )
      }
      configStatus.value = '保存成功'
      configStatusType.value = 'success'
      // 成功后：把 API Key 显示占位符回去
      if (reallySendKey) form.apiKey = '*****已配置*****'
    } else {
      ElMessage.error(resp.msg || '保存失败')
      configStatus.value = '保存失败'
      configStatusType.value = 'warning'
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
    configStatus.value = '保存失败'
    configStatusType.value = 'warning'
  } finally {
    saving.value = false
  }
}

onMounted(loadCurrent)
</script>

<style lang="scss" scoped>
.card-header-title { display: flex; align-items: center; gap: 8px; font-weight: 600; color: #303133; }
code { background:#f4f4f5; color:#d9001b; padding:1px 6px; border-radius:3px; font-size:12px; }
</style>
