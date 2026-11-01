import { Alert, Modal, Progress, Space, Tag, Typography } from 'antd'
import type { JobState, ReportMeta } from '../types'

const PLATFORM_LABELS: Record<string, string> = {
  tmall: '天猫',
  '1688': '1688',
  pdd: '拼多多',
}

const STATUS_TEXT: Record<string, string> = {
  running: '采集中',
  waiting: '等待',
  paused_captcha: '待人工处理',
  auth_required: '等待登录',
  cancelling: '正在结束',
  cancelled: '已取消',
  done: '已完成',
  failed: '失败',
  timeout: '超时',
}

const STATUS_COLOR: Record<string, string> = {
  running: 'processing',
  waiting: 'default',
  paused_captcha: 'warning',
  auth_required: 'warning',
  cancelling: 'warning',
  cancelled: 'default',
  done: 'success',
  failed: 'error',
  timeout: 'warning',
}

export function explainError(raw?: string | null): { reason: string; advice: string } | null {
  if (!raw) return null
  const msg = String(raw)
  const hit = (patterns: string[]) => patterns.some((p) => msg.includes(p))
  if (hit(['任务不存在'])) {
    return {
      reason: '服务端找不到这次采集的任务配置（常见于旧版本没有先把任务写入数据库）。',
      advice: '重启服务后重新点一次「开始采集」；如果仍出现，请把 data/logs 下对应日志发开发者。',
    }
  }
  if (hit(['worker 提前退出', 'worker 异常退出'])) {
    return {
      reason: '采集子进程异常退出，通常是启动浏览器失败、任务配置缺失或日志编码错误。',
      advice: '先关闭所有自动化浏览器窗口并重启服务；失败平台可单独重跑。日志路径见下方错误信息。',
    }
  }
  if (hit(['登录', 'auth_required', '未授权', 'cookie2', 'unb'])) {
    return {
      reason: '平台登录态缺失或已过期，采集不会在未登录状态下继续。',
      advice: '点对应平台的「重新登录」完成登录；天猫需要完成扫码后的全部确认，拼多多是手机号验证码。',
    }
  }
  if (hit(['验证码', '滑块', '访问异常', '访问受限', '安全验证', '风控', '系统检测'])) {
    return {
      reason: '平台触发了风控/验证码，当前账号或 IP 被临时限制。',
      advice: '若采集窗口里弹出滑块验证，请直接在窗口内拖动完成（程序会等待最多 3 分钟并自动继续）；超时后再冷却一段时间并降低页数/详情数重试。',
    }
  }
  if (hit(['解析到 0 个商品', '0 解析', '页面改版', 'EmptyResult'])) {
    return {
      reason: '页面结构变化、未登录，或平台返回了验证/APP 引导页，导致没有解析到商品。',
      advice: '先手动在采集窗口打开搜索页确认正常；把 data/debug 下的 HTML/截图和对应平台日志发开发者校准选择器。',
    }
  }
  if (hit(['TargetClosedError', 'context closed', '浏览器', 'BrowserContext'])) {
    return {
      reason: '采集浏览器被提前关闭，或残留的 Edge 进程占用了自动化 profile。',
      advice: '关闭所有自动化浏览器窗口（不要关日常浏览器），重启服务后重试；仍失败可把 browser.channel 临时改为空。',
    }
  }
  if (hit(['单批上限', '冷却', '续采'])) {
    return {
      reason: '拼多多网页模式限制单批详情数量（≤10 个），本批已暂停等待冷却。',
      advice: '等待约 10 分钟后，点采集页的「继续采集」续采剩余候选；不要立即重新开始新批次。',
    }
  }
  if (hit(['前往App', '前往APP', '降级', 'PddDegraded'])) {
    return {
      reason: '拼多多详情页出现 App 降级提示，关键数据不再返回。',
      advice: '立即停止该平台，人工在浏览器打开确认；冷却一段时间后再续采，不要连续重试。',
    }
  }
  if (hit(['超时', 'timeout', 'Timeout', '网络', 'connection', 'Connection'])) {
    return {
      reason: '网络请求超时或平台响应过慢。',
      advice: '检查网络后重试；高峰期可降低页数/详情数，或稍后再采。',
    }
  }
  return {
    reason: '采集过程中出现异常。',
    advice: '查看本次平台日志（data/logs/批次_平台.log）；把完整错误发给开发者可快速定位。',
  }
}

interface Props {
  open: boolean
  job: JobState | null
  report?: ReportMeta | null
  onCloseRequest: () => void
}

export default function RunModal({ open, job, report, onCloseRequest }: Props) {
  if (!job) return null
  const running = ['running', 'waiting', 'auth_required', 'cancelling', 'paused_captcha'].includes(
    job.status,
  )
  const hint = explainError(job.error || (job.status === 'failed' ? job.message : null))
  const statuses = job.platforms_status || []

  return (
    <Modal
      open={open}
      footer={null}
      closable
      maskClosable={false}
      keyboard={false}
      onCancel={onCloseRequest}
      width={620}
      title={null}
    >
      <div className="run-modal">
        <div className="cube-stage" aria-hidden>
          <div className="cube">
            <div className="cube-face cube-front" />
            <div className="cube-face cube-back" />
            <div className="cube-face cube-right" />
            <div className="cube-face cube-left" />
            <div className="cube-face cube-top" />
            <div className="cube-face cube-bottom" />
          </div>
        </div>
        <div style={{ textAlign: 'center', marginBottom: 12 }}>
          <Space size={8}>
            <Tag color={STATUS_COLOR[job.status] || 'default'}>
              {STATUS_TEXT[job.status] || job.status}
            </Tag>
            <Typography.Text type="secondary">
              {job.message || (running ? '正在采集…' : '任务已结束')}
            </Typography.Text>
          </Space>
          <div className="hint" style={{ marginTop: 6 }}>
            关键词：{job.task_name?.split(' · ')[0] || '—'}　批次：{job.batch || '—'}
          </div>
        </div>

        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          {statuses.length > 0 ? (
            statuses.map((s) => {
              const listPct = s.pages ? Math.min(100, Math.round((s.page_done / s.pages) * 100)) : 0
              const detailTarget = s.detail_total || 0
              const detailPct = detailTarget
                ? Math.min(100, Math.round((s.detail_done / detailTarget) * 100))
                : s.status === 'done'
                  ? 100
                  : 0
              return (
                <div key={s.task_id}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <Space size={6}>
                      <Tag color={STATUS_COLOR[s.status] || 'default'}>
                        {PLATFORM_LABELS[s.platform] || s.platform}
                      </Tag>
                      <span className="hint">{STATUS_TEXT[s.status] || s.status}</span>
                      <span className="hint">
                        列表 {s.page_done}/{s.pages} · 详情 {s.detail_done}/{detailTarget}
                      </span>
                    </Space>
                    <span className="hint">候选 {s.items_count}</span>
                  </div>
                  <Progress percent={listPct} size="small" showInfo={false} />
                  <Progress
                    percent={detailPct}
                    size="small"
                    showInfo={false}
                    status={s.status === 'failed' ? 'exception' : undefined}
                  />
                  {s.error && (
                    <div className="hint" style={{ color: '#cf1322' }}>
                      {s.error}
                    </div>
                  )}
                </div>
              )
            })
          ) : (
            <Progress percent={0} size="small" />
          )}
        </Space>

        {hint && (
          <Alert
            style={{ marginTop: 12 }}
            type={job.status === 'done' ? 'success' : 'error'}
            showIcon
            message={hint.reason}
            description={hint.advice}
          />
        )}

        {job.status === 'done' && report && (
          <Alert
            style={{ marginTop: 12 }}
            type="success"
            showIcon
            message="本次跨平台报告已生成"
            description={`${report.keyword} · ${report.platforms
              .map((p) => `${p.label} ${p.items} 条`)
              .join(' + ')}。关闭窗口后可在采集页下载。`}
          />
        )}

        <div className="hint" style={{ marginTop: 12, textAlign: 'center' }}>
          {running
            ? '点击右上角 × 可立即结束采集（会关闭所有采集进程），并询问是否保存本次结果'
            : '点击右上角 × 关闭窗口'}
        </div>
      </div>
    </Modal>
  )
}