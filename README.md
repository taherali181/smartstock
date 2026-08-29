# SmartStock

SmartStock is a RAG-first inventory operations platform: a fast operational workspace for catalog, stock, orders, purchasing, warehouses, forecasting, and evidence-backed answers from your business data.

This repository currently contains the first frontend milestone—a responsive React + TypeScript product prototype with dark/light themes and a clean, nearly monochromatic visual system.

## What is implemented

- A single RAG-first operational workspace rather than a dashboard with chat added to it
- Inventory records, risk summaries, recommendations, and citations rendered directly in answers
- One contextual side panel for inventory browsing, item details, source evidence, action plans, and conversation history
- Approval-gated replenishment actions that remain attached to the conversation
- Searchable inventory context and inspectable live/document sources
- New-conversation and follow-up prompt flows
- Responsive dark/light themes with a restrained blue accent
- Static typed demo data ready to be replaced by API queries

## Run locally

```bash
npm install
npm run dev
```

Quality checks:

```bash
npm run lint
npm run build
```

## Product direction

Competitor research confirmed the operational baseline. Zoho Inventory covers multi-warehouse stock, transfers, bins, serial/batch tracking, purchasing, multichannel orders, fulfillment, barcode workflows, automation, and reporting. Its current AI experience also supports natural-language inventory lookup and follow-up actions. Stitch Labs’ legacy strengths were centralized multichannel stock, allocation/reservation, routing, reorder guidance, and demand forecasting.

SmartStock will meet that baseline, then differentiate on:

1. Permission-aware answers grounded in live records and operating documents
2. Citations and a visible reasoning trail for every operational recommendation
3. Forecast confidence, backtesting, and explainable drivers—not black-box numbers
4. Action drafts with explicit human approval for purchase orders, transfers, and adjustments
5. A self-hostable open-model path for data-sensitive businesses

See [docs/PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md) for the scoped feature plan and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the proposed scalable backend.

## Research references

- [Zoho Inventory feature set](https://www.zoho.com/us/inventory/features/)
- [Zoho Inventory AI capabilities](https://www.zoho.com/us/inventory/features/ai-in-inventory/)
- [Zoho Inventory multi-channel workflows](https://www.zoho.com/us/inventory/multichannel-inventory-management/)
- [Stitch Labs feature archive](https://www.g2.com/products/stitch-labs/features)
- [Qwen3 model card](https://huggingface.co/Qwen/Qwen3-32B)
- [BGE-M3 model card](https://huggingface.co/BAAI/bge-m3)
- [pgvector](https://github.com/pgvector/pgvector)
- [vLLM serving documentation](https://docs.vllm.ai/en/latest/serving/online_serving/)

## Status

Frontend foundation only. All displayed business data is currently representative mock data. No backend, authentication, or model service is connected yet.
