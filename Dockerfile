FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG MAKEMKV_VERSION=1.18.3

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cdparanoia \
    curl \
    eject \
    file \
    flac \
    g++ \
    gcc \
    libavcodec-dev \
    libexpat1-dev \
    libgl1-mesa-dev \
    libssl-dev \
    make \
    pkg-config \
    python3 \
	python3-setuptools \
    udev \
    whipper \
    wget \
    zlib1g-dev \
    openjdk-11-jre-headless \
    gnupg \
    dirmngr \
    qtbase5-dev \
    less \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    wget -q https://www.makemkv.com/download/makemkv-oss-${MAKEMKV_VERSION}.tar.gz; \
    wget -q https://www.makemkv.com/download/makemkv-bin-${MAKEMKV_VERSION}.tar.gz; \
    tar -xzf makemkv-oss-${MAKEMKV_VERSION}.tar.gz; \
    tar -xzf makemkv-bin-${MAKEMKV_VERSION}.tar.gz; \
    cd makemkv-oss-${MAKEMKV_VERSION}; ./configure; make -j"$(nproc)"; make install; \
    cd /makemkv-bin-${MAKEMKV_VERSION}; mkdir -p tmp; \
	touch tmp/eula_accepted; make -j"$(nproc)"; make install; \
    rm -rf /makemkv-* /makemkv*.tar.gz

WORKDIR /opt/jack
COPY ./packaging /opt/jack/packaging
COPY ./jack /opt/jack/jack
RUN install -m 755 packaging/jack-udev /usr/local/bin/jack-udev \
 && install -m 755 packaging/udev-start /usr/local/bin/udev-start \
 && install -m 644 packaging/99-jack.rules /etc/udev/rules.d/99-jack.rules \
 && mkdir -p /var/lib/jack /data/output

EXPOSE 8080
ENV JACK_STATE_DIR=/var/lib/jack JACK_OUTPUT_DIR=/data/output JACK_HOST=0.0.0.0 JACK_PORT=8080 PYTHONPATH=/opt/jack
CMD ["python3", "-m", "jack", "container"]
