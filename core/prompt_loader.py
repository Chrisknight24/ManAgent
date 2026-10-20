"""
core/prompt_loader.py
=====================
Chargeur de prompts avec templating Jinja2 et support multilingue.
"""

import ast
import os
import re
import json
from typing import Dict, Any, List, Optional

try:
    from jinja2 import Environment, FileSystemLoader
    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False
    Environment = None
    FileSystemLoader = None

from utils.logger import Logger


def args_signature(params: Any) -> str:
    """Résumé COMPACT d'un schéma JSON d'arguments d'outil (F-bloc 3).

    Remplace le dump JSON complet (bruit + tokens) par une ligne utile pour
    remplir un appel : `nom` (+`*` si requis), valeurs fermées (`=a|b|...`),
    type, et description courte. Pur, tolérant (schéma non conforme -> "").
    """
    if not isinstance(params, dict):
        return ""
    props = params.get("properties")
    if not isinstance(props, dict) or not props:
        return ""
    required = params.get("required") or []
    required = set(required) if isinstance(required, (list, tuple, set)) else set()
    parts = []
    for name, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        piece = str(name) + ("*" if name in required else "")
        enum = spec.get("enum")
        if isinstance(enum, (list, tuple)) and enum:
            piece += "=" + "|".join(str(e) for e in enum)
        elif spec.get("type"):
            piece += ":" + str(spec["type"])
        desc = spec.get("description")
        if desc:
            piece += " — " + str(desc).strip()[:60]
        parts.append(piece)
    return "; ".join(parts)


def registry_compact(view: Any, prefix: str = "") -> str:
    """Vue REGISTRE compacte (F-bloc 4).

    Une ligne par variable : `- <prefix><nom> (type) : description — hint`.
    Retire les champs de bruit (`timestamp`) et tronque les descriptions
    longues. Tolérant aux vues sans `type`/`value_hint` (planner). Pur.
    """
    if not isinstance(view, dict) or not view:
        return ""
    lines = []
    for name, meta in view.items():
        meta = meta if isinstance(meta, dict) else {}
        typ = meta.get("type")
        hint = meta.get("value_hint")
        desc = str(meta.get("description", "") or "").strip()
        if len(desc) > 120:
            desc = desc[:119] + "…"
        head = f"- {prefix}{name}"
        if typ:
            head += f" ({typ})"
        if desc:
            head += f" : {desc}"
        if hint:
            head += f" — {hint}"
        lines.append(head)
    return "\n".join(lines)


class _UnsafeConditional(Exception):
    """Condition non evaluable sans Jinja2 : on refuse plutot que fuiter."""


def _eval_fallback_condition(expr: str, kwargs: Dict[str, Any]) -> bool:
    text = str(expr or "").strip()
    if not text:
        raise _UnsafeConditional("condition vide")

    context = dict(kwargs)

    def _defined(match: re.Match) -> str:
        name = match.group(1)
        negated = bool(match.group(2))
        key = f"__undefined_{name}" if negated else f"__defined_{name}"
        context[key] = (name not in kwargs) if negated else (name in kwargs)
        return key

    def _none(match: re.Match) -> str:
        name = match.group(1)
        negated = bool(match.group(2))
        key = f"__not_none_{name}" if negated else f"__none_{name}"
        context[key] = (kwargs.get(name) is not None) if negated else (kwargs.get(name) is None)
        return key

    text = re.sub(
        r"\b([A-Za-z_]\w*)\s+is\s+(not\s+)?defined\b",
        _defined,
        text,
    )
    text = re.sub(
        r"\b([A-Za-z_]\w*)\s+is\s+(not\s+)?none\b",
        _none,
        text,
    )

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise _UnsafeConditional(f"condition illisible: {expr!r}") from exc

    def evaluate(node: ast.AST) -> Any:
        if isinstance(node, ast.Name):
            return context.get(node.id, False)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp):
            values = [evaluate(value) for value in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
            raise _UnsafeConditional(f"operateur non supporte: {expr!r}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not evaluate(node.operand)
        if isinstance(node, ast.Compare):
            left = evaluate(node.left)
            for operator, comparator in zip(node.ops, node.comparators):
                right = evaluate(comparator)
                if isinstance(operator, ast.Eq):
                    ok = left == right
                elif isinstance(operator, ast.NotEq):
                    ok = left != right
                elif isinstance(operator, ast.Is):
                    ok = left is right
                elif isinstance(operator, ast.IsNot):
                    ok = left is not right
                elif isinstance(operator, ast.In):
                    ok = left in right
                elif isinstance(operator, ast.NotIn):
                    ok = left not in right
                else:
                    raise _UnsafeConditional(f"comparaison non supportee: {expr!r}")
                if not ok:
                    return False
                left = right
            return True
        raise _UnsafeConditional(f"expression non supportee: {expr!r}")

    try:
        return bool(evaluate(tree.body))
    except _UnsafeConditional:
        raise
    except Exception as exc:
        raise _UnsafeConditional(f"condition non evaluable: {expr!r}") from exc


class PromptLoader:
    """
    Charge les prompts depuis des fichiers Markdown avec Jinja2 (ou un fallback basique).
    Supporte le multi-langues via un sous-dossier par langue.
    """
    
    def __init__(self, base_dir: str = None, default_lang: str = "fr"):
        # IDE sync
        if base_dir is None:
            core_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(core_dir)
            base_dir = os.path.join(project_root, "prompts")
        self.base_dir = base_dir
        self.default_lang = default_lang
        self.env_cache: Dict[str, Any] = {}
        
    def _get_env(self, lang: str):
        """Retourne l'environnement Jinja2 pour une langue donnée."""
        if not JINJA2_AVAILABLE:
            return None
            
        if lang not in self.env_cache:
            template_dirs = [
                os.path.join(self.base_dir, lang),
                os.path.join(self.base_dir, "base"),
                self.base_dir
            ]
            existing_dirs = [d for d in template_dirs if os.path.isdir(d)]
            
            self.env_cache[lang] = Environment(
                loader=FileSystemLoader(existing_dirs),
                autoescape=False,
                trim_blocks=True,
                lstrip_blocks=True
            )
            # tojson lisible : le filtre Jinja par défaut échappe les accents
            # (détail → d\u00e9tail). JSON toujours valide et parsable,
            # juste lisible pour l'humain (logs, vue HTML). Modèle indifférent.
            self.env_cache[lang].filters["tojson"] = (
                lambda value, *args, **kwargs: json.dumps(
                    value, *args, **{**{"ensure_ascii": False}, **kwargs}
                )
            )
            self.env_cache[lang].filters["args_sig"] = args_signature
            self.env_cache[lang].filters["registry_compact"] = registry_compact
        return self.env_cache[lang]

    def _fallback_render(self, template_name: str, lang: str, kwargs: Dict[str, Any]) -> str:
        """Fallback basique si Jinja2 n'est pas disponible."""
        template_dirs = [
            os.path.join(self.base_dir, lang),
            os.path.join(self.base_dir, "base"),
            self.base_dir
        ]
        
        content = None
        for d in template_dirs:
            p = os.path.join(d, template_name)
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read()
                break
                
        if content is None:
            raise FileNotFoundError(f"Template {template_name} non trouvé dans {template_dirs}")
            
        content = re.sub(r"\{#.*?#\}", "", content, flags=re.S)
        parts = re.split(r"(\{%.*?%\}|\{\{.*?\}\})", content, flags=re.S)
        stack: List[Dict[str, Any]] = []
        output: List[str] = []

        def is_active() -> bool:
            return all(frame["active"] for frame in stack)

        def render_text(piece: str) -> str:
            def replace(match: re.Match) -> str:
                key = match.group(1)
                return str(kwargs[key]) if key in kwargs else ""

            return re.sub(r"\{\{\s*([A-Za-z_]\w*)\s*\}\}", replace, piece)

        for part in parts:
            if part.startswith("{%") and part.endswith("%}"):
                tag = part[2:-2].strip()
                if not tag:
                    continue
                head = tag.split(None, 1)[0]
                rest = tag[len(head):].strip()
                if head == "if":
                    parent = is_active()
                    condition = _eval_fallback_condition(rest, kwargs) if parent else False
                    stack.append({"active": condition, "taken": condition, "kind": "if"})
                elif head == "elif":
                    if not stack or stack[-1].get("kind") != "if":
                        raise _UnsafeConditional("elif sans if")
                    frame = stack[-1]
                    parent = all(item["active"] for item in stack[:-1])
                    if parent and not frame["taken"]:
                        condition = _eval_fallback_condition(rest, kwargs)
                        frame["active"] = condition
                        frame["taken"] = condition
                    else:
                        frame["active"] = False
                elif head == "else":
                    if not stack or stack[-1].get("kind") != "if":
                        raise _UnsafeConditional("else sans if")
                    frame = stack[-1]
                    parent = all(item["active"] for item in stack[:-1])
                    frame["active"] = parent and not frame["taken"]
                    frame["taken"] = True
                elif head == "endif":
                    if not stack or stack[-1].get("kind") != "if":
                        raise _UnsafeConditional("endif sans if")
                    stack.pop()
                else:
                    raise _UnsafeConditional(f"balise non supportee: {tag!r}")
            elif part.startswith("{{") and part.endswith("}}"):
                if is_active():
                    output.append(render_text(part))
            else:
                if is_active():
                    output.append(part)

        if stack:
            raise _UnsafeConditional("bloc conditionnel non ferme")
        rendered = "".join(output)
        if "{{" in rendered or "{%" in rendered:
            raise _UnsafeConditional("expression non resolue")
        return rendered

    def load(self, template_name: str, lang: Optional[str] = None, **kwargs) -> str:
        """
        Charge un template et le rend avec les variables fournies.
        Protège de façon agnostique le contexte contre les références circulaires.
        """
        if lang is None:
            lang = self.default_lang

        # Assainissement préventif des variables de contexte
        def _safe_ctx(val: Any, seen: set) -> Any:
            v_id = id(val)
            if v_id in seen:
                return "[Circular Reference]"
            if isinstance(val, dict):
                seen.add(v_id)
                res = {str(k): _safe_ctx(v, seen) for k, v in val.items()}
                seen.remove(v_id)
                return res
            elif isinstance(val, (list, tuple, set)):
                seen.add(v_id)
                res = [_safe_ctx(item, seen) for item in val]
                seen.remove(v_id)
                return res
            return val

        safe_kwargs = {k: _safe_ctx(v, set()) for k, v in kwargs.items()}
            
        if not JINJA2_AVAILABLE:
            try:
                return self._fallback_render(template_name, lang, safe_kwargs)
            except Exception as e:
                if lang != self.default_lang:
                    try:
                        return self._fallback_render(template_name, self.default_lang, safe_kwargs)
                    except Exception:
                        pass
                Logger.error(f"[PromptLoader] Impossible de charger '{template_name}' via fallback : {e}")
                return f"Prompt template '{template_name}'"
                
        env = self._get_env(lang)
        try:
            template = env.get_template(template_name)
            rendered = template.render(**safe_kwargs)
            self._maybe_record(template_name, lang, rendered, safe_kwargs)
            return rendered
        except Exception as e:
            if lang != self.default_lang:
                try:
                    env_default = self._get_env(self.default_lang)
                    template = env_default.get_template(template_name)
                    rendered = template.render(**safe_kwargs)
                    self._maybe_record(template_name, self.default_lang, rendered, safe_kwargs)
                    return rendered
                except Exception:
                    pass
            Logger.error(f"[PromptLoader] Impossible de charger '{template_name}' via jinja2 : {e}")
            return f"Prompt template '{template_name}'"

    _record_counter = 0

    def _maybe_record(self, template_name: str, lang: str, rendered: str, variables: dict) -> None:
        """Enregistre le prompt rendu pour inspection live (opt-in).

        Actif si MANAGENT_RECORD_PROMPTS=1. Écrit dans ./prompts_log/
        (donc data dir au runtime) : contenu rendu + NOMS des variables
        (jamais les valeurs : secrets). Best-effort, jamais bloquant.
        """
        try:
            import os as _os
            if _os.environ.get("MANAGENT_RECORD_PROMPTS") != "1":
                return
            PromptLoader._record_counter += 1
            safe_base = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in template_name)
            out_dir = _os.path.join(_os.getcwd(), "prompts_log")
            _os.makedirs(out_dir, exist_ok=True)
            fname = f"{PromptLoader._record_counter:04d}_{safe_base}.{lang}.md"
            with open(_os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
                f.write(rendered)
            with open(_os.path.join(out_dir, fname + ".vars.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(sorted(str(k) for k in (variables or {}).keys())))
        except Exception:
            pass

_loader: Optional[PromptLoader] = None

def get_prompt_loader() -> PromptLoader:
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader
