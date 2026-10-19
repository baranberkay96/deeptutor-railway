# DeepTutor for Railway: the official image, unchanged, plus a pre-start
# step that writes DeepTutor's JSON settings from Railway variables.
#
# Pinned to upstream release v1.6.15 (tag commit a31d826, image built
# 2026-10-18). The tag is also published as :latest, so the manifest-list
# digest is pinned as well. Upgrade: replace tag and digest with a newer
# release from https://github.com/HKUDS/DeepTutor/pkgs/container/deeptutor
# (`docker buildx imagetools inspect ghcr.io/hkuds/deeptutor:<version>`).
FROM ghcr.io/hkuds/deeptutor:1.6.15@sha256:602d5dfbac7430e364069ac6672642cb0d764e046d189bf596d9ddc6b775c535

COPY railway/ /opt/railway/

# 1. Add the FATAL-exit event listener to the image's supervisord programs.
# 2. Give the backend 30 s to finish in-flight turns on shutdown (supervisord's
#    default is 10 s before SIGKILL; DeepTutor drains for up to 60 s).
RUN chmod 0755 /opt/railway/entrypoint.sh \
    && cat /opt/railway/supervisor-railway.conf >> /etc/supervisor/conf.d/programs.conf \
    && sed -i '/^\[program:backend\]$/a stopwaitsecs=30' /etc/supervisor/conf.d/programs.conf \
    && grep -q '^\[eventlistener:railway_fatal_exit\]$' /etc/supervisor/conf.d/programs.conf \
    && test "$(grep -c '^stopwaitsecs=30$' /etc/supervisor/conf.d/programs.conf)" = 1

# Railway routes the public domain and the healthcheck to the Next.js
# frontend (PORT=3782); it proxies /api, /ws and /files to the backend.
EXPOSE 3782
ENTRYPOINT ["/opt/railway/entrypoint.sh"]
