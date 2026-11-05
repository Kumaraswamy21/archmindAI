# Versioned Java/Spring evaluation datasets

The packaged JSON files live in `backend/app/modules/evaluation/datasets/`. `java-code-search-v1` is the existing Phase 5 dataset and remains unchanged. Phase 7.6 adds `java-spring-search-v1` and `java-spring-agent-v1`, both version `1.0.0`, for the tracked `backend/evaluation/spring_sample_repository` fixture. Their shared snapshot is `sha256:ac782ebbcb61c49c891fe6faa76e163a44d80fa51e187f7a67e1a8c51c2308b8`.

Both formats require `dataset_id`, semantic `version`, `repository_snapshot_id`, and unique question IDs. Retrieval questions carry `question` and a nonempty list of `expected_symbols` (qualified names present in the indexed chunks). Agent questions carry `question`, `category`, `expected_intent`, `expected_workflow`, and `expect_abstention`. The loader checks schemas, identity, duplicate IDs, and intent-to-route consistency. The service checks the indexed snapshot before and after each run. Agent datasets are capped at 20 questions to bound costly evaluations.

The retrieval labels are defined by reading the four sample Java files. Questions R01–R08 cover endpoint lookup, method explanation, payment, constructor injection, storage, cancellation, change impact, and architecture. The agent labels A01–A06 cover the corresponding lookup, explanation, dependency, architecture, and change impact routes. A07 asks about Kafka publishing and A08 about fraud scoring; neither behavior exists in the fixture, so their ground truth is abstention. These are task labels, not claims that a generated answer is factually correct.

When adding a case, inspect the source and indexed symbols, set only defensible labels, and run the dataset snapshot test. When source or labels change, create a new dataset ID/version and record the new snapshot; preserve the old JSON file for reproducibility. Do not include private repository code or user prompts in packaged datasets.

Run instructions and scoring definitions are in [RAG and agent evaluation](rag-and-agents.md). The [measured Phase 7.6 report](java-spring-v1-results.md) records one live run and its limitations.
