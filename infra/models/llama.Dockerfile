# llama-server с CUDA на закреплённом коммите llama.cpp (тег b11691) и конвертер GGUF
# с патчем для Giga-Embeddings. Основа — .devops/cuda.Dockerfile llama.cpp на том же коммите.
# Контекст — infra/models. Цели: server (llama-server) и convert (конвертер для эмбеддингов).
# Патч меняет только конвертер на Python, llama-server собирается из чистого b11691.
# CUDA_DOCKER_ARCH — compute capability карты (86, 89, 90…), иначе сборка идёт под девять
# архитектур и занимает 30–60 минут.

ARG UBUNTU_VERSION=24.04
ARG CUDA_VERSION=12.8.1
ARG GCC_VERSION=14
ARG BASE_CUDA_DEV_CONTAINER=docker.io/nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${UBUNTU_VERSION}
ARG BASE_CUDA_RUN_CONTAINER=docker.io/nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu${UBUNTU_VERSION}
ARG LLAMA_CPP_REPO=https://github.com/ggml-org/llama.cpp.git
ARG LLAMA_CPP_COMMIT=623c16393c7d53a37df8bb69c6ea8ed6e5171f62
ARG UV_VERSION=0.12.17

FROM docker.io/library/ubuntu:${UBUNTU_VERSION} AS source
ARG LLAMA_CPP_REPO
ARG LLAMA_CPP_COMMIT
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN git init -q . \
    && git remote add origin "${LLAMA_CPP_REPO}" \
    && git fetch -q --depth 1 origin "${LLAMA_CPP_COMMIT}" \
    && git -c advice.detachedHead=false checkout -q FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${LLAMA_CPP_COMMIT}"
COPY giga-embeddings.patch /tmp/giga-embeddings.patch
RUN git apply --check /tmp/giga-embeddings.patch && git apply /tmp/giga-embeddings.patch

FROM ${BASE_CUDA_DEV_CONTAINER} AS build
ARG GCC_VERSION
ARG CUDA_DOCKER_ARCH=default
RUN apt-get update \
    && apt-get install -y gcc-${GCC_VERSION} g++-${GCC_VERSION} build-essential cmake python3 git libgomp1
ENV CC=gcc-${GCC_VERSION} CXX=g++-${GCC_VERSION} CUDAHOSTCXX=g++-${GCC_VERSION}
WORKDIR /app
COPY --from=source /app /app
# Флаги — как в .devops/cuda.Dockerfile, веб-интерфейс выключен, как в нашей сборке на Маке.
RUN if [ "${CUDA_DOCKER_ARCH}" != "default" ]; then \
      export CMAKE_ARGS="-DCMAKE_CUDA_ARCHITECTURES=${CUDA_DOCKER_ARCH}"; \
    fi \
    && cmake -B build \
      -DCMAKE_BUILD_TYPE=Release \
      -DGGML_NATIVE=OFF -DGGML_CUDA=ON -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON \
      -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
      -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_OPENSSL=OFF \
      ${CMAKE_ARGS} -DCMAKE_EXE_LINKER_FLAGS=-Wl,--allow-shlib-undefined . \
    && cmake --build build --config Release --target llama-server -j"$(nproc)"
RUN mkdir -p /app/lib && find build -name "*.so*" -exec cp -P {} /app/lib \;

FROM ${BASE_CUDA_RUN_CONTAINER} AS server
ARG LLAMA_CPP_COMMIT
LABEL org.opencontainers.image.revision=${LLAMA_CPP_COMMIT}
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/lib/ /app
COPY --from=build /app/build/bin/llama-server /app/
WORKDIR /app
ENV LD_LIBRARY_PATH=/app:/usr/local/cuda/lib64
ENTRYPOINT ["/app/llama-server"]

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM docker.io/library/ubuntu:${UBUNTU_VERSION} AS convert
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /llama.cpp
COPY --from=source /app/convert_hf_to_gguf.py ./
COPY --from=source /app/conversion ./conversion
COPY --from=source /app/gguf-py ./gguf-py
COPY --from=source /app/requirements ./requirements
