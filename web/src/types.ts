export interface TaskInfo {
  task_id: string
  name: string
  keywords: string
  pages: number
  detail_top: number
  batches: string[]
}

export interface ProductRow {
  item_id: string
  platform: string
  title: string | null
  image_url: string | null
  price_final: number | null
  sold_text: string | null
  sold_lb: number | null
  cart_text: string | null
  cart_lb: number | null
  shop_name: string | null
  shop_rating: number | null
  positive_rate: string | null
  rank: number | null
  captured_at: string | null
  product_url: string | null
  params: Record<string, string>
  skus: string[]
  sku_count: number
}

export interface ProductDetail {
  item_id: string
  platform?: string
  title: string | null
  image_url: string | null
  shop_name: string | null
  shop_rating: number | null
  positive_rate: string | null
  params: Record<string, string>
  skus: string[]
  product_url: string | null
  history: Array<{
    batch: string
    price_final: number | null
    sold_lb: number | null
    cart_lb: number | null
    captured_at: string | null
  }>
}

export interface AuthStatus {
  task_id: string
  name: string
  platform: string
  status: string
  reason?: string | null
}

export interface JobState {
  id: string
  type: 'collect' | 'login'
  phase?: 'auth' | 'collect' | 'login'
  auth_statuses?: AuthStatus[]
  status: string
  stage: string | null
  page_done: number
  pages: number
  items_count: number
  detail_done: number
  detail_total: number
  batch?: string
  task_id?: string
  task_name?: string
  task_platform?: string
  task_index?: number
  task_total?: number
  platforms?: string[]
  platforms_status?: PlatformRuntimeStatus[]
  message?: string | null
  error?: string | null
}

export interface SseEvent {
  type: 'job'
  job: JobState
}

export interface PlatformRuntimeStatus {
  task_id: string
  name: string
  platform: string
  status: string
  stage: string
  page_done: number
  pages: number
  items_count: number
  detail_done: number
  detail_total: number
  error?: string | null
}

export interface EnvStatus {
  logged_in: boolean
  busy: boolean
  pdd_api?: boolean
  profiles: { tmall: boolean; '1688': boolean; pdd: boolean }
}

export interface ReportMeta {
  batch: string
  keyword: string
  platforms: {
    task_id: string
    platform: string
    label: string
    status: string
    items: number
    details: number
  }[]
}

export interface ExpertSource {
  type: 'batch' | 'report' | 'note'
  task_id?: string
  batch?: string
  name?: string
  content?: string
}

export interface ExpertReviewIssue {
  severity?: string
  location?: string
  problem?: string
  fix?: string
  evidence_ids?: string[]
}

export interface ExpertReview {
  verdict?: 'pass' | 'revise'
  score?: number
  dimension_scores?: Record<string, number>
  issues?: ExpertReviewIssue[]
  summary?: string
}

export interface ExpertRound {
  round_no: number
  stage: 'draft' | 'review' | 'revise'
  content_md?: string | null
  review?: ExpertReview | null
  verdict?: string | null
  score?: number | null
  created_at?: string
}

export interface ExpertRun {
  id: string
  title: string
  keyword?: string
  status: string
  iterations?: number
  max_rounds?: number
  pass_score?: number
  final_report_md?: string | null
  error?: string | null
  created_at?: string
  finished_at?: string | null
  sources?: ExpertSource[]
  rounds?: ExpertRound[]
}

export interface HotwordKeyword {
  keyword: string
  last_at: string
  count: number
}

export interface HotwordRow {
  id: number
  keyword: string
  word: string
  rank: number
  created_at: string
}

export interface AnalysisBand {
  label: string
  count: number
  sold?: number
  min?: number
  max?: number
}

export interface AnalysisSummary {
  counts: {
    candidates: number
    detailed: number
    with_price: number
    with_price_detail?: number
    with_price_list?: number
    with_sold: number
  }
  price: {
    stats: {
      count: number
      min?: number
      p25?: number
      median?: number
      p75?: number
      max?: number
      avg?: number
    }
    bands: AnalysisBand[]
  }
  sold: { bands: AnalysisBand[]; median: number | null }
  shops: {
    shops: { name: string; count: number; sold: number }[]
    distinct: number
    top5_share: number
  }
  keywords: { word: string; count: number }[]
  params: { key: string; coverage: number; top: { value: string; count: number }[] }[]
  clusters: {
    cluster: string
    count: number
    avg_price: number | null
    avg_sold: number
    example: string
  }[]
  top_products: {
    item_id: string
    title: string | null
    price_final: number | null
    sold_text: string | null
    sold_lb: number | null
    cart_lb: number | null
    shop_name: string | null
    image_url: string | null
    product_url: string | null
    is_detail: number
  }[]
}