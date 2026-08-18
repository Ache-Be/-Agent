<template>
  <el-card shadow="hover">
    <template #header>
      <div class="card-header-title">
        <el-icon><Upload /></el-icon>
        <span>数据文件上传</span>
        <el-tag size="small" effect="plain" type="info" style="margin-left:auto">
          支持 .csv / .xlsx / .docx
        </el-tag>
      </div>
    </template>

    <el-radio-group v-model="uploadMode" style="margin-bottom:16px">
      <el-radio-button value="merge">合并模式（追加到已有分析）</el-radio-button>
      <el-radio-button value="overwrite">覆盖模式（清空所有已上传文件与历史报告，重跑）</el-radio-button>
    </el-radio-group>

    <el-upload
      :action="'/api/upload'"
      :headers="{}"
      name="file"
      :data="{ mode: uploadMode }"
      multiple
      drag
      :auto-upload="false"
      :on-change="handleFileChange"
      :on-success="handleSuccess"
      :on-error="handleError"
      :before-upload="beforeUpload"
      :http-request="customUpload"
      ref="uploadRef"
      accept=".csv,.xlsx,.docx"
      :show-file-list="false"
    >
      <el-icon class="upload-icon"><Upload /></el-icon>
      <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择文件</em></div>
      <template #tip>
        <div class="el-upload__tip">
          支持头歌实验 CSV、MOOC 班级 CSV、随堂测验 XLSX、单元练习/课堂活动 XLSX、Word 题目 DOCX；同一文件内容不会重复导入。
        </div>
      </template>
    </el-upload>

    <div class="actions" style="margin-top:16px;display:flex;gap:12px;justify-content:flex-end;align-items:center">
      <span style="margin-right:auto;font-size:13px;color:#606266">
        已选择 <b style="color:#1a73e8">{{ fileList.length }}</b> 个文件
      </span>
      <el-button @click="clearFiles">清空列表</el-button>
      <el-button type="primary" :loading="uploading" @click="submitUpload">
        <el-icon><Upload /></el-icon>开始上传分析
      </el-button>
    </div>

    <div v-if="fileList.length" class="file-list-box">
      <el-table
        :data="fileList"
        size="small"
        stripe
        height="260"
        style="width:100%"
      >
        <el-table-column prop="name" label="文件名" min-width="320" show-overflow-tooltip />
        <el-table-column label="大小" width="100" align="right">
          <template #default="{ row }">{{ formatSize((row.raw || row).size) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="tagType(row.name)">{{ tagLabel(row.name) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="70" align="center">
          <template #default="{ $index }">
            <el-button link type="danger" size="small" @click="removeFile($index)">
              <el-icon><Delete /></el-icon>
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="lastResult" class="result-box">
      <el-alert v-if="lastResult.message" :title="lastResult.message" type="info" :closable="false" show-icon style="margin-bottom:10px" />
      <el-alert v-if="lastResult.skipped?.length" :title="`跳过 ${lastResult.skipped.length} 个重复文件`" type="warning" :closable="false" style="margin-bottom:10px">
        <div slot="default" style="max-height:120px;overflow:auto">
          <div v-for="(n, i) in lastResult.skipped" :key="'s'+i" style="font-size:12px;color:#909399">· {{ n }}</div>
        </div>
      </el-alert>
      <el-alert v-if="lastResult.rejected?.length" :title="`${lastResult.rejected.length} 个文件未通过校验`" type="error" :closable="false">
        <div slot="default" style="max-height:200px;overflow:auto">
          <div v-for="(n, i) in lastResult.rejected" :key="'r'+i" style="font-size:12px;line-height:1.7;color:#f56c6c">· {{ n }}</div>
        </div>
      </el-alert>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElUpload } from 'element-plus'
import { useRouter } from 'vue-router'
import axios from 'axios'

const router = useRouter()
const uploadRef = ref<InstanceType<typeof ElUpload>>()
const uploadMode = ref('merge')
const uploading = ref(false)
const fileList = ref<any[]>([])
const lastResult = ref<any>(null)

const beforeUpload = (file: any) => {
  const allowed = ['.csv', '.xlsx', '.docx']
  const ok = allowed.some(s => file.name.toLowerCase().endsWith(s))
  if (!ok) {
    ElMessage.error(`不支持的文件格式：${file.name}`)
  }
  return ok
}

const handleFileChange = (_file: any, list: any[]) => {
  fileList.value = list
  lastResult.value = null
}

const clearFiles = () => {
  uploadRef.value?.clearFiles()
  fileList.value = []
  lastResult.value = null
}

const removeFile = (idx: number) => {
  fileList.value.splice(idx, 1)
}

const formatSize = (bytes: number) => {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let n = bytes
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`
}

const tagType = (name: string) => {
  const s = name.toLowerCase()
  if (s.endsWith('.csv')) return 'primary'
  if (s.endsWith('.xlsx')) return 'success'
  if (s.endsWith('.docx')) return 'warning'
  return 'info'
}
const tagLabel = (name: string) => {
  const s = name.toLowerCase()
  if (s.endsWith('.csv')) return 'CSV'
  if (s.endsWith('.xlsx')) return 'XLSX'
  if (s.endsWith('.docx')) return 'DOCX'
  return 'FILE'
}

const customUpload = (options: any) => {
  const { action, data, file, filename, headers, onError, onProgress, onSuccess } = options
  const form = new FormData()
  Object.entries(data || {}).forEach(([k, v]) => form.append(k, v as any))
  form.append(filename || 'file', file)
  const source = axios.CancelToken.source()
  return axios.post(action, form, {
    headers: headers || {},
    cancelToken: source.token,
    timeout: 10 * 60 * 1000,
    onUploadProgress: (evt: any) => {
      if (onProgress && evt.total) onProgress({ percent: (evt.loaded / evt.total) * 100 })
    }
  }).then(resp => {
    const body: any = resp.data || {}
    lastResult.value = {
      message: body.message || '',
      rejected: body.rejected || [],
      skipped: body.skipped || []
    }
    if (body.ok || (body.results && body.results.length) || body.message) {
      onSuccess(body, file)
    } else {
      onError(new Error(body.message || '上传失败'), file, body)
    }
  }).catch(err => {
    const msg = err?.response?.data?.message || err?.response?.data?.detail || err?.message || '请求失败'
    const rejected: string[] = err?.response?.data?.rejected || []
    const skipped: string[] = err?.response?.data?.skipped || []
    lastResult.value = {
      message: msg,
      rejected: rejected.length ? rejected : [`${file.name}（${msg}）`],
      skipped
    }
    onError(err, file, err?.response?.data)
  })
}

const handleSuccess = () => {
  uploading.value = false
  const r = lastResult.value || {}
  const rejN = (r.rejected || []).length
  const skipN = (r.skipped || []).length
  if (rejN && !(r.results && r.results.length)) {
    ElMessage.error(`上传失败：${rejN} 个文件未通过校验，请查看下方列表`)
  } else {
    ElMessage.success(r.message || '上传分析完成')
    if (!rejN && !skipN) {
      router.push('/dashboard')
    }
  }
}

const handleError = (err: any) => {
  uploading.value = false
  const msg = err?.message || lastResult.value?.message || '上传失败'
  ElMessage.error(msg)
}

const submitUpload = async () => {
  if (!fileList.value.length) {
    ElMessage.warning('请先选择要上传的文件')
    return
  }
  lastResult.value = null
  uploading.value = true

  // 一次性批量提交所有文件（只发1个请求，overwrite模式不会重复清空18次）
  // el-upload 的 http-request 是单文件级回调，这里直接自己组装 FormData
  try {
    const form = new FormData()
    form.append('mode', uploadMode.value)
    // 每个文件都用同一个字段名 files[]（FastAPI List[UploadFile] 标准写法）
    // 同时也放一份在 files（纯数组 key 不带括号），兼容双写法
    // relative_paths[]：把 webkitRelativePath（文件夹拖拽时带随堂测验/校区软件4-5班/...层级）带上去，后端才能区分同名不同班级的xlsx
    for (const item of fileList.value) {
      const rawFile = (item.raw || item) as File & { webkitRelativePath?: string }
      if (!rawFile) continue
      form.append('files', rawFile)
      form.append('files[]', rawFile)
      form.append('file', rawFile)  // 后端也兼容单 file 字段
      // 相对路径（拖拽文件夹时会有 随堂测验/校区软件4-5班/xxx.xlsx，单文件选择时就是 ''）
      form.append('relative_paths', rawFile.webkitRelativePath || '')
    }

    const resp = await axios.post('/api/upload', form, {
      timeout: 10 * 60 * 1000,
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (evt: any) => {
        if (evt.total) {
          const pct = (evt.loaded / evt.total) * 100
        }
      }
    })
    const body: any = resp.data || {}
    lastResult.value = {
      message: body.message || '',
      rejected: body.rejected || [],
      skipped: body.skipped || []
    }
    uploading.value = false
    if (body.ok || (body.results && body.results.length) || body.message) {
      handleSuccess?.()
    } else {
      handleError?.(new Error(body.message || '上传失败'))
    }
  } catch (err: any) {
    uploading.value = false
    const msg = err?.response?.data?.message || err?.response?.data?.detail
      || (err?.response && `HTTP ${err.response.status} ${err.response.statusText}`)
      || err?.message || '请求失败'
    const rejected: string[] = err?.response?.data?.rejected || []
    const skipped: string[] = err?.response?.data?.skipped || []
    lastResult.value = {
      message: msg,
      rejected: rejected.length ? rejected : [`批量提交失败：${msg}`],
      skipped
    }
    handleError?.(err)
  }
}
</script>

<style lang="scss" scoped>
.card-header-title { display: flex; align-items: center; gap: 8px; font-weight: 600; color: #303133; }
.upload-icon { font-size: 56px; color: #1a73e8; margin-bottom: 12px; }
.el-upload__text { font-size: 14px; color: #606266; em { color: #1a73e8; font-style: normal; } }
.el-upload__tip { font-size: 12px; color: #909399; margin-top: 8px; line-height: 1.6; }
.result-box { margin-top: 18px; border-top: 1px dashed #ebeef5; padding-top: 14px; }
.file-list-box { margin-top: 14px; border: 1px solid #ebeef5; border-radius: 6px; overflow: hidden; }
</style>
