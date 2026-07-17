"""Arquitectura multi-agente de Tailo (entregable semana 07 - Fase A).

El agente monolitico de la semana 5 se divide en:

  - RouterAgent           -> clasifica la intencion y delega (agents/router.py)
  - RagAgent              -> especialista en conocimiento estatico (agents/rag_agent.py)
  - TransactionalAgent    -> especialista en operaciones de cuenta (agents/transactional_agent.py)
  - Orchestrator          -> coordina el turno y emite eventos (agents/orchestrator.py)

El orquestador es la unica puerta de entrada que consume el resto del sistema
(server.py, evaluar_agente.py).
"""
from agents.orchestrator import Orchestrator, Route

__all__ = ["Orchestrator", "Route"]
