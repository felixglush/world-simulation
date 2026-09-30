FROM python:3.12-slim

# Use the same uv version as the cloud bootstrap.
RUN python -m pip install --no-cache-dir uv==0.12.21

WORKDIR /workspace
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen

COPY scripts/ scripts/
CMD ["uv", "run", "--frozen", "python", "scripts/check_environment.py"]
