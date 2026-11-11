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
import { DownloadOutlined, ReloadOutlined, RobotOutlined } from '@ant-design/icons'
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

interface Props {
  tasks: TaskInfo[]
  taskId?: string
  batch?: string
  onTaskChange?: (taskId?: string) => void
  onBatchChange?: (batch?: string) => void
  showSelector?: boolean
  reloadKey?: number
  title?: string
  extra?: React.ReactNode
}

export default function AnalysisPanel({
  tasks,
  taskId,
  batch,
  onTaskChange,
  onBatchChange,
  showSelector = true,
  reloadKey,
  title = '批次分析',
  extra,
}: Props) {
  const { message } = AntApp.useApp()
  const [summary, setSummary] = useState<AnalysisSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [llmReady, setLlmReady] = useState(false)
  const [insight, setInsight] = useState('')
  const [generating, setGenerating] = useState(false)

  const loadSummary = useCallback(
    async (silent = false) => {
      if (!batch || !taskId) {
        setSummary(null)
        setInsight('')
        return
      }
      if (!silent) setLoading(true)
      // 已保存的 AI 解读自动加载（每次打开都能看到）
      if (!silent) {
        api
          .savedInsights(taskId, batch)
          .then((r) => setInsight(r.saved ? r.text || '' : ''))
          .catch(() => setInsight(''))
      }
      try {
        setSummary(await api.analysisSummary(taskId, batch))
      } catch (e: any) {
        setSummary(null)
        if (!silent) message.error(e.message || '加载分析失败')
      } finally {
        if (!silent) setLoading(false)
      }
    },
    [batch, taskId, message],
  )

  useEffect(() => {
    loadSummary()
  }, [loadSummary, reloadKey])

  // 词表在后台生成时轮询刷新（约 10–30 秒；最多等 60 秒，避免死循环）
  const profileStatus = summary?.profile?.status
  useEffect(() => {
    if (profileStatus !== 'generating') return
    let tries = 0
    const timer = setInterval(() => {
      tries += 1
      if (tries > 20) {
        clearInterval(timer)
        return
      }
      loadSummary(true)
    }, 3000)
    return () => clearInterval(timer)
  }, [profileStatus, loadSummary])

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
      const res = await api.analysisInsights(taskId, batch)
      setInsight(res.text)
      message.success('AI 解读已生成并保存（同批次重新生成会覆盖旧报告）')
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
      title={title}
      extra={
        <Space wrap>
          {showSelector && (
            <>
              <Select
                style={{ width: 180 }}
                placeholder="品类"
                value={taskId}
                onChange={(v) => {
                  onTaskChange?.(v)
                  const t = tasks.find((x) => x.task_id === v)
                  onBatchChange?.(t?.batches[0])
                }}
                options={tasks.map((t) => ({ value: t.task_id, label: t.name }))}
              />
              <Select
                style={{ width: 190 }}
                placeholder="批次"
                value={batch}
                onChange={onBatchChange}
                options={(current?.batches || []).map((b) => ({ value: b, label: b }))}
              />
            </>
          )}
          <Button icon={<ReloadOutlined />} onClick={() => loadSummary()}>
            刷新
          </Button>
          {extra}
        </Space>
      }
    >
      {!batch && (
        <Alert
          type="info"
          showIcon
          message="还没有采集数据"
          description="先跑一个采集批次，分析会自动出现。"
        />
      )}
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
              <Card
                size="small"
                title={
                  <Space size={6}>
                    卖点词（标题命中）
                    <Tag color={summary.profile?.source === 'none' ? 'default' : 'blue'}>
                      {summary.profile?.label || '未生成'}
                    </Tag>
                  </Space>
                }
                extra={
                  summary.profile?.status === 'generating' ? (
                    <Space size={6}>
                      <Spin size="small" />
                      <span className="hint">正在分析卖点词…</span>
                    </Space>
                  ) : undefined
                }
              >
                {summary.profile?.status === 'generating' && (
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 10 }}
                    message="正在分析卖点词（首次约 10–30 秒）"
                    description="其余统计已可用；词表生成完成后本卡片会自动刷新。"
                  />
                )}
                {summary.profile?.status === 'unavailable' && (
                  <Alert
                    type="warning"
                    showIcon
                    style={{ marginBottom: 10 }}
                    message="未配置 LLM，仅统计参数卖点"
                    description="在 config/settings.yaml 的 llm 段填写 api_key 后重启，即可按品类自动生成卖点词表。"
                  />
                )}
                {summary.profile?.status === 'failed' && (
                  <Alert
                    type="error"
                    showIcon
                    style={{ marginBottom: 10 }}
                    message="卖点词表生成失败"
                    description={summary.profile?.message || '可刷新页面重试，或检查 LLM 配置与额度。'}
                  />
                )}
                <Table
                  size="small"
                  rowKey="word"
                  pagination={false}
                  dataSource={summary.selling_points?.title || []}
                  locale={{ emptyText: '暂无可识别卖点词' }}
                  columns={[
                    { title: '卖点词', dataIndex: 'word', ellipsis: true },
                    {
                      title: '来源',
                      dataIndex: 'source',
                      width: 64,
                      render: (v: string) =>
                        v === 'param' ? <Tag color="blue">参数</Tag> : <Tag>标题</Tag>,
                    },
                    { title: '商品数', dataIndex: 'items', width: 70 },
                    {
                      title: '覆盖率',
                      dataIndex: 'coverage',
                      width: 76,
                      render: (v: number) => `${Math.round((v || 0) * 100)}%`,
                    },
                    {
                      title: '均价',
                      dataIndex: 'avg_price',
                      width: 88,
                      render: (v: number | null) => (v == null ? '—' : `¥${v.toFixed(2)}`),
                    },
                    {
                      title: '均销量',
                      dataIndex: 'avg_sold',
                      width: 80,
                      render: (v: number | null) => (v == null ? '—' : v),
                    },
                  ]}
                  scroll={{ x: 520 }}
                />
              </Card>
            </Col>
          </Row>

          <Card
            size="small"
            title="参数卖点（key = value，按商品数排序）"
            style={{ marginTop: 16 }}
          >
            <Table
              size="small"
              rowKey={(r) => `${r.key}=${r.value}`}
              pagination={false}
              dataSource={summary.selling_points?.params || []}
              locale={{ emptyText: '详情样本不足，暂无参数卖点' }}
              columns={[
                { title: '参数', dataIndex: 'key', width: 120, ellipsis: true },
                { title: '取值', dataIndex: 'value', ellipsis: true },
                { title: '商品数', dataIndex: 'items', width: 80 },
                {
                  title: '覆盖率',
                  dataIndex: 'coverage',
                  width: 84,
                  render: (v: number) => `${Math.round((v || 0) * 100)}%`,
                },
                {
                  title: '均价',
                  dataIndex: 'avg_price',
                  width: 92,
                  render: (v: number | null) => (v == null ? '—' : `¥${v.toFixed(2)}`),
                },
                {
                  title: '均销量',
                  dataIndex: 'avg_sold',
                  width: 88,
                  render: (v: number | null) => (v == null ? '—' : v),
                },
              ]}
              scroll={{ x: 620 }}
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
                <RobotOutlined /> AI 解读（每个平台一份，重新生成覆盖旧的）
              </span>
            }
            style={{ marginTop: 16 }}
            extra={
              <Space>
                {insight && (
                  <Button
                    icon={<DownloadOutlined />}
                    href={`/api/analysis/insights/export?task_id=${encodeURIComponent(taskId || '')}&batch=${encodeURIComponent(batch || '')}`}
                  >
                    导出 MD
                  </Button>
                )}
                {llmReady ? (
                  <Button type="primary" loading={generating} onClick={generate}>
                    {insight ? '重新生成（覆盖）' : '生成解读'}
                  </Button>
                ) : (
                  <Tag>未配置 LLM</Tag>
                )}
              </Space>
            }
          >
            {!llmReady && !insight && (
              <Alert
                type="info"
                showIcon
                message="配置后可生成选品解读"
                description={
                  <div>
                    在 config/settings.yaml 的 llm 段填写 base_url、api_key、model，重启服务即可。
                  </div>
                }
              />
            )}
            {llmReady && !insight && (
              <div className="hint">
                点击右上角「生成解读」，基于本批数据输出五段分析（约 10–30 秒）；生成成功会自动保存。
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
