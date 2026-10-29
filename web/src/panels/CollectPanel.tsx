import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Progress,
  Select,
  Space,
  Tag,
} from 'antd'
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useSse } from '../sse'
import type { JobState } from '../types'

const STATUS_TEXT: Record<string, string> = {
  running: '采集中',
  waiting: '等待操作',
  paused_captcha: '等待人工验证',
  done: '已完成',
  failed: '失败',
  timeout: '超时',
}
const STATUS_COLOR: Record<string, string> = {
  running: 'processing',
  waiting: 'processing',
  paused_captcha: 'warning',
  done: 'success',
  failed: 'error',
  timeout: 'warning',
}

export default function CollectPanel() {
  const { message } = AntApp.useApp()
  const [form] = Form.useForm()
  const [job, setJob] = useState<JobState | null>(null)
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

  const start = async (values: any) => {
    try {
      const created = await api.collect({
        keyword: values.keyword,
        pages: values.pages ?? 1,
        detail_top: values.detail_top ?? 6,
        detail_order: values.detail_order ?? 'rank',
        price_min: values.price_min ?? null,
        price_max: values.price_max ?? null,
        exclude_words: String(values.exclude_words || '')
          .split(/[,，]/)
          .map((s) => s.trim())
          .filter(Boolean),
      })
      jobRef.current = created.id
      setJob(created)
      message.success('采集已开始：会弹出浏览器窗口，请不要关闭')
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
      <Card title="开始采集" style={{ marginBottom: 16 }}>
        <Form
          form={form}
          layout="inline"
          onFinish={start}
          initialValues={{ pages: 1, detail_top: 6, detail_order: 'rank' }}
          style={{ rowGap: 12 }}
        >
          <Form.Item
            name="keyword"
            label="商品关键词"
            rules={[{ required: true, message: '请输入要采集的商品' }]}
          >
            <Input placeholder="例如：儿童保温杯" style={{ width: 220 }} allowClear />
          </Form.Item>
          <Form.Item name="pages" label="搜索页数">
            <InputNumber min={1} max={10} />
          </Form.Item>
          <Form.Item name="detail_top" label="详情样本数">
            <InputNumber min={0} max={200} />
          </Form.Item>
          <Form.Item name="detail_order" label="详情顺序">
            <Select
              style={{ width: 150 }}
              options={[
                { value: 'rank', label: '页面顺序（拟人）' },
                { value: 'sold', label: '已售优先' },
              ]}
            />
          </Form.Item>
          <Form.Item name="price_min" label="价格下限">
            <InputNumber min={0} prefix="¥" style={{ width: 110 }} />
          </Form.Item>
          <Form.Item name="price_max" label="价格上限">
            <InputNumber min={0} prefix="¥" style={{ width: 110 }} />
          </Form.Item>
          <Form.Item name="exclude_words" label="排除词">
            <Input placeholder="逗号分隔，如：配件,替换" style={{ width: 190 }} allowClear />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<PlayCircleOutlined />}>
              开始采集
            </Button>
          </Form.Item>
        </Form>
        <div className="hint">
          每页约 40–60 个商品；详情样本数 = 进入详情页采集的头部商品数（越多越慢、风控压力越大）。
        </div>
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
          {job.status === 'paused_captcha' && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message="需要人工处理"
              description="到弹出的浏览器里完成滑块验证（或重新登录），然后点「继续采集」。"
              action={
                <Button size="small" icon={<ReloadOutlined />} onClick={resume}>
                  继续采集
                </Button>
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