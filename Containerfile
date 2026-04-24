# syntax=docker.io/docker/dockerfile:1.4
# 使用 Debian 13 (trixie) slim 版
FROM debian:13-slim AS base

ENV TZ=Asia/Shanghai
ENV DEBIAN_FRONTEND=noninteractive

# 安装必要的证书工具（需要先安装这些才能访问 HTTPS 镜像源）
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates openssl && \
    rm -rf /var/lib/apt/lists/*

# 替换 Debian apt 源为 mirrors.tuna.tsinghua.edu.cn (在中国大陆访问更快)
COPY <<EOF /etc/apt/sources.list.d/debian.sources
Types: deb
URIs: https://mirrors.tuna.tsinghua.edu.cn/debian
Suites: trixie
Components: main contrib non-free
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: https://mirrors.tuna.tsinghua.edu.cn/debian
Suites: trixie-updates
Components: main contrib non-free
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: https://mirrors.tuna.tsinghua.edu.cn/debian-security
Suites: trixie-security
Components: main contrib non-free
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF

# 安装必要工具：python3、pip、grep、curl
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        git \
        grep \
        # curl-impersonate 所需的 Firefox 的 TLS 库 \
        libnss3 \
        nss-plugin-pem \
        python3-pip \
        python3 \
        sqlite3 \
        sudo \
        tzdata \
    && \
    rm -rf /var/lib/apt/lists/* && \
    # 下载并安装 curl-impersonate \
    curl -L "https://github.com/lexiforest/curl-impersonate/releases/download/v1.5.1/curl-impersonate-v1.5.1.x86_64-linux-gnu.tar.gz" -o /tmp/curl-impersonate.tar.gz && \
    mkdir /tmp/curl-impersonate && \
    tar -xzf /tmp/curl-impersonate.tar.gz -C /tmp/curl-impersonate && \
    cp /tmp/curl-impersonate/curl-impersonate /usr/local/bin/ && \
    rm -rf /tmp/curl-impersonate* && \
    echo "#!/usr/bin/env bash\ndir=\${0%/*}\n\"\$dir/curl-impersonate\" --compressed --impersonate \"firefox147\" --doh-url \"https://cloudflare-dns.com/dns-query\" \"\$@\"" > /usr/local/bin/curl && \
    chmod +x /usr/local/bin/curl

# Default command
CMD ["tail", "-f", "/dev/null"]

FROM base AS python

# 替换 PyPI 源为 mirrors.tuna.tsinghua.edu.cn (在中国大陆访问更快)
ENV PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# 将用户本地二进制目录添加到 PATH
ENV PATH=/root/.local/bin:$PATH

# 配置 git 并安装 Python 工具和依赖
RUN git config --global --add safe.directory /workspace && \
    git config --global core.autocrlf true && \
    python3 -m pip install --no-cache-dir \
        "black" \
        "curl_cffi>=0.6.0" \
        "isort" \
        "pyright[nodejs]"
