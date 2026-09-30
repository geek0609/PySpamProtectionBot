CREATE TABLE IF NOT EXISTS chat_configuration
(
    chat_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    scanning_enabled INTEGER NOT NULL DEFAULT 1,
    scanning_behavior TEXT NOT NULL DEFAULT 'STRICT',
    join_protection_enabled INTEGER NOT NULL DEFAULT 1,
    join_protection_behavior TEXT NOT NULL DEFAULT 'STRICT',
    join_protection_notifications_enabled INTEGER NOT NULL DEFAULT 1,
    privacy_mode INTEGER NOT NULL DEFAULT 0,
    reporting_enabled INTEGER NOT NULL DEFAULT 1,
    moderator_notifications_enabled INTEGER NOT NULL DEFAULT 1,
    scanning_notifications_enabled INTEGER NOT NULL DEFAULT 1,
    reporting_notifications_enabled INTEGER NOT NULL DEFAULT 1,
    channel_link_id INTEGER DEFAULT NULL,
    channel_link_verification_code INTEGER DEFAULT NULL,
    channel_link_thread_id INTEGER DEFAULT NULL
);

-- Lookups by linked channel id and by verification code run when channels connect to the bot.
-- Without these indexes each lookup is a full table scan.
CREATE INDEX IF NOT EXISTS idx_chat_configuration_channel_link_id
    ON chat_configuration (channel_link_id);

CREATE INDEX IF NOT EXISTS idx_chat_configuration_verification_code
    ON chat_configuration (channel_link_verification_code);
