import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: () => import('@/layouts/BasicLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: { title: '仪表盘', icon: 'DataBoard' }
      },
      {
        path: 'upload',
        name: 'Upload',
        component: () => import('@/views/upload/index.vue'),
        meta: { title: '数据上传', icon: 'Upload' }
      },
      {
        path: 'files',
        name: 'Files',
        component: () => import('@/views/files/index.vue'),
        meta: { title: '数据文件', icon: 'Folder' }
      },
      {
        path: 'reports',
        name: 'Reports',
        component: () => import('@/views/reports/index.vue'),
        meta: { title: '预警报告', icon: 'Document' }
      },
      {
        path: 'students',
        name: 'Students',
        component: () => import('@/views/students/index.vue'),
        meta: { title: '学生画像', icon: 'User' }
      },
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/views/chat/index.vue'),
        meta: { title: 'AI 教学助手', icon: 'ChatDotRound' }
      },
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('@/views/settings/index.vue'),
        meta: { title: '系统设置', icon: 'Setting' }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, _from, next) => {
  const title = to.meta?.title
  if (title) {
    document.title = `${title} - 教学预警系统`
  } else {
    document.title = '教学预警系统'
  }
  next()
})

export default router
