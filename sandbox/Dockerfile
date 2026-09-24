# Step 3 secure coding sandbox: minimal image for ephemeral, network-isolated
# pytest runs of AI-generated code. No application code, no secrets, no
# database drivers, no docker client. Never runs as a service: each execution
# is `docker run --rm --network none` with tmpfs-bounded storage (see
# backend/app/coding_sandbox.py). Build: docker build -t sov-sandbox:latest ./sandbox
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Test runner only - the workspace arrives over stdin at run time.
RUN pip install --no-cache-dir pytest==8.3.4

# Non-root execution; the app's runtime env never reaches this container.
USER nobody
WORKDIR /workspace

# No CMD needed: docker run overrides the command with the tar+pytest script.
CMD ["python", "-c", "print('sandbox image: pass files via stdin')"]
