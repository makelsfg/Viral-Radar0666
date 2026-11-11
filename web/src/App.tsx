import { useEffect, useRef, useState } from 'react'
import { App as AntApp, Button, Layout, Space, Tabs, Tag } from 'antd'
import { BulbOutlined, RocketOutlined } from '@ant-design/icons'
import ExpertPanel from './panels/ExpertPanel'
import WorkbenchPanel from './panels/WorkbenchPanel'
import { api } from './api'
import { useSse } from './sse'
import type { EnvStatus } from './types'

const { Header, Content } = Layout

export default function App() {
  const { message } = AntApp.useApp()
  const [env, setEnv] = useState<EnvStatus | null>(null)
  const [loginRunning, setLoginRunning] = useState<'tmall' | '1688' | 'pdd' | null>(null)
  const loginRef = useRef<string | null>(null)
  const envRefreshRef = useRef(0)

  const loadEnv = () => {
    api
      .env()
      .then(setEnv)
      .catch(() => setEnv(null))
  }

  useEffect(() => {
    loadEnv()
  }, [])

  useSse((e: any) => {
    if (e?.type === 'job' && e.job?.type === 'collect') {
      const now = Date.now()
      if (now - envRefreshRef.current > 3000) {
        envRefreshRef.current = now
        loadEnv()
      }
      return
    }
    if (e?.type !== 'job' || e.job?.type !== 'login') return
    if (e.job.id !== loginRef.current) return
    if (e.job.status === 'done') {
      message.success(e.job.message || '登录成功')
      loginRef.current = null
      setLoginRunning(null)
      loadEnv()
    } else if (['failed', 'timeout'].includes(e.job.status)) {
      message.warning(e.job.message || '登录未完成，请重试')
      loginRef.current = null
      setLoginRunning(null)
    }
  })

  const startLogin = async (
    profile: 'tmall' | '1688' | 'pdd',
    site: 'taobao' | '1688' | 'pdd',
  ) => {
    try {
      const job = await api.login(profile, site)
      loginRef.current = job.id
      setLoginRunning(profile)
      message.info('浏览器已打开，请完成登录（拼多多为手机号验证码）；检测到登录态后自动完成')
    } catch (e: any) {
      message.error(e.message || '启动登录失败')
    }
  }

  const tmallOk = !!env?.profiles?.tmall
  const aliOk = !!env?.profiles?.['1688']
  const pddOk = !!env?.profiles?.pdd

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header className="app-header">
        <div className="brand">
          爆款雷达
          <span className="brand-sub">天猫 · 1688 · 拼多多 · 选品分析</span>
        </div>
        <Space size="middle" wrap>
          <Space size={4}>
            <Tag color={tmallOk ? 'success' : 'warning'}>
              天猫 {tmallOk ? '已登录' : '未登录'}
            </Tag>
            <Button
              size="small"
              loading={loginRunning === 'tmall'}
              onClick={() => startLogin('tmall', 'taobao')}
            >
              登录天猫
            </Button>
          </Space>
          <Space size={4}>
            <Tag color={aliOk ? 'success' : 'warning'}>
              1688 {aliOk ? '已登录' : '未登录'}
            </Tag>
            <Button
              size="small"
              loading={loginRunning === '1688'}
              onClick={() => startLogin('1688', '1688')}
            >
              登录1688
            </Button>
          </Space>
          <Space size={4}>
            <Tag color={pddOk ? 'success' : 'warning'}>
              拼多多 {pddOk ? '已登录' : '未登录'}
            </Tag>
            <Button
              size="small"
              loading={loginRunning === 'pdd'}
              onClick={() => startLogin('pdd', 'pdd')}
            >
              登录拼多多
            </Button>
          </Space>
        </Space>
      </Header>
      <Content className="app-content">
        <Tabs
          defaultActiveKey="workbench"
          items={[
            {
              key: 'workbench',
              label: (
                <span>
                  <RocketOutlined /> 工作台
                </span>
              ),
              children: <WorkbenchPanel />,
            },
            {
              key: 'expert',
              label: (
                <span>
                  <BulbOutlined /> 大佬分析
                </span>
              ),
              children: <ExpertPanel />,
            },
          ]}
        />
      </Content>
    </Layout>
  )
}