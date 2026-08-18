<template>
  <el-container class="layout-container">
    <el-aside :width="sidebarWidth" class="layout-aside">
      <div class="logo-wrap">
        <el-icon size="24" color="#fff"><DataBoard /></el-icon>
        <span v-show="!appStore.sidebarCollapsed" class="logo-title">教学预警系统</span>
      </div>
      <el-scrollbar class="menu-scroll">
        <el-menu
          :default-active="activeMenu"
          :collapse="appStore.sidebarCollapsed"
          :collapse-transition="false"
          router
          background-color="#001529"
          text-color="#bfcbd9"
          active-text-color="#ffffff"
          unique-opened
        >
          <el-menu-item
            v-for="item in menuList"
            :key="item.path"
            :index="item.path"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <template #title>{{ item.title }}</template>
          </el-menu-item>
        </el-menu>
      </el-scrollbar>
    </el-aside>

    <el-container>
      <el-header class="layout-header">
        <div class="header-left">
          <el-icon class="toggle-btn" :size="18" @click="appStore.toggleSidebar()">
            <Expand v-if="appStore.sidebarCollapsed" />
            <Fold v-else />
          </el-icon>
          <el-breadcrumb separator="/" class="breadcrumb">
            <el-breadcrumb-item :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item>{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-tag size="small" type="info" effect="plain" round class="file-tag">
            文件: {{ appStore.healthzInfo?.files ?? 0 }}
          </el-tag>
          <el-dropdown trigger="click">
            <span class="user-avatar">
              <el-avatar :size="32" :icon="UserFilled" />
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="go('/settings')">
                  <el-icon><Setting /></el-icon>设置
                </el-dropdown-item>
                <el-dropdown-item divided>
                  <el-icon><Check /></el-icon>v0.3+
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="layout-main">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted, watch, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/store/app'
import { getHealthz } from '@/api/health'
import { UserFilled, Check } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()

const sidebarWidth = computed(() => (appStore.sidebarCollapsed ? '64px' : '220px'))

const menuList = computed(() => {
  const routes = router.options.routes[0].children || []
  return routes
    .filter(r => r.meta?.title)
    .map(r => ({
      path: '/' + r.path,
      title: r.meta!.title as string,
      icon: r.meta!.icon as string
    }))
})

const activeMenu = computed(() => route.path)
const currentTitle = computed(() => (route.meta?.title as string) || '')

const go = (path: string) => router.push(path)

const fetchHealthz = async () => {
  try {
    const data = await getHealthz()
    appStore.healthzInfo = data as any
    appStore.apiConfigured = true
  } catch {
    appStore.healthzInfo = {
      status: 'error',
      time: '-',
      files: 0,
      has_analysis: false,
      conversations: 0
    }
  }
}

onMounted(fetchHealthz)
watch(() => route.fullPath, fetchHealthz)
</script>

<style lang="scss" scoped>
.layout-container {
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}

.layout-aside {
  background: #001529;
  transition: width 0.25s;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.logo-wrap {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  color: #fff;
  background: linear-gradient(135deg, #002140 0%, #001529 100%);
  border-bottom: 1px solid rgba(255,255,255,0.06);
  flex-shrink: 0;
}
.logo-title {
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
}

.menu-scroll {
  flex: 1;
  :deep(.el-menu) {
    border-right: none;
  }
  :deep(.el-menu-item) {
    height: 48px;
    line-height: 48px;
    &.is-active {
      background: #1a73e8 !important;
    }
    &:hover {
      background: rgba(26,115,232,0.2) !important;
    }
  }
}

.layout-header {
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  height: 60px;
  flex-shrink: 0;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.toggle-btn {
  cursor: pointer;
  color: #606266;
  &:hover { color: #1a73e8; }
}
.breadcrumb {
  font-size: 14px;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  .file-tag { margin-left: 6px; }
}
.user-avatar {
  margin-left: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
}

.layout-main {
  padding: 20px;
  overflow-y: auto;
  background: #f5f7fa;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
