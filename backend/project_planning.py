import inspect
import os
import re
from datetime import date
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel, Field

from .vault_model import add_manual_relationship, build_vault_index, normalize_tag_name


NEW_PROJECT_FOLDER_SENTINEL = "__new_project_folder__"
PROJECT_KIND_ALIASES = {
    "ops": "sec_ops",
    "unterricht": "teaching",
    "education": "teaching",
    "gamedev": "game_dev",
    "game_dev": "game_dev",
    "game_development": "game_dev",
}
DOCUMENT_TYPES = {
    "project",
    "requirements",
    "architecture",
    "module",
    "api",
    "data_model",
    "ui_flow",
    "risk",
    "decision",
    "implementation_plan",
    "test_plan",
    "glossary",
    "operations",
    "research",
    "research_question",
    "methodology",
    "findings",
    "audience",
    "outline",
    "draft",
    "revision",
    "security",
    "infrastructure",
    "monitoring",
    "runbook",
    "incident_response",
    "framework",
    "competencies",
    "didactics",
    "lesson_sequence",
    "materials",
    "solutions",
    "game_overview",
    "scope",
    "gameplay_loop",
    "game_architecture",
    "gameplay_systems",
    "level_design",
    "asset_pipeline",
    "production_plan",
    "balancing",
}
RELATIONSHIP_TYPES = {"manual", "relates_to", "depends_on", "blocks", "supports"}


PROJECT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "software": {
        "label": "Software",
        "documents": [
            {"filename": "00 Projektuebersicht.md", "title": "Projektuebersicht", "type": "project", "outline": ["Ziel", "Umfang", "Dokumente", "Offene Fragen"]},
            {"filename": "01 Anforderungen.md", "title": "Anforderungen", "type": "requirements", "outline": ["Muss-Anforderungen", "Soll-Anforderungen", "Nicht-Ziele"]},
            {"filename": "02 Architektur.md", "title": "Architektur", "type": "architecture", "outline": ["Bausteine", "Datenfluss", "Abhaengigkeiten"]},
            {"filename": "03 Implementierungsplan.md", "title": "Implementierungsplan", "type": "implementation_plan", "outline": ["Schnitt 1", "Schnitt 2", "Risiken"]},
            {"filename": "04 Testplan.md", "title": "Testplan", "type": "test_plan", "outline": ["Unit-Tests", "Integrationstests", "UI-Smokes"]},
            {"filename": "05 Risiken und offene Fragen.md", "title": "Risiken und offene Fragen", "type": "risk", "outline": ["Risiken", "Offene Fragen", "Entscheidungsbedarf"]},
            {"filename": "APIs und Schnittstellen.md", "title": "APIs und Schnittstellen", "type": "api", "outline": ["Eingaenge", "Ausgaenge", "Fehlerfaelle"]},
            {"filename": "Datenmodell.md", "title": "Datenmodell", "type": "data_model", "outline": ["Entitaeten", "Validierung", "Migration"]},
            {"filename": "Entscheidungen/ADR-0001-Grundarchitektur.md", "title": "ADR-0001-Grundarchitektur", "type": "decision", "outline": ["Kontext", "Entscheidung", "Konsequenzen"]},
        ],
        "relationships": [
            ("implementation_plan", "architecture", "depends_on", "Implementation depends on architecture"),
            ("implementation_plan", "requirements", "depends_on", "Implementation tracks requirements"),
            ("test_plan", "requirements", "depends_on", "Tests verify requirements"),
            ("risk", "architecture", "relates_to", "Risks affect architecture choices"),
            ("decision", "architecture", "supports", "Decision records architecture rationale"),
            ("api", "architecture", "depends_on", "APIs are derived from architecture"),
            ("data_model", "architecture", "depends_on", "Data model supports architecture"),
        ],
    },
    "research": {
        "label": "Research",
        "documents": [
            {"filename": "00 Forschungsuebersicht.md", "title": "Forschungsuebersicht", "type": "project", "outline": ["Thema", "Ziel", "Dokumente", "Offene Fragen"]},
            {"filename": "01 Forschungsfrage.md", "title": "Forschungsfrage", "type": "research_question", "outline": ["Leitfrage", "Teilfragen", "Abgrenzung"]},
            {"filename": "02 Quellenlage.md", "title": "Quellenlage", "type": "research", "outline": ["Primaerquellen", "Sekundaerquellen", "Bewertung"]},
            {"filename": "03 Methodik.md", "title": "Methodik", "type": "methodology", "outline": ["Vorgehen", "Kriterien", "Grenzen"]},
            {"filename": "04 Erkenntnisse.md", "title": "Erkenntnisse", "type": "findings", "outline": ["Befunde", "Muster", "Unsicherheiten"]},
            {"filename": "05 Offene Fragen.md", "title": "Offene Fragen", "type": "risk", "outline": ["Ungeklaertes", "Risiken", "Naechste Recherche"]},
        ],
        "relationships": [
            ("methodology", "research_question", "depends_on", "Methodology follows the research question"),
            ("findings", "research", "depends_on", "Findings are derived from sources"),
            ("findings", "methodology", "depends_on", "Findings follow methodology"),
            ("risk", "research_question", "relates_to", "Open questions refine the research question"),
        ],
    },
    "writing": {
        "label": "Writing",
        "documents": [
            {"filename": "00 Schreibprojekt.md", "title": "Schreibprojekt", "type": "project", "outline": ["Ziel", "Format", "Dokumente", "Offene Fragen"]},
            {"filename": "01 Zielgruppe und Aussage.md", "title": "Zielgruppe und Aussage", "type": "audience", "outline": ["Leser", "Kernaussage", "Ton"]},
            {"filename": "02 Gliederung.md", "title": "Gliederung", "type": "outline", "outline": ["Struktur", "Kapitel", "Argumentationsbogen"]},
            {"filename": "03 Recherche.md", "title": "Recherche", "type": "research", "outline": ["Quellen", "Notizen", "Luecken"]},
            {"filename": "04 Entwurf.md", "title": "Entwurf", "type": "draft", "outline": ["Rohfassung", "Szenen oder Abschnitte", "Arbeitsnotizen"]},
            {"filename": "05 Revision.md", "title": "Revision", "type": "revision", "outline": ["Pruefpunkte", "Feedback", "Naechste Fassung"]},
        ],
        "relationships": [
            ("outline", "audience", "depends_on", "Outline follows audience and message"),
            ("draft", "outline", "depends_on", "Draft follows outline"),
            ("draft", "research", "depends_on", "Draft uses research"),
            ("revision", "draft", "depends_on", "Revision improves the draft"),
        ],
    },
    "sec_ops": {
        "label": "Sec-Ops",
        "documents": [
            {"filename": "00 Sicherheitsuebersicht.md", "title": "Sicherheitsuebersicht", "type": "project", "outline": ["Ziel", "Scope", "Assets", "Offene Fragen"]},
            {"filename": "01 Infrastruktur.md", "title": "Infrastruktur", "type": "infrastructure", "outline": ["Systeme", "Zugaenge", "Datenfluesse"]},
            {"filename": "02 Monitoring.md", "title": "Monitoring", "type": "monitoring", "outline": ["Signale", "Alarme", "Dashboards"]},
            {"filename": "03 Runbook.md", "title": "Runbook", "type": "runbook", "outline": ["Routineablaeufe", "Checks", "Eskalation"]},
            {"filename": "04 Incident Response.md", "title": "Incident Response", "type": "incident_response", "outline": ["Erkennung", "Eindaemmung", "Kommunikation", "Nachbereitung"]},
            {"filename": "05 Risiken und Kontrollen.md", "title": "Risiken und Kontrollen", "type": "security", "outline": ["Risiken", "Kontrollen", "Restunsicherheit"]},
        ],
        "relationships": [
            ("monitoring", "infrastructure", "depends_on", "Monitoring observes infrastructure"),
            ("runbook", "monitoring", "depends_on", "Runbook reacts to monitoring signals"),
            ("incident_response", "runbook", "depends_on", "Incident response builds on runbooks"),
            ("security", "infrastructure", "relates_to", "Security controls protect infrastructure"),
        ],
    },
    "generic": {
        "label": "Generic",
        "documents": [
            {"filename": "00 Projektuebersicht.md", "title": "Projektuebersicht", "type": "project", "outline": ["Ziel", "Umfang", "Dokumente", "Offene Fragen"]},
            {"filename": "01 Ziele.md", "title": "Ziele", "type": "requirements", "outline": ["Ergebnis", "Nicht-Ziele", "Erfolgskriterien"]},
            {"filename": "02 Arbeitspakete.md", "title": "Arbeitspakete", "type": "implementation_plan", "outline": ["Paket 1", "Paket 2", "Abhaengigkeiten"]},
            {"filename": "03 Entscheidungen.md", "title": "Entscheidungen", "type": "decision", "outline": ["Entscheidungen", "Begruendung", "Konsequenzen"]},
            {"filename": "04 Risiken.md", "title": "Risiken", "type": "risk", "outline": ["Risiken", "Gegenmassnahmen", "Offene Punkte"]},
        ],
        "relationships": [
            ("implementation_plan", "requirements", "depends_on", "Work packages follow goals"),
            ("decision", "requirements", "supports", "Decisions support goals"),
            ("risk", "implementation_plan", "relates_to", "Risks affect work packages"),
        ],
    },
    "teaching": {
        "label": "Teaching",
        "documents": [
            {"filename": "00 Unterrichtsuebersicht.md", "title": "Unterrichtsuebersicht", "type": "project", "outline": ["Thema", "Zielgruppe", "Umfang", "Dokumente", "Offene Entscheidungen"]},
            {"filename": "01 Rahmenkriterien.md", "title": "Rahmenkriterien", "type": "framework", "outline": ["Bundesland", "Schulart", "Klasse", "Paedagogische Besonderheiten", "Vorwissen"]},
            {"filename": "02 Kompetenzen und Bildungsplan.md", "title": "Kompetenzen und Bildungsplan", "type": "competencies", "outline": ["Bildungsplanbezug", "G-Niveau", "M-Niveau", "E-Niveau", "Sozialkompetenzen", "Metakompetenzen"]},
            {"filename": "03 Wissenschaftliche Recherche.md", "title": "Wissenschaftliche Recherche", "type": "research", "outline": ["Sachstand", "Zentrale Begriffe", "Quellen", "Fehlvorstellungen"]},
            {"filename": "04 Didaktische Reduktion.md", "title": "Didaktische Reduktion", "type": "didactics", "outline": ["Zielgruppenbezug", "Reduktionen", "Modelle", "Differenzierung", "Paedagogische Begruendung"]},
            {"filename": "05 Verlaufsplan.md", "title": "Verlaufsplan", "type": "lesson_sequence", "outline": ["Stundenuebersicht", "Phasen", "Lehrerhandlung", "Schuelerhandlung", "Sozialform", "Sicherung"]},
            {"filename": "06 Materialien.md", "title": "Materialien", "type": "materials", "outline": ["Praesentationen", "Arbeitsblaetter", "Videos", "Tafelbilder", "Digitale Tools"]},
            {"filename": "07 Loesungen und Erwartungshorizont.md", "title": "Loesungen und Erwartungshorizont", "type": "solutions", "outline": ["Musterloesungen", "Erwartungshorizont", "Hilfen", "Niveaudifferenzierung"]},
            {"filename": "08 Kritische Review.md", "title": "Kritische Review", "type": "revision", "outline": ["Fachliche Stimmigkeit", "Bildungsplan-Abgleich", "Zielgruppenpassung", "Zeitrealismus", "Ueberarbeitungen"]},
        ],
        "relationships": [
            ("competencies", "framework", "depends_on", "Competencies depend on teaching context"),
            ("research", "competencies", "supports", "Research supports competency planning"),
            ("didactics", "research", "depends_on", "Didactic reduction follows research"),
            ("didactics", "framework", "depends_on", "Didactic reduction follows learner context"),
            ("lesson_sequence", "didactics", "depends_on", "Lesson sequence follows didactic reduction"),
            ("materials", "lesson_sequence", "depends_on", "Materials support lesson phases"),
            ("solutions", "materials", "depends_on", "Solutions correspond to planned materials"),
            ("revision", "lesson_sequence", "relates_to", "Review checks the full lesson plan"),
        ],
    },
    "game_dev": {
        "label": "GameDev",
        "documents": [
            {"filename": "00 Game Overview.md", "title": "Game Overview", "type": "game_overview", "outline": ["Core fantasy", "Genre", "Target platform", "Player promise", "Design pillars", "Open questions"]},
            {"filename": "01 Scope and MVP.md", "title": "Scope and MVP", "type": "scope", "outline": ["MVP boundaries", "Non-goals", "Complexity budget", "Release slice", "Expansion hooks"]},
            {"filename": "02 Core Gameplay Loop.md", "title": "Core Gameplay Loop", "type": "gameplay_loop", "outline": ["Moment-to-moment loop", "Progression loop", "Win and loss conditions", "Player decisions", "Feedback"]},
            {"filename": "03 Engine and Architecture.md", "title": "Engine and Architecture", "type": "game_architecture", "outline": ["Engine assumptions", "Scene or module structure", "Data flow", "Save/load", "Tooling"]},
            {"filename": "04 Gameplay Systems.md", "title": "Gameplay Systems", "type": "gameplay_systems", "outline": ["Units and actors", "Input", "AI and state machines", "Pathfinding", "Task queues", "Resources"]},
            {"filename": "05 Content and Level Design.md", "title": "Content and Level Design", "type": "level_design", "outline": ["Content types", "Level structure", "Progression", "Authoring workflow", "Replayability"]},
            {"filename": "06 Art Audio UI Pipeline.md", "title": "Art Audio UI Pipeline", "type": "asset_pipeline", "outline": ["Art style", "Animation", "Audio", "UI screens", "Asset production"]},
            {"filename": "07 Production Plan.md", "title": "Production Plan", "type": "production_plan", "outline": ["Milestones", "Implementation slices", "Dependencies", "Validation checkpoints", "Definition of done"]},
            {"filename": "08 Testing and Balancing.md", "title": "Testing and Balancing", "type": "balancing", "outline": ["Playtest plan", "Balance levers", "Debug tools", "Performance targets", "Regression checks"]},
            {"filename": "09 Risks and Open Questions.md", "title": "Risks and Open Questions", "type": "risk", "outline": ["Hidden complexity", "Engine risks", "Scope risks", "Unknowns", "Mitigations"]},
        ],
        "relationships": [
            ("scope", "game_overview", "depends_on", "Scope is derived from the game overview"),
            ("gameplay_loop", "scope", "depends_on", "The loop must fit the MVP scope"),
            ("game_architecture", "scope", "depends_on", "Architecture follows scope and engine assumptions"),
            ("gameplay_systems", "gameplay_loop", "depends_on", "Systems implement the core loop"),
            ("gameplay_systems", "game_architecture", "depends_on", "Systems must match the architecture"),
            ("level_design", "gameplay_systems", "depends_on", "Content depends on available systems"),
            ("asset_pipeline", "game_overview", "supports", "Assets support the game direction"),
            ("production_plan", "game_architecture", "depends_on", "Production follows architecture"),
            ("production_plan", "gameplay_systems", "depends_on", "Production plans system complexity"),
            ("balancing", "gameplay_loop", "depends_on", "Balancing validates the gameplay loop"),
            ("risk", "scope", "relates_to", "Risks can reshape scope"),
            ("risk", "gameplay_systems", "relates_to", "Hidden system complexity is tracked as risk"),
        ],
    },
}
PROJECT_KINDS = set(PROJECT_TEMPLATES)


class ProjectSpec(BaseModel):
    title: str
    slug: str
    kind: str = "generic"
    summary: str = ""


class PlannedFile(BaseModel):
    path: str
    title: str
    type: str
    status: str = "draft"
    tags: List[str] = Field(default_factory=list)
    frontmatter: Dict[str, Any] = Field(default_factory=dict)
    links: List[str] = Field(default_factory=list)
    outline: List[str] = Field(default_factory=list)
    content_preview: str = ""
    content: Optional[str] = None


class PlannedRelationship(BaseModel):
    source: str
    target: str
    type: str = "relates_to"
    reason: str = ""
    suggested: bool = False


class NewTag(BaseModel):
    tag: str
    reason: str = ""


class ProjectPlan(BaseModel):
    target_folder: str
    project: ProjectSpec
    files: List[PlannedFile]
    relationships: List[PlannedRelationship] = Field(default_factory=list)
    new_tags: List[NewTag] = Field(default_factory=list)
    conflicts: List[Dict[str, str]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    questions: List[str] = Field(default_factory=list)


class ProjectPlanRequest(BaseModel):
    target_folder: str = ""
    title: str
    description: str = ""
    custom_focus: str = ""
    kind: str = "software"
    generate_content: bool = False
    approved_concept: str = ""
    concept_approved: bool = False


class ProjectDescriptionImproveRequest(BaseModel):
    title: str = ""
    description: str = ""
    custom_focus: str = ""
    kind: str = "software"


class GameDevConceptDraftRequest(BaseModel):
    title: str = ""
    description: str = ""
    custom_focus: str = ""
    kind: str = "game_dev"


class ProjectPlanApplyRequest(BaseModel):
    plan: ProjectPlan
    confirm: bool = False
    confirm_conflicts: bool = False


class ProjectPlanValidationError(ValueError):
    pass


def slugify_project(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "untitled-project"


def normalize_project_kind(value: str) -> str:
    kind = re.sub(r"[^a-z0-9]+", "_", str(value or "generic").strip().lower()).strip("_")
    kind = PROJECT_KIND_ALIASES.get(kind, kind)
    return kind if kind in PROJECT_TEMPLATES else "generic"


def normalize_relative_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/").strip()
    if not raw or raw in {".", "/"}:
        return ""
    if re.match(r"^[A-Za-z]:", raw) or raw.startswith("/"):
        raise ProjectPlanValidationError("Absolute paths are not allowed")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ProjectPlanValidationError("Path traversal is not allowed")
    return "/".join(parts)


def normalize_project_target_folder(target_folder: str, project_slug: str) -> str:
    raw = str(target_folder or "").strip()
    if raw == NEW_PROJECT_FOLDER_SENTINEL:
        return normalize_relative_path(project_slug)
    prefix = f"{NEW_PROJECT_FOLDER_SENTINEL}::"
    if raw.startswith(prefix):
        parent = normalize_relative_path(raw[len(prefix):])
        return "/".join(part for part in [parent, project_slug] if part)
    return normalize_relative_path(raw)


def resolve_inside(base_dir: str, relative_path: str) -> str:
    clean = normalize_relative_path(relative_path)
    abs_base = os.path.abspath(base_dir)
    abs_target = os.path.abspath(os.path.join(abs_base, clean))
    if os.path.commonpath([abs_base, abs_target]) != abs_base:
        raise ProjectPlanValidationError("Path escapes the vault")
    return abs_target


def ensure_under_folder(target_folder: str, file_path: str) -> None:
    folder = normalize_relative_path(target_folder)
    path = normalize_relative_path(file_path)
    if not folder:
        return
    if path != folder and not path.startswith(f"{folder}/"):
        raise ProjectPlanValidationError(f"File path must stay under target folder: {path}")


def template_options() -> Dict[str, Any]:
    kinds = [
        {"key": key, "label": template["label"]}
        for key, template in PROJECT_TEMPLATES.items()
    ]
    return {
        "kinds": kinds,
        "document_types": sorted(DOCUMENT_TYPES),
        "default_kind": "software",
        "new_folder_sentinel": NEW_PROJECT_FOLDER_SENTINEL,
    }


def build_project_plan(
    vault_dir: str,
    request: ProjectPlanRequest,
    *,
    today: Optional[date] = None,
) -> ProjectPlan:
    title = request.title.strip()
    if not title:
        raise ProjectPlanValidationError("Project title is required")
    kind = normalize_project_kind(request.kind)
    summary = _project_summary_from_request(request, kind)
    slug = slugify_project(title)
    target_folder = normalize_project_target_folder(request.target_folder, slug)
    created = (today or date.today()).isoformat()
    existing_tags = {tag["name"] for tag in build_vault_index(vault_dir).get("tags", [])}
    specs = _document_specs(kind)
    files: List[PlannedFile] = []
    for spec in specs:
        path = _document_path(target_folder, spec["filename"])
        tags = [
            f"#project/{slug}",
            f"#type/{spec['type']}",
            "#status/draft",
        ]
        frontmatter = {
            "type": spec["type"],
            "project": slug,
            "status": "draft",
            "source": "ai_project_planning",
            "created": created,
        }
        links = _links_for(spec["filename"], specs, target_folder)
        content = render_project_markdown(
            title=spec["title"],
            project_title=title,
            project_slug=slug,
            description=summary,
            doc_type=spec["type"],
            created=created,
            tags=tags,
            links=links,
            outline=spec["outline"],
        )
        files.append(PlannedFile(
            path=path,
            title=spec["title"],
            type=spec["type"],
            status="draft",
            tags=tags,
            frontmatter=frontmatter,
            links=links,
            outline=spec["outline"],
            content_preview=_content_preview(content),
            content=content,
        ))
    relationships: List[PlannedRelationship] = []
    new_tags = _new_tags_for(files, existing_tags)
    plan = ProjectPlan(
        target_folder=target_folder,
        project=ProjectSpec(title=title, slug=slug, kind=kind, summary=summary),
        files=files,
        relationships=relationships,
        new_tags=new_tags,
        conflicts=[],
        warnings=[],
        questions=[],
    )
    _add_external_relationship_suggestions(vault_dir, plan)
    return validate_project_plan(vault_dir, plan, collect_conflicts=True)


def _project_summary_from_request(request: ProjectPlanRequest, kind: str) -> str:
    approved = request.approved_concept.strip()
    if kind == "game_dev" and request.concept_approved and approved:
        return _summary_with_custom_focus(approved, request.custom_focus)
    return _summary_with_custom_focus(request.description, request.custom_focus)


def _summary_with_custom_focus(description: str, custom_focus: str) -> str:
    summary = str(description or "").strip()
    focus = str(custom_focus or "").strip()
    if not focus:
        return summary
    if not summary:
        return f"Nutzerdefinierte Schwerpunkte:\n{focus}"
    return f"{summary}\n\nNutzerdefinierte Schwerpunkte:\n{focus}"


def validate_gamedev_concept_gate(request: ProjectPlanRequest) -> None:
    if request.generate_content and normalize_project_kind(request.kind) == "game_dev":
        if not request.concept_approved or not request.approved_concept.strip():
            raise ProjectPlanValidationError("GameDev requires an approved concept draft before creating the plan")


def validate_project_plan(
    vault_dir: str,
    plan: ProjectPlan,
    *,
    collect_conflicts: bool = False,
) -> ProjectPlan:
    target_folder = normalize_relative_path(plan.target_folder)
    seen: Set[str] = set()
    planned_paths: Set[str] = set()
    conflicts: List[Dict[str, str]] = []
    warnings: List[str] = []
    existing_notes = set(_markdown_notes(vault_dir))

    plan.project.kind = normalize_project_kind(plan.project.kind)
    if plan.project.kind not in PROJECT_KINDS:
        warnings.append(f"Unknown project kind normalized by caller: {plan.project.kind}")
    if not plan.project.slug:
        raise ProjectPlanValidationError("Project slug is required")

    for planned in plan.files:
        planned.path = normalize_relative_path(planned.path)
        ensure_under_folder(target_folder, planned.path)
        if not planned.path.lower().endswith(".md"):
            raise ProjectPlanValidationError(f"Planned file must be markdown: {planned.path}")
        lower = planned.path.lower()
        if lower in seen:
            raise ProjectPlanValidationError(f"Duplicate planned file path: {planned.path}")
        seen.add(lower)
        planned_paths.add(planned.path)
        resolve_inside(vault_dir, planned.path)
        if os.path.exists(os.path.join(vault_dir, planned.path)):
            conflicts.append({"path": planned.path, "reason": "file_exists"})
        if planned.type not in DOCUMENT_TYPES:
            raise ProjectPlanValidationError(f"Unsupported document type: {planned.type}")
        _validate_tags(planned.tags, plan.project.slug)
        if not planned.frontmatter.get("type"):
            raise ProjectPlanValidationError(f"Missing frontmatter type for {planned.path}")
        if not planned.frontmatter.get("project"):
            raise ProjectPlanValidationError(f"Missing frontmatter project for {planned.path}")
        if planned.frontmatter.get("source") != "ai_project_planning":
            raise ProjectPlanValidationError(f"Invalid source for {planned.path}")

    allowed_link_targets = planned_paths | existing_notes
    for planned in plan.files:
        for link in planned.links:
            target = _link_to_path(link, planned.path)
            if target and target not in allowed_link_targets:
                warnings.append(f"Link target does not exist in plan or vault: {link}")

    for relationship in plan.relationships:
        relationship.source = normalize_relative_path(relationship.source)
        relationship.target = normalize_relative_path(relationship.target)
        if relationship.source in planned_paths:
            ensure_under_folder(target_folder, relationship.source)
        if relationship.target in planned_paths:
            ensure_under_folder(target_folder, relationship.target)
        if relationship.source not in planned_paths and relationship.source not in existing_notes:
            raise ProjectPlanValidationError(f"Relationship source does not exist: {relationship.source}")
        if relationship.target not in planned_paths and relationship.target not in existing_notes:
            raise ProjectPlanValidationError(f"Relationship target does not exist: {relationship.target}")
        if relationship.source == relationship.target:
            raise ProjectPlanValidationError("Relationship source and target must differ")
        if relationship.type not in RELATIONSHIP_TYPES:
            raise ProjectPlanValidationError(f"Unsupported relationship type: {relationship.type}")

    for new_tag in plan.new_tags:
        normalized = normalize_hash_tag(new_tag.tag)
        if new_tag.tag != normalized:
            new_tag.tag = normalized
        if not new_tag.reason:
            raise ProjectPlanValidationError(f"New tag needs a reason: {new_tag.tag}")

    plan.target_folder = target_folder
    plan.conflicts = conflicts if collect_conflicts else plan.conflicts
    plan.warnings = sorted(set([*plan.warnings, *warnings]))
    return plan


def apply_project_plan(vault_dir: str, plan: ProjectPlan) -> Dict[str, Any]:
    plan = validate_project_plan(vault_dir, plan, collect_conflicts=True)
    if plan.conflicts:
        raise ProjectPlanValidationError("Plan has file conflicts")

    written: List[str] = []
    relationships: List[Dict[str, Any]] = []
    for planned in plan.files:
        abs_path = resolve_inside(vault_dir, planned.path)
        if os.path.exists(abs_path):
            raise ProjectPlanValidationError(f"File already exists: {planned.path}")
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        content = planned.content or render_project_markdown(
            title=planned.title,
            project_title=plan.project.title,
            project_slug=plan.project.slug,
            description=plan.project.summary,
            doc_type=planned.type,
            created=str(planned.frontmatter.get("created") or date.today().isoformat()),
            tags=planned.tags,
            links=planned.links,
            outline=planned.outline,
        )
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(planned.path)

    for relationship in plan.relationships:
        relationship_payload = relationship.model_dump() if hasattr(relationship, "model_dump") else relationship.dict()
        relationships.append(add_manual_relationship(vault_dir, relationship_payload))

    graph = build_vault_index(vault_dir)["graph"]
    return {
        "success": True,
        "created_files": written,
        "relationships": relationships,
        "graph": {
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
        },
    }


LLMCall = Callable[..., Awaitable[str]]
ProjectPlanProgressCallback = Callable[[Dict[str, Any]], Awaitable[None]]


async def improve_project_description_with_ai(
    request: ProjectDescriptionImproveRequest,
    *,
    llm_call: LLMCall,
) -> str:
    title = request.title.strip() or "Unbenanntes Projekt"
    kind = normalize_project_kind(request.kind)
    raw_description = request.description.strip()
    custom_focus = request.custom_focus.strip()
    messages = [
        {
            "role": "system",
            "content": (
                "Du verbesserst Projektbeschreibungen fuer ein Obsidian Project Planning Tool. "
                "Antworte ausschliesslich mit der finalen, direkt uebernehmbaren Projektbeschreibung. "
                "Der erste ausgegebene Satz muss bereits Teil dieser Projektbeschreibung sein. "
                "Gib kein Denkprotokoll, keine Analyse, keine Zusammenfassung der Eingabe, keine Begruendung, "
                "keine Alternativen, keine Meta-Kommentare und keine Formulierungen wie 'Wir haben', "
                "'Der Nutzer will', 'Ich werde', 'Moegliche Struktur' oder 'Also beginne' aus. "
                "Verwende keinen Markdown-Codeblock. "
                "Ermittle die Sprache der eigentlichen Projekteingabe und verwende diese Sprache fuer die gesamte "
                "Ausgabe, inklusive Abschnittsueberschriften. Wenn die Projekteingabe gemischtsprachig ist, "
                "verwende die Sprache des konkreten Projektziels. Korrigiere offensichtliche Tippfehler still. "
                "Erfinde keine Fakten, Quellen, Anforderungen, Rahmenbedingungen oder Bildungsplanbezuege."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Projektart: {PROJECT_TEMPLATES[kind]['label']}\n"
                f"Projekttitel: {title}\n\n"
                "Verbessere diesen Projektkontext so, dass daraus ein guter, arbeitsfaehiger Projektplan "
                "erstellt werden kann. Klaere Ziel, Rahmen, Zielgruppe, Ergebnis, Constraints, offene Fragen "
                "und Qualitaetskriterien, aber markiere fehlende Informationen als offene Fragen. "
                "Beruecksichtige die nutzerdefinierten Schwerpunkte, ohne sie zu ueberschreiben.\n\n"
                "Ausgabeformat:\n"
                "- Beginne direkt mit den lokalisierten Entsprechungen von 'Projektart:' und 'Projekttitel:' "
                "in der Ausgabesprache.\n"
                "- Verwende danach lokalisierte Entsprechungen der Abschnitte 'Ziel', 'Rahmen', 'Zielgruppe', "
                "'Geplantes Ergebnis', 'Bekannte Constraints', 'Offene Fragen' und 'Qualitaetskriterien'.\n"
                "- Schreibe knapp, konkret und planungsorientiert.\n"
                "- Markiere Unsicheres nur als offene Frage, nicht als Annahme.\n"
                "- Schreibe keine Saetze ueber die Eingabe, den Nutzer, deine Absicht oder deine Vorgehensweise.\n"
                "- Nenne keine internen Ueberlegungen dazu, wie du zu dieser Struktur gekommen bist.\n\n"
                f"Nutzerdefinierte Schwerpunkte:\n{custom_focus or '(keine)'}\n\n"
                f"Eingabe:\n{raw_description or '(leer)'}"
            ),
        },
    ]
    improved = (await _call_llm(llm_call, messages, max_tokens=1400, temperature=0.25)).strip()
    improved = _strip_project_description_metatext(_strip_markdown_fence(improved))
    return improved or raw_description


async def build_gamedev_concept_draft_with_ai(
    request: GameDevConceptDraftRequest,
    *,
    llm_call: LLMCall,
) -> Dict[str, Any]:
    title = request.title.strip() or "Untitled Game"
    raw_description = request.description.strip()
    custom_focus = request.custom_focus.strip()
    messages = [
        {
            "role": "system",
            "content": (
                "You create an editable GameDev concept draft before a project structure is generated. "
                "Write in English. Do not create files yet. Do not invent engine facts; mark assumptions and verification tasks. "
                "Be practical and scope-aware. Explicitly call out hidden complexity and production risks."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Game title: {title}\n\n"
                f"User prompt:\n{raw_description or '(empty)'}\n\n"
                f"User-defined focus and priorities:\n{custom_focus or '(none)'}\n\n"
                "Create a concept draft with these sections:\n"
                "1. Game concept\n"
                "2. Genre, perspective, 2D/3D, target platform\n"
                "3. Engine and tech assumptions\n"
                "4. MVP scope and non-goals\n"
                "5. Core gameplay loop\n"
                "6. Major systems and hidden complexity\n"
                "7. Production risks and mitigation\n"
                "8. Open questions before implementation planning\n\n"
                "For strategy games or games with units, explicitly discuss worker/unit complexity, pathfinding, task queues, AI states, "
                "resource flows, selection/input, save/load, debug tools, and balancing where relevant."
            ),
        },
    ]
    draft = _strip_markdown_fence(await _call_llm(
        llm_call,
        messages,
        max_tokens=2400,
        temperature=0.3,
    )).strip()
    if not draft:
        raise ProjectPlanValidationError("AI returned empty GameDev concept draft")
    warnings = _gamedev_draft_warnings(draft)
    return {"draft": draft, "warnings": warnings}


async def generate_project_plan_content(
    plan: ProjectPlan,
    *,
    llm_call: LLMCall,
    max_attempts: int = 3,
    progress_callback: Optional[ProjectPlanProgressCallback] = None,
) -> ProjectPlan:
    if normalize_project_kind(plan.project.kind) == "game_dev" and not plan.project.summary.strip():
        raise ProjectPlanValidationError("GameDev requires an approved concept draft before AI content generation")
    project_context = _initial_project_context(plan)
    file_list = _project_file_list(plan)
    warnings = list(plan.warnings)

    for index, planned in enumerate(plan.files):
        if progress_callback:
            await progress_callback({
                "type": "file_started",
                "index": index,
                "total": len(plan.files),
                "path": planned.path,
                "file_type": planned.type,
            })
        skeleton = planned.content or ""
        generated = None
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                messages = _file_generation_messages(
                    plan=plan,
                    planned=planned,
                    file_index=index,
                    file_list=file_list,
                    project_context=project_context,
                    skeleton=skeleton,
                )
                generated = _strip_markdown_fence(await _call_llm(
                    llm_call,
                    messages,
                    max_tokens=3200,
                    temperature=0.35,
                )).strip()
                if not generated:
                    raise ProjectPlanValidationError("AI returned empty content")
                planned.content = _ensure_project_markdown_contract(generated, planned, skeleton)
                planned.content_preview = _content_preview(planned.content)
                break
            except Exception as exc:
                last_error = str(exc)
        else:
            planned.content = skeleton
            planned.content_preview = _content_preview(skeleton)
            warning = f"AI generation failed for {planned.path} after {max_attempts} attempts"
            if last_error:
                warning = f"{warning}: {last_error}"
            warnings.append(warning)
            if progress_callback:
                await progress_callback({"type": "warning", "message": warning})
            project_context = _append_failed_file_context(project_context, planned, warning)
            if progress_callback:
                await progress_callback({
                    "type": "file_done",
                    "index": index,
                    "total": len(plan.files),
                    "file": planned.model_dump() if hasattr(planned, "model_dump") else planned.dict(),
                })
            continue

        project_context = await _safe_update_project_context(
            llm_call=llm_call,
            previous_context=project_context,
            planned=planned,
            generated_content=planned.content,
            plan=plan,
        )
        if progress_callback:
            await progress_callback({
                "type": "file_done",
                "index": index,
                "total": len(plan.files),
                "file": planned.model_dump() if hasattr(planned, "model_dump") else planned.dict(),
            })

    plan.warnings = sorted(set(warnings))
    return plan


async def _call_llm(
    llm_call: LLMCall,
    messages: List[Dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
) -> str:
    result = llm_call(messages, max_tokens=max_tokens, temperature=temperature)
    if inspect.isawaitable(result):
        result = await result
    return str(result or "")


def _initial_project_context(plan: ProjectPlan) -> str:
    return (
        f"Projekt: {plan.project.title}\n"
        f"Slug: {plan.project.slug}\n"
        f"Projektart: {PROJECT_TEMPLATES[normalize_project_kind(plan.project.kind)]['label']}\n"
        f"Ausgangskontext:\n{plan.project.summary.strip() or 'Keine Beschreibung angegeben.'}\n\n"
        "Bisher generierte Dateien: noch keine.\n"
        "Akkumulierte Entscheidungen, Begriffe und offene Fragen werden nach jeder Datei aktualisiert."
    )


def _project_file_list(plan: ProjectPlan) -> str:
    lines = []
    for idx, planned in enumerate(plan.files, start=1):
        outline = ", ".join(planned.outline)
        lines.append(f"{idx}. {planned.path} | Rolle: {planned.type} | Outline: {outline}")
    return "\n".join(lines)


def _gamedev_draft_warnings(draft: str) -> List[str]:
    text = draft.lower()
    checks = {
        "MVP scope is not clearly mentioned": ("mvp", "scope"),
        "Engine or tech stack assumptions are not clearly mentioned": ("engine", "tech"),
        "Risks are not clearly mentioned": ("risk", "risks"),
        "Open questions are not clearly mentioned": ("open question", "open questions"),
    }
    warnings = []
    for warning, needles in checks.items():
        if not any(needle in text for needle in needles):
            warnings.append(warning)
    return warnings


def _file_generation_messages(
    *,
    plan: ProjectPlan,
    planned: PlannedFile,
    file_index: int,
    file_list: str,
    project_context: str,
    skeleton: str,
) -> List[Dict[str, str]]:
    source_policy = (
        "Erfinde keine Quellen, Studien, Autorinnen, URLs oder Gesetzes-/Bildungsplandetails. "
        "Wenn echte Recherche noetig ist, schreibe klare To-dos, Suchbegriffe, Pruefkriterien und Platzhalter."
    )
    custom_focus = _custom_focus_from_summary(plan.project.summary)
    return [
        {
            "role": "system",
            "content": (
                "Du erzeugst eine einzelne Markdown-Datei fuer einen Obsidian-Projektordner. "
                "Du arbeitest sequenziell: vorherige Dateien sind als akkumulierte Projektkontext-Zusammenfassung gegeben. "
                "Antworte ausschliesslich mit dem vollstaendigen Markdown-Inhalt dieser Datei. "
                "Keine Markdown-Codefences, keine Vorrede, keine Erklaerung ausserhalb der Datei. "
                f"{source_policy}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Projekt: {plan.project.title}\n"
                f"Projektart: {PROJECT_TEMPLATES[normalize_project_kind(plan.project.kind)]['label']}\n"
                f"Zieldatei {file_index + 1} von {len(plan.files)}: {planned.path}\n"
                f"Dateityp: {planned.type}\n"
                f"Titel: {planned.title}\n"
                f"Tags: {' '.join(planned.tags)}\n"
                f"Links: {', '.join(planned.links) or '(keine)'}\n"
                f"Outline: {', '.join(planned.outline)}\n\n"
                f"Nutzerdefinierte Schwerpunkte:\n{custom_focus or '(keine)'}\n\n"
                "Komplette geplante Projektdateien:\n"
                f"{file_list}\n\n"
                "Akkumulierter Projektordner-Kontext aus vorherigen Dateien:\n"
                f"{project_context}\n\n"
                "Skeleton mit verpflichtender Struktur, Frontmatter, Links und Tags. "
                "Behalte Frontmatter, Projektlinks und Tags bei, aber ersetze Platzhalter durch arbeitsfertige Inhalte. "
                "Erfinde keine weiteren Wikilinks und verknuepfe Projektdokumente nicht direkt miteinander; "
                "die geplanten Links bilden bewusst eine Hub-and-Spoke-Struktur ueber die erste Projektdatei:\n"
                f"{skeleton}\n\n"
                "Schreibe die Datei arbeitsfertig: konkrete Abschnitte, Listen, Entscheidungen, To-dos, offene Fragen. "
                "Baue sinnvoll auf dem akkumulierten Kontext auf und widersprich frueheren Entscheidungen nicht."
            ),
        },
    ]


def _custom_focus_from_summary(summary: str) -> str:
    marker = "Nutzerdefinierte Schwerpunkte:"
    if marker not in str(summary or ""):
        return ""
    return str(summary or "").split(marker, 1)[1].strip()


async def _update_project_context(
    *,
    llm_call: LLMCall,
    previous_context: str,
    planned: PlannedFile,
    generated_content: str,
    plan: ProjectPlan,
) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "Du pflegst eine kompakte Kontextzusammenfassung fuer eine sequenzielle Projektordner-Generierung. "
                "Antworte nur mit der aktualisierten Zusammenfassung. Keine Codefences. "
                "Halte sie kompakt, aber erhalte Entscheidungen, Begriffe, offene Fragen, Abhaengigkeiten und Details, "
                "die spaetere Dateien brauchen."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Projekt: {plan.project.title}\n\n"
                f"Bisherige Kontextzusammenfassung:\n{previous_context}\n\n"
                f"Neu generierte Datei: {planned.path}\n"
                f"Inhalt:\n{generated_content}\n\n"
                "Aktualisiere die Projektordner-Kontextzusammenfassung fuer die naechste Datei."
            ),
        },
    ]
    updated = _strip_markdown_fence(await _call_llm(
        llm_call,
        messages,
        max_tokens=1800,
        temperature=0.2,
    )).strip()
    return _compact_text(updated or previous_context, limit=6000)


async def _safe_update_project_context(
    *,
    llm_call: LLMCall,
    previous_context: str,
    planned: PlannedFile,
    generated_content: str,
    plan: ProjectPlan,
) -> str:
    try:
        return await _update_project_context(
            llm_call=llm_call,
            previous_context=previous_context,
            planned=planned,
            generated_content=generated_content,
            plan=plan,
        )
    except Exception:
        return _compact_text(
            f"{previous_context}\n\n"
            f"Datei fertiggestellt: {planned.path}\n"
            f"Kurzauszug fuer Folgedateien:\n{_content_preview(generated_content)}",
            limit=6000,
        )


def _append_failed_file_context(project_context: str, planned: PlannedFile, warning: str) -> str:
    return _compact_text(
        f"{project_context}\n\nGenerierungswarnung fuer {planned.path}: {warning}\n"
        "Diese Datei blieb beim Skeleton; spaetere Dateien sollen fehlende Details als offene Punkte markieren.",
        limit=6000,
    )


def _ensure_project_markdown_contract(content: str, planned: PlannedFile, skeleton: str) -> str:
    text = content.strip()
    if not text.startswith("---"):
        skeleton_frontmatter = _frontmatter_block(skeleton)
        if skeleton_frontmatter:
            text = f"{skeleton_frontmatter}\n\n{text}"
    if not any(tag in text for tag in planned.tags):
        text = f"{text.rstrip()}\n\nTags: {' '.join(planned.tags)}"
    return text.rstrip() + "\n"


def _frontmatter_block(markdown: str) -> str:
    match = re.match(r"^(---\n.*?\n---)", markdown or "", flags=re.DOTALL)
    return match.group(1) if match else ""


def _strip_markdown_fence(value: str) -> str:
    text = str(value or "").strip()
    match = re.match(r"^```(?:markdown|md)?\s*(.*?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _strip_project_description_metatext(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    accepted_prefixes = (
        "projektart:",
        "projekttitel:",
        "project type:",
        "project kind:",
        "project title:",
        "ziel:",
        "goal:",
        "objective:",
    )
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower().startswith(accepted_prefixes):
            return "\n".join(lines[index:]).strip()
    return text


def _compact_text(value: str, *, limit: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0].rstrip() + "\n\n[Kontext gekuerzt]"


def render_project_markdown(
    *,
    title: str,
    project_title: str,
    project_slug: str,
    description: str,
    doc_type: str,
    created: str,
    tags: Iterable[str],
    links: Iterable[str],
    outline: Iterable[str],
) -> str:
    overview_link = next((link for link in links if re.match(r"\[\[[^]]*00 ", str(link))), "")
    project_line = f"Projekt: {overview_link}" if overview_link else f"Projekt: {project_title}"
    frontmatter = [
        "---",
        f"type: {doc_type}",
        f"project: {project_slug}",
        "status: draft",
        "source: ai_project_planning",
        f"created: {created}",
        "---",
        "",
    ]
    body = [
        f"# {title}",
        "",
        project_line,
        "",
        "## Kontext",
        "",
        description.strip() or "Projektkontext wird im naechsten Review ausgearbeitet.",
        "",
        "## Verknuepfte Notizen",
        "",
    ]
    link_lines = [f"- {link}" for link in links] or ["- [[00 Projektuebersicht]]"]
    outline_lines = ["", "## Arbeitsnotizen", ""]
    for item in outline:
        outline_lines.extend([f"### {item}", "", "- [ ] Klaeren und ausarbeiten.", ""])
    tag_line = ["Tags: " + " ".join(tags), ""]
    return "\n".join(frontmatter + body + link_lines + outline_lines + tag_line)


def normalize_hash_tag(tag: str) -> str:
    raw = str(tag or "").strip()
    raw = raw[1:] if raw.startswith("#") else raw
    return f"#{normalize_tag_name(raw)}"


def _validate_tags(tags: List[str], project_slug: str) -> None:
    normalized = {normalize_hash_tag(tag) for tag in tags}
    required = {
        f"#project/{project_slug}",
        "#status/draft",
    }
    if not required.issubset(normalized):
        raise ProjectPlanValidationError("Project and draft status tags are required")
    if not any(tag.startswith("#type/") for tag in normalized):
        raise ProjectPlanValidationError("A type tag is required")


def _document_specs(kind: str) -> List[Dict[str, Any]]:
    template = PROJECT_TEMPLATES[normalize_project_kind(kind)]
    return [dict(spec) for spec in template["documents"]]


def _document_path(target_folder: str, filename: str) -> str:
    return "/".join(part for part in [target_folder, filename] if part)


def _note_link_for_filename(target_folder: str, filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    return f"[[{_document_path(target_folder, stem)}]]"


def _links_for(filename: str, specs: List[Dict[str, Any]], target_folder: str) -> List[str]:
    def note_link(filename: str) -> str:
        return _note_link_for_filename(target_folder, filename)

    hub_filename = specs[0]["filename"]
    if filename == hub_filename:
        return [note_link(spec["filename"]) for spec in specs if spec["filename"] != hub_filename]
    return [note_link(hub_filename)]


def _relationships_for(files: List[PlannedFile], kind: str) -> List[PlannedRelationship]:
    return []


def _add_external_relationship_suggestions(vault_dir: str, plan: ProjectPlan, *, limit: int = 3) -> None:
    if not plan.files:
        return
    hub_path = plan.files[0].path
    planned_paths = {file.path for file in plan.files}
    project_terms = _project_terms(plan)
    suggestions: List[PlannedRelationship] = []
    for note in build_vault_index(vault_dir).get("notes", []):
        path = note.get("path", "")
        if not path or path in planned_paths:
            continue
        score = _external_note_score(note, project_terms, plan.project.slug)
        if score <= 0:
            continue
        suggestions.append(PlannedRelationship(
            source=hub_path,
            target=path,
            type="relates_to",
            reason=f"Suggested sparse project context match ({score})",
            suggested=True,
        ))
    suggestions.sort(key=lambda item: (-_relationship_score(item.reason), item.target.lower()))
    existing_keys = {(rel.source, rel.target, rel.type) for rel in plan.relationships}
    for suggestion in suggestions[:limit]:
        key = (suggestion.source, suggestion.target, suggestion.type)
        if key not in existing_keys:
            plan.relationships.append(suggestion)
            existing_keys.add(key)


def _project_terms(plan: ProjectPlan) -> Set[str]:
    raw = " ".join([
        plan.project.title,
        plan.project.slug.replace("-", " "),
        plan.project.summary,
    ])
    terms = {
        normalize_tag_name(term)
        for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", raw.lower())
    }
    stopwords = {"der", "die", "das", "und", "oder", "with", "the", "for", "from", "eine", "einer", "projekt", "project"}
    return {term for term in terms if term and term not in stopwords}


def _external_note_score(note: Dict[str, Any], project_terms: Set[str], project_slug: str) -> int:
    title_terms = {
        normalize_tag_name(term)
        for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", str(note.get("title", "")).lower())
    }
    tag_terms = {normalize_tag_name(tag) for tag in note.get("tags", [])}
    text = str(note.get("_content", "")).lower()
    score = 0
    score += len(project_terms & title_terms) * 4
    score += len(project_terms & tag_terms) * 3
    score += sum(1 for term in project_terms if term.replace("-", " ") in text)
    if project_slug in tag_terms:
        score += 6
    return score


def _relationship_score(reason: str) -> int:
    match = re.search(r"\((\d+)\)", str(reason or ""))
    return int(match.group(1)) if match else 0


def _new_tags_for(files: List[PlannedFile], existing_tags: Set[str]) -> List[NewTag]:
    tags = sorted({normalize_hash_tag(tag) for planned in files for tag in planned.tags})
    new_tags = []
    for tag in tags:
        name = tag[1:]
        if name not in existing_tags:
            new_tags.append(NewTag(tag=tag, reason="Required by Phase 4 project note schema"))
    return new_tags


def _markdown_notes(vault_dir: str) -> List[str]:
    notes: List[str] = []
    for root, dirs, files in os.walk(vault_dir):
        dirs[:] = [d for d in dirs if d != ".obsidian"]
        for file in files:
            if file.lower().endswith(".md"):
                notes.append(os.path.relpath(os.path.join(root, file), vault_dir).replace("\\", "/"))
    notes.sort(key=str.lower)
    return notes


def _link_to_path(link: str, source_path: str) -> Optional[str]:
    match = re.match(r"\[\[([^\]|#]+)", str(link or "").strip())
    if not match:
        return None
    target = match.group(1).strip()
    if not target.lower().endswith(".md"):
        target = f"{target}.md"
    if "/" not in target and "/" in source_path:
        local = f"{source_path.rsplit('/', 1)[0]}/{target}"
        return local
    return target


def _content_preview(content: str) -> str:
    lines = [line for line in content.splitlines() if line.strip()]
    return "\n".join(lines[:8])
