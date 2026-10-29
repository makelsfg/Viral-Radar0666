import { useEffect, useRef, useState } from 'react'
import { App as AntApp, Button, Layout, Space, Tabs, Tag } from 'antd'
import {
  BarChartOutlined,
  ExperimentOutlined,
  FireOutlined,
  TableOutlined,
  UserOutlined,
} from '@ant-design/icons'
import AnalysisPanel from './panels/AnalysisPanel'
import CollectPanel from './panels/CollectPanel'
import DataPanel from './panels/DataPanel'
import HotwordsPanel from './panels/HotwordsPanel'
import { api } from './api'
import { useSse } from './sse'

const { Header, Content } = Layout

export default function App() {
  const { message } = AntApp.useApp()
  const [loggedIn, setLoggedIn] = useState<boolean | null>(null)
  const [loginRunning, setLoginRunning] = useState(false)
  const loginRef = useRef<string | null>(null)

  const loadEnv = () => {
    api
      .env()
      .then((e) => setLoggedIn(!!e.logged_in))
      .catch(() => setLoggedIn(false))
  }

  useEffect(() => {
    loadEnv()
  }, [])

  useSse((e: any) => {
    if (e?.type !== 'job' || e.job?.type !== 'login') return
    if (e.job.id !== loginRef.current) return
    if (e.job.status === 'done') {
      message.success('登录成功')
      loginRef.current = null
      setLoginRunning(false)
      loadEnv()
    } else if (['failed', 'timeout'].includes(e.job.status)) {
      message.warning(e.job.message || '登录未完成，请重试')
      loginRef.current = null
      setLoginRunning(false)
    }
  })

  const startLogin = async () => {
    try {
      const job = await api.login()
      loginRef.current = job.id
      setLoginRunning(true)
      message.info('浏览器已打开，请扫码登录；检测到登录后自动完成')
    } catch (e: any) {
      message.error(e.message || '启动登录失败')
    }
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header className="app-header">
        <div className="brand">
          爆款雷达
          <span className="brand-sub">天猫品类采集</span>
        </div>
        <Space>
          <Tag color={loggedIn ? 'success' : 'warning'}>
            {loggedIn === null ? '检查登录态…' : loggedIn ? '已登录' : '未登录'}
          </Tag>
          <Button
            size="small"
            icon={<UserOutlined />}
            loading={loginRunning}
            onClick={startLogin}
          >
            {loggedIn ? '重新登录' : '扫码登录'}
          </Button>
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