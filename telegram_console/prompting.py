from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .projects import ProjectRecord
from .utils import shorten_text
from .workspace import WorkConfig, load_work_config, load_workspace_config

if TYPE_CHECKING:
    from .agent_chat import ProjectChatState


PromptProfile = Literal["answer", "execute", "review"]
ContextMode = Literal["full", "compact"]

PROFILE_LABELS: dict[PromptProfile, str] = {
    "answer": "ответ",
    "execute": "выполнение",
    "review": "проверка",
}

PROFILE_EXPECTATIONS: dict[PromptProfile, str] = {
    "answer": "содержательный ответ с допущениями и тем, что именно было проверено",
    "execute": "реальную работу в проекте, измененные файлы, проверки и оставшиеся блокеры",
    "review": "findings first, риски, testing gaps и честный verdict по качеству",
}

ANSWER_HINTS = (
    "что",
    "как",
    "почему",
    "объясни",
    "подскажи",
    "посмотри и скажи",
)
EXECUTE_HINTS = (
    "допиши",
    "исправь",
    "продолжи",
    "продолжай",
    "собери",
    "обнови",
    "сделай",
    "доведи",
    "подготовь",
    "напиши",
)
REVIEW_HINTS = (
    "проверь",
    "оцени",
    "review",
    "есть ли ошибки",
    "готово ли",
    "найди риски",
)


@dataclass(frozen=True)
class ProjectContextSnapshot:
    project_id: str
    project_title: str
    project_root: Path
    capabilities: tuple[str, ...]
    active_work_id: str | None
    active_work_title: str | None
    active_work_root: str | None
    source_of_truth: tuple[tuple[str, str], ...]
    thesis_sections: tuple[str, ...]
    article_outputs: tuple[str, ...]
    last_user_message: str | None
    last_assistant_summary: str | None
    current_focus: str
    context_mode: ContextMode


@dataclass(frozen=True)
class BuiltPrompt:
    profile: PromptProfile
    prompt_text: str
    detected_intent: str
    done_contract: tuple[str, ...]
    expected_output: str
    context_mode: ContextMode


class PromptBuilder:
    def classify_intent(self, user_text: str) -> PromptProfile:
        normalized = _normalize_text(user_text)

        if _matches_any(normalized, EXECUTE_HINTS):
            return "execute"
        if _matches_any(normalized, REVIEW_HINTS):
            return "review"
        if "?" in user_text or _matches_any(normalized, ANSWER_HINTS):
            return "answer"
        return "answer"

    def build_turn_prompt(
        self,
        project: ProjectRecord,
        work: WorkConfig | ProjectChatState,
        state: ProjectChatState | str,
        user_text: str | None = None,
        *,
        context_mode: ContextMode,
        current_focus: str,
    ) -> BuiltPrompt:
        if isinstance(work, WorkConfig):
            active_work = work
            active_state = state
            prompt_text = user_text
        else:
            active_work = self._resolve_work(project)
            active_state = work
            prompt_text = state if isinstance(state, str) else user_text

        if prompt_text is None:
            raise TypeError("PromptBuilder.build_turn_prompt() missing required argument: 'user_text'")

        profile = self.classify_intent(prompt_text)
        snapshot = self._build_snapshot(project, active_work, active_state, context_mode, current_focus)
        done_contract = self._done_contract(profile)
        return BuiltPrompt(
            profile=profile,
            prompt_text=self._render_prompt(snapshot, prompt_text, profile, done_contract),
            detected_intent=PROFILE_LABELS[profile],
            done_contract=done_contract,
            expected_output=PROFILE_EXPECTATIONS[profile],
            context_mode=context_mode,
        )

    def _build_snapshot(
        self,
        project: ProjectRecord,
        work: WorkConfig,
        state: ProjectChatState,
        context_mode: ContextMode,
        current_focus: str,
    ) -> ProjectContextSnapshot:
        return ProjectContextSnapshot(
            project_id=project.id,
            project_title=project.title,
            project_root=project.root_dir,
            capabilities=project.capabilities,
            active_work_id=work.slug,
            active_work_title=work.title,
            active_work_root=str(work.work_dir.relative_to(project.root_dir)),
            source_of_truth=self._source_of_truth(project, work),
            thesis_sections=self._thesis_sections(project, work),
            article_outputs=self._article_outputs(project, work),
            last_user_message=state.last_user_message,
            last_assistant_summary=state.last_assistant_summary,
            current_focus=current_focus,
            context_mode=context_mode,
        )

    def _source_of_truth(self, project: ProjectRecord, work: WorkConfig) -> tuple[tuple[str, str], ...]:
        items: list[tuple[str, str]] = []
        agents = project.root_dir / "AGENTS.md"
        if agents.exists():
            items.append((str(agents), "общая оркестрация проекта"))

        workspace = project.root_dir / "workspace.toml"
        if workspace.exists():
            items.append((str(workspace), "машинно-читаемая конфигурация workspace"))

        master_protocol = project.root_dir / "meta" / "master-protocol.md"
        if master_protocol.exists():
            items.append((str(master_protocol), "единый рабочий регламент"))

        legacy_canon = project.root_dir / "meta" / "project-canon.md"
        if project.supports("thesis"):
            if legacy_canon.exists():
                items.append((str(legacy_canon), "legacy compatibility shim для thesis lane"))
            elif legacy_canon.parent.exists():
                items.append((str(legacy_canon), "legacy compatibility shim path для старых ссылок"))

        work_toml = work.work_dir / "work.toml"
        if work_toml.exists():
            items.append((str(work_toml), "конфигурация активной работы"))
        if work.work_canon_path.exists():
            items.append((str(work.work_canon_path), "канон активной работы"))

        return tuple(items)

    def _thesis_sections(self, project: ProjectRecord, work: WorkConfig) -> tuple[str, ...]:
        if not project.supports("thesis") or not work.thesis:
            return ()
        sections_dir = work.thesis.manuscript_sections_dir
        if not sections_dir.exists():
            return ()
        items = [
            str(path.relative_to(project.root_dir))
            for path in sorted(sections_dir.glob("*.md"))
            if path.name.casefold() != "readme.md"
        ]
        return tuple(items)

    def _article_outputs(self, project: ProjectRecord, work: WorkConfig) -> tuple[str, ...]:
        if not project.supports("article") or not work.article:
            return ()
        final_dir = work.article.final_dir
        if not final_dir.exists():
            return ()
        items = [
            str(path.relative_to(project.root_dir))
            for path in sorted(final_dir.glob("*.md"))
            if path.name.casefold() != "readme.md" and not path.name.endswith("-checklist.md")
        ]
        return tuple(items)

    def _resolve_work(self, project: ProjectRecord) -> WorkConfig:
        workspace = load_workspace_config(project.root_dir)
        work_id = project.default_work or (project.works[0] if project.works else workspace.default_work)
        return load_work_config(workspace, work_id)

    def _done_contract(self, profile: PromptProfile) -> tuple[str, ...]:
        if profile == "execute":
            return (
                "Не останавливайся на анализе: если задача требует работы в проекте, реально выполни ее.",
                "В конце перечисли, что сделано и какие файлы изменены.",
                "Отдельно перечисли проверки, которые были выполнены.",
                "Если задача не доведена до конца, честно перечисли blockers и оставшиеся риски.",
            )
        if profile == "review":
            return (
                "Отвечай findings first: сначала проблемы и риски, потом краткий итог.",
                "Для каждого finding укажи severity, файл или зону и короткое объяснение.",
                "Если явных проблем нет, скажи об этом прямо и добавь residual risks или testing gaps.",
            )
        return (
            "Ответь по существу и не уходи в общий шум.",
            "Явно назови допущения, если без них нельзя ответить честно.",
            "Если опираешься на файлы проекта, коротко скажи, что именно проверил.",
        )

    def _render_prompt(
        self,
        snapshot: ProjectContextSnapshot,
        user_text: str,
        profile: PromptProfile,
        done_contract: tuple[str, ...],
    ) -> str:
        lines = [
            "Ты работаешь как локальный Codex-агент, вызванный из Telegram-консоли.",
            "Твоя задача: качественно отработать пользовательский запрос в контексте активного проекта.",
            f"Режим ответа: {PROFILE_LABELS[profile]}.",
            f"Режим контекста: {'полный' if snapshot.context_mode == 'full' else 'краткий recap'}.",
            "",
            "Проект:",
            f"- Название: {snapshot.project_title}",
            f"- ID: {snapshot.project_id}",
            f"- Корень проекта: {snapshot.project_root}",
            f"- Возможности: {', '.join(snapshot.capabilities) if snapshot.capabilities else 'не указаны'}",
            f"- Активная работа: {snapshot.active_work_title or snapshot.active_work_id or 'не выбрана'}",
            f"- Корень активной работы: {snapshot.active_work_root or 'не найден'}",
        ]

        lines.extend(["", "Source of truth:"])
        if snapshot.source_of_truth:
            for path, role in snapshot.source_of_truth:
                lines.append(f"- {path} — {role}")
        else:
            lines.append("- Канонические файлы не найдены, опирайся на структуру проекта и реальные файлы в корне.")

        if snapshot.context_mode == "full":
            lines.extend(["", "Инвентарь проекта:"])
            thesis_txt = _format_collection(snapshot.thesis_sections) if snapshot.thesis_sections else "не найдены"
            lines.append(f"- Thesis sections: {thesis_txt}")
            art_txt = _format_collection(snapshot.article_outputs) if snapshot.article_outputs else "не найдены"
            lines.append(f"- Готовые article outputs: {art_txt}")
        else:
            lines.extend(["", "Краткий recap проекта:"])

        lines.extend(
            [
                f"- Последнее сообщение пользователя: {snapshot.last_user_message or 'пока нет'}",
                f"- Последний summary агента: {snapshot.last_assistant_summary or 'пока нет'}",
                f"- Что сейчас в фокусе: {snapshot.current_focus}",
                "",
                "Как работать:",
                "- Не копируй длинные проектные документы в ответ. Если задача требует действий, "
                "сам открой нужные файлы внутри проекта.",
                "- Для thesis/article соблюдай AGENTS.md, workspace.toml, work.toml, work-canon "
                "и master protocol как source of truth.",
                "- Не заявляй завершенность, если реально остались blockers.",
                "",
                "Definition of done:",
            ]
        )
        lines.extend(f"- {item}" for item in done_contract)
        lines.extend(
            [
                "",
                "Запрос пользователя:",
                user_text.strip(),
            ]
        )
        return "\n".join(lines)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _matches_any(normalized_text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in normalized_text for hint in hints)


def _format_collection(items: tuple[str, ...], *, limit: int = 6) -> str:
    visible = [shorten_text(item, limit=90) for item in items[:limit]]
    if len(items) > limit:
        visible.append(f"и еще {len(items) - limit}")
    return ", ".join(visible)
