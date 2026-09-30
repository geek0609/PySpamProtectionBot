CREATE TABLE IF NOT EXISTS secretary_configuration
(
    id INTEGER PRIMARY KEY,
    business_connection_id TEXT NOT NULL DEFAULT '',
    behavior TEXT NOT NULL DEFAULT 'STRICT',
    privacy_mode INTEGER NOT NULL DEFAULT 0,
    scanning_enabled INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_secretary_configuration_connection
    ON secretary_configuration (business_connection_id);
