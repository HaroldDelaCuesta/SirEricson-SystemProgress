-- FRESH INSTALL. Import in phpMyAdmin (Import tab).
-- WARNING: the next line wipes any old subdivision_db. If you already have data you want to keep,
-- do NOT import this file; run upgrade_v3.sql instead.
DROP DATABASE IF EXISTS subdivision_db;
CREATE DATABASE subdivision_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE subdivision_db;

CREATE TABLE users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  full_name     VARCHAR(120) NOT NULL,
  username      VARCHAR(40)  NOT NULL UNIQUE,
  email         VARCHAR(120) NOT NULL UNIQUE,
  phone         VARCHAR(30)  DEFAULT NULL,
  address       VARCHAR(160) DEFAULT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role          ENUM('sysadmin','subadmin','security','maintenance','resident') NOT NULL,
  status        ENUM('pending','active','inactive') NOT NULL DEFAULT 'pending',
  must_change_password TINYINT(1) NOT NULL DEFAULT 0,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_login    TIMESTAMP NULL DEFAULT NULL
) ENGINE=InnoDB;

CREATE TABLE settings (
  key_name VARCHAR(60) PRIMARY KEY,
  value    VARCHAR(255) NOT NULL
) ENGINE=InnoDB;
INSERT INTO settings (key_name, value) VALUES ('allow_registration','1'), ('site_name','Subdivision Portal');

CREATE TABLE system_errors (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT DEFAULT NULL,
  path       VARCHAR(255),
  message    TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------- security ----------
CREATE TABLE user_sessions (
  token      VARCHAR(64) PRIMARY KEY,
  user_id    INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ip         VARCHAR(45),
  user_agent VARCHAR(255),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE login_attempts (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  identifier VARCHAR(120) NOT NULL,
  ip         VARCHAR(45) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (identifier, created_at),
  INDEX (ip, created_at)
) ENGINE=InnoDB;

CREATE TABLE login_history (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT DEFAULT NULL,
  identifier VARCHAR(120) NOT NULL,
  success    TINYINT(1) NOT NULL,
  reason     VARCHAR(40) NOT NULL,
  ip         VARCHAR(45),
  user_agent VARCHAR(255),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (user_id, created_at),
  INDEX (created_at)
) ENGINE=InnoDB;

CREATE TABLE audit_log (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT DEFAULT NULL,
  action     VARCHAR(60) NOT NULL,
  detail     VARCHAR(255) DEFAULT NULL,
  ip         VARCHAR(45),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (created_at),
  INDEX (action, ip, created_at)
) ENGINE=InnoDB;

CREATE TABLE password_resets (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT NOT NULL,
  token_hash CHAR(64) NOT NULL UNIQUE,
  expires_at DATETIME NOT NULL,
  used_at    DATETIME DEFAULT NULL,
  ip         VARCHAR(45),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE notifications (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT NOT NULL,
  message    VARCHAR(255) NOT NULL,
  link       VARCHAR(255) DEFAULT NULL,
  is_read    TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (user_id, is_read, created_at),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- resident problem reports ----------
CREATE TABLE maintenance_requests (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  resident_id       INT NOT NULL,
  assigned_to       INT DEFAULT NULL,
  category          VARCHAR(60) NOT NULL DEFAULT 'Uncategorized',
  priority          ENUM('low','normal','high','urgent') NOT NULL DEFAULT 'normal',
  location          VARCHAR(160),
  description       TEXT NOT NULL,
  status            ENUM('pending','in_progress','completed') NOT NULL DEFAULT 'pending',
  work_notes        TEXT,
  verified          TINYINT(1) NOT NULL DEFAULT 0,
  due_date          DATE DEFAULT NULL,
  resident_response ENUM('none','confirmed','reopened') NOT NULL DEFAULT 'none',
  reopen_note       TEXT,
  rating            TINYINT DEFAULT NULL,
  feedback          TEXT,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  assigned_at       DATETIME DEFAULT NULL,
  completed_at      DATETIME DEFAULT NULL,
  updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX (status), INDEX (assigned_to, status), INDEX (resident_id),
  FOREIGN KEY (resident_id) REFERENCES users(id),
  FOREIGN KEY (assigned_to) REFERENCES users(id)
) ENGINE=InnoDB;

CREATE TABLE report_photos (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  request_id  INT NOT NULL,
  file        VARCHAR(64) NOT NULL UNIQUE,
  kind        ENUM('report','completion') NOT NULL,
  uploaded_by INT NOT NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (request_id) REFERENCES maintenance_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (uploaded_by) REFERENCES users(id)
) ENGINE=InnoDB;

CREATE TABLE report_history (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  request_id INT NOT NULL,
  actor_id   INT DEFAULT NULL,
  text       VARCHAR(500) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (request_id, created_at),
  FOREIGN KEY (request_id) REFERENCES maintenance_requests(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- lost and found ----------
CREATE TABLE lost_found (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  reported_by        INT NOT NULL,
  handled_by         INT DEFAULT NULL,
  kind               ENUM('lost','found') NOT NULL,
  item_name          VARCHAR(120) NOT NULL,
  description        TEXT,
  location           VARCHAR(160),
  finder_name        VARCHAR(120) DEFAULT NULL,
  photo              VARCHAR(64) DEFAULT NULL,
  status             ENUM('open','claimed','released','closed','removed') NOT NULL DEFAULT 'open',
  claimant_id        INT DEFAULT NULL,
  claim_details      TEXT,
  claimed_at         DATETIME DEFAULT NULL,
  verification_notes TEXT,
  id_checked         VARCHAR(80) DEFAULT NULL,
  released_to        VARCHAR(120) DEFAULT NULL,
  released_at        DATETIME DEFAULT NULL,
  created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX (kind, status), INDEX (claimant_id),
  FOREIGN KEY (reported_by) REFERENCES users(id),
  FOREIGN KEY (handled_by)  REFERENCES users(id)
) ENGINE=InnoDB;

-- ---------- announcements and activities ----------
CREATE TABLE announcements (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  author_id  INT NOT NULL,
  kind       ENUM('announcement','activity') NOT NULL DEFAULT 'announcement',
  title      VARCHAR(160) NOT NULL,
  body       TEXT NOT NULL,
  event_date DATE DEFAULT NULL,
  published  TINYINT(1) NOT NULL DEFAULT 0,
  pinned     TINYINT(1) NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (author_id) REFERENCES users(id)
) ENGINE=InnoDB;

-- ---------- security desk logs ----------
CREATE TABLE visitor_log (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  visitor_name  VARCHAR(120) NOT NULL,
  purpose       VARCHAR(160) NOT NULL,
  unit_visited  VARCHAR(160) NOT NULL,
  id_presented  VARCHAR(80) DEFAULT NULL,
  vehicle_plate VARCHAR(20) DEFAULT NULL,
  notes         TEXT,
  time_in       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  time_out      DATETIME DEFAULT NULL,
  recorded_by   INT NOT NULL,
  INDEX (time_in),
  FOREIGN KEY (recorded_by) REFERENCES users(id)
) ENGINE=InnoDB;

CREATE TABLE incident_log (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  category         VARCHAR(60) NOT NULL,
  severity         ENUM('low','medium','high') NOT NULL DEFAULT 'low',
  location         VARCHAR(160),
  description      TEXT NOT NULL,
  status           ENUM('open','resolved') NOT NULL DEFAULT 'open',
  resolution_notes TEXT,
  occurred_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  recorded_by      INT NOT NULL,
  resolved_by      INT DEFAULT NULL,
  resolved_at      DATETIME DEFAULT NULL,
  INDEX (occurred_at), INDEX (status),
  FOREIGN KEY (recorded_by) REFERENCES users(id)
) ENGINE=InnoDB;

CREATE TABLE vehicles (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  resident_id   INT NOT NULL,
  plate_number  VARCHAR(20) NOT NULL UNIQUE,
  make_model    VARCHAR(80),
  color         VARCHAR(30),
  sticker_no    VARCHAR(30),
  status        ENUM('active','expired','revoked') NOT NULL DEFAULT 'active',
  expires_on    DATE DEFAULT NULL,
  registered_by INT NOT NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (resident_id) REFERENCES users(id),
  FOREIGN KEY (registered_by) REFERENCES users(id)
) ENGINE=InnoDB;

CREATE TABLE shift_reports (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  security_id     INT NOT NULL,
  shift           ENUM('morning','afternoon','night') NOT NULL,
  shift_date      DATE NOT NULL,
  visitors_count  INT NOT NULL DEFAULT 0,
  incidents_count INT NOT NULL DEFAULT 0,
  summary         TEXT NOT NULL,
  handover_notes  TEXT,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (shift_date),
  FOREIGN KEY (security_id) REFERENCES users(id)
) ENGINE=InnoDB;

-- ---------- monthly dues ----------
CREATE TABLE dues (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  resident_id INT NOT NULL,
  period      CHAR(7) NOT NULL,                       -- 'YYYY-MM'
  amount      DECIMAL(10,2) NOT NULL,
  status      ENUM('unpaid','paid','waived') NOT NULL DEFAULT 'unpaid',
  paid_on     DATE DEFAULT NULL,
  method      VARCHAR(30) DEFAULT NULL,
  reference   VARCHAR(60) DEFAULT NULL,
  recorded_by INT DEFAULT NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY one_per_month (resident_id, period),
  FOREIGN KEY (resident_id) REFERENCES users(id)
) ENGINE=InnoDB;
