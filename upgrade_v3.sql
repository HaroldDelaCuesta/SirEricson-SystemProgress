-- UPGRADE for an existing install (keeps all your data).
-- phpMyAdmin: select subdivision_db > SQL tab > paste this whole file > Go.
-- Safe to run twice. Needs MariaDB (that is what XAMPP uses).
USE subdivision_db;

-- security tables from the earlier update (skipped if you already have them)
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password TINYINT(1) NOT NULL DEFAULT 0 AFTER status;
CREATE TABLE IF NOT EXISTS user_sessions (
  token VARCHAR(64) PRIMARY KEY, user_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ip VARCHAR(45), user_agent VARCHAR(255),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS login_attempts (
  id INT AUTO_INCREMENT PRIMARY KEY, identifier VARCHAR(120) NOT NULL, ip VARCHAR(45) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, INDEX (identifier, created_at), INDEX (ip, created_at)
) ENGINE=InnoDB;

-- new tables
CREATE TABLE IF NOT EXISTS login_history (
  id INT AUTO_INCREMENT PRIMARY KEY, user_id INT DEFAULT NULL, identifier VARCHAR(120) NOT NULL,
  success TINYINT(1) NOT NULL, reason VARCHAR(40) NOT NULL, ip VARCHAR(45), user_agent VARCHAR(255),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, INDEX (user_id, created_at), INDEX (created_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS audit_log (
  id INT AUTO_INCREMENT PRIMARY KEY, user_id INT DEFAULT NULL, action VARCHAR(60) NOT NULL,
  detail VARCHAR(255) DEFAULT NULL, ip VARCHAR(45), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (created_at), INDEX (action, ip, created_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS password_resets (
  id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, token_hash CHAR(64) NOT NULL UNIQUE,
  expires_at DATETIME NOT NULL, used_at DATETIME DEFAULT NULL, ip VARCHAR(45),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS notifications (
  id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, message VARCHAR(255) NOT NULL, link VARCHAR(255) DEFAULT NULL,
  is_read TINYINT(1) NOT NULL DEFAULT 0, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (user_id, is_read, created_at), FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- reports
ALTER TABLE maintenance_requests
  ADD COLUMN IF NOT EXISTS due_date DATE DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS resident_response ENUM('none','confirmed','reopened') NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS reopen_note TEXT NULL,
  ADD COLUMN IF NOT EXISTS rating TINYINT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS feedback TEXT NULL,
  ADD COLUMN IF NOT EXISTS assigned_at DATETIME DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS completed_at DATETIME DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
CREATE TABLE IF NOT EXISTS report_photos (
  id INT AUTO_INCREMENT PRIMARY KEY, request_id INT NOT NULL, file VARCHAR(64) NOT NULL UNIQUE,
  kind ENUM('report','completion') NOT NULL, uploaded_by INT NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (request_id) REFERENCES maintenance_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (uploaded_by) REFERENCES users(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_history (
  id INT AUTO_INCREMENT PRIMARY KEY, request_id INT NOT NULL, actor_id INT DEFAULT NULL, text VARCHAR(500) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, INDEX (request_id, created_at),
  FOREIGN KEY (request_id) REFERENCES maintenance_requests(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- lost and found
ALTER TABLE lost_found
  MODIFY status ENUM('open','claimed','released','closed','removed') NOT NULL DEFAULT 'open',
  ADD COLUMN IF NOT EXISTS finder_name VARCHAR(120) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS photo VARCHAR(64) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS claimant_id INT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS claim_details TEXT NULL,
  ADD COLUMN IF NOT EXISTS claimed_at DATETIME DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS verification_notes TEXT NULL,
  ADD COLUMN IF NOT EXISTS id_checked VARCHAR(80) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS released_to VARCHAR(120) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS released_at DATETIME DEFAULT NULL;

-- announcements
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS pinned TINYINT(1) NOT NULL DEFAULT 0;

-- security desk + dues
CREATE TABLE IF NOT EXISTS visitor_log (
  id INT AUTO_INCREMENT PRIMARY KEY, visitor_name VARCHAR(120) NOT NULL, purpose VARCHAR(160) NOT NULL,
  unit_visited VARCHAR(160) NOT NULL, id_presented VARCHAR(80) DEFAULT NULL, vehicle_plate VARCHAR(20) DEFAULT NULL,
  notes TEXT, time_in DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, time_out DATETIME DEFAULT NULL,
  recorded_by INT NOT NULL, INDEX (time_in), FOREIGN KEY (recorded_by) REFERENCES users(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS incident_log (
  id INT AUTO_INCREMENT PRIMARY KEY, category VARCHAR(60) NOT NULL, severity ENUM('low','medium','high') NOT NULL DEFAULT 'low',
  location VARCHAR(160), description TEXT NOT NULL, status ENUM('open','resolved') NOT NULL DEFAULT 'open',
  resolution_notes TEXT, occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, recorded_by INT NOT NULL,
  resolved_by INT DEFAULT NULL, resolved_at DATETIME DEFAULT NULL, INDEX (occurred_at), INDEX (status),
  FOREIGN KEY (recorded_by) REFERENCES users(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS vehicles (
  id INT AUTO_INCREMENT PRIMARY KEY, resident_id INT NOT NULL, plate_number VARCHAR(20) NOT NULL UNIQUE,
  make_model VARCHAR(80), color VARCHAR(30), sticker_no VARCHAR(30),
  status ENUM('active','expired','revoked') NOT NULL DEFAULT 'active', expires_on DATE DEFAULT NULL,
  registered_by INT NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (resident_id) REFERENCES users(id), FOREIGN KEY (registered_by) REFERENCES users(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS shift_reports (
  id INT AUTO_INCREMENT PRIMARY KEY, security_id INT NOT NULL, shift ENUM('morning','afternoon','night') NOT NULL,
  shift_date DATE NOT NULL, visitors_count INT NOT NULL DEFAULT 0, incidents_count INT NOT NULL DEFAULT 0,
  summary TEXT NOT NULL, handover_notes TEXT, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (shift_date), FOREIGN KEY (security_id) REFERENCES users(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS dues (
  id INT AUTO_INCREMENT PRIMARY KEY, resident_id INT NOT NULL, period CHAR(7) NOT NULL, amount DECIMAL(10,2) NOT NULL,
  status ENUM('unpaid','paid','waived') NOT NULL DEFAULT 'unpaid', paid_on DATE DEFAULT NULL,
  method VARCHAR(30) DEFAULT NULL, reference VARCHAR(60) DEFAULT NULL, recorded_by INT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY one_per_month (resident_id, period),
  FOREIGN KEY (resident_id) REFERENCES users(id)
) ENGINE=InnoDB;
