FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 1000 recorder \
    && useradd --uid 1000 --gid recorder --no-create-home recorder \
    && mkdir /data && chown recorder:recorder /data && chmod 700 /data
COPY *.py *.html /app/
COPY static/ /app/static/
RUN chmod -R a+rX,go-w /app
ENV DATA_DIR=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BIND_HOST=0.0.0.0 PORT=18770
USER recorder:recorder
EXPOSE 18770
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','18770')+'/health',timeout=3).read()"
ENTRYPOINT ["python", "-u", "/app/app.py"]
