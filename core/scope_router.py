"""
core/scope_router.py
====================
Routeur de scope en LANGAGE NATUREL (DETTE-3, P5).

L'entité ne choisit pas un `scope` technique : elle pose une question.
Un méta-LLM lit la question + la liste courte des providers (data_type +
description de périmètre) et décide lesquels consulter. S'il n'y en a aucun,
le refus est explicite — ce n'est pas un rejet de plan.

Aucune action n'est dispatchée ici : uniquement des lectures.
"""
from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.i18n import _
from core.prompt_loader import get_prompt_loader
from utils.logger import Logger


class ScopeRoute(BaseModel):
    """Décision du méta-LLM routeur."""

    providers: List[str] = Field(
        default_factory=list,
        description=_("Noms des providers à consulter (vide = aucun ne peut répondre)."),
    )
    reason: str = Field(
        default="",
        description=_("Pourquoi ces providers, ou pourquoi aucun."),
    )


class ScopeRouter:
    """Choisit les providers à consulter pour une question en langage naturel."""

    def __init__(self, runtime_state, llm=None, entity=None):
        self.runtime_state = runtime_state
        self.llm = llm
        self.entity = entity or getattr(runtime_state, "_scope_router_entity", None)

    # ------------------------------------------------------------------
    # Candidats
    # ------------------------------------------------------------------

    def _providers(self) -> Dict[str, Any]:
        try:
            providers = self.entity.get_data_providers() if self.entity else {}
        except Exception:
            providers = {}
        return providers if isinstance(providers, dict) else {}

    def candidates(self) -> Dict[str, Dict[str, str]]:
        """Vue COURTE : data_type + description de périmètre, jamais les targets."""
        out: Dict[str, Dict[str, str]] = {}
        for name, provider in self._providers().items():
            try:
                data_type = str(provider.get_data_type())
                scope = ""
                if hasattr(provider, "get_scope_description"):
                    scope = str(provider.get_scope_description() or "")
                out[str(name)] = {"data_type": data_type, "scope": scope}
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------
    # Routage + réponse
    # ------------------------------------------------------------------

    @staticmethod
    def _refusal(question: str, reason: str = "") -> Dict[str, Any]:
        return {
            "result": False,
            "data": None,
            "error_reason": _(
                "Aucun scope ne peut traiter cette demande : {question}"
            ).format(question=question),
            "reason": reason,
        }

    def _emit_event(self, question: str, selected: List[str], answered: bool,
                    reason: str) -> None:
        """Trace le routage (P5.6 : mesurer refus et qualité)."""
        try:
            ctx = getattr(self.runtime_state, "execution_context", None) or {}

            def _get(key):
                try:
                    return ctx.get(key) if hasattr(ctx, "get") else getattr(ctx, key, None)
                except Exception:
                    return None

            Logger.event(
                "scope_route",
                mission_id=_get("mission_id") or getattr(self.runtime_state, "mission_id", None),
                solver_id=_get("solver_id"),
                question_chars=len(question),
                providers=list(selected),
                answered=bool(answered),
                reason=str(reason or "")[:200],
            )
        except Exception:
            pass

    async def route(self, question: str) -> ScopeRoute:
        candidates = self.candidates()
        if not candidates or self.llm is None:
            return ScopeRoute(providers=[], reason=_("Aucun scope disponible."))
        try:
            prompt = get_prompt_loader().load(
                "scope_router.md",
                lang=getattr(self.runtime_state, "language", "en"),
                question=question,
                scopes=candidates,
            )
            decision = await self.llm.generate_structured(
                prompt=prompt, schema=ScopeRoute, tag="ScopeRoute",
                with_discovery=False,
            )
        except Exception as exc:
            Logger.warning(f"[ScopeRouter] Routage impossible ({exc}).")
            return ScopeRoute(providers=[], reason=str(exc))
        if isinstance(decision, dict):
            try:
                decision = ScopeRoute(**decision)
            except Exception:
                return ScopeRoute(providers=[], reason=_("Décision de routage invalide."))
        return decision if isinstance(decision, ScopeRoute) else ScopeRoute()

    async def answer(self, question: str) -> Dict[str, Any]:
        question = str(question or "").strip()
        if not question:
            return {
                "result": False,
                "data": None,
                "error_reason": _("La question d'investigation est vide."),
            }
        candidates = self.candidates()
        if not candidates:
            return self._refusal(question, _("Aucun provider enregistré."))

        decision = await self.route(question)
        selected = [p for p in (decision.providers or []) if p in candidates][:3]
        if not selected:
            self._emit_event(question, [], False, decision.reason)
            return self._refusal(question, decision.reason)

        answers: List[str] = []
        providers = self._providers()
        for name in selected:
            provider = providers.get(name)
            fn = getattr(provider, "answer", None)
            if fn is None:
                continue
            try:
                result = fn(question)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                Logger.warning(f"[ScopeRouter] Provider '{name}' en échec ({exc}).")
                continue
            text = str(result).strip() if result is not None else ""
            if text:
                answers.append(f"[{name}] {text}")

        if not answers:
            self._emit_event(question, selected, False, decision.reason)
            return {
                "result": False,
                "data": None,
                "error_reason": _(
                    "Les scopes {scopes} n'ont pas pu répondre à : {question}"
                ).format(scopes=", ".join(selected), question=question),
                "reason": decision.reason,
            }

        self._emit_event(question, selected, True, decision.reason)
        return {
            "result": True,
            "data": "\n\n".join(answers),
            "sources": selected,
            "reason": decision.reason,
        }
