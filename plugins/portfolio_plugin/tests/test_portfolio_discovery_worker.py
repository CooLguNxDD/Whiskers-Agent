"""portfolio_discovery_worker self-registration.

core's register_all() no longer imports this plugin (that core -> plugin edge was
removed); the plugin registers itself from on_ready, so the registration path
belongs to the plugin's own suite.
"""

from core_graph.worker.worker_registry import WorkerRegistry

from plugins.portfolio_plugin.discovery.worker import register as register_portfolio


def test_portfolio_worker_registers_itself():
    """The plugin's own register() is what puts the discovery worker on the registry."""
    reg = WorkerRegistry()
    register_portfolio(reg)
    assert reg.is_registered("portfolio_discovery_worker")


def test_register_is_idempotent():
    """on_ready may run twice across a hot-reload; the spec must not be shadowed."""
    reg = WorkerRegistry()
    register_portfolio(reg)
    register_portfolio(reg)
    assert reg.names().count("portfolio_discovery_worker") == 1
