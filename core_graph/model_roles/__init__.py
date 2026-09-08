"""Spec-driven multi-model role selection for the LangGraph orchestrator.

Model choice per pipeline role (triage, planner, summary, specialist stages, ...)
is declarative data — a ``ModelRoleSpec`` mirroring
``core_graph.subgraphs.specialist.flow_spec.FlowSpec`` — resolved against the
existing ``llm_pool`` strength column via ``core.llm_config_service``. See
``role_spec.py`` for the schema, ``registry.py`` for storage/precedence,
``resolver.py`` for selector→client resolution, and ``ladder.py`` for the
generic multi-rung runner nodes call instead of hand-coded cascades.
"""
