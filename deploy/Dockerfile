FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir websockets cryptography
COPY rsupport/__init__.py rsupport/relay.py rsupport/protocol.py rsupport/config.py rsupport/__main__.py /app/rsupport/
# __main__ imports only what the chosen sub-command needs, so the relay image stays tiny
ENV PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "-m", "rsupport", "relay", "--host", "0.0.0.0", "--port", "8765", "--trust-proxy"]
