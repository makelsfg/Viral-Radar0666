import { useCallback, useEffect, useState } from 'react'
import { App as AntApp, Alert, Button, Card, Checkbox, Empty, Select, Space, Spin, Tabs } from 'antd'
import {
  BarChartOutlined,
  DownloadOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  ReloadOutlined,
  TableOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import type { TaskInfo } from '../types'
import CollectPanel from './CollectPanel'
import DataPanel from './DataPanel'
import AnalysisPanel from './AnalysisPanel'

export default function WorkbenchPanel() {
  const { message } = AntApp.useApp()
  const [tasks, setTasks] = useState<TaskInfo[]>([])
  const [taskId, setTaskId] = useState<string>()
  const [batch, setBatch] = useState<string>()
  const [onlyDetail, setOnlyDetail] = useState(true)
  const [onlyAi, setOnlyAi] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [reportMd, setReportMd] = useState('')
  const [reportLoading, setReportLoading] = useState(false)
  const [reportError, setReportError] = useState('')
  const [stage, setStage] = useState<'collect' | 'data' | 'analysis'>('collect')

  const loadTasks = useCallback(
    async (keep?: { taskId?: string; batch?: string }) => {
      const list = await api.tasks()
      setTasks(list)
      // 批次可能属于任意平台任务，优先定位真正拥有该批次的任务
      const owner = keep?.batch
        ? list.find((t) => t.batches.includes(keep.batch!))
        : undefined
      const wantedTask =
        owner?.task_id ??
        (keep?.taskId && list.some((t) => t.task_id === keep.taskId)
          ? keep.taskId
          : list[0]?.task_id)
      setTaskId(wantedTask)
      const task = list.find((t) => t.task_id === wantedTask)
      const wantedBatch =
        keep?.batch && task?.batches.includes(keep.batch) ? keep.batch : task?.batches[0]
      setBatch(wantedBatch)
      return { taskId: wantedTask, batch: wantedBatch }
    },
    [],
  )

  useEffect(() => {
    loadTasks().catch((e: any) => message.error(e.message || '加载品类失败'))
  }, [loadTasks, message])

  const loadReport = useCallback(
    async (b?: string) => {
      if (!b) {
        setReportMd('')
        setReportError('')
        return
      }
      setReportLoading(true)
      setReportError('')
      try {
        const r = await api.analysisReport(b)
        setReportMd(r.markdown)
      } catch (e: any) {
        setReportMd('')
        setReportError(e.message || '生成报告失败')
      } finally {
        setReportLoading(false)
      }
    },
    [],
  )

  useEffect(() => {
    loadReport(batch)
  }, [batch, loadReport])

  // 采集结束 → 自动切到数据、刷新、再生成报告
  const onCollected = useCallback(
    async (b: string, collectedTaskId?: string) => {
      try {
        const kept = await loadTasks({ taskId: collectedTaskId, batch: b })
        setReloadKey((k) => k + 1)
        if (kept.batch) {
          await loadReport(kept.batch)
          setStage('data')
          message.success('采集完成，已生成单批次报告')
        }
      } catch {
        /* ignore */
      }
    },
    [loadTasks, loadReport, message],
  )

  const deleteTask = async () => {
    if (!taskId) return
    try {
      await api.deleteTask(taskId)
      message.success('品类已删除')
      await loadTasks()
    } catch (e: any) {
      message.error(e.message || '删除品类失败')
    }
  }

  const deleteBatch = async () => {
    if (!taskId || !batch) return
    try {
      await api.deleteBatch(taskId, batch)
      message.success('批次已删除')
      await loadTasks({ taskId })
    } catch (e: any) {
      message.error(e.message || '删除批次失败')
    }
  }

  // 「只看有 AI 分析」过滤：只保留有 AI 解读报告的批次
  const batchOptions = tasks.flatMap((t) =>
    (t.batches || [])
      .filter((b) => !onlyAi || (t.ai_batches || []).includes(b))
      .map((b) => ({ value: `${t.task_id}::${b}`, label: `${t.name} · ${b}` })),
  )

  const currentValue = taskId && batch ? `${taskId}::${batch}` : undefined
  const currentValid =
    !onlyAi || batchOptions.some((o) => o.value === currentValue)

  return (
    <div className="workbench">
      <Card
        size="small"
        className="workbench-bar"
        title="当前批次"
        extra={
          <Space wrap>
            <Select
              style={{ width: 240 }}
              placeholder="选择品类 · 批次"
              value={currentValid ? currentValue : undefined}
              onChange={(v: string) => {
                const [tid, b] = v.split('::')
                setTaskId(tid)
                setBatch(b)
              }}
              showSearch
              optionFilterProp="label"
              options={batchOptions}
              notFoundContent={onlyAi ? '暂无带 AI 分析的批次' : undefined}
            />
            <Checkbox
              checked={onlyAi}
              onChange={(e) => setOnlyAi(e.target.checked)}
            >
              只看有 AI 分析
            </Checkbox>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                loadTasks({ taskId, batch })
                setReloadKey((k) => k + 1)
                loadReport(batch)
              }}
            >
              刷新
            </Button>
          </Space>
        }
      >
        <Tabs
          activeKey={stage}
          onChange={(k) => setStage(k as typeof stage)}
          items={[
            {
              key: 'collect',
              forceRender: true,
              label: (
                <span>
                  <ExperimentOutlined /> 1 · 采集
                </span>
              ),
              children: (
                <CollectPanel onCollected={onCollected} hideReportAlert />
              ),
            },
            {
              key: 'data',
              forceRender: true,
              label: (
                <span>
                  <TableOutlined /> 2 · 数据
                </span>
              ),
              children: (
                <DataPanel
                  taskId={taskId}
                  batch={batch}
                  onlyDetail={onlyDetail}
                  onOnlyDetailChange={setOnlyDetail}
                  reloadKey={reloadKey}
                  onDeleteTask={deleteTask}
                  onDeleteBatch={deleteBatch}
                />
              ),
            },
            {
              key: 'analysis',
              forceRender: true,
              label: (
                <span>
                  <BarChartOutlined /> 3 · 分析
                </span>
              ),
              children: (
                <>
                  <AnalysisPanel
                    tasks={tasks}
                    taskId={taskId}
                    batch={batch}
                    showSelector={false}
                    reloadKey={reloadKey}
                    title="批次分析"
                  />
                  {/* 报告模块融合进分析：预览导出效果（已保存的 AI 解读自动合并进报告） */}
                  <Card
                    size="small"
                    style={{ marginTop: 16 }}
                    title={
                      <span>
                        <FileTextOutlined /> 单批次报告预览{batch ? ` · ${batch}` : ''}
                      </span>
                    }
                    extra={
                      <Space>
                        <Button
                          icon={<ReloadOutlined />}
                          onClick={() => loadReport(batch)}
                          disabled={!batch}
                        >
                          重新生成
                        </Button>
                        <Button
                          type="primary"
                          icon={<DownloadOutlined />}
                          disabled={!batch || !reportMd}
                          href={
                            batch
                              ? `/api/analysis/report/export?batch=${encodeURIComponent(batch)}`
                              : undefined
                          }
                        >
                          导出 Markdown
                        </Button>
                      </Space>
                    }
                  >
                    {!batch && (
                      <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description="还没有批次，先去「1 · 采集」跑一次"
                      />
                    )}
                    {batch && reportLoading && (
                      <div style={{ padding: 32, textAlign: 'center' }}>
                        <Spin />
                      </div>
                    )}
                    {batch && !reportLoading && reportError && (
                      <Alert type="warning" showIcon message={reportError} />
                    )}
                    {batch && !reportLoading && reportMd && (
                      <div className="expert-report">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{reportMd}</ReactMarkdown>
                      </div>
                    )}
                    {batch && !reportLoading && !reportMd && !reportError && (
                      <div className="hint">报告为空：该批次没有可统计的数据。</div>
                    )}
                    <div className="hint" style={{ marginTop: 8 }}>
                      导出的 Markdown 会自动合并各平台已保存的 AI 解读（没有就不加）。
                    </div>
                  </Card>
                </>
              ),
            },
          ]}
        />
      </Card>
    </div>
  )
}
