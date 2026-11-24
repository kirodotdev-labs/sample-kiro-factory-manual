# The worker image. Lambda builds it from the context lib/bundle.ts assembles (microvm/ and the
# rendered bundle/) on top of the managed MicroVM base image,
# runs worker.py, and snapshots it once /ready answers.
# kiro-cli, gh and the Python packages (requirements.txt) are pinned by version and sha256. To move
# kiro-cli, take the version and the kirocli-aarch64-linux-musl.zip sha256 from
# ${KIRO_CLI_RELEASES}/latest/manifest.json. aarch64 glibc builds of kiro-cli need glibc 2.39 and
# AL2023 has 2.34, so this installs the musl build.
# The worker runs on python3.12: AL2023's python3 is 3.9, which current boto3 no longer supports.
# Add the toolchain the repo's CI config installs (a language runtime, compilers) to the dnf line or
# as pinned downloads like gh's.
FROM public.ecr.aws/lambda/microvms:al2023-minimal

ARG KIRO_CLI_RELEASES=https://desktop-release.q.us-east-1.amazonaws.com
ARG KIRO_CLI_VERSION=3.5.0
ARG KIRO_CLI_SHA256=c20631cef6337b7621ad7d781dba820d49b4477caa1532f13bdaca5305511d9f
ARG GH_VERSION=2.103.0
ARG GH_SHA256=7f322df34e624d14f5ec0c4acb121fd6dfa14392fdbbd1066aa8e0a05ac3721e

RUN set -eux; \
    pm="$(command -v dnf || command -v microdnf)"; \
    "$pm" install -y git python3.12 python3.12-pip unzip tar gzip openssl jq; \
    "$pm" clean all

RUN set -eux; \
    cd /tmp; \
    curl --proto '=https' --tlsv1.2 -fsSL -o kirocli.zip \
      "${KIRO_CLI_RELEASES}/${KIRO_CLI_VERSION}/kirocli-aarch64-linux-musl.zip"; \
    echo "${KIRO_CLI_SHA256}  kirocli.zip" | sha256sum -c -; \
    unzip -q kirocli.zip; \
    install -m 0755 kirocli/bin/* /usr/local/bin/; \
    rm -rf kirocli kirocli.zip; \
    curl --proto '=https' --tlsv1.2 -fsSL -o gh.tar.gz \
      "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_arm64.tar.gz"; \
    echo "${GH_SHA256}  gh.tar.gz" | sha256sum -c -; \
    tar -xzf gh.tar.gz; \
    install -D -m 0755 "gh_${GH_VERSION}_linux_arm64/bin/gh" /usr/local/libexec/gh; \
    rm -rf gh.tar.gz "gh_${GH_VERSION}_linux_arm64"

COPY requirements.txt /tmp/requirements.txt
RUN python3.12 -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY gh-wrapper.sh /usr/local/bin/gh
RUN chmod 0755 /usr/local/bin/gh
COPY worker.py run_credentials.py /opt/worker/
# The instance rendered against the Manual (lib/bundle.ts); the host profile names /opt/factory.
COPY bundle /opt/factory

CMD ["python3.12", "/opt/worker/worker.py"]
