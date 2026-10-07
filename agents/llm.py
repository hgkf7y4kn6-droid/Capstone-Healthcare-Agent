"""
Local LLM loader — no API key, no account, no cloud provider.

Loads a small open-weight instruction-tuned model via the `transformers`
package. This is the one place in the codebase that knows how the model is
actually loaded; everywhere else calls either `generate()` for a plain
one-shot text completion (used by evaluation/*.py), or `get_crewai_llm()`
to get a `crewai.BaseLLM` instance for use in `Agent(llm=...)` (used by
agents/crew.py).

Why a custom crewai.BaseLLM instead of CrewAI's built-in `LLM` class? CrewAI's
native `LLM` class routes through LiteLLM, which targets hosted API
providers (OpenAI, Anthropic, a remote Hugging Face Inference endpoint,
Ollama, etc.) — it has no built-in way to drive an in-process `transformers`
pipeline directly. CrewAI's documented extension point for exactly this case
is subclassing `crewai.BaseLLM` and implementing `call()` — see
https://docs.crewai.com/en/learn/custom-llm ("This allows you to integrate
any LLM provider that doesn't have built-in support in LiteLLM").

Tool-calling: `supports_function_calling()` is overridden to return False.
Small local open models generally don't support reliable native function-
calling, so this tells CrewAI to use its own internal text-based
Thought/Action/Observation tool-use protocol (the same approach CrewAI's
CrewAgentExecutor uses by default for any model, function-calling-capable
or not) rather than trying to pass a structured `tools=` schema and expect
back a structured tool-call response.

First run will download the model weights from Hugging Face (a few hundred
MB to a few GB depending on LOCAL_LLM_MODEL) and cache them locally
(~/.cache/huggingface by default) — this needs network access once, but no
account or API key. Every run after that is fully offline.
"""

import os
import sys
import threading
from typing import Any, Dict, List, Optional, Union

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LOCAL_LLM_MODEL, LOCAL_LLM_DEVICE, LOCAL_LLM_TEMPERATURE, LOCAL_LLM_MAX_NEW_TOKENS

_PIPELINE_CACHE = {}
_PIPELINE_LOCK = threading.Lock()


def _load_pipeline(model_id: str, device: str, max_new_tokens: int):
    """Builds (and process-wide caches) a raw transformers text-generation
    pipeline. Loading model weights is slow (seconds to minutes depending
    on model size and hardware) and memory-heavy, so this only happens once
    per unique model/device, not per request. Double-checked locking: both
    Streamlit and FastAPI can serve concurrent requests on separate
    threads, so without a lock, two threads racing on first use could each
    see an empty cache and both load a multi-GB model into memory at once."""
    import transformers

    cache_key = (model_id, device)
    if cache_key in _PIPELINE_CACHE:
        return _PIPELINE_CACHE[cache_key]

    with _PIPELINE_LOCK:
        if cache_key in _PIPELINE_CACHE:  # re-check: another thread may have just finished
            return _PIPELINE_CACHE[cache_key]
        pipe = transformers.pipeline(
            "text-generation",
            model=model_id,
            device=-1 if device == "cpu" else None,
            device_map="auto" if device == "cuda" else None,
            max_new_tokens=max_new_tokens,
            return_full_text=False,
        )
        _PIPELINE_CACHE[cache_key] = pipe
        return pipe


def generate(messages: List[Dict[str, str]], temperature: Optional[float] = None,
             max_new_tokens: Optional[int] = None) -> str:
    """Plain one-shot text generation: OpenAI-style messages in
    ([{"role": "system"/"user"/"assistant", "content": "..."}]), generated
    text out. Uses the model's own chat template (via the tokenizer) so the
    prompt is formatted the way the model was actually instruction-tuned
    for. This is the single lowest-level entry point — both
    LocalCrewLLM.call() (below) and evaluation/*.py's one-shot judge/
    synthesis calls go through this."""
    try:
        pipe = _load_pipeline(LOCAL_LLM_MODEL, LOCAL_LLM_DEVICE, LOCAL_LLM_MAX_NEW_TOKENS)
    except ImportError as e:
        raise RuntimeError(
            "Local LLM dependencies are not installed. Run "
            "`pip install -r requirements.txt` (needs transformers, torch, "
            f"accelerate). Original error: {e}"
        )

    temp = LOCAL_LLM_TEMPERATURE if temperature is None else temperature
    gen_kwargs = {
        "temperature": max(temp, 0.01),  # 0.0 breaks some sampling configs
        "do_sample": temp > 0.0,
        "repetition_penalty": 1.1,
    }
    if max_new_tokens is not None:
        gen_kwargs["max_new_tokens"] = max_new_tokens

    output = pipe(messages, **gen_kwargs)
    # transformers' text-generation pipeline, given a chat-style message
    # list, returns [{"generated_text": [...full conversation including the
    # new assistant turn...]}] — the new turn is always the last message.
    result = output[0]["generated_text"]
    if isinstance(result, list):
        return result[-1].get("content", "")
    return str(result)


def _build_local_crew_llm_class():
    """Defines the actual BaseLLM subclass at call time, so `import
    agents.llm` doesn't hard-require `crewai` to be installed — only
    get_crewai_llm() does. Consistent with the graceful-degradation pattern
    used throughout this project (e.g. optional Tavily/FAISS/Celery)."""
    from crewai import BaseLLM

    class _LocalCrewLLM(BaseLLM):
        def __init__(self, model: str, temperature: Optional[float] = None,
                     max_new_tokens: Optional[int] = None):
            super().__init__(model=model, temperature=temperature)
            self.max_new_tokens = max_new_tokens

        def call(
            self,
            messages: Union[str, List[Dict[str, str]]],
            tools: Optional[List[dict]] = None,
            callbacks: Optional[List[Any]] = None,
            available_functions: Optional[Dict[str, Any]] = None,
        ) -> Union[str, Any]:
            if isinstance(messages, str):
                messages = [{"role": "user", "content": messages}]
            return generate(messages, temperature=self.temperature, max_new_tokens=self.max_new_tokens)

        def supports_function_calling(self) -> bool:
            # Force CrewAI's text-based Thought/Action/Observation tool
            # protocol rather than a structured function-calling schema —
            # small local models don't reliably support the latter.
            return False

        def supports_stop_words(self) -> bool:
            return True

        def get_context_window_size(self) -> int:
            # Conservative default; most small instruction-tuned models
            # support at least this much. Override via a bigger
            # LOCAL_LLM_MODEL if you need more and know its real window.
            return 8192

    return _LocalCrewLLM


_CREW_LLM_CACHE = {}
_CREW_LLM_LOCK = threading.Lock()


def get_crewai_llm(temperature: Optional[float] = None):
    """Returns a cached crewai.BaseLLM instance backed by the local
    transformers pipeline, for use as Agent(llm=...)."""
    temp = LOCAL_LLM_TEMPERATURE if temperature is None else temperature
    cache_key = (LOCAL_LLM_MODEL, LOCAL_LLM_DEVICE, round(temp, 3))
    if cache_key in _CREW_LLM_CACHE:
        return _CREW_LLM_CACHE[cache_key]

    with _CREW_LLM_LOCK:
        if cache_key in _CREW_LLM_CACHE:
            return _CREW_LLM_CACHE[cache_key]
        try:
            local_llm_class = _build_local_crew_llm_class()
        except ImportError as e:
            raise RuntimeError(
                "crewai is not installed. Run `pip install -r requirements.txt`. "
                f"Original error: {e}"
            )
        _CREW_LLM_CACHE[cache_key] = local_llm_class(
            model=LOCAL_LLM_MODEL, temperature=temp, max_new_tokens=LOCAL_LLM_MAX_NEW_TOKENS
        )
        return _CREW_LLM_CACHE[cache_key]


def is_model_downloaded() -> bool:
    """Best-effort check for whether LOCAL_LLM_MODEL is already cached
    locally, so callers (e.g. the Streamlit UI) can warn the user before a
    slow first-run download rather than let it happen silently mid-request."""
    try:
        from huggingface_hub import scan_cache_dir
        cache_info = scan_cache_dir()
        return any(repo.repo_id == LOCAL_LLM_MODEL for repo in cache_info.repos)
    except Exception:
        return False
