# ADR 0006: self-hosted model routing

Status: accepted

Use administrator-configured, revision-pinned model profiles. Default generation is Apache-2.0 IBM Granite 4.1 8B; default embeddings are Apache-2.0 Qwen3-Embedding-0.6B. vLLM and TEI serve private GPU routes; quantized llama.cpp provides degraded CPU generation. External model APIs are disabled. Promotion and fallback require compatible security/capability profiles and a passing evaluation release.
