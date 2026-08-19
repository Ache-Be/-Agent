<template>
  <div class="chat-page">
    <el-row :gutter="12" style="height:100%">
      <el-col :span="6" style="height:100%">
        <el-card shadow="hover" class="conv-card" v-loading="convLoading">
          <template #header>
            <div class="card-header-title">
              <el-icon><ChatDotRound /></el-icon>
              <span>对话列表</span>
              <el-button size="small" type="primary" style="margin-left:auto" @click="createConv">
                <el-icon style="font-weight:700"><Refresh /></el-icon>新建
              </el-button>
            </div>
          </template>
          <el-input v-model="convSearch" size="small" placeholder="搜索对话标题..." clearable style="margin-bottom:10px">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <div class="conv-list">
            <div
              v-for="c in filteredConvs"
              :key="c.id"
              class="conv-item"
              :class="{ active: currentConvId === c.id }"
              @click="selectConv(c.id)"
            >
              <div class="conv-title">
                <span :title="c.title">{{ c.title || '新对话' }}</span>
                <span
                  class="more-btn"
                  @click.stop="openConvMenu($event, c)"
                  title="更多操作"
                >⋯</span>
              </div>
              <div class="conv-meta">
                <span>{{ c.created_at }}</span>
                <span>{{ c.msg_count }} 条</span>
              </div>
            </div>
            <el-empty v-if="!filteredConvs.length" description="暂无对话" :image-size="80" />
          </div>
        </el-card>
      </el-col>

      <el-col :span="18" style="height:100%">
        <el-card shadow="hover" class="chat-card">
          <template #header>
            <div class="card-header-title">
              <span style="display:inline-flex;align-items:center;gap:8px">
                <el-icon><Cpu /></el-icon>
                <span class="chat-title">{{ currentConv?.title || 'AI 教学助手' }}</span>
              </span>
            </div>
          </template>

          <div class="chat-body" ref="chatBodyRef">
            <el-empty v-if="!messages.length && !streaming"
              description="上传数据后，可向我提问关于学生学习情况的问题" :image-size="120" />
            <div v-else class="msg-list">
              <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
                <el-avatar :size="32" :icon="m.role === 'user' ? UserFilled : Cpu" />
                <div class="msg-bubble">
                  <div class="markdown-body" v-html="renderMarkdown(m.content)"></div>
                  <div v-if="m.doc" class="doc-download">
                    <el-button type="primary" size="small" :icon="Download" @click="downloadDoc(m.doc.url_path, m.doc.filename)">
                      下载 Word 报告
                    </el-button>
                  </div>
                </div>
              </div>
              <div v-if="streaming || assistantDraft" class="msg assistant">
                <el-avatar :size="32" :icon="Cpu" />
                <div class="msg-bubble draft">
                  <template v-if="streaming">
                    <span class="markdown-body" v-html="renderMarkdown(assistantDraft)"></span>
                    <span class="cursor">|</span>
                  </template>
                  <template v-else>
                    <span class="markdown-body" v-html="renderMarkdown(assistantDraft)"></span>
                  </template>
                </div>
              </div>
            </div>
          </div>

          <div class="chat-input">
            <el-input
              v-model="input"
              type="textarea"
              :rows="3"
              resize="none"
              placeholder="输入问题，Enter 发送，Shift+Enter 换行"
              @keydown.enter.exact.prevent="sendMessage"
              :disabled="streaming || !currentConvId"
            />
            <div class="input-actions">
              <span style="color:#909399;font-size:12px">
                {{ currentConvId ? 'SSE 流式打印（按段输出）' : '请先选择或新建对话' }}
              </span>
              <el-button type="danger" plain size="small" :disabled="!streaming" @click="abortStream">中断输出</el-button>
              <el-button type="primary" :loading="streaming" :disabled="!input.trim() || !currentConvId" @click="sendMessage">
                {{ streaming ? '输出中…' : '发送' }}
              </el-button>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox, ElMenu, ElMenuItem } from 'element-plus'
import {
  listConversations,
  getConversation,
  createConversation,
  updateConversation,
  deleteConversation,
  type Conversation,
} from '@/api/chat'
import { UserFilled, Cpu, Download } from '@element-plus/icons-vue'
import { marked } from 'marked'

const convLoading = ref(false)
const convSearch = ref('')
const conversations = ref<Conversation[]>([])
const currentConvId = ref('')
const messages = ref<any[]>([])
const input = ref('')
const streaming = ref(false)
const assistantDraft = ref('')
const pendingDoc = ref<any>(null)  // 报告类回复：后端发 doc 事件后暂存，done 时挂到消息上
const chatBodyRef = ref<HTMLElement | null>(null)
let sseClient: EventSource | null = null
let streamAbortController: AbortController | null = null

const downloadDoc = (urlPath?: string, filename?: string) => {
  if (!urlPath) {
    ElMessage.warning('报告文件不存在，可能已被清理')
    return
  }
  window.open(urlPath, '_blank')
}

const currentConv = computed(() => conversations.value.find(c => c.id === currentConvId.value))

const filteredConvs = computed(() => {
  if (!convSearch.value) return conversations.value
  const k = convSearch.value.toLowerCase()
  return conversations.value.filter(c => c.title.toLowerCase().includes(k))
})

const renderMarkdown = (content: string) => {
  try {
    return marked.parse(content) as string
  } catch {
    return content.replace(/\n/g, '<br>')
  }
}

const scrollBottom = async () => {
  await nextTick()
  if (chatBodyRef.value) {
    chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight
  }
}

const loadConvs = async () => {
  convLoading.value = true
  try {
    const data = await listConversations()
    conversations.value = (data as any)?.conversations || (Array.isArray(data) ? data : [])
  } finally {
    convLoading.value = false
  }
}

const createConv = async () => {
  try {
    const conv = await createConversation()
    conversations.value.unshift(conv as any)
    currentConvId.value = (conv as any).id
    messages.value = []
    assistantDraft.value = ''
    pendingDoc.value = null
    ElMessage.success('已创建新对话')
  } catch {}
}

const selectConv = async (id: string) => {
  if (streaming.value) {
    ElMessage.warning('当前对话输出中，请等待或先「中断输出」')
    return
  }
  if (currentConvId.value === id) return
  currentConvId.value = id
  try {
    const data = await getConversation(id)
    messages.value = (data as any)?.messages || []
  } catch {}
  assistantDraft.value = ''
  pendingDoc.value = null
  scrollBottom()
}

const openConvMenu = async (e: MouseEvent, c: Conversation) => {
  e.stopPropagation()
  try {
    const action = await ElMessageBox({
      title: `对话：${c.title || '新对话'}`,
      message: '选择要执行的操作',
      showCancelButton: true,
      confirmButtonText: '重命名',
      cancelButtonText: '删除',
      distinguishCancelAndClose: true,
      customClass: 'conv-action-box',
    } as any)
    // 点确定 → 重命名
    if (action === 'confirm') {
      const { value } = await ElMessageBox.prompt('请输入新的标题', '重命名对话', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        inputValue: c.title || '',
        inputPlaceholder: '对话标题',
      })
      const newTitle = String(value || '').trim() || '新对话'
      await updateConversation(c.id, { title: newTitle })
      const idx = conversations.value.findIndex(x => x.id === c.id)
      if (idx >= 0) conversations.value[idx].title = newTitle
      ElMessage.success('已重命名')
    }
  } catch (actionOrErr: any) {
    // 点取消 → 删除
    if (actionOrErr === 'cancel') {
      try {
        await ElMessageBox.confirm(
          `确定删除对话「${c.title || '新对话'}」吗？删除后不可恢复。`,
          '删除对话',
          { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
        )
        await deleteConversation(c.id)
        conversations.value = conversations.value.filter(x => x.id !== c.id)
        if (currentConvId.value === c.id) {
          currentConvId.value = ''
          messages.value = []
        }
        ElMessage.success('已删除')
      } catch {}
    }
  }
}

const closeSSE = () => {
  if (sseClient) {
    try { sseClient.close() } catch {}
    sseClient = null
  }
  if (streamAbortController) {
    try { streamAbortController.abort() } catch {}
    streamAbortController = null
  }
}

const abortStream = () => {
  closeSSE()
  if (streaming.value && assistantDraft.value) {
    messages.value.push({ role: 'assistant', content: assistantDraft.value, doc: pendingDoc.value || undefined })
    pendingDoc.value = null
    assistantDraft.value = ''
  }
  streaming.value = false
}

const sendMessage = async () => {
  if (!input.value.trim() || streaming.value) return
  const userMsg = input.value.trim()
  input.value = ''
  messages.value.push({ role: 'user', content: userMsg })
  streaming.value = true
  assistantDraft.value = ''
  scrollBottom()

  streamAbortController = new AbortController()

  try {
    if (!currentConvId.value) {
      const n = await createConversation()
      currentConvId.value = (n as any).id
      conversations.value.unshift(n as any)
    }

    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify({ message: userMsg, conversation_id: currentConvId.value, stream: true }),
      signal: streamAbortController.signal,
    })
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`)
    }
    const reader = resp.body!.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let idx: number
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.substring(0, idx)
        buffer = buffer.substring(idx + 2)
        parseFrame(frame)
      }
    }
    if (buffer.trim()) parseFrame(buffer)

    if (streaming.value && assistantDraft.value) {
      messages.value.push({ role: 'assistant', content: assistantDraft.value })
    }
  } catch (e: any) {
    if (e?.name !== 'AbortError') {
      const err = `请求失败：${e?.message || '未知错误'}`
      if (streaming.value) assistantDraft.value += err
      else messages.value.push({ role: 'assistant', content: err })
    }
  } finally {
    streaming.value = false
    assistantDraft.value = ''
    streamAbortController = null
    closeSSE()
    scrollBottom()
    loadConvs()
  }
}

const parseFrame = (frame: string) => {
  const lines = frame.split('\n').map(l => l.trimEnd())
  let event = 'message'
  const dataParts: string[] = []
  for (const l of lines) {
    if (l.startsWith('event:')) event = l.substring(6).trim()
    else if (l.startsWith('data:')) dataParts.push(l.substring(5).trimStart())
  }
  if (!dataParts.length) return
  let payload: any
  try {
    payload = JSON.parse(dataParts.join('\n'))
  } catch {
    payload = { delta: dataParts.join('\n') }
  }
  handleEvent(event, payload)
}

const handleEvent = (event: string, payload: any) => {
  const realEvent = payload?.event || event
  if (realEvent === 'init') {
    if (payload?.conversation_id && payload.conversation_id !== currentConvId.value) {
      currentConvId.value = payload.conversation_id
    }
    return
  }
  if (realEvent === 'delta') {
    assistantDraft.value += payload?.delta ?? ''
    scrollBottom()
    return
  }
  if (realEvent === 'doc') {
    // 报告类回复：记录 Word 下载信息，done 时挂到本条回复上
    pendingDoc.value = { url_path: payload?.url_path, filename: payload?.filename }
    return
  }
  if (realEvent === 'done') {
    const content = assistantDraft.value || payload?.reply || ''
    messages.value.push({ role: 'assistant', content, doc: pendingDoc.value || undefined })
    pendingDoc.value = null
    assistantDraft.value = ''
    streaming.value = false
    if (payload?.title) {
      const idx = conversations.value.findIndex(c => c.id === currentConvId.value)
      if (idx >= 0) conversations.value[idx].title = payload.title
    }
    return
  }
  if (realEvent === 'error') {
    const msg = payload?.message || '（流式错误）'
    assistantDraft.value += `\n\n❌ ${msg}`
    streaming.value = false
    messages.value.push({ role: 'assistant', content: assistantDraft.value })
    assistantDraft.value = ''
    return
  }
  assistantDraft.value += payload?.delta ?? JSON.stringify(payload)
  scrollBottom()
}

onBeforeUnmount(() => {
  closeSSE()
})

onMounted(async () => {
  await loadConvs()
  if (conversations.value.length) {
    await selectConv(conversations.value[0].id)
  }
})
</script>

<style lang="scss" scoped>
.chat-page { height: 100%; }
:deep(.el-row) { height: 100%; }
.card-header-title { display: flex; align-items: center; gap: 8px; font-weight: 600; color: #303133; }
.chat-title { font-size: 15px; }
.conv-card :deep(.el-card__body) { height: calc(100% - 60px); display: flex; flex-direction: column; padding: 14px; }
.conv-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.conv-item {
  padding: 10px 12px; border-radius: 8px; cursor: pointer;
  border: 1px solid transparent; transition: all .2s;
  &:hover { background: #f5f7fa; }
  &.active { background: #e8f0fe; border-color: #1a73e8; }
  .conv-title {
    font-size: 13px; font-weight: 500; color:#303133;
    display:flex; align-items:center; gap:6px;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    > span:first-child {
      flex: 1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    }
  }
  .more-btn {
    flex: none;
    width: 24px; height: 24px; line-height: 20px; text-align: center;
    border-radius: 6px; color: #606266; font-weight: 700; font-size: 18px;
    letter-spacing: 1px;
    opacity: 0; transition: all .15s;
    user-select: none;
    &:hover { background: #e4e7ed; color: #303133; }
  }
  &:hover .more-btn { opacity: 1; }
  .conv-meta { display:flex; justify-content:space-between; margin-top:6px; font-size: 11px; color:#909399; }
}

.chat-card { height: 100%; display: flex; flex-direction: column; }
.chat-card :deep(.el-card__header) { flex-shrink: 0; }
.chat-card :deep(.el-card__body) { flex: 1; display: flex; flex-direction: column; padding: 16px; overflow: hidden; }
.chat-body { flex: 1; overflow-y: auto; padding-right: 6px; }
.msg-list { display: flex; flex-direction: column; gap: 16px; }
.msg {
  display: flex; gap: 10px; align-items: flex-start;
  &.user { flex-direction: row-reverse; .msg-bubble { background: #1a73e8; color:#fff; } }
  .msg-bubble {
    padding: 10px 14px; border-radius: 12px; background: #fff;
    box-shadow: 0 1px 2px rgba(0,0,0,0.06);
    max-width: 78%; line-height: 1.65; font-size: 14px;
    border: 1px solid #ebeef5;
    &.draft { color: #303133; background: #fafcff; }
    .cursor { display:inline-block; margin-left: 2px; animation: blink 1s step-end infinite; color:#1a73e8; font-weight:700; }
  }
  :deep(.markdown-body) {
    p { margin: 4px 0; }
    code { background: rgba(26,115,232,0.1); padding: 2px 6px; border-radius: 4px; font-size: 13px; }
    pre { background: #272822; color:#f8f8f2; padding: 10px 12px; border-radius: 6px; overflow-x: auto; }
    table { border-collapse: collapse; th, td { border: 1px solid #ddd; padding: 4px 8px; } }
  }
  .doc-download { margin-top: 10px; padding-top: 10px; border-top: 1px dashed #ebeef5; }
}
@keyframes blink { 50% { opacity: 0; } }

.chat-input { margin-top: 12px; flex-shrink: 0; border-top: 1px solid #ebeef5; padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.input-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
.input-actions > span:first-child { margin-right: auto; }
</style>
