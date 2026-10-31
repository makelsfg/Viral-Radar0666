import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Descriptions,
  Drawer,
  Image,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import { DeleteOutlined, DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useSse } from '../sse'
import type { ProductDetail, ProductRow, TaskInfo } from '../types'

export default function DataPanel() {
  const { message } = AntApp.useApp()
  const [tasks, setTasks] = useState<TaskInfo[]>([])
  const [taskId, setTaskId] = useState<string | undefined>()
  const [batch, setBatch] = useState<string | undefined>()
  const [rows, setRows] = useState<ProductRow[]>([])
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<ProductDetail | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [onlyDetail, setOnlyDetail] = useState(true)
  const currentTaskRef = useRef<string | undefined>(undefined)
  currentTaskRef.current = taskId

  const loadTasks = useCallback(async (keep?: { taskId?: string; batch?: string }) => {
    const list = await api.tasks()
    setTasks(list)
    const wantedTask =
      keep?.taskId && list.some((t) => t.task_id === keep.taskId) ? keep.taskId : list[0]?.task_id
    setTaskId(wantedTask)
    const task = list.find((t) => t.task_id === wantedTask)
    const wantedBatch =
      keep?.batch && task?.batches.includes(keep.batch) ? keep.batch : task?.batches[0]
    setBatch(wantedBatch)
  }, [])

  const loadRows = useCallback(
    async (t?: string, b?: string, detailOnly = true) => {
      if (!t || !b) {
        setRows([])
        return
      }
      setLoading(true)
      try {
        setRows(await api.products(t, b, detailOnly))
      } catch (e: any) {
        message.error(e.message || '加载数据失败')
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
    loadRows(taskId, batch, onlyDetail)
  }, [taskId, batch, onlyDetail, loadRows])

  useSse((e: any) => {
    if (e?.type !== 'job' || e.job?.type !== 'collect' || e.job.status !== 'done') return
    const currentTask = currentTaskRef.current
    loadTasks({ taskId: currentTask, batch: e.job.batch })
      .then(() => loadRows(currentTask, e.job.batch, onlyDetail))
      .catch(() => undefined)
  })

  const openDetail = async (row: ProductRow) => {
    try {
      setDetail(await api.product(row.item_id, row.platform))
      setDrawerOpen(true)
    } catch (e: any) {
      message.error(e.message || '加载详情失败')
    }
  }

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

  const columns: TableProps<ProductRow>['columns'] = [
    {
      title: '主图',
      dataIndex: 'image_url',
      width: 64,
      render: (url: string | null) =>
        url ? (
          <Image
            src={url}
            width={48}
            height={48}
            preview={false}
            style={{ objectFit: 'cover', borderRadius: 6 }}
          />
        ) : (
          '—'
        ),
    },
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
      render: (t: string | null) => t || '—',
    },
    {
      title: '到手价',
      dataIndex: 'price_final',
      width: 92,
      sorter: (a, b) => (a.price_final ?? 0) - (b.price_final ?? 0),
      render: (v: number | null) => (v == null ? '—' : `¥${v.toFixed(2)}`),
    },
    {
      title: '已售',
      dataIndex: 'sold_text',
      width: 108,
      defaultSortOrder: 'descend',
      sorter: (a, b) => (a.sold_lb ?? -1) - (b.sold_lb ?? -1),
      render: (t: string | null) => t || '—',
    },
    { title: '加购', dataIndex: 'cart_text', width: 118, render: (t: string | null) => t || '—' },
    {
      title: '店铺',
      dataIndex: 'shop_name',
      width: 150,
      ellipsis: true,
      render: (t: string | null) => t || '—',
    },
    {
      title: '评分',
      dataIndex: 'shop_rating',
      width: 70,
      render: (v: number | null) => (v == null ? '—' : v.toFixed(1)),
    },
    {
      title: '好评率',
      dataIndex: 'positive_rate',
      width: 78,
      render: (t: string | null) => t || '—',
    },
    { title: 'SKU', dataIndex: 'sku_count', width: 60 },
    {
      title: '链接',
      dataIndex: 'product_url',
      width: 64,
      render: (u: string | null) =>
        u ? (
          <a href={u} target="_blank" rel="noreferrer" onClick={(ev) => ev.stopPropagation()}>
            打开
          </a>
        ) : (
          '—'
        ),
    },
  ]

  const current = tasks.find((t) => t.task_id === taskId)

  return (
    <Card
      title="采集结果"
      extra={
        <Space wrap>
          <Select
            style={{ width: 200 }}
            placeholder="选择品类"
            value={taskId}
            onChange={(v) => {
              setTaskId(v)
              const t = tasks.find((x) => x.task_id === v)
              setBatch(t?.batches[0])
            }}
            options={tasks.map((t) => ({ value: t.task_id, label: t.name }))}
          />
          <Popconfirm
            title="删除该品类？"
            description="将删除该品类及其所有批次数据，且不可恢复。"
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={deleteTask}
            disabled={!taskId}
          >
            <Tooltip title="删除当前品类">
              <Button danger type="text" icon={<DeleteOutlined />} disabled={!taskId} />
            </Tooltip>
          </Popconfirm>
          <Select
            style={{ width: 200 }}
            placeholder="选择批次"
            value={batch}
            onChange={setBatch}
            options={(current?.batches || []).map((b) => ({ value: b, label: b }))}
          />
          <Popconfirm
            title="删除该批次？"
            description="将删除该批次的采集结果，且不可恢复。"
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={deleteBatch}
            disabled={!batch}
          >
            <Tooltip title="删除当前批次">
              <Button danger type="text" icon={<DeleteOutlined />} disabled={!batch} />
            </Tooltip>
          </Popconfirm>
          <Switch
            checked={onlyDetail}
            onChange={setOnlyDetail}
            checkedChildren="只看已采详情"
            unCheckedChildren="含未采候选"
          />
          <Button icon={<ReloadOutlined />} onClick={() => loadRows(taskId, batch, onlyDetail)}>
            刷新
          </Button>
          <Button
            type="primary"
            ghost
            icon={<DownloadOutlined />}
            disabled={!taskId || !batch}
            href={
              taskId && batch
                ? `/api/export?task_id=${encodeURIComponent(taskId)}&batch=${encodeURIComponent(
                    batch,
                  )}&only_detail=${onlyDetail ? 1 : 0}`
                : undefined
            }
          >
            导出 CSV
          </Button>
        </Space>
      }
    >
      {tasks.length === 0 && (
        <Alert
          type="info"
          showIcon
          message="还没有采集数据"
          description="切换到「采集」页，输入商品关键词并开始采集；完成后数据会出现在这里。"
        />
      )}
      {tasks.length > 0 && rows.length === 0 && !loading && (
        <Alert
          type="info"
          showIcon
          message="当前批次还没有采到详情"
          description="可以切换批次，或关掉「只看已采详情」查看候选商品。"
          action={
            onlyDetail ? (
              <Button size="small" onClick={() => setOnlyDetail(false)}>
                显示候选商品
              </Button>
            ) : undefined
          }
        />
      )}

      {tasks.length > 0 && (
        <Table<ProductRow>
          rowKey="item_id"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          scroll={{ x: 1000 }}
          onRow={(record) => ({
            onClick: () => openDetail(record),
            style: { cursor: 'pointer' },
          })}
        />
      )}

      <Drawer
        width={560}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={detail?.title || '商品详情'}
      >
        {detail && (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            {detail.image_url && <Image src={detail.image_url} width={180} />}
            <Descriptions column={1} size="small">
              <Descriptions.Item label="店铺">{detail.shop_name || '—'}</Descriptions.Item>
              <Descriptions.Item label="评分">{detail.shop_rating ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="好评率">{detail.positive_rate || '—'}</Descriptions.Item>
              <Descriptions.Item label="链接">
                {detail.product_url ? (
                  <a href={detail.product_url} target="_blank" rel="noreferrer">
                    打开天猫商品页
                  </a>
                ) : (
                  '—'
                )}
              </Descriptions.Item>
            </Descriptions>

            <Typography.Title level={5} style={{ marginBottom: 0 }}>
              参数
            </Typography.Title>
            {Object.keys(detail.params || {}).length > 0 ? (
              <Descriptions column={1} size="small" bordered>
                {Object.entries(detail.params).map(([k, v]) => (
                  <Descriptions.Item key={k} label={k}>
                    {String(v)}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            ) : (
              <div className="hint">暂无参数（详情页未采到或解析失败，可查 data/raw/）</div>
            )}

            <Typography.Title level={5} style={{ marginBottom: 0 }}>
              SKU
            </Typography.Title>
            <div>
              {(detail.skus || []).map((s: string) => (
                <Tag key={s} style={{ marginBottom: 6 }}>
                  {s}
                </Tag>
              ))}
              {(detail.skus || []).length === 0 && <span className="hint">暂无 SKU</span>}
            </div>

            <Typography.Title level={5} style={{ marginBottom: 0 }}>
              历史批次
            </Typography.Title>
            <Table
              size="small"
              rowKey="batch"
              pagination={false}
              dataSource={detail.history || []}
              columns={[
                { title: '批次', dataIndex: 'batch' },
                {
                  title: '到手价',
                  dataIndex: 'price_final',
                  render: (v: number | null) => (v == null ? '—' : `¥${v.toFixed(2)}`),
                },
                { title: '已售', dataIndex: 'sold_lb' },
                { title: '加购', dataIndex: 'cart_lb' },
              ]}
            />
          </Space>
        )}
      </Drawer>
    </Card>
  )
}