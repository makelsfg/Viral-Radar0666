import { useEffect, useState } from 'react'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Col,
  Input,
  Row,
  Select,
  Space,
  Table,
  Tag,
} from 'antd'
import { FireOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { HotwordKeyword, HotwordRow } from '../types'

export default function HotwordsPanel() {
  const { message } = AntApp.useApp()
  const [keyword, setKeyword] = useState('')
  const [loading, setLoading] = useState(false)
  const [fetched, setFetched] = useState<string[]>([])
  const [keywords, setKeywords] = useState<HotwordKeyword[]>([])
  const [selected, setSelected] = useState<string>()
  const [rows, setRows] = useState<HotwordRow[]>([])

  const loadKeywords = async (keep?: string) => {
    const list = await api.hotwordKeywords()
    setKeywords(list)
    const next = keep && list.some((k) => k.keyword === keep) ? keep : list[0]?.keyword
    setSelected(next)
    setRows(next ? await api.hotwordHistory(next) : [])
  }

  useEffect(() => {
    loadKeywords().catch((e: any) => message.error(e.message || '加载热词历史失败'))
  }, [])

  const fetchWords = async () => {
    const kw = keyword.trim()
    if (!kw) {
      message.warning('请输入要查询的关键词')
      return
    }
    setLoading(true)
    try {
      const res = await api.fetchHotwords(kw)
      setFetched(res.words)
      message.success(`获取到 ${res.words.length} 个联想词`)
      await loadKeywords(res.keyword)
    } catch (e: any) {
      message.error(e.message || '获取联想词失败')
    } finally {
      setLoading(false)
    }
  }

  const changeSelected = async (kw: string) => {
    setSelected(kw)
    try {
      setRows(await api.hotwordHistory(kw))
    } catch (e: any) {
      message.error(e.message || '加载热词历史失败')
    }
  }

  return (
    <Card
      title={
        <span>
          <FireOutlined /> 搜索热词
        </span>
      }
      extra={
        <Space>
          <Input
            style={{ width: 220 }}
            placeholder="输入关键词，如：儿童保温杯"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onPressEnter={fetchWords}
            allowClear
          />
          <Button type="primary" loading={loading} onClick={fetchWords}>
            获取联想词
          </Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="来源：淘宝搜索框联想词（公开建议接口，单次查询仅一次轻量请求）"
        description="用于发现细分需求词与选品方向；建议低频、按需查询，不要批量刷词。"
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card size="small" title="本次结果">
            {fetched.length === 0 ? (
              <span className="hint">输入关键词后点「获取联想词」</span>
            ) : (
              <ol style={{ margin: 0, paddingLeft: 20 }}>
                {fetched.map((w) => (
                  <li key={w} style={{ marginBottom: 6 }}>
                    {w}
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card
            size="small"
            title="历史热词"
            extra={
              <Select
                style={{ width: 220 }}
                placeholder="选择关键词"
                value={selected}
                onChange={changeSelected}
                options={keywords.map((k) => ({
                  value: k.keyword,
                  label: `${k.keyword}（${k.count}）`,
                }))}
              />
            }
          >
            {rows.length === 0 ? (
              <span className="hint">暂无历史记录</span>
            ) : (
              <Table
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={rows}
                columns={[
                  { title: '排名', dataIndex: 'rank', width: 60 },
                  { title: '联想词', dataIndex: 'word' },
                  {
                    title: '采集时间',
                    dataIndex: 'created_at',
                    width: 170,
                    render: (t: string) => <span className="hint">{t}</span>,
                  },
                ]}
              />
            )}
          </Card>
        </Col>
      </Row>

      <div className="hint" style={{ marginTop: 12 }}>
        <Tag>提示</Tag>
        联想词反映的是搜索需求方向，不等于销量；可以和「分析」页的价格带/卖点组合对照使用。
      </div>
    </Card>
  )
}