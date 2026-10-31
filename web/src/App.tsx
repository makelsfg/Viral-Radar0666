import { useEffect, useRef, useState } from 'react'
import { App as AntApp, Button, Layout, Space, Tabs, Tag } from 'antd'
import {
  BarChartOutlined,
  ExperimentOutlined,
  FireOutlined,
  TableOutlined,
} from '@ant-design/icons'
import AnalysisPanel from './panels/AnalysisPanel'
import CollectPanel from './panels/CollectPanel'
import DataPanel from './panels/DataPanel'
import HotwordsPanel from './panels/HotwordsPanel'
import { api } from './api'
import { useSse } from './sse'
import type { EnvStatus } from './types'

const { Header, Content } = Layout

export default function App() {
  const { message } = AntApp.useApp()
  const [env, setEnv] = useState<EnvStatus | null>(null)
  const [loginRunning, setLoginRunning] = useState<'tmall' | '1688' | null>(null)
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

  const startLogin = async (profile: 'tmall' | '1688', site: 'taobao' | '1688') => {
    try {
      const job = await api.login(profile, site)
      loginRef.current = job.id
      setLoginRunning(profile)
      message.info('浏览器已打开，请扫码登录；检测到完整登录态后自动完成')
    } catch (e: any) {
      message.error(e.message || '启动登录失败')
    }
  }

  const tmallOk = !!env?.profiles?.tmall
  const aliOk = !!env?.profiles?.['1688']

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header className="app-header">
        <div className="brand">
          爆款雷达
          <span className="brand-sub">天猫 · 1688 · 选品分析</span>
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
        </Space>
      </Header>
      <Content className="app-content">
        <Tabs
          defaultActiveKey="collect"
          items={[
            {
              key: 'collect',
              label: (
                <span>
                  <ExperimentOutlined /> 采集
                </span>
              ),
              children: <CollectPanel />,
            },
            {
              key: 'data',
              label: (
                <span>
                  <TableOutlined /> 数据
                </span>
              ),
              children: <DataPanel />,
            },
            {
              key: 'analysis',
              label: (
                <span>
                  <BarChartOutlined /> 分析
                </span>
              ),
              children: <AnalysisPanel />,
            },
            {
              key: 'hotwords',
              label: (
                <span>
                  <FireOutlined /> 热词
                </span>
              ),
              children: <HotwordsPanel />,
            },
          ]}
        />
      </Content>
    </Layout>
  )
}