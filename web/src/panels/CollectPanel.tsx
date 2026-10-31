import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Progress,
  Select,
  Space,
  Switch,
  Tag,
} from 'antd'
import { DownloadOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useSse } from '../sse'
import type { JobState, ReportMeta } from '../types'

const STATUS_TEXT: Record<string, string> = {
  running: '采集中',
  waiting: '等待操作',
  paused_captcha: '等待人工验证',
  auth_required: '等待登录',
  done: '已完成',
  failed: '失败',
  timeout: '超时',
}
const STATUS_COLOR: Record<string, string> = {
  running: 'processing',
  waiting: 'processing',
  paused_captcha: 'warning',
  auth_required: 'warning',
  done: 'success',
  failed: 'error',
  timeout: 'warning',
}
const PLATFORM_LABELS: Record<string, string> = {
  tmall: '天猫',
  '1688': '1688',
  pdd: '拼多多',
}

export default function CollectPanel() {
  const { message } = AntApp.useApp()
  const [form] = Form.useForm()
  const [job, setJob] = useState<JobState | null>(null)
  const [conservative, setConservative] = useState(true)
  const [loginRunning, setLoginRunning] = useState(false)
  const [report, setReport] = useState<ReportMeta | null>(null)
  const jobRef = useRef<string | null>(null)

  const applyJob = (j: JobState) => {
    if (j.id !== jobRef.current) return
    setJob({ ...j })
  }

  useSse((e: any) => {
    if (e?.type === 'job' && e.job?.type === 'collect') applyJob(e.job)
  })

  // SSE 兜底：任务进行中时每 3 秒轮询一次（断线也能追进度）
  useEffect(() => {
    if (!job || ['done', 'failed', 'timeout'].includes(job.status)) return
    const timer = window.setInterval(async () => {
      const id = jobRef.current
      if (!id) return
      try {
        applyJob(await api.job(id))
      } catch {
        /* ignore */
      }
    }, 3000)
    return () => window.clearInterval(timer)
  }, [job?.id, job?.status])

  // 采集结束后查询本批次报告信息（含各平台条数）
  useEffect(() => {
    if (!job?.batch || !['done', 'failed'].includes(job.status)) {
      setReport(null)
      return
    }
    let alive = true
    api
      .reportMeta(job.batch)
      .then((m) => alive && setReport(m))
      .catch(() => alive && setReport(null))
    return () => {
      alive = false
    }
  }, [job?.status, job?.batch])

  const start = async (values: any) => {
    try {
      const created = await api.collect({
        keyword: values.keyword,
        platforms:
          Array.isArray(values.platforms) && values.platforms.length > 0
            ? values.platforms
            : ['tmall'],
        pages: values.pages ?? 1,
        detail_top: values.detail_top ?? 0,
        detail_order: 'sold',
        pace: values.pace ?? 'safe',
        mode: conservative ? 'conservative' : 'standard',
        price_min: values.price_min ?? null,
        price_max: values.price_max ?? null,
        exclude_words: [],
      })
      jobRef.current = created.id
      setJob(created)
      message.success('已开始：先检查登录状态，未授权平台会弹出登录窗口')
    } catch (e: any) {
      message.error(e.message || '启动采集失败')
    }
  }

  const resume = async () => {
    if (!job) return
    try {
      const created = await api.resume(job.id)
      jobRef.current = created.id
      setJob(created)
      message.info('已继续采集')
    } catch (e: any) {
      message.error(e.message || '续采失败')
    }
  }

  const openLogin = async () => {
    setLoginRunning(true)
    try {
      const profile =
        job?.task_platform === '1688'
          ? '1688'
          : job?.task_platform === 'pdd'
            ? 'pdd'
            : 'tmall'
      const site = profile === '1688' ? '1688' : profile === 'pdd' ? 'pdd' : 'taobao'
      await api.login(profile, site)
      message.info('浏览器已打开，请完成扫码登录，然后点「继续采集」')
    } catch (e: any) {
      message.error(e.message || '打开登录失败')
    } finally {
      setLoginRunning(false)
    }
  }

  const needLogin = !!job?.error && job.error.includes('登录')
  const showAuth = job?.phase === 'auth' || job?.status === 'auth_required'

  const retryAuth = async (platform: string) => {
    if (!job) return
    try {
      const created = await api.continueJob(job.id, platform)
      jobRef.current = created.id
      setJob(created)
      message.info('正在打开登录窗口，请完成扫码')
    } catch (e: any) {
      message.error(e.message || '启动登录失败')
    }
  }

  const pagesPercent = job
    ? Math.min(100, Math.round((job.page_done / Math.max(job.pages, 1)) * 100))
    : 0
  const detailTarget = job
    ? Math.min(job.detail_total || 0, job.items_count || job.detail_total || 0)
    : 0
  const detailPercent =
    job && detailTarget > 0
      ? Math.min(100, Math.round((job.detail_done / detailTarget) * 100))
      : 0

  return (
    <>
      <Card
        title="开始采集"
        style={{ marginBottom: 16 }}
        extra={
          <Space>
            <Switch
              checked={conservative}
              onChange={(v) => {
                setConservative(v)
                form.setFieldsValue({ pages: 1, detail_top: v ? 0 : 6 })
              }}
            />
            <span>保守模式（只采列表，不进详情）</span>
          </Space>
        }
      >
        <Form
          form={form}
          layout="inline"
          onFinish={start}
          initialValues={{
            pages: 1,
            detail_top: 0,
            pace: 'safe',
            platforms: ['tmall'],
          }}
          style={{ rowGap: 12 }}
        >
          <Form.Item
            name="platforms"
            label="采集平台"
            rules={[{ required: true, message: '请至少选择一个平台' }]}
          >
            <Checkbox.Group
              options={[
                { label: '天猫', value: 'tmall' },
                { label: '1688', value: '1688' },
                { label: '拼多多（实验性）', value: 'pdd' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="keyword"
            label="商品关键词"
            rules={[{ required: true, message: '请输入要采集的商品' }]}
          >
            <Input placeholder="例如：儿童保温杯" style={{ width: 220 }} allowClear />
          </Form.Item>
          <Form.Item name="pages" label="搜索页数">
            <InputNumber min={1} max={10} disabled={conservative} />
          </Form.Item>
          <Form.Item name="detail_top" label="详情样本数">
            <InputNumber min={0} max={200} disabled={conservative} />
          </Form.Item>
          <Form.Item name="pace" label="节奏">
            <Select
              style={{ width: 110 }}
              options={[
                { value: 'safe', label: '安全' },
                { value: 'balanced', label: '平衡' },
                { value: 'fast', label: '快速' },
              ]}
            />
          </Form.Item>
          <Form.Item name="price_min" label="价格下限">
            <InputNumber min={0} prefix="¥" style={{ width: 110 }} />
          </Form.Item>
          <Form.Item name="price_max" label="价格上限">
            <InputNumber min={0} prefix="¥" style={{ width: 110 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<PlayCircleOutlined />}>
              开始采集
            </Button>
          </Form.Item>
        </Form>
        <div className="hint">
          每页约 40–60 个商品；详情样本数 = 按已售下限从高到低选取的头部商品数（越多越慢、风控压力越大）。
          1688 / 拼多多首次使用前，请点右上角对应「登录」完成扫码（各平台独立登录）。
          拼多多为移动网页实验版，选择器可能随平台改版，失败时请查看日志。
        </div>
        {conservative && (
          <Alert
            type="success"
            showIcon
            style={{ marginTop: 12 }}
            message="保守模式：不访问商品详情页，风控最低"
            description="只采集搜索结果列表（标题、主图、价格、已售、店铺），不采 SKU/参数/评分；到「数据」页关掉「只看已采详情」即可查看候选商品。"
          />
        )}
      </Card>

      {job && (
        <Card
          title="采集进度"
          extra={<Tag color={STATUS_COLOR[job.status] || 'default'}>{STATUS_TEXT[job.status] || job.status}</Tag>}
        >
          <Space size="large" wrap>
            <span>关键词：{job.task_name || '—'}</span>
            <span>批次：{job.batch || '—'}</span>
            <span className="hint">{job.message}</span>
          </Space>

          {showAuth ? (
            <div style={{ marginTop: 10 }}>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {(job.auth_statuses || []).map((s) => {
                  const text: Record<string, string> = {
                    authorized: '已授权',
                    logging_in: '等待扫码',
                    unauthorized: '未授权',
                    waiting: '待登录',
                  }
                  const color =
                    s.status === 'authorized'
                      ? 'success'
                      : s.status === 'logging_in'
                        ? 'processing'
                        : s.status === 'unauthorized'
                          ? 'error'
                          : 'default'
                  return (
                    <Space key={s.platform} size={10} wrap>
                      <Tag color={color}>{PLATFORM_LABELS[s.platform] || s.platform}</Tag>
                      <span className="hint">
                        {text[s.status] || s.status}
                        {s.reason ? `（${s.reason}）` : ''}
                      </span>
                      {s.status !== 'authorized' && job.status === 'auth_required' && (
                        <Button size="small" onClick={() => retryAuth(s.platform)}>
                          重新登录
                        </Button>
                      )}
                    </Space>
                  )
                })}
              </Space>
              {job.status === 'auth_required' ? (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginTop: 12 }}
                  message="登录未完成，采集不会开始"
                  description="请点击对应平台的「重新登录」，完成扫码后系统会自动开始采集。"
                />
              ) : (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginTop: 12 }}
                  message="登录检查中"
                  description="请在弹出的浏览器窗口里完成扫码；登录成功后窗口会自动关闭。"
                />
              )}
            </div>
          ) : (
            <>
          {job.platforms_status && job.platforms_status.length > 0 ? (
            <div style={{ marginTop: 10 }}>
              <span className="hint" style={{ marginRight: 8 }}>
                平台执行状态（并行）：
              </span>
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                {job.platforms_status.map((s) => {
                  const stateText: Record<string, string> = {
                    waiting: '等待',
                    running: '采集中',
                    paused_captcha: '待人工处理',
                    failed: '失败',
                    done: '已完成',
                  }
                  const color =
                    s.status === 'done'
                      ? 'success'
                      : s.status === 'running'
                        ? 'processing'
                        : s.status === 'failed'
                          ? 'error'
                          : s.status === 'paused_captcha'
                            ? 'warning'
                            : 'default'
                  return (
                    <Space key={s.task_id} size={8} wrap>
                      <Tag color={color}>{PLATFORM_LABELS[s.platform] || s.platform}</Tag>
                      <span className="hint">{stateText[s.status] || s.status}</span>
                      <span className="hint">
                        列表 {s.page_done}/{s.pages} · 详情 {s.detail_done}/{s.detail_total} · 候选 {s.items_count}
                      </span>
                      {s.error && (
                        <span className="hint" style={{ color: '#cf1322' }}>
                          {s.error}
                        </span>
                      )}
                    </Space>
                  )
                })}
              </Space>
            </div>
          ) : job.platforms && job.platforms.length > 1 ? (
            <div style={{ marginTop: 10 }}>
              <span className="hint" style={{ marginRight: 8 }}>
                平台队列（串行执行）：
              </span>
              <Space wrap>
                {job.platforms.map((p, idx) => {
                  const currentIdx = job.task_index ?? 1
                  const state = idx + 1 < currentIdx ? 'done' : idx + 1 === currentIdx ? 'running' : 'waiting'
                  return (
                    <Tag
                      key={p}
                      color={state === 'done' ? 'success' : state === 'running' ? 'processing' : 'default'}
                    >
                      {PLATFORM_LABELS[p] || p}
                      {state === 'running' ? ' · 进行中' : state === 'done' ? ' · 已完成' : ' · 等待'}
                    </Tag>
                  )
                })}
              </Space>
            </div>
          ) : null}

          {(!job.platforms_status || job.platforms_status.length <= 1) && (
            <div style={{ marginTop: 16 }}>
              <div className="hint">
                搜索页 {job.page_done}/{job.pages}　候选商品 {job.items_count} 个
              </div>
              <Progress percent={pagesPercent} />
              <div className="hint" style={{ marginTop: 8 }}>
                商品详情 {job.detail_done}/{detailTarget || '—'}
              </div>
              <Progress
                percent={detailPercent}
                status={job.status === 'failed' ? 'exception' : undefined}
              />
            </div>
          )}
            </>
          )}
          {report && ['done', 'failed'].includes(job.status) && (
            <Alert
              type={job.status === 'done' ? 'success' : 'warning'}
              showIcon
              style={{ marginTop: 12 }}
              message={
                job.status === 'done'
                  ? '采集完成，已生成跨平台合并报告'
                  : '采集已结束（部分平台可能失败），可导出已有数据'
              }
              description={
                `${report.keyword} · ` +
                report.platforms
                  .map((p) => `${p.label} ${p.items} 条（详情 ${p.details}）`)
                  .join(' + ') +
                '。报告按平台分组，各平台销量口径不同，不做跨平台销量排名。'
              }
              action={
                <Button
                  type="primary"
                  size="small"
                  icon={<DownloadOutlined />}
                  href={`/api/report?batch=${encodeURIComponent(job.batch || '')}&only_detail=0`}
                >
                  下载报告
                </Button>
              }
            />
          )}
          {job.status === 'paused_captcha' && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message={needLogin ? '需要重新登录' : '需要人工处理'}
              description={
                needLogin
                  ? '当前登录态不完整（需 cookie2 + unb）。点「打开登录」完成扫码，再点「继续采集」。'
                  : '到弹出的浏览器里完成滑块验证（或重新登录），然后点「继续采集」。'
              }
              action={
                <Space>
                  {needLogin && (
                    <Button size="small" loading={loginRunning} onClick={openLogin}>
                      打开登录
                    </Button>
                  )}
                  <Button size="small" icon={<ReloadOutlined />} onClick={resume}>
                    继续采集
                  </Button>
                </Space>
              }
            />
          )}
          {job.status === 'failed' && job.error && (
            <Alert type="error" showIcon style={{ marginTop: 12 }} message={job.error} />
          )}
        </Card>
      )}
    </>
  )
}