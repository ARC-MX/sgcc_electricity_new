ARG VARIANT="3.12-bookworm"
FROM mcr.microsoft.com/vscode/devcontainers/python:${VARIANT}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV SET_CONTAINER_TIMEZONE=true
ENV CONTAINER_TIMEZONE=Asia/Shanghai
ENV TZ=Asia/Shanghai 

ARG TARGETARCH
ARG VERSION
ENV VERSION=DOCKER
# ENV PYTHON_IN_DOCKER='PYTHON_IN_DOCKER'


RUN apt-get --allow-releaseinfo-change update \
    && apt-get install -y --no-install-recommends jq chromium chromium-driver fonts-noto-cjk tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && dpkg-reconfigure --frontend noninteractive tzdata

RUN mkdir /data \
    && chown vscode:vscode /data

ENV LANG=C.UTF-8