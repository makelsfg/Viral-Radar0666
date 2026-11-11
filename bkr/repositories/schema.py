"""数据访问层：表结构定义（SQLite / MySQL 两套 DDL）。"""
from __future__ import annotations

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS dim_task (
  task_id       TEXT PRIMARY KEY,
  platform      TEXT NOT NULL DEFAULT 'tmall',
  name          TEXT NOT NULL,
  keywords      TEXT NOT NULL,
  sort          TEXT DEFAULT 'sale-desc',
  pages         INTEGER DEFAULT 2,
  detail_top    INTEGER DEFAULT 20,
  detail_order  TEXT DEFAULT 'rank',
  pace          TEXT DEFAULT 'safe',
  price_min     REAL,
  price_max     REAL,
  exclude_words TEXT,
  enabled       INTEGER DEFAULT 1,
  created_at    TEXT
);

CREATE TABLE IF NOT EXISTS product (
  platform      TEXT NOT NULL DEFAULT 'tmall',
  item_id       TEXT NOT NULL,
  title         TEXT,
  shop_name     TEXT,
  shop_rating   REAL,
  positive_rate TEXT,
  sku_names     TEXT,
  params_json   TEXT,
  image_url     TEXT,
  video_url     TEXT,
  product_url   TEXT,
  first_seen_at TEXT,
  updated_at    TEXT,
  PRIMARY KEY (platform, item_id)
);

CREATE TABLE IF NOT EXISTS fact_snapshot (
  snapshot_id  INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id      TEXT NOT NULL,
  platform     TEXT NOT NULL DEFAULT 'tmall',
  item_id      TEXT NOT NULL,
  batch        TEXT NOT NULL,
  captured_at  TEXT NOT NULL,
  rank         INTEGER,
  price_final  REAL,
  price_text   TEXT,
  sold_text    TEXT,
  sold_lb      INTEGER,
  cart_text    TEXT,
  cart_lb      INTEGER,
  title        TEXT,
  image_url    TEXT,
  shop_name    TEXT,
  page_no      INTEGER,
  is_detail    INTEGER DEFAULT 0,
  UNIQUE (task_id, item_id, batch)
);
CREATE INDEX IF NOT EXISTS idx_snap_task_batch ON fact_snapshot (task_id, batch);
CREATE INDEX IF NOT EXISTS idx_snap_item ON fact_snapshot (item_id);

CREATE TABLE IF NOT EXISTS batch_run (
  batch        TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  status       TEXT NOT NULL,
  stage        TEXT,
  page_done    INTEGER DEFAULT 0,
  detail_done  INTEGER DEFAULT 0,
  items_count  INTEGER DEFAULT 0,
  captcha_hits INTEGER DEFAULT 0,
  config_json  TEXT,
  error        TEXT,
  started_at   TEXT,
  finished_at  TEXT,
  PRIMARY KEY (batch, task_id)
);

CREATE TABLE IF NOT EXISTS platform_auth (
  platform    TEXT PRIMARY KEY,
  authorized  INTEGER NOT NULL DEFAULT 0,
  updated_at  TEXT NOT NULL,
  note        TEXT
);

CREATE TABLE IF NOT EXISTS ai_insight (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    TEXT,
  batch      TEXT NOT NULL,
  kind       TEXT NOT NULL DEFAULT 'task',
  content_md TEXT NOT NULL,
  model      TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expert_run (
  id              TEXT PRIMARY KEY,
  title           TEXT NOT NULL,
  keyword         TEXT,
  sources_json    TEXT NOT NULL,
  status          TEXT NOT NULL,
  max_rounds      INTEGER NOT NULL DEFAULT 3,
  pass_score      INTEGER NOT NULL DEFAULT 85,
  iterations      INTEGER DEFAULT 0,
  final_report_md TEXT,
  evidence_json   TEXT,
  error           TEXT,
  created_at      TEXT,
  finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS expert_round (
  run_id      TEXT NOT NULL,
  round_no    INTEGER NOT NULL,
  stage       TEXT NOT NULL,
  content_md  TEXT,
  review_json TEXT,
  verdict     TEXT,
  score       INTEGER,
  created_at  TEXT,
  PRIMARY KEY (run_id, round_no, stage)
);

CREATE TABLE IF NOT EXISTS agent_run (
  run_id      TEXT PRIMARY KEY,
  batch       TEXT,
  platform    TEXT,
  task_id     TEXT,
  status      TEXT,
  steps       INTEGER DEFAULT 0,
  llm_calls   INTEGER DEFAULT 0,
  summary     TEXT,
  started_at  TEXT,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_step (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT NOT NULL,
  idx         INTEGER,
  state_hash  TEXT,
  state_json  TEXT,
  tool        TEXT,
  args_json   TEXT,
  result_json TEXT,
  thought     TEXT,
  created_at  TEXT
);

CREATE TABLE IF NOT EXISTS analysis_profile (
  keyword      TEXT PRIMARY KEY,
  profile_json TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT 'llm',
  model        TEXT,
  hit_rate     REAL,
  created_at   TEXT,
  updated_at   TEXT
);
"""

SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS dim_task (
  task_id       VARCHAR(64)  NOT NULL,
  platform      VARCHAR(32)  NOT NULL DEFAULT 'tmall',
  name          VARCHAR(255) NOT NULL,
  keywords      TEXT         NOT NULL,
  sort          VARCHAR(32)  DEFAULT 'sale-desc',
  pages         INT          DEFAULT 2,
  detail_top    INT          DEFAULT 20,
  detail_order  VARCHAR(32)  DEFAULT 'rank',
  pace          VARCHAR(32)  DEFAULT 'safe',
  price_min     DOUBLE,
  price_max     DOUBLE,
  exclude_words TEXT,
  enabled       TINYINT      DEFAULT 1,
  created_at    DATETIME,
  PRIMARY KEY (task_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS product (
  platform      VARCHAR(32)  NOT NULL DEFAULT 'tmall',
  item_id       VARCHAR(64)  NOT NULL,
  title         TEXT,
  shop_name     VARCHAR(255),
  shop_rating   DOUBLE,
  positive_rate VARCHAR(64),
  sku_names     TEXT,
  params_json   LONGTEXT,
  image_url     TEXT,
  video_url     TEXT,
  product_url   TEXT,
  first_seen_at DATETIME,
  updated_at    DATETIME,
  PRIMARY KEY (platform, item_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS fact_snapshot (
  snapshot_id  INT AUTO_INCREMENT PRIMARY KEY,
  task_id      VARCHAR(64) NOT NULL,
  platform     VARCHAR(32) NOT NULL DEFAULT 'tmall',
  item_id      VARCHAR(64) NOT NULL,
  batch        VARCHAR(64) NOT NULL,
  captured_at  DATETIME    NOT NULL,
  `rank`       INT,
  price_final  DOUBLE,
  price_text   VARCHAR(128),
  sold_text    VARCHAR(128),
  sold_lb      INT,
  cart_text    VARCHAR(128),
  cart_lb      INT,
  title        TEXT,
  image_url    TEXT,
  shop_name    VARCHAR(255),
  page_no      INT,
  is_detail    TINYINT     DEFAULT 0,
  UNIQUE KEY uk_snapshot (task_id, item_id, batch),
  KEY idx_snap_task_batch (task_id, batch),
  KEY idx_snap_item (item_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS batch_run (
  batch           VARCHAR(64) NOT NULL,
  task_id         VARCHAR(64) NOT NULL,
  status          VARCHAR(32) NOT NULL,
  stage           VARCHAR(32),
  page_done       INT DEFAULT 0,
  detail_done     INT DEFAULT 0,
  items_count     INT DEFAULT 0,
  captcha_hits    INT DEFAULT 0,
  config_json     LONGTEXT,
  error           TEXT,
  agent_recovered TINYINT DEFAULT 0,
  started_at      DATETIME,
  finished_at     DATETIME,
  PRIMARY KEY (batch, task_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS platform_auth (
  platform   VARCHAR(32) NOT NULL,
  authorized TINYINT NOT NULL DEFAULT 0,
  updated_at DATETIME NOT NULL,
  note       VARCHAR(255),
  PRIMARY KEY (platform)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ai_insight (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  task_id    VARCHAR(64),
  batch      VARCHAR(64) NOT NULL,
  kind       VARCHAR(16) NOT NULL DEFAULT 'task',
  content_md LONGTEXT NOT NULL,
  model      VARCHAR(64),
  created_at DATETIME NOT NULL,
  KEY idx_ai_batch (batch)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS expert_run (
  id              VARCHAR(64) NOT NULL,
  title           VARCHAR(255) NOT NULL,
  keyword         VARCHAR(128),
  sources_json    LONGTEXT NOT NULL,
  status          VARCHAR(32) NOT NULL,
  max_rounds      INT NOT NULL DEFAULT 3,
  pass_score      INT NOT NULL DEFAULT 85,
  iterations      INT DEFAULT 0,
  final_report_md LONGTEXT,
  evidence_json   LONGTEXT,
  error           TEXT,
  created_at      DATETIME,
  finished_at     DATETIME,
  PRIMARY KEY (id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS expert_round (
  run_id      VARCHAR(64) NOT NULL,
  round_no    INT NOT NULL,
  stage       VARCHAR(32) NOT NULL,
  content_md  LONGTEXT,
  review_json LONGTEXT,
  verdict     VARCHAR(32),
  score       INT,
  created_at  DATETIME,
  PRIMARY KEY (run_id, round_no, stage)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS agent_run (
  run_id      VARCHAR(64) NOT NULL,
  batch       VARCHAR(64),
  platform    VARCHAR(32),
  task_id     VARCHAR(64),
  status      VARCHAR(32),
  steps       INT DEFAULT 0,
  llm_calls   INT DEFAULT 0,
  summary     TEXT,
  started_at  DATETIME,
  finished_at DATETIME,
  PRIMARY KEY (run_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS agent_step (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  run_id      VARCHAR(64) NOT NULL,
  idx         INT,
  state_hash  VARCHAR(64),
  state_json  LONGTEXT,
  tool        VARCHAR(64),
  args_json   LONGTEXT,
  result_json LONGTEXT,
  thought     TEXT,
  created_at  DATETIME,
  KEY idx_step_run (run_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS analysis_profile (
  keyword      VARCHAR(128) NOT NULL,
  profile_json LONGTEXT NOT NULL,
  source       VARCHAR(32) NOT NULL DEFAULT 'llm',
  model        VARCHAR(64),
  hit_rate     DOUBLE,
  created_at   DATETIME,
  updated_at   DATETIME,
  PRIMARY KEY (keyword)
) ENGINE=InnoDB;
"""
