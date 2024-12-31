#!/bin/bash

# Read configuration from options.json
CONFIG_PATH="/data/options.json"

if [ ! -f "$CONFIG_PATH" ]; then
    echo "Error: Configuration file not found $CONFIG_PATH"
    exit 1
fi
# Use jq to read config and set environment variables
export PHONE_NUMBER=$(jq -r '.phone // empty' "$CONFIG_PATH")
export PASSWORD=$(jq -r '.password // empty' "$CONFIG_PATH")
export IGNORE_USER_ID=$(jq -r '.IGNORE_USER_ID // empty' "$CONFIG_PATH")
export ENABLE_DATABASE_STORAGE=$(jq -r '.enable_database_storage // "false"' "$CONFIG_PATH")
export DB_NAME=$(jq -r '.db_name // "homeassistant.db"' "$CONFIG_PATH")
export HASS_URL=$(jq -r '.hass_url // empty' "$CONFIG_PATH")
export HASS_TOKEN=$(jq -r '.hass_token // empty' "$CONFIG_PATH")
export JOB_START_TIME=$(jq -r '.job_start_time // "00:00"' "$CONFIG_PATH")
export DRIVER_IMPLICITLY_WAIT_TIME=$(jq -r '.driver_implicitly_wait_time // "10"' "$CONFIG_PATH")
export RETRY_TIMES_LIMIT=$(jq -r '.retry_times_limit // "3"' "$CONFIG_PATH")
export LOGIN_EXPECTED_TIME=$(jq -r '.login_expected_time // "60"' "$CONFIG_PATH")
export RETRY_WAIT_TIME_OFFSET_UNIT=$(jq -r '.retry_wait_time_offset_unit // "5"' "$CONFIG_PATH")
export LOG_LEVEL=$(jq -r '.log_level // "INFO"' "$CONFIG_PATH")
export DATA_RETENTION_DAYS=$(jq -r '.data_retention_days // "30"' "$CONFIG_PATH")
export RECHARGE_NOTIFY=$(jq -r '.recharge_notify // "false"' "$CONFIG_PATH")
export BALANCE=$(jq -r '.balance // "50"' "$CONFIG_PATH")
export PUSHPLUS_TOKEN=$(jq -r '.pushplus_token // empty' "$CONFIG_PATH")


# Check required environment variables
if [ -z "$PHONE_NUMBER" ]; then
    echo "Error: Phone number not set"
    exit 1
fi

if [ -z "$PASSWORD" ]; then
    echo "Error: Password not set"
    exit 1
fi

if [ -z "$HASS_URL" ]; then
    echo "Error: Home Assistant URL not set"
    exit 1
fi

if [ -z "$HASS_TOKEN" ]; then
    echo "Error: Home Assistant Token not set"
    exit 1
fi

# Output environment variables log
echo "Environment variables setup completed:"
echo "Phone Number: ${PHONE_NUMBER:-Not Set}"
echo "Home Assistant URL: ${HASS_URL:-Not Set}"
echo "Job Start Time: ${JOB_START_TIME:-Not Set}"
echo "Log Level: ${LOG_LEVEL:-Not Set}"
echo "Data Retention Days: ${DATA_RETENTION_DAYS:-Not Set}"

# Start main program
python3 /app/main.py

