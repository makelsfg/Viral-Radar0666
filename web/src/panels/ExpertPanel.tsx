import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Col,
  Collapse,
  Divider,
  Empty,
  Input,
  InputNumber,
  List,
  Popconfirm,
  Row,
  Select,
  Space,
  Tag,
  Typography,
  Upload,
} from 'antd'
import {
  DeleteOutlined,
  DownloadOutlined,
  PlusOutlined,
  StopOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { useSse } from '../sse'
import type { ExpertReviewIssue, ExpertRound, ExpertRun, ExpertSource, TaskInfo } from '../types'

const STATUS_TEXT: Record<string, string> = {
  running: '生成中',
  cancelling: '正在取消',
  needs_human: '需人工复核',
  cancelled: '已取消',
  done: '已完成',
  failed: '失败',
}
const STATUS_COLOR: Record<string, string> = {
  running: 'processing',
  cancelling: 'warning',
  needs_human: 'warning',
  cancelled: 'default',
  done: 'success',
  failed: 'error',
}

function errorHint(msg?: string | null): string | null {
  if (!msg) return null
  if (msg.includes('API Key') || msg.includes('api_key') || msg.includes('未配置')) {
    return '到 config/settings.yaml 的 expert.writer / expert.reviewer 填写 base_url、api_key、model，重启后重试。'
  }
  if (msg.includes('langchain') || msg.includes('langgraph')) {
    return '先激活项目 venv 并执行 pip install -r requirements.txt，再重启服务。'
  }
  if (msg.toLowerCase().includes('timeout') || msg.toLowerCase().includes('connection')) {
    return '检查模型服务地址、网络与额度后重试。'
  }
  if (msg.includes('没有可用证据')) {
    return '至少选择一个有数据的批次，或上传一份报告/填写手动笔记。'
  }
  return '查看后端控制台日志；确认两个模型的配置与额度。'
}

function roundGroups(rounds: ExpertRound[] = []) {
  const map = new Map<number, { draft?: ExpertRound; review?: ExpertRound }>()
  for (const r of rounds) {
    const g = map.get(r.round_no) || {}
    if (r.stage === 'review') g.review = r
    else g.draft = r
    map.set(r.round_no, g)
  }
  return Array.from(map.entries()).sort((a, b) => a[0] - b[0])
}

export default function ExpertPanel() {
  const { message } = AntApp.useApp()
  const [tasks, setTasks] = useState<TaskInfo[]>([])
  const [runs, setRuns] = useState<ExpertRun[]>([])
  const [current, setCurrent] = useState<ExpertRun | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [reports, setReports] = useState<{ name: string; content: string }[]>([])
  const [note, setNote] = useState('')
  const [title, setTitle] = useState('')
  const [keyword, setKeyword] = useState('')
  const [maxRounds, setMaxRounds] = useState(3)
  const [passScore, setPassScore] = useState(85)
  const [creating, setCreating] = useState(false)
  const currentRef = useRef<string | null>(null)
  const refreshTimer = useRef<number | null>(null)
  currentRef.current = current?.id || null

  const batchOptions = useMemo(
    () =>
      tasks.flatMap((t) =>
        (t.batches || []).map((b) => ({
          value: `${t.task_id}::${b}`,
          label: `${t.name} · ${b}`,
        })),
      ),
    [tasks],
  )

  const loadRun = async (id: string) => {
    try {
      const run = await api.expertRun(id)
      if (currentRef.current === id) setCurrent(run)
      setRuns((prev) => {
        const rest = prev.filter((r) => r.id !== id)
        return [{ ...run }, ...rest].slice(0, 20)
      })
    } catch {
      /* ignore */
    }
  }

  const scheduleRefresh = (id: string) => {
    if (refreshTimer.current) window.clearTimeout(refreshTimer.current)
    refreshTimer.current = window.setTimeout(() => loadRun(id), 400)
  }

  useEffect(() => {
    api.tasks().then(setTasks).catch(() => undefined)
    api.expertRuns().then(setRuns).catch(() => undefined)
  }, [])

  useSse((e: any) => {
    if (e?.type !== 'expert') return
    const id = e.run?.id
    if (id && id === currentRef.current) scheduleRefresh(id)
  })

  useEffect(() => {
    if (!current) return
    if (!['running', 'cancelling'].includes(current.status)) return
    const timer = window.setInterval(() => {
      if (currentRef.current) loadRun(currentRef.current)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [current?.id, current?.status])

  const addReports = (files: File[]) => {
    files.forEach((file) => {
      const reader = new FileReader()
      reader.onload = () => {
        const content = String(reader.result || '')
        setReports((prev) => [...prev, { name: file.name, content }])
      }
      reader.readAsText(file)
    })
  }

  const create = async () => {
    const sources: ExpertSource[] = []
    selected.forEach((v) => {
      const [task_id, batch] = v.split('::')
      if (task_id && batch) sources.push({ type: 'batch', task_id, batch })
    })
    reports.forEach((r) => sources.push({ type: 'report', name: r.name, content: r.content }))
    if (note.trim()) sources.push({ type: 'note', name: '手动笔记', content: note.trim() })
    if (sources.length === 0) {
      message.warning('至少选择一个批次，或上传报告/填写笔记')
      return
    }
    setCreating(true)
    try {
      const run = await api.createExpertRun({
        title: title.trim() || `${keyword.trim() || '品类'} · 大佬分析`,
        keyword: keyword.trim(),
        sources,
        max_rounds: maxRounds,
        pass_score: passScore,
      })
      setCurrent(run)
      currentRef.current = run.id
      setRuns((prev) => [run, ...prev.filter((r) => r.id !== run.id)].slice(0, 20))
      message.success('已开始生成，右侧可看每轮写稿与审查')
    } catch (e: any) {
      message.error(e.message || '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const cancel = async () => {
    if (!current) return
    try {
      await api.cancelExpertRun(current.id)
      message.info('正在取消…')
    } catch (e: any) {
      message.error(e.message || '取消失败')
    }
  }

  const remove = async (id: string) => {
    try {
      await api.deleteExpertRun(id)
      if (current?.id === id) setCurrent(null)
      setRuns((prev) => prev.filter((r) => r.id !== id))
      message.success('已删除')
    } catch (e: any) {
      message.error(e.message || '删除失败')
    }
  }

  const groups = roundGroups(current?.rounds)
  const lastReview = current
    ? [...(current.rounds || [])].reverse().find((r) => r.stage === 'review')
    : undefined
  const running = current && ['running', 'cancelling'].includes(current.status)

  return (
    <Row gutter={16}>
      <Col xs={24} lg={9}>
        <Card title="新建大佬分析" size="small">
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <Input
              placeholder="报告标题（留空自动生成）"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <Input
              placeholder="关键词（如：手表）"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <Select
              mode="multiple"
              allowClear
              showSearch
              optionFilterProp="label"
              style={{ width: '100%' }}
              placeholder="选择采集批次（可多选、可跨平台）"
              value={selected}
              onChange={setSelected}
              options={batchOptions}
            />
            <Space wrap>
              <Upload
                accept=".md,.txt"
                multiple
                showUploadList={false}
                beforeUpload={(file) => {
                  addReports([file as File])
                  return false
                }}
              >
                <Button icon={<PlusOutlined />}>上传已有报告</Button>
              </Upload>
              <Typography.Text type="secondary">支持 .md / .txt，可多份</Typography.Text>
            </Space>
            {reports.length > 0 && (
              <Space wrap>
                {reports.map((r, idx) => (
                  <Tag
                    key={`${r.name}-${idx}`}
                    closable
                    onClose={() => setReports((prev) => prev.filter((_, i) => i !== idx))}
                  >
                    {r.name}
                  </Tag>
                ))}
              </Space>
            )}
            <Input.TextArea
              rows={3}
              placeholder="手动补充：供应链、成本、已有资源、限制条件等"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <Space>
              <span className="hint">最大轮次</span>
              <InputNumber min={1} max={5} value={maxRounds} onChange={(v) => setMaxRounds(Number(v || 3))} />
              <span className="hint">通过分数</span>
              <InputNumber min={60} max={100} value={passScore} onChange={(v) => setPassScore(Number(v || 85))} />
            </Space>
            <Button type="primary" loading={creating} onClick={create} block>
              生成报告
            </Button>
          </Space>
        </Card>

        <Card title="历史记录" size="small" style={{ marginTop: 16 }}>
          {runs.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有分析记录" />
          ) : (
            <List
              size="small"
              dataSource={runs}
              renderItem={(r) => (
                <List.Item
                  style={{ cursor: 'pointer' }}
                  onClick={() => loadRun(r.id)}
                  actions={[
                    <Popconfirm
                      key="del"
                      title="删除这条分析记录？"
                      okText="删除"
                      cancelText="取消"
                      onConfirm={(e) => {
                        e?.stopPropagation()
                        remove(r.id)
                      }}
                    >
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Popconfirm>,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <Space size={6}>
                        <span>{r.title}</span>
                        <Tag color={STATUS_COLOR[r.status] || 'default'}>
                          {STATUS_TEXT[r.status] || r.status}
                        </Tag>
                      </Space>
                    }
                    description={`${r.keyword || '—'} · ${(r.created_at || '').replace('T', ' ')}`}
                  />
                </List.Item>
              )}
            />
          )}
        </Card>
      </Col>

      <Col xs={24} lg={15}>
        <Card
          title={current ? current.title : '报告预览'}
          size="small"
          extra={
            current && (
              <Space>
                {running && (
                  <Button size="small" icon={<StopOutlined />} onClick={cancel}>
                    停止
                  </Button>
                )}
                {current.final_report_md && (
                  <Button
                    size="small"
                    icon={<DownloadOutlined />}
                    href={`/api/expert/runs/${current.id}/export`}
                  >
                    下载 Markdown
                  </Button>
                )}
              </Space>
            )
          }
        >
          {!current ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="选择批次/上传报告后，点「生成报告」"
            />
          ) : (
            <>
              <Space wrap style={{ marginBottom: 10 }}>
                <Tag color={STATUS_COLOR[current.status] || 'default'}>
                  {STATUS_TEXT[current.status] || current.status}
                </Tag>
                <span className="hint">
                  轮次 {current.iterations || 0}/{current.max_rounds || 3}
                </span>
                <span className="hint">
                  {lastReview?.score != null ? `审查得分 ${lastReview.score}` : '等待审查'}
                </span>
              </Space>

              {current.error && (
                <Alert
                  type="error"
                  showIcon
                  style={{ marginBottom: 10 }}
                  message={current.error}
                  description={errorHint(current.error) || undefined}
                />
              )}
              {current.status === 'needs_human' && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 10 }}
                  message="达到最大轮次仍未通过审查"
                  description="下方保留的是历史最高分草稿，建议人工复核审查意见后再使用。"
                />
              )}

              {groups.length > 0 && (
                <Collapse
                  size="small"
                  style={{ marginBottom: 12 }}
                  items={groups.map(([round, g]) => {
                    const review = g.review?.review
                    return {
                      key: String(round),
                      label: (
                        <Space wrap>
                          <span>第 {round} 轮</span>
                          {review?.verdict && (
                            <Tag color={review.verdict === 'pass' ? 'success' : 'warning'}>
                              {review.verdict === 'pass' ? '通过' : '打回'}
                            </Tag>
                          )}
                          {review?.score != null && <span className="hint">{review.score} 分</span>}
                        </Space>
                      ),
                      children: (
                        <div>
                          {review && (
                            <div style={{ marginBottom: 10 }}>
                              <Typography.Text strong>审查意见</Typography.Text>
                              {review.summary && (
                                <div className="hint" style={{ marginTop: 4 }}>
                                  {review.summary}
                                </div>
                              )}
                              {(review.issues || []).map((it: ExpertReviewIssue, idx: number) => (
                                <div key={idx} className="hint" style={{ marginTop: 6 }}>
                                  <Tag
                                    color={
                                      it.severity === 'blocker'
                                        ? 'error'
                                        : it.severity === 'major'
                                          ? 'warning'
                                          : 'default'
                                    }
                                  >
                                    {it.severity || 'issue'}
                                  </Tag>
                                  {it.location}：{it.problem}
                                  {it.fix && <div>修复：{it.fix}</div>}
                                </div>
                              ))}
                            </div>
                          )}
                          <Divider style={{ margin: '8px 0' }} />
                          <pre className="expert-draft">{g.draft?.content_md || '—'}</pre>
                        </div>
                      ),
                    }
                  })}
                />
              )}

              {current.final_report_md ? (
                <div className="expert-report">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {current.final_report_md}
                  </ReactMarkdown>
                </div>
              ) : (
                running && (
                  <Alert
                    type="info"
                    showIcon
                    message="正在生成报告"
                    description="大佬写稿 → 审查打分 → 不通过自动重写；每轮内容可展开查看。"
                  />
                )
              )}
            </>
          )}
        </Card>
      </Col>
    </Row>
  )
}