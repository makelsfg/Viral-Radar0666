import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Col,
  Empty,
  Image,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import { ReloadOutlined, RobotOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { AnalysisSummary, TaskInfo } from '../types'

function BandBars({ items }: { items: { label: string; count: number; sold?: number }[] }) {
  const max = Math.max(1, ...items.map((i) => i.count))
  return (
    <div>
      {items.map((it) => (
        <div key={it.label} className="band-row">
          <span className="band-label">{it.label}</span>
          <div className="band-track">
            <div
              className="band-fill"
              style={{ width: `${Math.max(4, (it.count / max) * 100)}%` }}
            />
          </div>
          <span className="band-value">
            {it.count} 个{it.sold ? ` · 销量 ${it.sold}` : ''}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function AnalysisPanel() {
  const { message } = AntApp.useApp()
  const [tasks, setTasks] = useState<TaskInfo[]>([])
  const [taskId, setTaskId] = useState<string>()
  const [batch, setBatch] = useState<string>()
  const [summary, setSummary] = useState<AnalysisSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [llmReady, setLlmReady] = useState(false)
  const [insight, setInsight] = useState('')
  const [generating, setGenerating] = useState(false)

  const loadTasks = useCallback(async (keep?: { taskId?: string; batch?: string }) => {
    const list = await api.tasks()
    setTasks(list)
    const wantedTask =
      keep?.taskId && list.some((t) => t.task_id === keep.taskId)
        ? keep.taskId
        : list[0]?.task_id
    setTaskId(wantedTask)
    const task = list.find((t) => t.task_id === wantedTask)
    const wantedBatch =
      keep?.batch && task?.batches.includes(keep.batch) ? keep.batch : task?.batches[0]
    setBatch(wantedBatch)
  }, [])

  const loadSummary = useCallback(
    async (t?: string, b?: string) => {
      if (!t || !b) {
        setSummary(null)
        return
      }
      setLoading(true)
      setInsight('')
      try {
        setSummary(await api.analysisSummary(t, b))
      } catch (e: any) {
        setSummary(null)
        message.error(e.message || '加载分析失败')
      } finally {
        setLoading(false)
      }
    },
    [message],
  )

  useEffect(() => {
    loadTasks().catch((e: any) => message.error(e.message || '加载品类失败'))
  }, [loadTasks, message])

  useEffect(() => {
    loadSummary(taskId, batch)
  }, [taskId, batch, loadSummary])

  useEffect(() => {
    api
      .llmStatus()
      .then((r) => setLlmReady(!!r.configured))
      .catch(() => setLlmReady(false))
  }, [])

  const generate = async () => {
    if (!taskId || !batch) return
    setGenerating(true)
    try {
      setInsight((await api.analysisInsights(taskId, batch)).text)
    } catch (e: any) {
      message.error(e.message || '生成解读失败')
    } finally {
      setGenerating(false)
    }
  }

  const current = tasks.find((t) => t.task_id === taskId)
  const stats = summary?.price.stats

  const topColumns: TableProps<AnalysisSummary['top_products'][number]>['columns'] = [
    {
      title: '主图',
      dataIndex: 'image_url',
      width: 56,
      render: (u: string | null) =>
        u ? (
          <Image
            src={u}
            width={40}
            height={40}
            preview={false}
            style={{ objectFit: 'cover', borderRadius: 6 }}
          />
        ) : (
          '—'
        ),
    },
    {
      title: '商品',
      dataIndex: 'title',
      ellipsis: true,
      render: (t: string | null) => t || '—',
    },
    {
      title: '到手价',
      dataIndex: 'price_final',
      width: 88,
      render: (v: number | null) => (v == null ? '—' : `¥${v.toFixed(2)}`),
    },
    {
      title: '已售',
      dataIndex: 'sold_text',
      width: 100,
      render: (t: string | null) => t || '—',
    },
    {
      title: '店铺',
      dataIndex: 'shop_name',
      width: 140,
      ellipsis: true,
      render: (t: string | null) => t || '—',
    },
  ]

  return (
    <Card
      title="批次分析"
      extra={
        <Space wrap>
          <Select
            style={{ width: 180 }}
            placeholder="品类"
            value={taskId}
            onChange={(v) => {
              setTaskId(v)
              const t = tasks.find((x) => x.task_id === v)
              setBatch(t?.batches[0])
            }}
            options={tasks.map((t) => ({ value: t.task_id, label: t.name }))}
          />
          <Select
            style={{ width: 190 }}
            placeholder="批次"
            value={batch}
            onChange={setBatch}
            options={(current?.batches || []).map((b) => ({ value: b, label: b }))}
          />
          <Button icon={<ReloadOutlined />} onClick={() => loadSummary(taskId, batch)}>
            刷新
          </Button>
        </Space>
      }
    >
      {tasks.length === 0 && (
        <Alert
          type="info"
          showIcon
          message="还没有采集数据"
          description="先去「采集」页跑一个批次，分析会自动出现。"
        />
      )}
      {tasks.length > 0 && !batch && <Empty description="该品类还没有批次" />}
      {batch && loading && (
        <div style={{ padding: 32, textAlign: 'center' }}>
          <Spin />
        </div>
      )}
      {batch && !loading && !summary && <Empty description="该批次没有可分析的数据" />}

      {batch && summary && (
        <>
          <Row gutter={[16, 16]}>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic title="候选商品" value={summary.counts.candidates} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic title="采到详情" value={summary.counts.detailed} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="价格中位数"
                  value={stats?.median ?? '—'}
                  prefix="¥"
                  precision={2}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic title="已售中位数" value={summary.sold.median ?? '—'} />
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            <Col xs={24} lg={12}>
              <Card
                size="small"
                title={`价格带分布（详情价 ${summary.counts.with_price_detail ?? 0} / 列表价 ${
                  summary.counts.with_price_list ?? 0
                }）`}
              >
                <BandBars items={summary.price.bands} />
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card size="small" title={`销量区间分布（样本 ${summary.counts.with_sold}）`}>
                <BandBars items={summary.sold.bands} />
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            <Col xs={24} lg={9}>
              <Card
                size="small"
                title={`店铺集中度（${summary.shops.distinct} 家，TOP5 销量占比 ${(
                  summary.shops.top5_share * 100
                ).toFixed(0)}%）`}
              >
                <Table
                  size="small"
                  rowKey="name"
                  pagination={false}
                  dataSource={summary.shops.shops}
                  columns={[
                    { title: '店铺', dataIndex: 'name', ellipsis: true },
                    { title: '商品', dataIndex: 'count', width: 60 },
                    { title: '销量', dataIndex: 'sold', width: 80 },
                  ]}
                />
              </Card>
            </Col>
            <Col xs={24} lg={15}>
              <Card size="small" title="标题关键词">
                {summary.keywords.map((k) => (
                  <Tag key={k.word} style={{ marginBottom: 6 }}>
                    {k.word} × {k.count}
                  </Tag>
                ))}
                {summary.keywords.length === 0 && (
                  <span className="hint">暂无可识别关键词</span>
                )}
              </Card>
            </Col>
          </Row>

          <Card
            size="small"
            title="参数画像（覆盖率 ≥40% 的字段）"
            style={{ marginTop: 16 }}
          >
            {summary.params.map((p) => (
              <div key={p.key} style={{ marginBottom: 10 }}>
                <b>{p.key}</b>{' '}
                <span className="hint">覆盖 {Math.round(p.coverage * 100)}%</span>
                <div style={{ marginTop: 4 }}>
                  {p.top.map((v) => (
                    <Tag key={v.value}>
                      {v.value} × {v.count}
                    </Tag>
                  ))}
                </div>
              </div>
            ))}
            {summary.params.length === 0 && (
              <span className="hint">详情样本不足，暂无参数画像</span>
            )}
          </Card>

          <Card
            size="small"
            title="卖点组合（价格带 · 规格 · 材质 · 特征词）"
            style={{ marginTop: 16 }}
          >
            <Table
              size="small"
              rowKey="cluster"
              pagination={false}
              dataSource={summary.clusters}
              columns={[
                { title: '组合', dataIndex: 'cluster', ellipsis: true },
                { title: '商品数', dataIndex: 'count', width: 80 },
                {
                  title: '均价',
                  dataIndex: 'avg_price',
                  width: 90,
                  render: (v: number | null) => (v == null ? '—' : `¥${v.toFixed(2)}`),
                },
                { title: '均销量', dataIndex: 'avg_sold', width: 90 },
                { title: '示例', dataIndex: 'example', ellipsis: true },
              ]}
            />
          </Card>

          <Card
            size="small"
            title="爆款候选 Top10（按已售下限）"
            style={{ marginTop: 16 }}
          >
            <Table
              size="small"
              rowKey="item_id"
              pagination={false}
              dataSource={summary.top_products}
              columns={topColumns}
              scroll={{ x: 760 }}
            />
          </Card>

          <Card
            size="small"
            title={
              <span>
                <RobotOutlined /> AI 解读
              </span>
            }
            style={{ marginTop: 16 }}
            extra={
              llmReady ? (
                <Button type="primary" loading={generating} onClick={generate}>
                  生成解读
                </Button>
              ) : (
                <Tag>未配置 LLM</Tag>
              )
            }
          >
            {!llmReady && (
              <Alert
                type="info"
                showIcon
                message="配置后可生成选品解读"
                description={
                  <div>
                    在启动前设置环境变量后重启服务：
                    <br />
                    <code>$env:BK_LLM_API_KEY="sk-..."</code>
                    <br />
                    <code>$env:BK_LLM_BASE_URL="https://api.deepseek.com"</code>
                    <br />
                    <code>$env:BK_LLM_MODEL="deepseek-chat"</code>
                    <br />
                    <code>.\start.bat</code>
                  </div>
                }
              />
            )}
            {llmReady && !insight && (
              <div className="hint">
                点击右上角「生成解读」，基于本批数据输出五段分析（约 10–30 秒）。
              </div>
            )}
            {insight && (
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
                {insight}
              </Typography.Paragraph>
            )}
          </Card>
        </>
      )}
    </Card>
  )
}