#!/bin/bash
# DeepTutor on Railway: write runtime settings from the service variables,
# then hand over to the image's unchanged entrypoint (/app/entrypoint.sh),
# which fixes volume ownership, exports the JSON settings and starts
# supervisord with the FastAPI backend (127.0.0.1:8001) and the Next.js
# frontend (0.0.0.0:$PORT).
set -euo pipefail

log() { printf '[railway] %s\n' "$*"; }

DATA_DIR=/app/data

# Railway volumes are ext4 filesystems with an empty lost+found at the root.
if [ -d "$DATA_DIR/lost+found" ]; then
    rmdir "$DATA_DIR/lost+found" 2>/dev/null || log "keeping non-empty $DATA_DIR/lost+found"
fi

# DeepTutor resolves its data tree from the working directory (/app/data).
cd /app
python /opt/railway/configure.py

# The seed values now live in data/user/settings/*.json on the volume. Keep
# them out of the environment that the app, its tools and CLI apps inherit.
unset DEEPTUTOR_ADMIN_PASSWORD LLM_API_KEY EMBEDDING_API_KEY DEEPTUTOR_REDIS_URL

exec /app/entrypoint.sh "$@"
