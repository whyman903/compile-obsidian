from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import yaml

from compile.config import Config
from compile.page_ir import (
    PageDraft,
    PagePatch,
    PageSection,
    apply_page_patch,
    coerce_page_artifact,
    has_managed_sections,
    render_page_draft,
)
from compile.source_packet import SourcePacket
from compile.verify import FILLER_PHRASES


def deterministic_cleanup(content: str) -> str:
    """Remove known fluff patterns without LLM cost."""
    # 1. Remove filler phrases (case-insensitive)
    for phrase in FILLER_PHRASES:
        content = re.sub(re.escape(phrase), "", content, flags=re.IGNORECASE)

    # 2. Remove empty sentences left behind (". ." or ". ,")
    content = re.sub(r"\.\s*\.", ".", content)
    content = re.sub(r",\s*\.", ".", content)

    # 3. Remove "This section discusses..." / "In this section we..." openers
    content = re.sub(
        r"(?i)(?:this section|in this section|here we|we discuss|we present|this page)\s+"
        r"(?:discusses?|presents?|provides?|describes?|outlines?|explores?|examines?|covers?)\s+",
        "",
        content,
    )

    # 4. Collapse multiple blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)

    # 5. Remove trailing whitespace on lines
    content = "\n".join(line.rstrip() for line in content.splitlines())

    return content.strip()


MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
    "claude-haiku-3-5-20241022": {"input": 0.80, "output": 4.0},
    "default": {"input": 3.0, "output": 15.0},
}


@dataclass
class UsageTracker:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    by_method: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, method: str, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls += 1
        if method not in self.by_method:
            self.by_method[method] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        self.by_method[method]["input_tokens"] += input_tokens
        self.by_method[method]["output_tokens"] += output_tokens
        self.by_method[method]["calls"] += 1

    def estimated_cost(self, model: str = "") -> float:
        pricing = MODEL_PRICING.get(model, MODEL_PRICING.get("default", {}))
        input_rate = pricing.get("input", 3.0)
        output_rate = pricing.get("output", 15.0)
        return (self.input_tokens * input_rate + self.output_tokens * output_rate) / 1_000_000

    def summary(self, model: str = "") -> str:
        cost = self.estimated_cost(model)
        return f"Tokens: {self.input_tokens:,} in / {self.output_tokens:,} out | Est. cost: ${cost:.2f} | Calls: {self.calls}"


class Compiler:
    """All LLM calls for wiki compilation."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.usage = UsageTracker()
        if not config.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is required. Set it in .env or your environment."
            )

    def analyze_image(self, image_path: "Path", context: str = "") -> dict[str, Any]:
        """Analyze an image using Claude's vision API.

        Returns a dict with description, text_content, diagram_type,
        key_claims, entities, and data_points extracted from the image.
        Gated behind config.vision_enabled.
        """
        import base64

        if not self.config.vision_enabled:
            return {
                "description": "Vision analysis is disabled in config.",
                "text_content": "",
                "diagram_type": "unknown",
                "key_claims": [],
                "entities": [],
                "data_points": [],
            }

        suffix = image_path.suffix.lower().lstrip(".")
        media_types = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        media_type = media_types.get(suffix, "image/png")

        image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")

        user_content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_data,
                },
            },
            {
                "type": "text",
                "text": (
                    f'Analyze this image for a maintained wiki on "{self.config.topic}". {context}\n\n'
                    "Return JSON with: "
                    '"description" (what the image shows), '
                    '"text_content" (any OCR text visible), '
                    '"diagram_type" (chart/table/diagram/photo/other), '
                    '"key_claims" (array of factual claims extractable from the image), '
                    '"entities" (named entities visible), '
                    '"data_points" (any specific numbers/values).'
                ),
            },
        ]

        system = "You are a vision analysis assistant for a maintained wiki. Return valid JSON only."

        last_error: Exception | None = None
        response: httpx.Response | None = None
        for attempt in range(4):
            try:
                response = httpx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.config.anthropic_api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.config.anthropic_model,
                        "max_tokens": 4096,
                        "temperature": 0.2,
                        "system": system,
                        "messages": [{"role": "user", "content": user_content}],
                    },
                    timeout=120.0,
                )
                if response.status_code == 200:
                    break
                if response.status_code not in {408, 409, 429, 500, 502, 503, 504} or attempt == 3:
                    try:
                        error_body = response.json()
                        error_msg = error_body.get("error", {}).get("message", response.text[:200])
                    except Exception:
                        error_msg = response.text[:200]
                    raise RuntimeError(f"Anthropic API error ({response.status_code}): {error_msg}")
                time.sleep(min(8, 2 ** attempt))
            except (httpx.TimeoutException, httpx.TransportError) as error:
                last_error = error
                if attempt == 3:
                    raise RuntimeError(f"Anthropic API transport error: {error}") from error
                time.sleep(min(8, 2 ** attempt))

        if response is None or response.status_code != 200:
            if last_error is not None:
                raise RuntimeError(f"Anthropic API transport error: {last_error}") from last_error
            raise RuntimeError("Anthropic API request failed without a response.")

        payload = response.json()

        # Track token usage
        usage = payload.get("usage", {})
        resp_input_tokens = usage.get("input_tokens", 0)
        resp_output_tokens = usage.get("output_tokens", 0)
        if resp_input_tokens or resp_output_tokens:
            self.usage.record("analyze_image", resp_input_tokens, resp_output_tokens)

        parts = payload.get("content", [])
        text = "\n".join(
            part.get("text", "") for part in parts if part.get("type") == "text"
        )
        # Strip code fences
        text = re.sub(r"^\s*```(?:json)?\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        # Return a minimal result if JSON parsing fails
        return {
            "description": text.strip()[:500],
            "text_content": "",
            "diagram_type": "unknown",
            "key_claims": [],
            "entities": [],
            "data_points": [],
        }

    def analyze_source(self, title: str, text: str) -> dict[str, Any]:
        """Analyze a source document and extract structured notes."""
        max_chars = 20000
        truncated = text[:max_chars]
        warnings: list[str] = []
        if len(text) > max_chars:
            warnings.append(
                f"Source text exceeded {max_chars} characters; analysis used a truncated view."
            )
        prompt = self._source_analysis_prompt(
            title=title,
            source_body=f"Source text:\n{truncated}",
        )

        result = self._call_json(
            system="You extract structured, faithful source analyses for a maintained wiki. Output valid JSON only.",
            prompt=prompt,
            max_tokens=2800,
            method_name="analyze_source",
        )
        result["_source_text"] = text
        if warnings:
            result["analysis_warnings"] = warnings
        return self._finalize_source_analysis(result)

    def analyze_source_packet(self, packet: SourcePacket) -> dict[str, Any]:
        """Analyze a source packet, using chunk-aware analysis for long sources."""
        if packet.source_type == "pdf":
            result = self._analyze_pdf_packet(packet)
            result.setdefault("analysis_warnings", []).extend(packet.warnings)
            return self._finalize_source_analysis(result)

        if len(packet.analysis_text) <= 24000 and len(packet.chunks) <= 1:
            result = self.analyze_source(packet.title, packet.analysis_text)
            result.setdefault("analysis_warnings", []).extend(packet.warnings)
            return self._finalize_source_analysis(result)

        chunk_payloads = []
        for chunk in packet.chunks[:8]:
            chunk_payloads.append(self._analyze_source_chunk(packet, chunk))

        merged = self._merge_chunk_analyses(packet, chunk_payloads)
        warnings = list(dict.fromkeys([*packet.warnings, *merged.get("analysis_warnings", [])]))
        if warnings:
            merged["analysis_warnings"] = warnings
        merged["_source_text"] = packet.full_text
        return self._finalize_source_analysis(merged)

    def _analyze_source_chunk(self, packet: SourcePacket, chunk: Any) -> dict[str, Any]:
        prompt = f"""You are analyzing one chunk of a source document for a maintained wiki on "{self.config.topic}".

Source title: {packet.title}
Chunk label: {getattr(chunk, 'label', 'Chunk')}
Raw path: {packet.raw_path}

Chunk text:
{getattr(chunk, 'text', '')}

Return strict JSON with these keys:
- "summary"
- "key_claims"
- "methods"
- "metrics"
- "equations"
- "limitations"
- "concepts"
- "entities"
- "open_questions"
- "tags"

Rules:
- Extract only what is in this chunk.
- Keep numbers exact.
- Keep equations in LaTeX when explicit.
- Do not do cross-chunk synthesis.
- Output JSON only."""
        result = self._call_json(
            system="You extract chunk-local evidence from source documents for a maintained wiki. Output valid JSON only.",
            prompt=prompt,
            max_tokens=1800,
            method_name="analyze_source_chunk",
        )
        result["chunk_label"] = getattr(chunk, "label", "Chunk")
        return self._finalize_source_analysis(result, preserve_profile=False)

    def _merge_chunk_analyses(self, packet: SourcePacket, chunk_payloads: list[dict[str, Any]]) -> dict[str, Any]:
        prompt = f"""You are merging chunk-level analyses into one faithful source packet for a maintained wiki on "{self.config.topic}".

Source title: {packet.title}
Raw path: {packet.raw_path}

Chunk analyses:
{json.dumps(chunk_payloads, indent=2)}

Return strict JSON with the same keys as a full source analysis:
- "title"
- "summary"
- "key_claims"
- "methods"
- "metrics"
- "equations"
- "limitations"
- "concepts"
- "entities"
- "open_questions"
- "tags"
- "analysis_warnings"

Rules:
- Deduplicate overlapping claims.
- Preserve exact numbers and equations when explicit.
- Prefer compression over narration.
- Do not invent cross-chunk claims that are not supported by the chunk analyses.
- Mention in analysis_warnings that the packet was merged from chunks.

Output JSON only."""
        merged = self._call_json(
            system="You merge chunk-local evidence into one faithful source analysis for a maintained wiki. Output valid JSON only.",
            prompt=prompt,
            method_name="merge_chunk_analyses",
            max_tokens=2600,
        )
        warnings = list(merged.get("analysis_warnings", []) or [])
        warnings.append("Source analysis was merged from multiple chunks.")
        merged["analysis_warnings"] = list(dict.fromkeys(str(item).strip() for item in warnings if str(item).strip()))
        return merged

    def _analyze_pdf_packet(self, packet: SourcePacket) -> dict[str, Any]:
        raw_path = self.config.workspace_root / packet.raw_path
        pdf_bytes = raw_path.read_bytes()
        content_blocks = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(pdf_bytes).decode("utf-8"),
                },
                "title": packet.title,
                "context": (
                    f"Research wiki topic: {self.config.topic}. "
                    f"Raw path: {packet.raw_path}."
                ),
            },
            {
                "type": "text",
                "text": self._source_analysis_prompt(
                    title=packet.title,
                    source_body=(
                        "Analyze the attached PDF directly. "
                        "Use both textual and visual PDF content, including tables, charts, figures, and equations when present."
                    ),
                ),
            },
        ]
        result = self._call_json(
            system="You extract structured, faithful source analyses for a maintained wiki. Output valid JSON only.",
            prompt=content_blocks,
            max_tokens=2800,
            method_name="analyze_pdf",
        )
        result["_source_text"] = packet.analysis_text
        warnings = list(result.get("analysis_warnings", []) or [])
        warnings.append("Source analysis used Anthropic native PDF support.")
        result["analysis_warnings"] = list(
            dict.fromkeys(str(item).strip() for item in warnings if str(item).strip())
        )
        return result

    def _finalize_source_analysis(
        self,
        analysis: dict[str, Any],
        *,
        preserve_profile: bool = True,
    ) -> dict[str, Any]:
        normalized = dict(analysis)
        if not normalized.get("concepts") and normalized.get("themes"):
            normalized["concepts"] = list(normalized.get("themes") or [])
        for key in ("concepts", "entities", "methods", "limitations", "open_questions", "tags"):
            normalized[key] = self._coerce_text_list(normalized.get(key))
        normalized["metrics"] = list(normalized.get("metrics") or [])
        normalized["equations"] = list(normalized.get("equations") or [])
        normalized["key_claims"] = self._normalize_claims(normalized.get("key_claims"))
        profile = self._source_profile_for_analysis(normalized, preserve_profile=preserve_profile)
        normalized["source_profile"] = profile
        normalized["evidence_atoms"] = self._evidence_atoms_for_analysis(normalized, profile=profile)
        return normalized

    def _coerce_text_list(self, value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()]

    def _normalize_claims(self, claims: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in claims or []:
            if isinstance(item, dict):
                payload = dict(item)
            else:
                payload = {"text": str(item).strip()}
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            payload["text"] = text
            payload["concepts"] = self._coerce_text_list(payload.get("concepts"))
            payload["entities"] = self._coerce_text_list(payload.get("entities"))
            confidence = payload.get("confidence")
            payload["confidence"] = self._coerce_float(confidence, default=0.9)
            normalized.append(payload)
        return normalized

    def _coerce_float(self, value: Any, *, default: float) -> float:
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    def _source_profile_for_analysis(
        self,
        source_analysis: dict[str, Any],
        *,
        preserve_profile: bool = True,
    ) -> dict[str, Any]:
        raw_profile = source_analysis.get("source_profile")
        profile = dict(raw_profile) if preserve_profile and isinstance(raw_profile, dict) else {}

        source_kind = str(profile.get("source_kind") or "").strip() or self._infer_source_kind(source_analysis)
        evidence_mode = str(profile.get("evidence_mode") or "").strip() or self._infer_evidence_mode(source_analysis, source_kind=source_kind)
        time_orientation = str(profile.get("time_orientation") or "").strip() or self._infer_time_orientation(source_analysis, source_kind=source_kind)
        roles = [
            role
            for role in self._coerce_text_list(profile.get("recommended_page_roles"))
            if role in {"source", "concept", "entity", "question", "comparison", "timeline", "output"}
        ]
        if not roles:
            roles = self._recommended_page_roles(
                source_analysis,
                source_kind=source_kind,
                evidence_mode=evidence_mode,
                time_orientation=time_orientation,
            )
        section_family = str(profile.get("recommended_section_family") or "").strip() or self._recommended_section_family(
            source_kind=source_kind,
            evidence_mode=evidence_mode,
            time_orientation=time_orientation,
        )
        return {
            "source_kind": source_kind,
            "evidence_mode": evidence_mode,
            "time_orientation": time_orientation,
            "recommended_page_roles": roles,
            "recommended_section_family": section_family,
        }

    def _infer_source_kind(self, source_analysis: dict[str, Any]) -> str:
        title = str(source_analysis.get("title") or "").casefold()
        blob = " ".join(
            [
                title,
                str(source_analysis.get("summary") or "").casefold(),
                " ".join(str(item.get("text") or "").casefold() for item in source_analysis.get("key_claims", []) or [] if isinstance(item, dict)),
            ]
        )
        if any(marker in title for marker in ("journal", "diary", "entry", "daily log", "work log")):
            return "journal_entry"
        if any(marker in blob for marker in ("thesis", "premise", "objection", "therefore", "ought", "epistem", "metaphys", "moral argument")):
            return "philosophy_text"
        if source_analysis.get("metrics") or source_analysis.get("equations") or source_analysis.get("methods"):
            return "empirical_paper"
        if any(marker in title for marker in ("essay", "article")):
            return "essay"
        return "theoretical_paper"

    def _infer_evidence_mode(self, source_analysis: dict[str, Any], *, source_kind: str) -> str:
        if source_kind == "journal_entry":
            return "reflective"
        if source_kind in {"philosophy_text", "essay"}:
            return "argumentative"
        if source_analysis.get("metrics") or source_analysis.get("equations") or source_analysis.get("methods"):
            return "empirical"
        if self._infer_time_orientation(source_analysis, source_kind=source_kind) == "chronological":
            return "narrative"
        return "mixed"

    def _infer_time_orientation(self, source_analysis: dict[str, Any], *, source_kind: str) -> str:
        if source_kind == "journal_entry":
            return "chronological"
        text = " ".join(
            [
                str(source_analysis.get("title") or ""),
                str(source_analysis.get("summary") or ""),
                " ".join(self._coerce_text_list(source_analysis.get("open_questions"))),
            ]
        ).casefold()
        if re.search(r"\b(19|20)\d{2}\b", text):
            return "chronological"
        if any(marker in text for marker in ("today", "yesterday", "tomorrow", "monday", "tuesday", "january", "february", "march")):
            return "dated"
        return "timeless"

    def _recommended_page_roles(
        self,
        source_analysis: dict[str, Any],
        *,
        source_kind: str,
        evidence_mode: str,
        time_orientation: str,
    ) -> list[str]:
        roles = ["source"]
        if evidence_mode in {"empirical", "argumentative", "mixed", "reflective", "narrative"}:
            roles.append("concept")
        if source_analysis.get("entities"):
            roles.append("entity")
        if evidence_mode in {"empirical", "argumentative", "mixed"}:
            roles.append("question")
        if evidence_mode == "argumentative":
            roles.append("comparison")
        if source_kind == "journal_entry" or time_orientation == "chronological":
            roles.append("timeline")
        return list(dict.fromkeys(roles))

    def _recommended_section_family(
        self,
        *,
        source_kind: str,
        evidence_mode: str,
        time_orientation: str,
    ) -> str:
        if source_kind == "journal_entry" or evidence_mode in {"reflective", "narrative"} or time_orientation == "chronological":
            return "journal_note"
        if source_kind in {"philosophy_text", "essay"} or evidence_mode == "argumentative":
            return "argument_note"
        if evidence_mode == "mixed":
            return "mixed_note"
        return "empirical_note"

    def _evidence_atoms_for_analysis(
        self,
        source_analysis: dict[str, Any],
        *,
        profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        atoms: list[dict[str, Any]] = []
        raw_atoms = source_analysis.get("evidence_atoms") or []
        if isinstance(raw_atoms, list) and raw_atoms:
            for item in raw_atoms:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                atoms.append(
                    {
                        "kind": str(item.get("kind") or "claim").strip() or "claim",
                        "text": text,
                        "explicitness": self._coerce_float(item.get("explicitness", item.get("confidence", 0.9)), default=0.9),
                        "themes": self._coerce_text_list(item.get("themes") or item.get("concepts")),
                        "entities": self._coerce_text_list(item.get("entities")),
                        "time_anchor": str(item.get("time_anchor") or "").strip(),
                        "stance": str(item.get("stance") or "neutral").strip() or "neutral",
                        "evidence_mode": str(item.get("evidence_mode") or profile.get("evidence_mode") or "mixed").strip(),
                    }
                )
        if atoms:
            return atoms[:16]

        default_kind = {
            "empirical": "claim",
            "argumentative": "argument",
            "reflective": "reflection",
            "narrative": "event",
        }.get(str(profile.get("evidence_mode") or ""), "observation")
        for claim in source_analysis.get("key_claims", []) or []:
            text = str(claim.get("text") or "").strip() if isinstance(claim, dict) else str(claim).strip()
            if not text:
                continue
            atoms.append(
                {
                    "kind": default_kind,
                    "text": text,
                    "explicitness": self._coerce_float(claim.get("confidence", 0.9), default=0.9) if isinstance(claim, dict) else 0.9,
                    "themes": self._coerce_text_list(claim.get("concepts") if isinstance(claim, dict) else source_analysis.get("concepts")),
                    "entities": self._coerce_text_list(claim.get("entities") if isinstance(claim, dict) else source_analysis.get("entities")),
                    "time_anchor": "",
                    "stance": "neutral",
                    "evidence_mode": str(profile.get("evidence_mode") or "mixed"),
                }
            )
        for item in self._coerce_text_list(source_analysis.get("open_questions"))[:4]:
            atoms.append(
                {
                    "kind": "question",
                    "text": item,
                    "explicitness": 0.8,
                    "themes": self._coerce_text_list(source_analysis.get("concepts")),
                    "entities": self._coerce_text_list(source_analysis.get("entities")),
                    "time_anchor": "",
                    "stance": "open",
                    "evidence_mode": str(profile.get("evidence_mode") or "mixed"),
                }
            )
        for item in self._coerce_text_list(source_analysis.get("limitations"))[:4]:
            atoms.append(
                {
                    "kind": "observation",
                    "text": item,
                    "explicitness": 0.85,
                    "themes": self._coerce_text_list(source_analysis.get("concepts")),
                    "entities": self._coerce_text_list(source_analysis.get("entities")),
                    "time_anchor": "",
                    "stance": "limitation",
                    "evidence_mode": str(profile.get("evidence_mode") or "mixed"),
                }
            )
        return atoms[:16]

    def _source_analysis_prompt(self, *, title: str, source_body: str) -> str:
        return f"""You are analyzing a source document for a maintained wiki on "{self.config.topic}".

Source title: {title}

{source_body}

Return strict JSON with these keys:
- "title": A clear, descriptive title for this source (use the original if good)
- "summary": A compressed 1-2 sentence summary of what matters most in the source
- "source_profile": Object with keys:
  - "source_kind": one of empirical_paper, theoretical_paper, philosophy_text, journal_entry, essay, meeting_note, article, book_chapter
  - "evidence_mode": one of empirical, argumentative, reflective, narrative, procedural, mixed
  - "time_orientation": one of timeless, dated, chronological
  - "recommended_page_roles": array drawn from source, concept, entity, question, comparison, timeline, output
  - "recommended_section_family": short identifier like empirical_note, argument_note, journal_note, mixed_note
- "key_claims": Array of 3-10 objects with keys:
  - "text": specific, concrete statement made in the source; for non-empirical material this can be a thesis, argument, observation, reflection, or decision
  - "confidence": float from 0.0 to 1.0 for how explicit the claim is in the source
  - "concepts": Array of reusable concepts this claim supports
  - "entities": Array of named entities this claim mentions
- "evidence_atoms": Array of 4-12 objects with keys:
  - "kind": claim, argument, event, decision, reflection, observation, distinction, or question
  - "text": exact or near-exact statement from the source
  - "explicitness": float from 0.0 to 1.0
  - "themes": Array of reusable themes this atom supports
  - "entities": Array of named entities mentioned in the atom
  - "time_anchor": explicit date or temporal marker when present
  - "stance": neutral, support, objection, limitation, open, or other compact label
  - "evidence_mode": empirical, argumentative, reflective, narrative, procedural, or mixed
- "methods": Array of 0-6 concise bullets describing method, setup, or procedure when applicable
- "metrics": Array of 0-10 objects with keys:
  - "label": metric name
  - "value": exact value or range as written
  - "context": what the metric refers to
- "equations": Array of 0-6 objects with keys when applicable:
  - "latex": equation in LaTeX when explicit in the source
  - "meaning": short explanation of what it represents
- "limitations": Array of 0-6 concise bullets describing limitations, caveats, or failure modes stated by the source
- "concepts": Array of 1-5 durable themes or concepts worth possible standalone tracking in the wiki (multi-word phrases preferred)
- "entities": Array of 0-5 named entities worth possible standalone tracking when they matter beyond this source
- "open_questions": Array of 0-3 unresolved questions raised by or about the source
- "tags": Array of 4-8 topical tags

Rules:
- Be faithful to the source. Do not invent claims.
- Preserve exact numbers instead of paraphrasing them.
- If a field does not apply, return an empty array or a compact default object instead of forcing paper-style content.
- `concepts`, `entities`, and `open_questions` should be standalone-page candidates only when they seem durable and reusable beyond this single source.
- For journals, essays, or philosophy texts, prefer reflections, arguments, distinctions, and decisions over fake metrics or methods.
- If no equations are explicit, return an empty equations array.
- Do not add motivational framing or significance language."""

    def plan_wiki_updates(
        self,
        source_analysis: dict[str, Any],
        index_content: str,
        existing_pages: dict[str, str],
        evidence_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Decide what wiki pages to create or update after ingesting a source."""
        pages_context = ""
        for path, content in existing_pages.items():
            # Include first ~800 chars of each relevant page
            pages_context += f"\n--- {path} ---\n{content[:800]}\n"
        evidence_text = json.dumps(evidence_context or {}, indent=2)

        prompt = f"""You are maintaining a wiki on "{self.config.topic}".

A new source has been analyzed:
{json.dumps(source_analysis, indent=2)}

Current wiki index:
{index_content}

Existing wiki pages that may be relevant:
{pages_context or "(No existing pages yet beyond the index and overview.)"}

Evidence and graph context:
{evidence_text}

Decide what wiki pages to create or update. Return strict JSON with key "operations", an array where each item has:
- "action": "create" or "update"
- "path": Relative path under wiki/ using the page title as the filename (e.g. "sources/Planner-Executor Loops.md", "concepts/Tool Grounding.md")
- "title": Page title (must match the filename stem exactly)
- "page_type": "source", "concept", "entity", or "question"
- "reason": Brief explanation of why this page should be created/updated
- "key_points": Array of key things to include or add
- "status": Optional maturity state for knowledge pages: "seed", "emerging", or "stable"

Rules:
- ALWAYS create a source page in sources/ for the new source
- Prefer UPDATE over CREATE when an existing concept, entity, or question page already covers the same idea
- Create concept pages for significant ideas that span or could span multiple sources
- Create entity pages only for important named entities worth tracking
- Create question pages for genuinely open questions worth investigating
- When updating an existing page, explain what new information to add
- Use the evidence context to find opportunities to deepen cross-source synthesis instead of creating more single-source leaves
- Be selective but dense. 4-12 operations is typical when the vault already has material.
- IMPORTANT: Filenames must use the page title with spaces, NOT kebab-case. Example: "concepts/Progressive Autonomy.md" not "concepts/progressive-autonomy.md". This is required for Obsidian wikilink resolution."""

        result = self._call_json(
            system="You plan updates to a maintained wiki. Output valid JSON only.",
            prompt=prompt,
            max_tokens=1500,
            method_name="plan_wiki_updates",
        )
        return result.get("operations", [])

    def plan_batch_updates(
        self,
        batch_analyses: list[dict[str, Any]],
        page_catalog: list[dict[str, Any]],
        evidence_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        compact_batch = [
            {
                "title": item.get("title"),
                "summary": item.get("summary"),
                "concepts": item.get("concepts", []),
                "entities": item.get("entities", []),
                "open_questions": item.get("open_questions", []),
                "source_profile": item.get("source_profile", {}),
                "claim_count": len(item.get("key_claims", []) or []),
                "metric_count": len(item.get("metrics", []) or []),
                "equation_count": len(item.get("equations", []) or []),
            }
            for item in batch_analyses
        ]
        prompt = f"""You are planning a GLOBAL batch update to a maintained wiki on "{self.config.topic}".

New source analyses in this batch:
{json.dumps(compact_batch, indent=2)}

Current page catalog:
{json.dumps(page_catalog, indent=2)}

Evidence context:
{json.dumps(evidence_context or {}, indent=2)}

        Return strict JSON with key "operations", where each item has:
        - action: "create" or "update"
        - title
        - page_type
        - reason
        - status
        - key_points

        Rules:
        - Think globally across the whole batch, not one source at a time.
        - Prefer updating existing pages over creating duplicates.
        - A source may touch many pages if the evidence warrants it.
        - Create exactly one source page per new source.
        - Only mark knowledge pages stable if multiple sources support them.
        - Be selective about question pages; create them only when the question is durable.
        - Prefer durable concept-like themes, entities, and questions that are mentioned by multiple sources in the batch or already exist in the wiki.
        - Avoid generic page titles like "Analysis", "Results", "Method", "Framework", or author-name pages.
        - `key_points` should be short, concrete bullets describing what the page should integrate from the batch evidence.

        Output JSON only."""
        result = self._call_json(
            system="You plan global batch updates to a maintained wiki. Output valid JSON only.",
            prompt=prompt,
            max_tokens=2200,
            method_name="plan_batch_updates",
        )
        return result.get("operations", [])

    def write_page(
        self,
        operation: dict[str, Any],
        source_analysis: dict[str, Any],
        existing_content: str | None,
        related_page_titles: list[str],
        raw_source_path: str = "",
    ) -> str:
        """Compile a page artifact and materialize it into markdown."""
        artifact_payload = self.compile_page_artifact(
            operation=operation,
            source_analysis=source_analysis,
            existing_content=existing_content,
            related_page_titles=related_page_titles,
            raw_source_path=raw_source_path,
        )
        action = operation.get("action", "create")
        page_type = operation.get("page_type", "source")
        title = operation.get("title", "Untitled")
        status = operation.get("status", "seed" if page_type in {"concept", "entity", "question"} else "stable")
        artifact = coerce_page_artifact(
            artifact_payload,
            fallback_title=title,
            fallback_page_type=page_type,
            fallback_status=status,
        )
        derived_status = self._derive_page_status(
            page_type=str(page_type),
            requested_status=str(status),
            source_analysis=source_analysis,
        )
        if isinstance(artifact, PagePatch):
            artifact.frontmatter_updates["status"] = derived_status
            if not existing_content or not has_managed_sections(existing_content):
                raise ValueError("Cannot apply a page patch to unmanaged or missing content.")
            return apply_page_patch(existing_content, artifact, raw_source_path=raw_source_path)
        artifact.status = derived_status
        artifact = self._ensure_substantive_draft(
            artifact,
            operation=operation,
            source_analysis=source_analysis,
            related_page_titles=related_page_titles,
        )
        return render_page_draft(artifact, existing_content=existing_content, raw_source_path=raw_source_path)

    def _derive_page_status(
        self,
        *,
        page_type: str,
        requested_status: str,
        source_analysis: dict[str, Any],
    ) -> str:
        if page_type not in {"concept", "entity", "question"}:
            return requested_status or "stable"
        support_count = len(self._supporting_sources(source_analysis))
        if support_count <= 1:
            return "seed"
        if requested_status in {"", "seed"}:
            return "stable"
        return requested_status or "stable"

    def compile_page_artifact(
        self,
        *,
        operation: dict[str, Any],
        source_analysis: dict[str, Any],
        existing_content: str | None,
        related_page_titles: list[str],
        raw_source_path: str = "",
    ) -> dict[str, Any]:
        page_type = str(operation.get("page_type", "source")).strip() or "source"
        if page_type == "source":
            return self._compile_source_page_draft(
                operation=operation,
                source_analysis=source_analysis,
                existing_content=existing_content,
                raw_source_path=raw_source_path,
            )
        if existing_content and has_managed_sections(existing_content):
            return self._compile_page_patch(
                operation=operation,
                source_analysis=source_analysis,
                existing_content=existing_content,
                related_page_titles=related_page_titles,
            )
        return self._compile_knowledge_page_draft(
            operation=operation,
            source_analysis=source_analysis,
            existing_content=existing_content,
            related_page_titles=related_page_titles,
        )

    def _compile_source_page_draft(
        self,
        *,
        operation: dict[str, Any],
        source_analysis: dict[str, Any],
        existing_content: str | None,
        raw_source_path: str,
    ) -> dict[str, Any]:
        title = str(operation.get("title") or source_analysis.get("title") or "Untitled").strip() or "Untitled"
        existing_note = f"\nExisting page content to migrate or improve:\n{existing_content}\n" if existing_content else ""
        source_profile = self._source_profile_for_analysis(source_analysis)
        section_shape = self._source_section_shape(source_profile)
        shape_text = "\n".join(f"- {section_id} | {heading}" for section_id, heading in section_shape)
        prompt = f"""You are compiling a source note for a maintained wiki on "{self.config.topic}".

Page title: {title}
Raw source path: {raw_source_path or "(unknown)"}
Source profile:
{json.dumps(source_profile, indent=2)}
Source analysis:
{json.dumps(source_analysis, indent=2)}
{existing_note}

Return strict JSON with keys:
- title
- page_type
- status
- summary
- tags
- sources
- source_ids
- cssclasses
- sections

`sections` must be an array of objects with keys:
- id
- heading
- body

Use this exact source-note shape:
{shape_text}

Rules:
- This should read like a compact wiki article, not a notebook dump.
- Start with what matters. No generic introduction.
- Preserve exact numbers and ranges where present.
- Use the source profile to decide whether this reads like empirical evidence, argumentation, narrative, or reflection.
- `synopsis` should be one short paragraph, optionally followed by up to three bullets.
- `claims`, `arguments`, `events`, `reflections`, `decisions`, `objections`, and `distinctions` should prefer concise bullets.
- `key_numbers` should be a markdown table body only when metrics exist.
- If equations are explicit, format each one as:
  ### Short Label

  $$...$$

  - Meaning: ...
- If there are no equations, write "- None explicit in source."
- Use bullets and tables instead of long prose whenever possible.
- Avoid bold lead-ins on every bullet.
- Do not write hype or framing. Banned phrases include: "significant advancement", "represents a", "fundamental", "notable", "innovative", "comprehensive", "robust framework", "novel approach".
- Do not add provenance; it will be inserted deterministically.
- `page_type` must be "source" and `status` must be "stable".
- Include the raw source path in `sources`.

Output JSON only."""
        return self._call_json(
            system="You write compressed, high-signal source notes for a maintained wiki. Output valid JSON only.",
            prompt=prompt,
            max_tokens=2200,
            method_name="compile_source_page_draft",
        )

    def _compile_knowledge_page_draft(
        self,
        *,
        operation: dict[str, Any],
        source_analysis: dict[str, Any],
        existing_content: str | None,
        related_page_titles: list[str],
    ) -> dict[str, Any]:
        page_type = str(operation.get("page_type", "concept")).strip() or "concept"
        title = str(operation.get("title") or "Untitled").strip() or "Untitled"
        status = str(operation.get("status") or ("seed" if page_type in {"concept", "entity", "question"} else "stable")).strip()
        page_mode = self._knowledge_page_mode(page_type=page_type, source_analysis=source_analysis)
        related_links = ", ".join(f"[[{title}]]" for title in related_page_titles[:12]) if related_page_titles else "none yet"
        existing_note = f"\nExisting page content to migrate or improve:\n{existing_content}\n" if existing_content else ""
        section_shape = self._section_shape_for_page_type(page_type, source_analysis=source_analysis)
        shape_text = "\n".join(f"- {section_id} | {heading}" for section_id, heading in section_shape)
        prompt = f"""You are compiling a {page_mode} {page_type} page for a maintained wiki on "{self.config.topic}".

Page title: {title}
Requested status: {status}
Page mode: {page_mode}
Reason: {operation.get("reason", "")}
Key points: {json.dumps(operation.get("key_points", []))}
Source profile:
{json.dumps(self._source_profile_for_analysis(source_analysis), indent=2)}
Source analysis:
{json.dumps(source_analysis, indent=2)}
Related pages already in the wiki: {related_links}
{existing_note}

        Return strict JSON with keys:
        - title
        - page_type
        - status
        - summary
        - tags
        - sources
        - source_ids
        - cssclasses
        - sections

        `sections` must be an array of objects with keys:
        - id
        - heading
        - body

        Return exactly one object for each required section below.
        Every `body` must contain substantive markdown. Never return "-", "TBD", or an empty placeholder.
        If evidence is limited, state that explicitly in the body.

        Use this exact section shape for this page type:
        {shape_text}

Rules:
- This is a maintained knowledge artifact, not an essay.
- Write it like a maintained wiki page, not a notebook dump.
- Prefer tables and bullets over prose.
- The first section should read like article prose, not bullet soup.
- Every claim must cite its source with [[Source Title]]. Do not write generic introductions. Start with specific claims.
- If this is a provisional page, keep it compact, evidence-led, and explicit about limitations. Do not invent agreements or tensions.
- If this is a synthesis page, compare sources directly and only surface agreements or tensions that the evidence supports.
- If only one source supports the page, say so explicitly and keep the page provisional.
- Use `> [!tension]` and `> [!open-question]` callouts when they improve readability.
- `Related` should be a plain wikilink list.
- Do not write generic motivation like "This concept is important because..."
- Use [[Page Title]] wikilinks only from this allowlist: {related_links}
- `sections` entries must contain markdown bodies only, no heading lines.
- Banned phrases: "significant advancement", "novel approach", "robust framework", "comprehensive overview", "groundbreaking", "state-of-the-art", "cutting-edge", "paradigm shift", "revolutionize", "game-changing", "exciting development", "promising results", "important contribution", "noteworthy", "it is worth noting", "it should be noted", "importantly", "interestingly", "in recent years", "has gained significant attention", "has attracted considerable interest", "plays a crucial role", "paves the way", "opens the door".

Output JSON only."""
        return self._call_json(
            system="You write compressed maintained knowledge pages with explicit sourcing. Every claim must cite its source with [[Source Title]]. Output valid JSON only.",
            prompt=prompt,
            max_tokens=2800,
            method_name="compile_knowledge_page_draft",
        )

    def _compile_page_patch(
        self,
        *,
        operation: dict[str, Any],
        source_analysis: dict[str, Any],
        existing_content: str,
        related_page_titles: list[str],
    ) -> dict[str, Any]:
        page_type = str(operation.get("page_type", "concept")).strip() or "concept"
        title = str(operation.get("title", "Untitled")).strip() or "Untitled"
        related_links = ", ".join(f"[[{title}]]" for title in related_page_titles[:12]) if related_page_titles else "none yet"
        prompt = f"""You are updating an existing compiler-managed {page_type} page in a maintained wiki on "{self.config.topic}".

Page title: {title}
Reason: {operation.get("reason", "")}
Key points to integrate: {json.dumps(operation.get("key_points", []))}
Source analysis:
{json.dumps(source_analysis, indent=2)}

Current page content:
{existing_content}

Return strict JSON with keys:
- frontmatter_updates
- section_patches

`section_patches` must be an array of objects with keys:
- section_id
- mode   (replace, append, or delete)
- heading
- body
- after_section_id

Rules:
- Update only the sections affected by the new evidence.
- Do not rewrite untouched sections.
- Prefer replacing a section over broad rewrites.
- Keep the page compressed and explicit about what comes from which source.
- Use [[Page Title]] wikilinks only from this allowlist: {related_links}

Output JSON only."""
        return self._call_json(
            system="You update compiler-managed wiki pages by section. Output valid JSON only.",
            prompt=prompt,
            max_tokens=1800,
            method_name="compile_page_patch",
        )

    def _ensure_substantive_draft(
        self,
        draft: PageDraft,
        *,
        operation: dict[str, Any],
        source_analysis: dict[str, Any],
        related_page_titles: list[str],
    ) -> PageDraft:
        if draft.page_type == "source":
            return self._normalize_source_draft(
                draft,
                operation=operation,
                source_analysis=source_analysis,
            )

        section_shape = self._section_shape_for_page_type(draft.page_type, source_analysis=source_analysis)
        if not section_shape:
            return draft

        section_map = {section.section_id: section for section in draft.sections}
        seen_ids = set()
        sections: list[PageSection] = []
        related_links = self._related_links(draft.title, related_page_titles, source_analysis)
        for section_id, heading in section_shape:
            seen_ids.add(section_id)
            current = section_map.get(section_id)
            body = current.body.strip() if current else ""
            if section_id == "key_numbers":
                body = self._metrics_block(source_analysis)
            elif section_id in {"claims_by_source", "what_sources_say"}:
                grouped_claims = self._claims_by_source_block(
                    source_analysis,
                    page_title=draft.title,
                    page_type=draft.page_type,
                )
                if grouped_claims:
                    body = grouped_claims
            elif section_id in {"evidence", "current_evidence"}:
                evidence_block = self._evidence_block(source_analysis)
                if evidence_block:
                    body = evidence_block
            elif section_id == "related":
                body = "\n".join(f"- {link}" for link in related_links) if related_links else "- No strong related pages yet."
            if self._is_placeholder_body(body):
                body = self._fallback_section_body(
                    page_type=draft.page_type,
                    section_id=section_id,
                    page_title=draft.title,
                    operation=operation,
                    source_analysis=source_analysis,
                    related_page_titles=related_page_titles,
                )
            sections.append(PageSection(section_id=section_id, heading=heading, body=body))

        for section in draft.sections:
            if section.section_id not in seen_ids and not self._is_placeholder_body(section.body):
                sections.append(section)

        # Run deterministic cleanup on every section body
        for section in sections:
            section.body = deterministic_cleanup(section.body)

        analysis_title = str(source_analysis.get("title", "")).strip()
        supporting_sources = self._supporting_sources(source_analysis)
        fallback_sources = [
            str(item.get("title") or item.get("source_title") or "").strip()
            for item in supporting_sources
            if str(item.get("title") or item.get("source_title") or "").strip()
        ]
        fallback_source_ids = [
            str(item.get("source_id") or "").strip()
            for item in supporting_sources
            if str(item.get("source_id") or "").strip()
        ]
        sources = draft.sources or fallback_sources or ([analysis_title] if analysis_title else [])
        summary = draft.summary.strip() or self._fallback_summary(
            page_title=draft.title,
            page_type=draft.page_type,
            operation=operation,
            source_analysis=source_analysis,
        )
        summary = deterministic_cleanup(summary)
        cssclasses = draft.cssclasses or [draft.page_type, draft.status]
        return PageDraft(
            title=draft.title,
            page_type=draft.page_type,
            status=draft.status,
            summary=summary,
            tags=draft.tags,
            sources=sources,
            source_ids=draft.source_ids or fallback_source_ids,
            cssclasses=cssclasses,
            sections=sections,
        )

    def _claims_by_source_block(
        self,
        source_analysis: dict[str, Any],
        *,
        page_title: str,
        page_type: str,
    ) -> str:
        grouped: dict[str, list[str]] = {}
        target = page_title.casefold()

        for supporting in self._supporting_sources(source_analysis):
            source_title = str(supporting.get("title") or supporting.get("source_title") or "").strip()
            if not source_title:
                continue
            claims: list[str] = []
            for item in supporting.get("key_claims", []) or []:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    concepts = [str(value).casefold() for value in item.get("concepts", []) or []]
                    entities = [str(value).casefold() for value in item.get("entities", []) or []]
                else:
                    text = str(item).strip()
                    concepts = []
                    entities = []
                if not text:
                    continue
                if page_type == "concept":
                    if target not in concepts and target not in text.casefold():
                        continue
                elif page_type == "entity":
                    if target not in entities and target not in text.casefold():
                        continue
                claims.append(text)
            if claims:
                grouped[source_title] = list(dict.fromkeys(claims))[:4]

        if not grouped:
            return ""

        blocks: list[str] = []
        for source_title, claims in list(grouped.items())[:6]:
            blocks.append(f"### [[{source_title}]]")
            blocks.append("")
            blocks.extend(f"- {claim}" for claim in claims)
            blocks.append("")
        return "\n".join(blocks).strip()

    def _evidence_block(self, source_analysis: dict[str, Any]) -> str:
        supporting_sources = self._supporting_sources(source_analysis)
        if not supporting_sources:
            return ""
        bullets: list[str] = []
        for supporting in supporting_sources[:6]:
            source_title = str(supporting.get("title") or supporting.get("source_title") or "").strip()
            claim_texts = self._claim_texts(supporting)
            if claim_texts:
                bullets.extend(claim_texts[:3])
                continue
            summary = str(supporting.get("summary") or "").strip()
            if source_title and summary:
                bullets.append(f"[[{source_title}]]: {summary}")
        return "\n".join(f"- {item}" for item in list(dict.fromkeys(bullets))[:10]).strip()

    def _normalize_source_draft(
        self,
        draft: PageDraft,
        *,
        operation: dict[str, Any],
        source_analysis: dict[str, Any],
    ) -> PageDraft:
        section_shape = self._source_section_shape(self._source_profile_for_analysis(source_analysis))
        section_map = {section.section_id: section for section in draft.sections}
        sections: list[PageSection] = []
        for section_id, heading in section_shape:
            current = section_map.get(section_id)
            body = (current.body if current else "").strip()
            if section_id == "key_numbers":
                body = self._metrics_block(source_analysis)
            elif section_id == "equations":
                body = self._equations_block(source_analysis)
            elif section_id in {"method_setup", "limitations", "open_questions", "synopsis", "thesis", "arguments", "objections", "distinctions", "events", "reflections", "decisions", "evidence"}:
                body = self._source_section_body(
                    section_id=section_id,
                    source_analysis=source_analysis,
                    operation=operation,
                )
            elif self._is_placeholder_body(body):
                body = self._source_section_body(
                    section_id=section_id,
                    source_analysis=source_analysis,
                    operation=operation,
                )
            sections.append(PageSection(section_id=section_id, heading=heading, body=body))

        summary = draft.summary.strip() or str(source_analysis.get("summary", "")).strip()
        analysis_title = str(source_analysis.get("title", "")).strip()
        sources = draft.sources or ([analysis_title] if analysis_title else [])
        cssclasses = draft.cssclasses or ["source", draft.status]
        return PageDraft(
            title=draft.title,
            page_type=draft.page_type,
            status=draft.status,
            summary=summary,
            tags=draft.tags,
            sources=sources,
            source_ids=draft.source_ids,
            cssclasses=cssclasses,
            sections=sections,
        )

    def _section_shape_for_page_type(
        self,
        page_type: str,
        *,
        source_analysis: dict[str, Any] | None = None,
    ) -> list[tuple[str, str]]:
        mode = self._knowledge_page_mode(page_type=page_type, source_analysis=source_analysis or {})
        if page_type == "concept":
            if mode == "provisional":
                return [
                    ("definition", "Definition"),
                    ("evidence", "Evidence"),
                    ("limitations", "Limitations"),
                    ("open_questions", "Open Questions"),
                    ("related", "Related"),
                ]
            return [
                ("definition", "Definition"),
                ("claims_by_source", "Claims by Source"),
                ("agreements", "Agreements"),
                ("tensions", "Tensions"),
                ("key_numbers", "Key Numbers"),
                ("open_questions", "Open Questions"),
                ("related", "Related"),
            ]
        if page_type == "entity":
            if mode == "provisional":
                return [
                    ("identity", "Identity"),
                    ("evidence", "Evidence"),
                    ("limitations", "Limitations"),
                    ("open_questions", "Open Questions"),
                    ("related", "Related"),
                ]
            return [
                ("identity", "Identity"),
                ("claims_by_source", "Claims by Source"),
                ("key_numbers", "Key Numbers"),
                ("open_questions", "Open Questions"),
                ("related", "Related"),
            ]
        if page_type == "question":
            if mode == "provisional":
                return [
                    ("question", "Question"),
                    ("current_evidence", "Current Evidence"),
                    ("next_steps", "Next Steps"),
                    ("related", "Related"),
                ]
            return [
                ("question", "Question"),
                ("what_sources_say", "What Sources Say"),
                ("tensions", "Tensions"),
                ("next_steps", "Next Steps"),
                ("related", "Related"),
            ]
        return []

    def _is_placeholder_body(self, body: str) -> bool:
        normalized = body.strip().lower()
        if not normalized:
            return True
        if normalized in {"-", "*", "tbd", "none", "n/a", "todo"}:
            return True
        return normalized.replace("\n", "").strip("-* ") == ""

    def _fallback_summary(
        self,
        *,
        page_title: str,
        page_type: str,
        operation: dict[str, Any],
        source_analysis: dict[str, Any],
    ) -> str:
        summary = str(source_analysis.get("summary", "")).strip()
        supporting_sources = self._supporting_sources(source_analysis)
        if summary:
            prefix = {
                "concept": f"{page_title} is currently supported by one source: ",
                "entity": f"{page_title} appears in one supporting source: ",
                "question": f"{page_title} remains open; current evidence says: ",
            }.get(page_type, "")
            if len(supporting_sources) >= 2:
                prefix = ""
            return f"{prefix}{summary}".strip()
        key_points = [str(item).strip() for item in operation.get("key_points", []) if str(item).strip()]
        if key_points:
            return key_points[0]
        return f"Provisional {page_type} page for {page_title} pending more source support."

    def _knowledge_page_mode(self, *, page_type: str, source_analysis: dict[str, Any]) -> str:
        if page_type not in {"concept", "entity", "question"}:
            return "reference"
        return "synthesis" if len(self._supporting_sources(source_analysis)) >= 2 else "provisional"

    def _source_section_shape(self, source_profile: dict[str, Any]) -> list[tuple[str, str]]:
        family = str(source_profile.get("recommended_section_family") or "empirical_note").strip()
        if family == "journal_note":
            return [
                ("synopsis", "Synopsis"),
                ("events", "Events"),
                ("reflections", "Reflections"),
                ("decisions", "Decisions"),
                ("open_questions", "Open Questions"),
            ]
        if family == "argument_note":
            return [
                ("synopsis", "Synopsis"),
                ("thesis", "Thesis"),
                ("arguments", "Arguments"),
                ("objections", "Objections"),
                ("distinctions", "Distinctions"),
                ("open_questions", "Open Questions"),
            ]
        if family == "mixed_note":
            return [
                ("synopsis", "Synopsis"),
                ("evidence", "Evidence"),
                ("reflections", "Reflections"),
                ("limitations", "Limitations"),
                ("open_questions", "Open Questions"),
            ]
        return [
            ("synopsis", "Synopsis"),
            ("claims", "Claims"),
            ("key_numbers", "Key Numbers"),
            ("equations", "Equations"),
            ("method_setup", "Method / Setup"),
            ("limitations", "Limitations"),
            ("open_questions", "Open Questions"),
        ]

    def _fallback_section_body(
        self,
        *,
        page_type: str,
        section_id: str,
        page_title: str,
        operation: dict[str, Any],
        source_analysis: dict[str, Any],
        related_page_titles: list[str],
    ) -> str:
        analysis_title = str(source_analysis.get("title", "")).strip() or "Current Source"
        summary = self._summary_text(source_analysis)
        claims = self._claim_texts(source_analysis)
        key_points = [str(item).strip() for item in operation.get("key_points", []) if str(item).strip()]
        limitations = self._limit_texts(source_analysis)
        open_questions = self._open_question_texts(source_analysis)
        methods = self._method_texts(source_analysis)
        related_links = self._related_links(page_title, related_page_titles, source_analysis)
        supporting_sources = self._supporting_sources(source_analysis)
        if supporting_sources:
            support_links = ", ".join(f"[[{item['title']}]]" for item in supporting_sources[:6])
        else:
            support_links = f"[[{analysis_title}]]"
        support_note = f"> [!warning] Provisional\n> This page is currently backed by {max(len(supporting_sources), 1)} source(s): {support_links}."

        if page_type == "concept":
            if section_id == "definition":
                first_claim = claims[0] if claims else summary or (key_points[0] if key_points else f"{page_title} is tracked as a provisional concept in this workspace.")
                return f"{page_title} is currently described in [[{analysis_title}]] as follows: {first_claim}\n\n{support_note}"
            if section_id == "evidence":
                evidence = self._evidence_block(source_analysis)
                if evidence:
                    return evidence
                return f"- [[{analysis_title}]]: {summary or 'Current source mentions this concept but the extracted evidence is thin.'}"
            if section_id == "claims_by_source":
                if claims:
                    return "\n".join(f"- {item}" for item in claims[:10])
                return f"- [[{analysis_title}]]: {summary or 'Current source mentions this concept but the extracted claim list is thin.'}"
            if section_id == "agreements":
                return f"{support_note}\n\n- Cross-source agreement cannot be established until additional sources cover {page_title}."
            if section_id == "tensions":
                if limitations:
                    bullets = "\n".join(f"> - [[{analysis_title}]] limitation: {item}" for item in limitations[:4])
                    return f"> [!tension] Current tensions\n> No cross-source contradiction is established yet.\n{bullets}"
                return f"> [!tension] Current tensions\n> No cross-source contradiction is established yet for {page_title}."
            if section_id == "limitations":
                if limitations:
                    return "> [!warning] Limitations\n" + "\n".join(f"> - {item}" for item in limitations[:4])
                return f"> [!warning] Limitations\n> - Current evidence for {page_title} comes from only one source."
            if section_id == "key_numbers":
                return self._metrics_block(source_analysis)
            if section_id == "open_questions":
                if open_questions:
                    return "> [!open-question] Open questions\n" + "\n".join(f"> - {item}" for item in open_questions[:5])
                return f"> [!open-question] Open questions\n> - Which later sources reinforce, refine, or contradict [[{analysis_title}]] on {page_title}?"
            if section_id == "related":
                return "\n".join(f"- {link}" for link in related_links) if related_links else f"- [[{analysis_title}]]"

        if page_type == "entity":
            if section_id == "identity":
                identity = summary or (claims[0] if claims else f"{page_title} appears in the current source packet.")
                return f"{page_title} currently appears in [[{analysis_title}]] as follows: {identity}\n\n{support_note}"
            if section_id == "evidence":
                evidence = self._evidence_block(source_analysis)
                if evidence:
                    return evidence
                return f"- [[{analysis_title}]]: {summary or f'{page_title} is referenced but not deeply analyzed in the current source.'}"
            if section_id == "claims_by_source":
                if claims:
                    return "\n".join(f"- {item}" for item in claims[:10])
                return f"- [[{analysis_title}]]: {summary or f'{page_title} is referenced but not deeply analyzed in the current source.'}"
            if section_id == "limitations":
                if limitations:
                    return "> [!warning] Limitations\n" + "\n".join(f"> - {item}" for item in limitations[:4])
                return f"> [!warning] Limitations\n> - This entity page is still provisional and only lightly supported."
            if section_id == "key_numbers":
                return self._metrics_block(source_analysis)
            if section_id == "open_questions":
                if open_questions:
                    return "> [!open-question] Open questions\n" + "\n".join(f"> - {item}" for item in open_questions[:5])
                return f"> [!open-question] Open questions\n> - How does {page_title} compare once more sources mention it?"
            if section_id == "related":
                return "\n".join(f"- {link}" for link in related_links) if related_links else f"- [[{analysis_title}]]"

        if page_type == "question":
            if section_id == "question":
                question_text = page_title if page_title.endswith("?") else f"{page_title}?"
                return f"{question_text}\n\n{support_note}"
            if section_id == "current_evidence":
                evidence = self._evidence_block(source_analysis)
                if evidence:
                    return evidence
                return f"- [[{analysis_title}]]: {summary or 'Current evidence is limited.'}"
            if section_id == "what_sources_say":
                if claims:
                    return "\n".join(f"- {item}" for item in claims[:10])
                return f"- [[{analysis_title}]]: {summary or 'Current evidence is limited.'}"
            if section_id == "tensions":
                if limitations:
                    return "> [!tension] Current tensions\n" + "\n".join(f"> - {item}" for item in limitations[:4])
                return f"> [!tension] Current tensions\n> - The main tension is incomplete evidence: only [[{analysis_title}]] currently informs this question."
            if section_id == "next_steps":
                if open_questions:
                    return "\n".join(f"- {item}" for item in open_questions[:5])
                if methods:
                    return "\n".join(f"- Verify with a source that addresses: {item}" for item in methods[:3])
                return f"- Look for sources that directly address {page_title}."
            if section_id == "related":
                return "\n".join(f"- {link}" for link in related_links) if related_links else f"- [[{analysis_title}]]"

        return summary or "- Additional evidence needed."

    def _metrics_block(self, source_analysis: dict[str, Any]) -> str:
        metrics = self._metric_rows(source_analysis)
        rows: list[tuple[str, str, str]] = []
        for item in metrics:
            if isinstance(item, dict):
                metric = str(item.get("label") or item.get("metric") or item.get("name") or "").strip()
                value = str(item.get("value") or item.get("range") or item.get("result") or "").strip()
                context = str(item.get("context") or item.get("notes") or "").strip()
                source_title = str(item.get("source_title") or "").strip()
                if source_title and context and source_title not in context:
                    context = f"{context} ({source_title})"
                elif source_title and not context:
                    context = source_title
                if metric and value:
                    rows.append((metric, value, context))
            else:
                text = str(item).strip()
                if text:
                    rows.append(("Metric", text, ""))
        if not rows:
            return "- No explicit quantitative metrics were extracted from the current supporting source."
        lines = ["| Metric | Value | Context |", "|--------|-------|---------|"]
        for metric, value, context in rows[:8]:
            lines.append(f"| {metric} | {value} | {context or '-'} |")
        return "\n".join(lines)

    def _equations_block(self, source_analysis: dict[str, Any]) -> str:
        equations = self._equation_rows(source_analysis)
        blocks: list[str] = []
        for index, item in enumerate(equations, start=1):
            if not isinstance(item, dict):
                continue
            latex = str(item.get("latex") or "").strip()
            meaning = str(item.get("meaning") or "").strip()
            if not latex:
                continue
            label = str(item.get("label") or "").strip() or self._equation_label(meaning, index)
            equation = latex if latex.startswith("$$") else f"$$\n{latex}\n$$"
            lines = [f"### {label}", "", equation]
            if meaning:
                lines.extend(["", f"- Meaning: {meaning}"])
            source_title = str(item.get("source_title") or "").strip()
            if source_title:
                lines.extend(["", f"- Source: [[{source_title}]]"])
            blocks.append("\n".join(lines).strip())
        return "\n\n".join(blocks) if blocks else "- None explicit in source."

    def _equation_label(self, meaning: str, index: int) -> str:
        if not meaning:
            return f"Equation {index}"
        stopwords = {"the", "a", "an", "as", "of", "for", "to", "and", "or", "in", "on", "with", "over", "under", "minimum", "needed"}
        words = [word for word in re.split(r"[^A-Za-z0-9]+", meaning) if word and word.lower() not in stopwords]
        label = " ".join(words[:4]).strip()
        return label.title() if label else f"Equation {index}"

    def _source_section_body(
        self,
        *,
        section_id: str,
        source_analysis: dict[str, Any],
        operation: dict[str, Any],
    ) -> str:
        summary = self._summary_text(source_analysis)
        claims = self._claim_texts(source_analysis)
        atoms = list(source_analysis.get("evidence_atoms") or [])
        methods = self._method_texts(source_analysis)
        limitations = self._limit_texts(source_analysis)
        open_questions = self._open_question_texts(source_analysis)
        key_points = [str(item).strip() for item in operation.get("key_points", []) if str(item).strip()]

        if section_id in {"core_contribution", "synopsis"}:
            lead = summary or (claims[0] if claims else (key_points[0] if key_points else "This source adds new evidence to the workspace."))
            bullets = claims[:3]
            return lead + ("\n\n" + "\n".join(f"- {item}" for item in bullets) if bullets else "")
        if section_id == "claims":
            if claims:
                return "\n".join(f"- {item}" for item in claims[:8])
            return f"- {summary or 'No concrete claims were extracted from the source.'}"
        if section_id == "evidence":
            evidence = self._evidence_block(source_analysis)
            return evidence or f"- {summary or 'No reusable evidence snippets were extracted from the source.'}"
        if section_id == "thesis":
            lead = summary or (claims[0] if claims else (key_points[0] if key_points else "No explicit thesis was extracted."))
            return lead
        if section_id == "arguments":
            arguments = [
                str(atom.get("text") or "").strip()
                for atom in atoms
                if isinstance(atom, dict) and str(atom.get("kind") or "") in {"argument", "claim", "observation"}
            ]
            if arguments:
                return "\n".join(f"- {item}" for item in arguments[:8])
            if claims:
                return "\n".join(f"- {item}" for item in claims[:8])
            return f"- {summary or 'No explicit arguments were extracted from the source.'}"
        if section_id == "objections":
            if limitations:
                return "\n".join(f"- {item}" for item in limitations[:6])
            objections = [
                str(atom.get("text") or "").strip()
                for atom in atoms
                if isinstance(atom, dict) and str(atom.get("stance") or "") in {"objection", "limitation"}
            ]
            if objections:
                return "\n".join(f"- {item}" for item in objections[:6])
            return "- No explicit objections or caveats were extracted from the source."
        if section_id == "distinctions":
            distinctions = [
                str(atom.get("text") or "").strip()
                for atom in atoms
                if isinstance(atom, dict) and str(atom.get("kind") or "") == "distinction"
            ]
            if distinctions:
                return "\n".join(f"- {item}" for item in distinctions[:6])
            concepts = self._coerce_text_list(source_analysis.get("concepts"))
            if concepts:
                return "\n".join(f"- {item}" for item in concepts[:6])
            return "- No explicit distinctions were extracted from the source."
        if section_id == "events":
            events = [
                str(atom.get("text") or "").strip()
                for atom in atoms
                if isinstance(atom, dict) and str(atom.get("kind") or "") in {"event", "decision"}
            ]
            if events:
                return "\n".join(f"- {item}" for item in events[:8])
            if claims:
                return "\n".join(f"- {item}" for item in claims[:6])
            return "- No explicit events were extracted from the source."
        if section_id == "reflections":
            reflections = [
                str(atom.get("text") or "").strip()
                for atom in atoms
                if isinstance(atom, dict) and str(atom.get("kind") or "") in {"reflection", "observation"}
            ]
            if reflections:
                return "\n".join(f"- {item}" for item in reflections[:8])
            if limitations:
                return "\n".join(f"- {item}" for item in limitations[:6])
            return f"- {summary or 'No explicit reflections were extracted from the source.'}"
        if section_id == "decisions":
            decisions = [
                str(atom.get("text") or "").strip()
                for atom in atoms
                if isinstance(atom, dict) and str(atom.get("kind") or "") == "decision"
            ]
            if decisions:
                return "\n".join(f"- {item}" for item in decisions[:6])
            return "- No explicit decisions or commitments were extracted from the source."
        if section_id == "method_setup":
            if methods:
                return "\n".join(f"- {item}" for item in methods[:8])
            return "- Method and setup details were not explicit in the extracted view."
        if section_id == "limitations":
            if limitations:
                return "> [!warning] Limitations\n" + "\n".join(f"> - {item}" for item in limitations[:6])
            return "> [!warning] Limitations\n> - No explicit limitations were extracted from the current source."
        if section_id == "open_questions":
            if open_questions:
                return "> [!open-question] Open questions\n" + "\n".join(f"> - {item}" for item in open_questions[:6])
            return "> [!open-question] Open questions\n> - No explicit open questions were extracted from the current source."
        if section_id == "key_numbers":
            return self._metrics_block(source_analysis)
        if section_id == "equations":
            return self._equations_block(source_analysis)
        return summary or "- Additional evidence needed."

    def _claim_texts(self, source_analysis: dict[str, Any]) -> list[str]:
        claims: list[str] = []
        for item in source_analysis.get("key_claims", []) or []:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                source_title = str(item.get("source_title") or source_analysis.get("title") or "").strip()
            else:
                text = str(item).strip()
                source_title = str(source_analysis.get("title") or "").strip()
            if text:
                if source_title and not text.startswith("[["):
                    claims.append(f"[[{source_title}]]: {text}")
                else:
                    claims.append(text)
        return claims

    def _related_links(
        self,
        page_title: str,
        related_page_titles: list[str],
        source_analysis: dict[str, Any],
    ) -> list[str]:
        available = {title.casefold(): title for title in related_page_titles}
        ordered: list[str] = []
        for candidate in [source_analysis.get("title"), *source_analysis.get("concepts", []), *source_analysis.get("entities", [])]:
            text = str(candidate or "").strip()
            if not text or text.casefold() == page_title.casefold():
                continue
            match = available.get(text.casefold())
            if match and match not in ordered:
                ordered.append(match)
        if ordered:
            return [f"[[{title}]]" for title in ordered[:6]]

        page_tokens = {token for token in re.split(r"[^a-z0-9]+", page_title.casefold()) if len(token) > 2}
        for candidate in related_page_titles:
            candidate_tokens = {token for token in re.split(r"[^a-z0-9]+", candidate.casefold()) if len(token) > 2}
            if page_tokens & candidate_tokens and candidate not in ordered:
                ordered.append(candidate)
        return [f"[[{title}]]" for title in ordered[:6]]

    def _supporting_sources(self, source_analysis: dict[str, Any]) -> list[dict[str, Any]]:
        supporting = source_analysis.get("supporting_sources") or []
        results: list[dict[str, Any]] = []
        for item in supporting:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("source_title") or "").strip()
            if not title:
                continue
            results.append(item)
        if results:
            return results
        title = str(source_analysis.get("title") or "").strip()
        return [{"title": title, **source_analysis}] if title else []

    def _summary_text(self, source_analysis: dict[str, Any]) -> str:
        summary = str(source_analysis.get("summary", "")).strip()
        if summary:
            return summary
        supporting = self._supporting_sources(source_analysis)
        summaries = [str(item.get("summary") or "").strip() for item in supporting if str(item.get("summary") or "").strip()]
        return " ".join(dict.fromkeys(summaries[:3])).strip()

    def _method_texts(self, source_analysis: dict[str, Any]) -> list[str]:
        methods = [str(item).strip() for item in source_analysis.get("methods", []) if str(item).strip()]
        if methods:
            return methods
        output: list[str] = []
        for supporting in self._supporting_sources(source_analysis):
            source_title = str(supporting.get("title") or supporting.get("source_title") or "").strip()
            for item in supporting.get("methods", []) or []:
                text = str(item).strip()
                if not text:
                    continue
                output.append(f"{text} ({source_title})" if source_title else text)
        return output

    def _limit_texts(self, source_analysis: dict[str, Any]) -> list[str]:
        limitations = [str(item).strip() for item in source_analysis.get("limitations", []) if str(item).strip()]
        if limitations:
            return limitations
        output: list[str] = []
        for supporting in self._supporting_sources(source_analysis):
            source_title = str(supporting.get("title") or supporting.get("source_title") or "").strip()
            for item in supporting.get("limitations", []) or []:
                text = str(item).strip()
                if text:
                    output.append(f"{text} ({source_title})" if source_title else text)
        return output

    def _open_question_texts(self, source_analysis: dict[str, Any]) -> list[str]:
        questions = [str(item).strip() for item in source_analysis.get("open_questions", []) if str(item).strip()]
        if questions:
            return questions
        output: list[str] = []
        for supporting in self._supporting_sources(source_analysis):
            for item in supporting.get("open_questions", []) or []:
                text = str(item).strip()
                if text:
                    output.append(text)
        return output

    def _metric_rows(self, source_analysis: dict[str, Any]) -> list[Any]:
        metrics = source_analysis.get("metrics", []) or []
        if metrics:
            return metrics
        rows: list[dict[str, Any]] = []
        for supporting in self._supporting_sources(source_analysis):
            source_title = str(supporting.get("title") or supporting.get("source_title") or "").strip()
            for item in supporting.get("metrics", []) or []:
                if not isinstance(item, dict):
                    continue
                merged = dict(item)
                if source_title and "source_title" not in merged:
                    merged["source_title"] = source_title
                rows.append(merged)
        return rows

    def _equation_rows(self, source_analysis: dict[str, Any]) -> list[Any]:
        equations = source_analysis.get("equations", []) or []
        if equations:
            return equations
        rows: list[dict[str, Any]] = []
        for supporting in self._supporting_sources(source_analysis):
            source_title = str(supporting.get("title") or supporting.get("source_title") or "").strip()
            for item in supporting.get("equations", []) or []:
                if not isinstance(item, dict):
                    continue
                merged = dict(item)
                if source_title and "source_title" not in merged:
                    merged["source_title"] = source_title
                rows.append(merged)
        return rows

    def write_index(self, pages_by_type: dict[str, list[dict[str, str]]]) -> str:
        """Generate the index.md content."""
        prompt = f"""You are updating the master index for a maintained wiki on "{self.config.topic}".

Current wiki pages by type:
{json.dumps(pages_by_type, indent=2)}

Write an index.md page with:
1. YAML frontmatter: title "Index", type "index", updated timestamp
2. A top heading: "# {self.config.topic} — Index"
3. Sections for each page type: Sources, Concepts, Entities, Open Questions, Outputs
4. Each page listed as: - [[Page Title]] — one-line summary
5. If a section has no pages, write "_No pages yet._"

Use [[Page Title]] wikilinks. Keep summaries to one short line each. Output raw markdown directly — do NOT wrap frontmatter or any part of the output in code fences."""

        return self._call_text(
            system="You maintain the index page of a maintained wiki. Output clean markdown.",
            prompt=prompt,
            max_tokens=2000,
            method_name="write_index",
        )

    def answer_query(
        self,
        question: str,
        relevant_pages: dict[str, str],
        raw_snippets: list[dict[str, Any]] | None = None,
        output_format: str = "note",
    ) -> str:
        """Answer a question against the wiki contents."""
        context = ""
        for path, content in relevant_pages.items():
            context += f"\n--- {path} ---\n{content[:3000]}\n"
        raw_context = ""
        for snippet in raw_snippets or []:
            source_title = str(snippet.get("source_title") or snippet.get("source_id") or "source").strip()
            label = str(snippet.get("label") or "snippet").strip()
            raw_context += f"\n--- raw:{source_title} / {label} ---\n{str(snippet.get('text') or '')[:2000]}\n"

        format_instructions = {
            "note": "Return a polished markdown note with headings and citations.",
            "comparison": "Return a comparison-oriented markdown note with a compact comparison table near the top.",
            "marp": "Return a Marp-compatible slide deck in markdown with frontmatter and slide separators.",
            "mermaid": "Return a markdown note that includes at least one mermaid diagram fence capturing the core relationships.",
            "chart-spec": "Return a markdown note with a chart recommendation and an embedded JSON chart spec in a fenced code block.",
            "auto": "Choose the best of note, comparison, marp, mermaid, or chart-spec based on the question.",
        }.get(output_format, "Return a polished markdown note with headings and citations.")

        prompt = f"""You are answering a research question using a maintained wiki on "{self.config.topic}".

Question: {question}
Requested output format: {output_format}

Relevant wiki pages:
{context}

Relevant raw source snippets:
{raw_context or "(No raw snippets were needed.)"}

Write a grounded answer using the requested format.

Format guidance:
{format_instructions}

Core requirements:
1. Start with a clear, direct answer
2. Cite specific wiki pages using [[Page Title]] wikilinks
3. Note where sources agree or disagree
4. Flag any gaps or uncertainties
5. Suggest follow-up questions if relevant
6. Prefer maintained wiki pages when they are sufficient; use raw snippets as verification or gap-filling support

Ground your answer in the wiki content. Do not make claims unsupported by the pages above."""

        return self._call_text(
            system="You answer research questions grounded in a maintained wiki. Cite sources with [[wikilinks]]. Be thorough but honest about what the wiki does and doesn't cover.",
            prompt=prompt,
            max_tokens=2000,
            method_name="answer_query",
        )

    def synthesize_concept_page(
        self,
        *,
        concept_name: str,
        claims: list[dict[str, Any]],
        existing_content: str | None,
        related_page_titles: list[str],
    ) -> str:
        related_links = ", ".join(f"[[{title}]]" for title in related_page_titles[:10]) if related_page_titles else "none yet"
        prompt = f"""You are rewriting a concept page for a maintained wiki on "{self.config.topic}".

Concept: {concept_name}
Existing content:
{existing_content or "(No existing content.)"}

Structured claims grouped from multiple sources:
{json.dumps(claims, indent=2)}

Related pages that already exist: {related_links}

Write a concept page that genuinely synthesizes across sources. Requirements:
- Frontmatter with title, type, status, summary, sources, source_ids, created, updated, tags, cssclasses
- `type: concept`
- `status: stable`
- `cssclasses` should include `concept` and `stable`
- Cite which sources support each claim and where they diverge
- Use a table if it improves comparison
- Use [[Page Title]] wikilinks only for pages in the related-page list above
- Do not use a generic canned structure; organize around the evidence
"""
        return self._call_text(
            system="You rewrite concept pages to synthesize evidence across sources. Output markdown directly.",
            prompt=prompt,
            max_tokens=2200,
            method_name="synthesize_concept_page",
        )

    def write_comparison_page(
        self,
        *,
        title: str,
        left_name: str,
        right_name: str,
        claims: list[dict[str, Any]],
        related_page_titles: list[str],
    ) -> str:
        related_links = ", ".join(f"[[{page}]]" for page in related_page_titles[:12]) if related_page_titles else "none yet"
        prompt = f"""You are writing a comparison page for a maintained wiki on "{self.config.topic}".

Title: {title}
Left concept: {left_name}
Right concept: {right_name}
Claim evidence:
{json.dumps(claims, indent=2)}

Related pages that already exist: {related_links}

Write a comparison page with:
- Frontmatter including title, type, status, summary, sources, source_ids, created, updated, tags, cssclasses
- `type: comparison`
- `status: stable`
- `cssclasses` including `comparison` and `stable`
- A compact comparison table early in the page
- Explicit notes on overlap, divergence, and open questions
- [[Page Title]] wikilinks only to existing related pages
"""
        return self._call_text(
            system="You write comparison pages grounded in multiple sources. Output markdown directly.",
            prompt=prompt,
            max_tokens=1800,
            method_name="write_comparison_page",
        )

    def lint_wiki(
        self,
        index_content: str,
        page_summaries: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Audit the wiki for quality issues."""
        prompt = f"""You are auditing a maintained wiki on "{self.config.topic}" for quality issues.

Wiki index:
{index_content}

Page summaries (path: first ~500 chars):
{json.dumps(page_summaries, indent=2)}

Check for these issues and return strict JSON with key "issues", an array where each item has:
- "severity": "high", "medium", or "low"
- "type": one of "contradiction", "orphan", "duplicate", "stale", "gap", "weak_source"
- "title": Short issue title
- "description": What the problem is
- "affected_pages": Array of page paths involved
- "suggestion": How to fix it

Look for:
- Contradictions between pages
- Orphan pages with no inbound links from other pages
- Near-duplicate concept/entity pages
- Important concepts mentioned but lacking their own page
- Missing cross-references between related pages
- Sources that haven't contributed to any synthesis pages
- Claims that seem weakly supported

Be specific and actionable. Only report real issues."""

        result = self._call_json(
            system="You audit maintained wikis for quality. Output valid JSON only.",
            prompt=prompt,
            max_tokens=1500,
            method_name="lint_wiki",
        )
        return result.get("issues", [])

    def _call_text(
        self,
        system: str,
        prompt: str | list[dict[str, Any]],
        max_tokens: int,
        method_name: str = "",
    ) -> str:
        last_error: Exception | None = None
        response: httpx.Response | None = None
        for attempt in range(4):
            try:
                response = httpx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.config.anthropic_api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.config.anthropic_model,
                        "max_tokens": max_tokens,
                        "temperature": 0.2,
                        "system": system,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=120.0,
                )
                if response.status_code == 200:
                    break
                if response.status_code not in {408, 409, 429, 500, 502, 503, 504} or attempt == 3:
                    try:
                        error_body = response.json()
                        error_msg = error_body.get("error", {}).get("message", response.text[:200])
                    except Exception:
                        error_msg = response.text[:200]
                    raise RuntimeError(f"Anthropic API error ({response.status_code}): {error_msg}")
                time.sleep(min(8, 2 ** attempt))
            except (httpx.TimeoutException, httpx.TransportError) as error:
                last_error = error
                if attempt == 3:
                    raise RuntimeError(f"Anthropic API transport error: {error}") from error
                time.sleep(min(8, 2 ** attempt))

        if response is None or response.status_code != 200:
            if last_error is not None:
                raise RuntimeError(f"Anthropic API transport error: {last_error}") from last_error
            raise RuntimeError("Anthropic API request failed without a response.")

        payload = response.json()

        # Track token usage from the API response
        usage = payload.get("usage", {})
        resp_input_tokens = usage.get("input_tokens", 0)
        resp_output_tokens = usage.get("output_tokens", 0)
        if resp_input_tokens or resp_output_tokens:
            self.usage.record(method_name or "unknown", resp_input_tokens, resp_output_tokens)

        parts = payload.get("content", [])
        text = "\n".join(
            part.get("text", "") for part in parts if part.get("type") == "text"
        )
        # Strip code fences if the model wraps output in them
        text = re.sub(r"^\s*```(?:markdown|md|yaml|json)?\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
        # Also strip inline code fences around frontmatter
        text = re.sub(r"```yaml\s*\n(---\n)", r"\1", text)
        text = re.sub(r"(---)\n```", r"\1", text)
        return text.strip()

    def _call_json(
        self,
        system: str,
        prompt: str | list[dict[str, Any]],
        max_tokens: int,
        method_name: str = "",
    ) -> dict[str, Any]:
        text = self._call_text(system=system, prompt=prompt, max_tokens=max_tokens, method_name=method_name)
        primary = self._extract_json_candidate(text)
        if primary is None:
            raise ValueError(f"Model did not return JSON. Response: {text[:200]}")
        candidates = [primary]
        if primary != text:
            candidates.append(text)

        for candidate in candidates:
            parsed = self._parse_json_like(candidate)
            if parsed is not None:
                return parsed

        repair_prompt = f"""Convert the following malformed or truncated JSON-like text into valid strict JSON.

Rules:
- Preserve keys and values
- Do not summarize or drop fields
- If the object is truncated, complete missing quotes/brackets/braces conservatively
- Return JSON only

Malformed text:
{primary}
"""
        repaired = self._call_text(
            system="You repair malformed JSON. Output valid JSON only.",
            prompt=repair_prompt,
            max_tokens=max(max_tokens, 3200),
            method_name=f"{method_name}:json_repair" if method_name else "json_repair",
        )
        repaired_parsed = self._parse_json_like(repaired)
        if repaired_parsed is not None:
            return repaired_parsed
        raise ValueError(f"Model did not return valid JSON. Response: {text[:300]}")

    def _parse_json_like(self, text: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
        try:
            parsed = yaml.safe_load(text)
            return parsed if isinstance(parsed, dict) else None
        except yaml.YAMLError:
            return None

    def _extract_json_candidate(self, text: str) -> str | None:
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]

        return text[start:]
