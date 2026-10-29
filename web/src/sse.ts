import { useEffect, useRef } from 'react'

/** 订阅后端 SSE（/api/events），组件卸载时自动关闭。 */
export function useSse(onEvent: (data: any) => void) {
  const handler = useRef(onEvent)
  handler.current = onEvent

  useEffect(() => {
    const es = new EventSource('/api/events')
    es.onmessage = (ev) => {
      try {
        handler.current(JSON.parse(ev.data))
      } catch {
        /* 忽略心跳等非 JSON 消息 */
      }
    }
    return () => es.close()
  }, [])
}