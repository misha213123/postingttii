FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/srv/data
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
COPY cloud_render/requirements.txt /srv/cloud_render/requirements.txt
RUN pip install --no-cache-dir -r /srv/cloud_render/requirements.txt
COPY app /srv/app
COPY cloud_render /srv/cloud_render
RUN mkdir -p /srv/data
EXPOSE 10000
CMD ["uvicorn","cloud_render.app:app","--host","0.0.0.0","--port","10000","--workers","1"]
