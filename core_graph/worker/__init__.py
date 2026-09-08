"""
Async embedding pipeline for the dynamic graph.

  * ``job_producer`` — diffs the RouteRegistry against ``route_embeddings`` and
    enqueues rows in ``embedding_jobs`` that need (re-)embedding.
  * ``embedding_worker`` — claims batches via ``FOR UPDATE SKIP LOCKED``,
    embeds them in a single batched API call, upserts vectors into
    ``route_embeddings``, marks jobs ``done`` / ``failed``.

Both modules require ``DATABASE_URL`` + ``MASTER_KEY`` to be set.

``register_all`` covers core workers only; plugins self-register in their own
lifecycle hooks.
"""

from core_graph.worker.job_producer import enqueue_pending
from core_graph.worker.embedding_worker import run as run_worker, stop as stop_worker
from core_graph.worker.worker_registry import WorkerRegistry, WorkerSpec, get_worker_registry


def register_all(registry: WorkerRegistry) -> None:
    """Register the **core** workers only.

    Plugins self-register their own workers from their lifecycle hooks (see
    portfolio_plugin and world_semantic_plugin ``on_ready`` / ``on_load``), so
    core never imports a plugin to find a worker.
    """
    from core_graph.worker import embedding_worker, content_sync_worker, checkpoint_sweeper, telemetry_ttl_sweeper, artifact_sweeper
    embedding_worker.register(registry)
    content_sync_worker.register(registry)
    checkpoint_sweeper.register(registry)
    telemetry_ttl_sweeper.register(registry)
    artifact_sweeper.register(registry)

__all__ = ["enqueue_pending", "run_worker", "stop_worker", "get_worker_registry", "register_all"]
