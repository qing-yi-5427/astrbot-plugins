"""Guarded, reversible runtime overrides for the official LivingMemory plugin."""

import ast
import asyncio
import hashlib
import inspect
import json
import math
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

from astrbot.api import logger

from .memory_evidence import contextual_mentions, focus_recall_query, split_evidence, is_embedding_transport_error


class CompatibilityPatch:
    def __init__(self):
        self.manifest = json.loads((Path(__file__).parent / "overrides.json").read_text())
        self.applied = []
        self.state = "waiting"
        self.reason = "LivingMemory has not loaded yet"
        self._reported = None

    @staticmethod
    def _module(suffix):
        return next((m for name, m in list(sys.modules.items())
                     if name == suffix or name.endswith("." + suffix)), None)

    @staticmethod
    def _hash(function):
        source = textwrap.dedent(inspect.getsource(function))
        return CompatibilityPatch._source_hash(source)

    @staticmethod
    def _source_hash(source):
        node = ast.parse(source).body[0]
        # Source segments and inspect.getsource indent docstrings differently.
        # Documentation edits are not an API change; compare executable syntax.
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if child.body and isinstance(child.body[0], ast.Expr) and isinstance(child.body[0].value, ast.Constant) and isinstance(child.body[0].value.value, str):
                    child.body = child.body[1:]
        return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()

    def apply(self):
        actions = []
        try:
            for spec in self.manifest["patches"]:
                module = self._module(spec["module"])
                if module is None:
                    self.state, self.reason = "waiting", "LivingMemory is not fully initialized"
                    return False
                owner = getattr(module, spec["owner"]) if spec["owner"] else module
                current = getattr(owner, spec["name"], None)
                if current is not None and getattr(current, "_memory_recall_fix", None) is self:
                    continue
                expected = self._source_hash(spec["original_source"]) if spec.get("original_source") else None
                if expected is None:
                    if current is not None:
                        raise RuntimeError(f"new helper already exists: {spec['name']}")
                elif current is None or self._hash(current) != expected:
                    raise RuntimeError(f"upstream implementation changed: {spec['module']}.{spec['name']}")
                namespace = module.__dict__
                # Helpers operate only inside the memory plugin's module namespace.
                helpers = {"replace": replace, "math": math, "focus_recall_query": focus_recall_query,
                           "split_evidence": split_evidence, "contextual_mentions": contextual_mentions,
                           "asyncio": asyncio, "logger": logger,
                           "is_embedding_transport_error": is_embedding_transport_error}
                if spec.get("wrap_original"):
                    helpers[spec["wrap_original"]] = current
                actions.append((owner, current, spec, namespace, helpers))
            if not actions:
                self.state, self.reason = "active", "All guarded overrides are active"
                return True
            compiled = []
            for owner, current, spec, namespace, helpers in actions:
                working = dict(namespace)
                working.update(helpers)
                exec(compile(spec["source"], f"<memory-recall-fix:{spec['name']}>", "exec"), working)
                function = working[spec["name"]]
                # Functions resolve live module globals, including tool context and
                # providers recreated after an official plugin reload.
                from types import FunctionType
                function = FunctionType(function.__code__, namespace, function.__name__,
                                        function.__defaults__, function.__closure__)
                function.__kwdefaults__ = working[spec["name"]].__kwdefaults__
                function._memory_recall_fix = self
                compiled.append((owner, current, spec, namespace, helpers, function))
            for owner, original, spec, namespace, helpers, function in compiled:
                saved_globals = {name: (name in namespace, namespace.get(name)) for name in helpers}
                namespace.update(helpers)
                setattr(owner, spec["name"], function)
                self.applied.append((owner, spec["name"], original, function, namespace, helpers, saved_globals))
            self.state, self.reason = "active", "All guarded overrides are active"
            logger.info("[memory_recall_fix] Compatible runtime fixes applied (%s overrides); official source is unchanged", len(self.applied))
            return True
        except Exception as exc:
            self.restore()
            self.state, self.reason = "incompatible", str(exc)
            if self.reason != self._reported:
                logger.warning("[memory_recall_fix] Compatibility check failed; using official behavior: %s", self.reason)
                self._reported = self.reason
            return False

    def restore(self):
        for owner, name, original, function, namespace, helpers, saved in reversed(self.applied):
            if getattr(owner, name, None) is function:
                if original is None:
                    delattr(owner, name)
                else:
                    setattr(owner, name, original)
            for key, value in helpers.items():
                if namespace.get(key) is value:
                    present, old = saved[key]
                    if present:
                        namespace[key] = old
                    else:
                        namespace.pop(key, None)
        self.applied.clear()
        self.state, self.reason = "inactive", "Official methods restored"
