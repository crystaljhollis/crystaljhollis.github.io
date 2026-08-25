#!/usr/bin/env python3
"""Desktop wizard for applying archival image metadata and optional AI drafts.

This is intentionally separate from batch_alt_text.py. It keeps the original
alt-text-only command-line workflow available while providing a safer UI for
the broader human-in-the-loop workflow:

1. Choose a metadata template and an image folder.
2. Paste human-reviewed intake fields.
3. Apply the template and IPTC/XMP fields to the images.
4. Optionally generate alt text for the dedicated accessibility field.
5. Optionally generate a Markdown handoff of image-specific optional keywords.

ExifTool performs metadata-only writes. Image pixels are never recompressed.
Optional keywords are never written to image files by this wizard.
"""

from __future__ import annotations

import base64
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_MODEL = os.environ.get("OPENAI_ALT_TEXT_MODEL", "gpt-4.1-mini")
CONFIG_PATH = Path.home() / ".metadata_wizard_templates.json"
KEYCHAIN_SERVICE = "Portfolio Metadata Wizard"
KEYCHAIN_ACCOUNT = "openai-api-key"

DEFAULT_TEMPLATE_SPECS = [
    ("Generic Archive Template", "generic_archive_template.xmp"),
    ("Editorial Photography Template", "editorial_photography_template.xmp"),
    ("Event Photography Template", "event_photography_template.xmp"),
]

ALT_TEXT_PROMPT = """You write accessibility alt text for a professional photo archive.

Describe only what is visibly present in the image. Return one concise sentence,
or two short sentences when needed, with a target length of 20 to 250 characters.
Focus on the main subject, action, setting, and a few meaningful visual details.
Include short, clearly readable text in quotation marks only when that text is
important to understanding the image. Do not invent names, identities, dates,
locations, emotions, relationships, or events that cannot be supported by the
image. Do not list every visible person or word. Avoid phrases such as "image of"
or "photo of" unless they are genuinely useful. Use neutral, specific language.

Return only the final alt-text sentence. Do not add a label, quotation marks
around the whole answer, commentary, or a confidence score.
"""

OPTIONAL_KEYWORD_PROMPT = """You create optional, image-specific keyword suggestions
for a professional photo archive.

Suggest up to eight keywords or short phrases supported by what is visibly shown
in the image. Include visible objects, actions, readable text, and a person's
name only when the name is clearly visible in the image or the person is plainly
identified by readable event material. Do not suggest collection-level batch
keywords. Do not infer an event, location, person, or subject that is not visible.
Do not repeat broad generic keywords unless they are specifically useful for this
image. Return JSON only in this exact shape:
{"keywords": ["keyword one", "keyword two"]}
"""

FIELD_LABELS = {
    "headline": "HEADLINE",
    "description": "DESCRIPTION",
    "batch_keywords": "BATCH KEYWORDS",
    "description_writer": "DESCRIPTION WRITER",
    "sublocation": "SUBLOCATION",
    "city": "CITY",
    "state": "STATE/PROVINCE",
    "country": "COUNTRY",
    "title": "TITLE",
    "job_identifier": "JOB IDENTIFIER",
}


@dataclass
class TemplateEntry:
    name: str
    path: Path


def ensure_exiftool() -> None:
    if not shutil.which("exiftool"):
        raise RuntimeError(
            "ExifTool is required for metadata writes. Install it with: brew install exiftool"
        )


def keychain_available() -> bool:
    """Return whether this computer has the macOS Keychain CLI available."""

    return sys.platform == "darwin" and shutil.which("security") is not None


def load_saved_api_key() -> str:
    """Load the wizard's API key from macOS Keychain, if one is saved."""

    if not keychain_available():
        return ""

    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""

    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def save_keychain_password(api_key: str) -> None:
    """Answer the macOS security command's hidden password prompt securely."""

    # `security add-generic-password -w` reads the password from a controlling
    # terminal, not from a normal subprocess stdin pipe. A pseudo-terminal lets
    # us answer that prompt without putting the API key in process arguments.
    import errno
    import pty
    import select
    import signal
    import time

    command = [
        "security",
        "add-generic-password",
        "-U",
        "-a",
        KEYCHAIN_ACCOUNT,
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
    ]
    pid, master_fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(command[0], command)
        except OSError:
            os._exit(127)

    child_status: int | None = None
    deadline = time.monotonic() + 5
    try:
        os.write(master_fd, f"{api_key}\n".encode())

        while time.monotonic() < deadline:
            waited_pid, status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                child_status = status
                break

            try:
                ready, _writeable, _exceptional = select.select(
                    [master_fd], [], [], 0.1
                )
                if ready:
                    # Drain prompts and command output without ever logging it.
                    os.read(master_fd, 4096)
            except OSError as exc:
                if exc.errno not in {errno.EIO, errno.EBADF}:
                    raise

        if child_status is None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise RuntimeError("The macOS Keychain operation timed out. Try again.")

        exit_code = os.waitstatus_to_exitcode(child_status)
        if exit_code != 0:
            raise RuntimeError(
                "macOS Keychain rejected the save operation. "
                "Check that your login Keychain is unlocked."
            )
    except OSError as exc:
        raise RuntimeError(f"Could not access macOS Keychain: {exc}") from exc
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass


def save_api_key(api_key: str) -> None:
    """Save an API key to macOS Keychain without placing it in command arguments."""

    if not keychain_available():
        raise RuntimeError(
            "macOS Keychain is unavailable. Set OPENAI_API_KEY in the environment "
            "instead."
        )

    save_keychain_password(api_key)


def delete_saved_api_key() -> bool:
    """Delete the wizard's saved API key and report whether one was removed."""

    if not keychain_available() or not load_saved_api_key():
        return False

    try:
        result = subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                KEYCHAIN_SERVICE,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not access macOS Keychain: {exc}") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or "The Keychain operation failed."
        raise RuntimeError(detail)
    return True


def resolve_api_key() -> tuple[str, str]:
    """Return the key and its source, preferring the wizard's saved key."""

    saved_key = load_saved_api_key()
    if saved_key:
        return saved_key, "App Keychain"

    environment_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if environment_key:
        return environment_key, "OPENAI_API_KEY environment variable"

    return "", ""


def template_directories() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("METADATA_WIZARD_TEMPLATES", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    script_root = Path(__file__).resolve().parent
    candidates.append(script_root / "templates")
    candidates.append(Path.cwd() / "templates")

    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if str(resolved) not in seen:
            seen.add(str(resolved))
            result.append(resolved)
    return result


def resolve_template_file(filename: str) -> Path:
    raw = Path(filename).expanduser()
    if raw.is_file():
        return raw.resolve()

    for directory in template_directories():
        candidate = directory / raw.name
        if candidate.is_file():
            return candidate.resolve()

    return raw.resolve()


def default_templates() -> list[TemplateEntry]:
    return [
        TemplateEntry(name, resolve_template_file(filename))
        for name, filename in DEFAULT_TEMPLATE_SPECS
    ]


def load_templates() -> list[TemplateEntry]:
    if not CONFIG_PATH.is_file():
        return default_templates()

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        entries = [
            TemplateEntry(str(item["name"]), resolve_template_file(str(item["path"])))
            for item in data
            if item.get("name") and item.get("path")
        ]
        return entries or default_templates()
    except (OSError, json.JSONDecodeError, TypeError, KeyError, AttributeError):
        return default_templates()


def save_templates(entries: list[TemplateEntry]) -> None:
    payload = [{"name": entry.name, "path": str(entry.path)} for entry in entries]
    try:
        CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def find_keyword_list() -> Path | None:
    env_path = os.environ.get("METADATA_WIZARD_KEYWORDS", "").strip()
    candidates = [
        Path(env_path).expanduser() if env_path else None,
        Path(__file__).resolve().parent / "approved_keywords.txt",
        Path.cwd() / "approved_keywords.txt",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()
    return None


def load_approved_keywords(path: Path | None) -> dict[str, str]:
    if not path or not path.is_file():
        return {}

    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        term = raw_line.strip()
        if not term or term.startswith("[") or term.endswith(":"):
            continue
        result.setdefault(term.casefold(), term)
    return result


def collect_images(folder: Path, recursive: bool = True) -> list[Path]:
    if folder.is_file():
        return [folder] if folder.suffix.lower() in SUPPORTED_EXTENSIONS else []
    if not folder.is_dir():
        return []

    iterator = folder.rglob("*") if recursive else folder.glob("*")
    images = [
        path.resolve()
        for path in iterator
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(set(images), key=lambda path: str(path).casefold())


def image_data_url(path: Path, max_dimension: int = 2400) -> str:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required for AI image analysis. Install it with: "
            "python3 -m pip install Pillow"
        ) from exc

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)

    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def clean_model_text(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    cleaned = re.sub(
        r"^(?:alt\s*text|alt|description)\s*:\s*",
        "",
        cleaned,
        flags=re.I,
    )
    return cleaned.strip().strip('"“”').strip()


def generate_alt_text(client: Any, path: Path, model: str, context: str) -> str:
    context_block = ""
    if context.strip():
        context_block = (
            "\nOptional project context follows. Use it only to clarify the task. "
            "Do not add facts that are not visible in the image:\n"
            f"{context.strip()}\n"
        )

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": ALT_TEXT_PROMPT + context_block},
                    {
                        "type": "input_image",
                        "image_url": image_data_url(path),
                        "detail": "high",
                    },
                ],
            }
        ],
        max_output_tokens=120,
    )
    result = clean_model_text(response.output_text)
    if not result:
        raise RuntimeError("The model returned an empty alt-text draft")
    return result


def parse_keyword_response(text: str) -> list[str]:
    cleaned = text.strip()
    fence = chr(96) * 3
    cleaned = re.sub(r"^\s*" + re.escape(fence) + r"(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*" + re.escape(fence) + r"$", "", cleaned)

    values: Any
    try:
        values = json.loads(cleaned)
    except json.JSONDecodeError:
        values = None

    if isinstance(values, dict):
        values = values.get("keywords", [])
    elif not isinstance(values, list):
        values = re.split(r"[,;\n]+", cleaned)

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = str(value).strip().strip('"“”').strip(" -*")
        keyword = " ".join(keyword.split())
        if not keyword:
            continue
        key = keyword.casefold()
        if key not in seen:
            seen.add(key)
            result.append(keyword)
    return result[:8]


def generate_optional_keywords(client: Any, path: Path, model: str) -> list[str]:
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": OPTIONAL_KEYWORD_PROMPT},
                    {
                        "type": "input_image",
                        "image_url": image_data_url(path),
                        "detail": "high",
                    },
                ],
            }
        ],
        max_output_tokens=180,
    )
    return parse_keyword_response(response.output_text)


def read_alt_text(path: Path) -> str:
    result = subprocess.run(
        ["exiftool", "-j", "-XMP-iptcCore:AltTextAccessibility", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return str(json.loads(result.stdout)[0].get("AltTextAccessibility", ""))


def split_keywords(value: str) -> list[str]:
    parts = re.split(r"[;,\n]+", value)
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        keyword = " ".join(part.strip().split())
        if not keyword:
            continue
        key = keyword.casefold()
        if key not in seen:
            seen.add(key)
            result.append(keyword)
    return result


def format_job_identifier(collection_number: str, batch_number: str) -> str:
    collection = re.sub(
        r"^COLL(?:ECTION)?[-\s]*", "", collection_number.strip(), flags=re.I
    )
    batch = re.sub(r"^BATCH[-\s]*", "", batch_number.strip(), flags=re.I)
    if not collection or not batch:
        raise ValueError("Enter both the collection number and the batch number.")
    return f"COLL-{collection}, BATCH-{batch}"


def add_dual_tag(args: list[str], first: str, second: str, value: str) -> None:
    if value.strip():
        args.extend([f"-{first}={value}", f"-{second}={value}"])


def add_description_writer(args: list[str], value: str) -> None:
    """Write Description Writer to XMP and handle the legacy IPTC limit.

    The XMP field is photoshop:CaptionWriter. The older IPTC IIM
    Writer-Editor field is limited to 32 bytes, so a longer value must not be
    allowed to become the only value Bridge can see.
    """
    if not value.strip():
        return

    args.append(f"-XMP-photoshop:CaptionWriter={value}")
    if len(value.encode("utf-8")) <= 32:
        args.append(f"-IPTC:Writer-Editor={value}")
    else:
        # Remove a stale or truncated legacy value from an earlier run. The
        # complete value remains in XMP, which has no 32-byte IIM limit.
        args.append("-IPTC:Writer-Editor=")


def build_metadata_arguments(fields: dict[str, str]) -> list[str]:
    args: list[str] = []
    add_dual_tag(args, "IPTC:Headline", "XMP-photoshop:Headline", fields["headline"])
    add_dual_tag(args, "IPTC:Caption-Abstract", "XMP-dc:Description", fields["description"])
    add_description_writer(args, fields["description_writer"])
    add_dual_tag(args, "IPTC:Sub-location", "XMP-iptcCore:Location", fields["sublocation"])
    add_dual_tag(args, "IPTC:City", "XMP-photoshop:City", fields["city"])
    add_dual_tag(args, "IPTC:Province-State", "XMP-photoshop:State", fields["state"])
    add_dual_tag(args, "IPTC:Country-PrimaryLocationName", "XMP-photoshop:Country", fields["country"])
    add_dual_tag(args, "IPTC:ObjectName", "XMP-dc:Title", fields["title"])
    add_dual_tag(
        args,
        "IPTC:OriginalTransmissionReference",
        "XMP-photoshop:TransmissionReference",
        fields["job_identifier"],
    )

    keywords = split_keywords(fields["batch_keywords"])
    if keywords:
        joined = ";".join(keywords)
        args.extend(["-sep", ";", f"-IPTC:Keywords={joined}", f"-XMP-dc:Subject={joined}"])
    return args


def apply_metadata(path: Path, template: Path, fields: dict[str, str]) -> None:
    command = [
        "exiftool",
        "-overwrite_original",
        "-P",
        "-tagsFromFile",
        str(template),
        "-all:all",
        *build_metadata_arguments(fields),
        str(path),
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or "ExifTool metadata write failed")


def write_alt_text(path: Path, text: str) -> None:
    result = subprocess.run(
        [
            "exiftool",
            "-overwrite_original",
            "-P",
            f"-XMP-iptcCore:AltTextAccessibility={text}",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or "ExifTool alt-text write failed")


def classify_keywords(keywords: list[str], approved: dict[str, str]) -> tuple[list[str], list[str]]:
    approved_matches: list[str] = []
    review_exceptions: list[str] = []
    for keyword in keywords:
        match = approved.get(keyword.casefold())
        if match:
            if match not in approved_matches:
                approved_matches.append(match)
        elif keyword not in review_exceptions:
            review_exceptions.append(keyword)
    return approved_matches, review_exceptions


def markdown_cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ").strip()


def write_optional_keywords_markdown(
    output_folder: Path,
    selected_folder: Path,
    rows: list[tuple[Path, list[str], list[str], str]],
    keyword_list_path: Path | None,
) -> Path:
    output_path = output_folder / f"{selected_folder.name}_optional_keywords.md"
    lines = [
        f"# {selected_folder.name} Optional Keyword Draft",
        "",
        "This file was generated by the metadata wizard for human QC.",
        "Optional keywords were not written into the image files.",
        "Collection-level batch keywords were not generated or included.",
        "",
        "Review terms may include visible names or text that are not exact matches",
        "to the approved keyword list. Confirm all suggestions in Adobe Bridge.",
        "",
        f"Keyword list used: {keyword_list_path or 'Not found; all suggestions need review'}",
        "",
        "| File | Approved-list candidates | Review exceptions |",
        "| --- | --- | --- |",
    ]

    for path, approved, review, error in rows:
        relative_name = (
            str(path.relative_to(selected_folder))
            if path.is_relative_to(selected_folder)
            else path.name
        )
        approved_text = ", ".join(approved)
        review_text = ", ".join(review)
        if error:
            review_text = f"Generator error: {error}"
        lines.append(
            f"| {markdown_cell(relative_name)} | "
            f"{markdown_cell(approved_text)} | {markdown_cell(review_text)} |"
        )

    lines.extend(
        [
            "",
            "## QC notes",
            "",
            "- Bridge may convert commas to semicolons when keywords are entered.",
            "- Add names or persons only after visually confirming them in Bridge.",
            "- This is a draft for review, not a controlled-vocabulary approval.",
        ]
    )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


class MetadataWizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Metadata Wizard")
        self.geometry("980x940")
        self.minsize(820, 760)

        self.templates = load_templates()
        self.selected_template = tk.StringVar(
            value=self.templates[0].name if self.templates else ""
        )
        self.folder_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=True)
        self.skip_existing_alt_var = tk.BooleanVar(value=True)
        self.run_alt_var = tk.StringVar(value="No")
        self.run_optional_var = tk.StringVar(value="No")
        self.model_var = tk.StringVar(value=DEFAULT_MODEL)
        self.collection_number_var = tk.StringVar()
        self.batch_number_var = tk.StringVar()
        self.job_preview_var = tk.StringVar(value="COLL-##, BATCH-##")
        self.keyword_list_path = find_keyword_list()
        self.busy = False
        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.field_vars: dict[str, tk.StringVar] = {
            "headline": tk.StringVar(),
            "batch_keywords": tk.StringVar(),
            "description_writer": tk.StringVar(value="Archive Metadata Team"),
            "sublocation": tk.StringVar(),
            "city": tk.StringVar(value="Dallas"),
            "state": tk.StringVar(value="TX"),
            "country": tk.StringVar(value="United States"),
            "title": tk.StringVar(),
        }
        self.description_text: ScrolledText | None = None
        self.intake_paste_text: ScrolledText | None = None
        self.template_list_frame: ttk.Frame | None = None
        self.log_text: ScrolledText | None = None
        self.apply_button: ttk.Button | None = None
        self.api_status_var = tk.StringVar()
        self.keyword_status_var = tk.StringVar()

        self._build_ui()
        self._refresh_status_labels()

    def _build_ui(self) -> None:
        scroll_container = ttk.Frame(self)
        scroll_container.pack(fill="both", expand=True)

        scroll_canvas = tk.Canvas(scroll_container, highlightthickness=0)
        scroll_bar = ttk.Scrollbar(
            scroll_container,
            orient="vertical",
            command=scroll_canvas.yview,
        )
        scroll_canvas.configure(yscrollcommand=scroll_bar.set)
        scroll_bar.pack(side="right", fill="y")
        scroll_canvas.pack(side="left", fill="both", expand=True)

        outer = ttk.Frame(scroll_canvas, padding=12)
        window_id = scroll_canvas.create_window((0, 0), window=outer, anchor="nw")

        def update_scroll_region(_event: tk.Event) -> None:
            scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))

        def fit_scroll_width(event: tk.Event) -> None:
            scroll_canvas.itemconfigure(window_id, width=event.width)

        outer.bind("<Configure>", update_scroll_region)
        scroll_canvas.bind("<Configure>", fit_scroll_width)

        def scroll_with_mouse(event: tk.Event) -> None:
            if getattr(event, "num", None) == 4:
                scroll_canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                scroll_canvas.yview_scroll(1, "units")
            elif getattr(event, "delta", 0):
                # Tk reports larger deltas on some trackpads, so preserve the
                # direction while keeping one gesture from jumping too far.
                amount = -1 if event.delta > 0 else 1
                scroll_canvas.yview_scroll(amount, "units")

        def bind_mousewheel(_event: tk.Event) -> None:
            self.bind_all("<MouseWheel>", scroll_with_mouse)
            self.bind_all("<Button-4>", scroll_with_mouse)
            self.bind_all("<Button-5>", scroll_with_mouse)

        def unbind_mousewheel(_event: tk.Event) -> None:
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")

        scroll_canvas.bind("<Enter>", bind_mousewheel)
        scroll_canvas.bind("<Leave>", unbind_mousewheel)

        folder_frame = ttk.LabelFrame(outer, text="Image files", padding=8)
        folder_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(folder_frame, text="Folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(folder_frame, textvariable=self.folder_var).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Button(folder_frame, text="Browse...", command=self.choose_folder).grid(
            row=0, column=2
        )
        ttk.Checkbutton(
            folder_frame,
            text="Include subfolders",
            variable=self.recursive_var,
        ).grid(row=1, column=1, sticky="w", pady=(6, 0))
        folder_frame.columnconfigure(1, weight=1)

        template_frame = ttk.LabelFrame(
            outer,
            text="Apply which Metadata Template?",
            padding=8,
        )
        template_frame.pack(fill="x", pady=(0, 8))
        self.template_list_frame = ttk.Frame(template_frame)
        self.template_list_frame.pack(fill="x")
        self._rebuild_template_list()
        ttk.Button(
            template_frame,
            text="+ Add template",
            command=self.add_template,
        ).pack(anchor="w", pady=(8, 0))

        metadata_frame = ttk.LabelFrame(
            outer,
            text="Apply IPTC Metadata",
            padding=8,
        )
        metadata_frame.pack(fill="x", pady=(0, 8))
        self._build_metadata_fields(metadata_frame)

        options_frame = ttk.LabelFrame(outer, text="Automation options", padding=8)
        options_frame.pack(fill="x", pady=(0, 8))
        self._build_options(options_frame)

        button_frame = ttk.Frame(outer)
        button_frame.pack(fill="x", pady=(0, 8))
        self.apply_button = ttk.Button(
            button_frame,
            text="Apply to Files",
            command=self.start_apply,
        )
        self.apply_button.pack(side="left")
        ttk.Button(button_frame, text="Clear log", command=self.clear_log).pack(
            side="left", padx=8
        )
        ttk.Label(
            button_frame,
            text="Metadata writes use ExifTool and do not recompress image pixels.",
        ).pack(side="right")

        log_frame = ttk.LabelFrame(outer, text="Run log", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.log_text = ScrolledText(log_frame, height=10, wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def _rebuild_template_list(self) -> None:
        if self.template_list_frame is None:
            return
        for child in self.template_list_frame.winfo_children():
            child.destroy()

        if not self.templates:
            ttk.Label(
                self.template_list_frame,
                text="No templates configured. Use + Add template.",
            ).pack(anchor="w")
            return

        for index, entry in enumerate(self.templates):
            row = ttk.Frame(self.template_list_frame)
            row.pack(fill="x", pady=2)
            ttk.Radiobutton(
                row,
                text=entry.name,
                variable=self.selected_template,
                value=entry.name,
            ).pack(side="left")

            if entry.path.is_file():
                ttk.Label(row, text=str(entry.path), foreground="#555555").pack(
                    side="left", padx=10
                )
            else:
                ttk.Label(
                    row,
                    text=f"Missing: {entry.path}",
                    foreground="#a33a2b",
                ).pack(side="left", padx=10)
                ttk.Button(
                    row,
                    text="Locate...",
                    command=lambda i=index: self.locate_template(i),
                ).pack(side="left")

            ttk.Button(
                row,
                text="−",
                width=3,
                command=lambda i=index: self.remove_template(i),
            ).pack(side="right")

    def _build_metadata_fields(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text="Paste a labeled intake record here, then parse it into the fields below:",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 3))
        self.intake_paste_text = ScrolledText(parent, height=5, wrap="word")
        self.intake_paste_text.grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=(0, 8), pady=3
        )
        ttk.Button(
            parent,
            text="Parse pasted fields",
            command=self.parse_intake_fields,
        ).grid(row=1, column=3, sticky="ne", pady=3)

        ttk.Label(parent, text="HEADLINE").grid(row=2, column=0, sticky="nw", pady=3)
        ttk.Entry(parent, textvariable=self.field_vars["headline"]).grid(
            row=2, column=1, columnspan=3, sticky="ew", padx=8, pady=3
        )

        ttk.Label(parent, text="DESCRIPTION").grid(row=3, column=0, sticky="nw", pady=3)
        self.description_text = ScrolledText(parent, height=4, wrap="word")
        self.description_text.grid(
            row=3, column=1, columnspan=3, sticky="ew", padx=8, pady=3
        )

        ttk.Label(parent, text="BATCH KEYWORDS").grid(
            row=4, column=0, sticky="nw", pady=3
        )
        ttk.Entry(parent, textvariable=self.field_vars["batch_keywords"]).grid(
            row=4, column=1, columnspan=3, sticky="ew", padx=8, pady=3
        )

        grid_fields = [
            ("description_writer", 5, 0),
            ("sublocation", 5, 2),
            ("city", 6, 0),
            ("state", 6, 2),
            ("country", 7, 0),
            ("title", 7, 2),
        ]
        for key, row, column in grid_fields:
            ttk.Label(parent, text=FIELD_LABELS[key]).grid(
                row=row, column=column, sticky="w", pady=3
            )
            ttk.Entry(parent, textvariable=self.field_vars[key]).grid(
                row=row,
                column=column + 1,
                sticky="ew",
                padx=(8, 14 if column == 0 else 8),
                pady=3,
            )

        ttk.Label(parent, text="COLLECTION NUMBER").grid(
            row=8, column=0, sticky="w", pady=3
        )
        ttk.Entry(parent, textvariable=self.collection_number_var).grid(
            row=8, column=1, sticky="ew", padx=8, pady=3
        )
        ttk.Label(parent, text="BATCH NUMBER").grid(row=8, column=2, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=self.batch_number_var).grid(
            row=8, column=3, sticky="ew", padx=8, pady=3
        )

        ttk.Label(parent, text="JOB IDENTIFIER").grid(
            row=9, column=0, sticky="w", pady=3
        )
        ttk.Label(
            parent,
            textvariable=self.job_preview_var,
            foreground="#555555",
        ).grid(row=9, column=1, columnspan=3, sticky="w", padx=8, pady=3)

        for variable in (self.collection_number_var, self.batch_number_var):
            variable.trace_add("write", self._update_job_preview)

        for column in range(4):
            parent.columnconfigure(column, weight=1)

    def _build_options(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Run Alt Text generator?").grid(
            row=0, column=0, sticky="w", pady=3
        )
        for column, value in enumerate(("Yes", "No"), start=1):
            ttk.Radiobutton(
                parent,
                text=value,
                variable=self.run_alt_var,
                value=value,
            ).grid(row=0, column=column, sticky="w", padx=5)

        ttk.Label(parent, text="Run Optional Keyword generator?").grid(
            row=1, column=0, sticky="w", pady=3
        )
        for column, value in enumerate(("Yes", "No"), start=1):
            ttk.Radiobutton(
                parent,
                text=value,
                variable=self.run_optional_var,
                value=value,
            ).grid(row=1, column=column, sticky="w", padx=5)

        ttk.Checkbutton(
            parent,
            text="Skip files that already contain Alt Text (Accessibility)",
            variable=self.skip_existing_alt_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        ttk.Label(parent, text="OpenAI model").grid(
            row=3, column=0, sticky="w", pady=3
        )
        ttk.Entry(parent, textvariable=self.model_var, width=28).grid(
            row=3, column=1, sticky="w", padx=5, pady=3
        )
        api_controls = ttk.Frame(parent)
        api_controls.grid(row=3, column=2, columnspan=2, sticky="w", padx=10, pady=3)
        ttk.Label(api_controls, textvariable=self.api_status_var).pack(
            side="left"
        )
        ttk.Button(
            api_controls,
            text="Set up key...",
            command=self.configure_api_key,
        ).pack(side="left", padx=(10, 0))
        ttk.Button(
            api_controls,
            text="Forget saved key",
            command=self.forget_saved_api_key,
        ).pack(side="left", padx=(6, 0))

        ttk.Label(parent, textvariable=self.keyword_status_var).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        ttk.Button(
            parent,
            text="Locate keyword list...",
            command=self.locate_keyword_list,
        ).grid(row=4, column=3, sticky="e", pady=(4, 0))

    def _refresh_status_labels(self) -> None:
        _api_key, source = resolve_api_key()
        if source == "App Keychain":
            self.api_status_var.set("App key saved in Keychain")
        elif source:
            self.api_status_var.set("OPENAI_API_KEY detected in environment")
        else:
            self.api_status_var.set("No OpenAI API key configured")

        if self.keyword_list_path:
            self.keyword_status_var.set(f"Keyword list: {self.keyword_list_path}")
        else:
            self.keyword_status_var.set(
                "Keyword list not found. Optional suggestions will need review."
            )

    def configure_api_key(self) -> None:
        """Prompt for a key and save it to the current user's macOS Keychain."""

        dialog = tk.Toplevel(self)
        dialog.title("Set up OpenAI API key")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        body = ttk.Frame(dialog, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=(
                "Paste the OpenAI API key for this computer.\n"
                "It will be saved in macOS Keychain, not in the project folder."
            ),
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        key_var = tk.StringVar()
        key_entry = ttk.Entry(body, textvariable=key_var, width=62, show="*")
        key_entry.pack(fill="x", pady=(0, 10))

        button_row = ttk.Frame(body)
        button_row.pack(fill="x")

        def save_from_dialog() -> None:
            api_key = key_var.get().strip()
            if not api_key:
                messagebox.showerror(
                    "API key required",
                    "Paste an API key before saving.",
                    parent=dialog,
                )
                return

            try:
                save_api_key(api_key)
            except RuntimeError as exc:
                messagebox.showerror("Could not save API key", str(exc), parent=dialog)
                return

            self._refresh_status_labels()
            self.log("Saved the OpenAI API key to this Mac user's Keychain.")
            dialog.destroy()

        ttk.Button(button_row, text="Save to Keychain", command=save_from_dialog).pack(
            side="right"
        )
        ttk.Button(button_row, text="Cancel", command=dialog.destroy).pack(
            side="right", padx=(0, 8)
        )
        key_entry.bind("<Return>", lambda _event: save_from_dialog())
        key_entry.bind("<Escape>", lambda _event: dialog.destroy())
        key_entry.focus_set()

    def forget_saved_api_key(self) -> None:
        """Remove only the key saved by this wizard from macOS Keychain."""

        if not load_saved_api_key():
            messagebox.showinfo(
                "No saved key",
                "This wizard does not have an API key saved in Keychain.",
                parent=self,
            )
            return

        if not messagebox.askyesno(
            "Forget saved key",
            "Remove this wizard's saved API key from macOS Keychain?",
            parent=self,
        ):
            return

        try:
            delete_saved_api_key()
        except RuntimeError as exc:
            messagebox.showerror("Could not remove API key", str(exc), parent=self)
            return

        self._refresh_status_labels()
        self.log("Removed the wizard's saved API key from Keychain.")

    def _update_job_preview(self, *_args: Any) -> None:
        if self.collection_number_var.get().strip() and self.batch_number_var.get().strip():
            try:
                self.job_preview_var.set(
                    format_job_identifier(
                        self.collection_number_var.get(),
                        self.batch_number_var.get(),
                    )
                )
            except ValueError:
                self.job_preview_var.set("COLL-##, BATCH-##")
        else:
            self.job_preview_var.set("COLL-##, BATCH-##")

    def parse_intake_fields(self) -> None:
        if self.intake_paste_text is None:
            return

        source = self.intake_paste_text.get("1.0", "end").strip()
        if not source:
            messagebox.showinfo(
                "Nothing to parse",
                "Paste the labeled intake fields first.",
                parent=self,
            )
            return

        labels = [
            "DESCRIPTION WRITER",
            "BATCH KEYWORDS",
            "STATE/PROVINCE",
            "JOB IDENTIFIER",
            "SUBLOCATION",
            "HEADLINE",
            "DESCRIPTION",
            "COUNTRY",
            "COLLECTION NUMBER",
            "BATCH NUMBER",
            "CITY",
            "TITLE",
        ]
        label_pattern = "|".join(
            re.escape(label) for label in sorted(labels, key=len, reverse=True)
        )
        pattern = re.compile(
            rf"(?ims)^\s*({label_pattern})\s*:\s*(.*?)(?=^\s*(?:{label_pattern})\s*:|\Z)"
        )

        parsed = {
            label.casefold(): " ".join(value.strip().split())
            for label, value in pattern.findall(source)
        }
        for label, value in pattern.findall(source):
            if label.casefold() == "description":
                parsed[label.casefold()] = value.strip()

        field_map = {
            "headline": "headline",
            "description": "description",
            "batch keywords": "batch_keywords",
            "description writer": "description_writer",
            "sublocation": "sublocation",
            "city": "city",
            "state/province": "state",
            "country": "country",
            "title": "title",
        }
        changed = 0
        for label, key in field_map.items():
            if label in parsed:
                value = parsed[label]
                if key == "description" and self.description_text is not None:
                    self.description_text.delete("1.0", "end")
                    self.description_text.insert("1.0", value)
                else:
                    self.field_vars[key].set(value)
                changed += 1

        job_identifier = parsed.get("job identifier", "")
        if job_identifier:
            match = re.match(
                r"\s*COLL(?:ECTION)?[-\s]*(\S+)\s*,\s*BATCH[-\s]*(\S+)\s*",
                job_identifier,
                flags=re.I,
            )
            if match:
                self.collection_number_var.set(match.group(1).rstrip(","))
                self.batch_number_var.set(match.group(2).rstrip(","))
                changed += 1

        if "collection number" in parsed:
            self.collection_number_var.set(parsed["collection number"])
            changed += 1
        if "batch number" in parsed:
            self.batch_number_var.set(parsed["batch number"])
            changed += 1

        if changed:
            self.log(f"Parsed {changed} intake field(s) into the form.")
        else:
            messagebox.showwarning(
                "No labeled fields found",
                "Use labels such as HEADLINE:, DESCRIPTION:, CITY:, and JOB IDENTIFIER:.",
                parent=self,
            )

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose image folder")
        if selected:
            self.folder_var.set(selected)

    def locate_keyword_list(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose approved keyword list",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if selected:
            self.keyword_list_path = Path(selected).resolve()
            self._refresh_status_labels()

    def add_template(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose metadata template",
            filetypes=[("XMP templates", "*.xmp"), ("All files", "*.*")],
        )
        if not selected:
            return

        path = Path(selected).resolve()
        name = simpledialog.askstring(
            "Template name",
            "Name to show in the template list:",
            initialvalue=path.stem.replace("_", " "),
            parent=self,
        )
        if not name or not name.strip():
            return

        self.templates.append(TemplateEntry(name.strip(), path))
        self.selected_template.set(name.strip())
        save_templates(self.templates)
        self._rebuild_template_list()

    def locate_template(self, index: int) -> None:
        if index >= len(self.templates):
            return

        selected = filedialog.askopenfilename(
            title="Locate metadata template",
            filetypes=[("XMP templates", "*.xmp"), ("All files", "*.*")],
        )
        if selected:
            self.templates[index].path = Path(selected).resolve()
            save_templates(self.templates)
            self._rebuild_template_list()

    def remove_template(self, index: int) -> None:
        if index >= len(self.templates):
            return

        entry = self.templates[index]
        if not messagebox.askyesno(
            "Remove template",
            f"Remove '{entry.name}' from this computer's list?\n\n"
            "This does not delete the template file.",
            parent=self,
        ):
            return

        was_selected = self.selected_template.get() == entry.name
        self.templates.pop(index)
        if was_selected:
            self.selected_template.set(
                self.templates[0].name if self.templates else ""
            )
        save_templates(self.templates)
        self._rebuild_template_list()

    def selected_template_entry(self) -> TemplateEntry:
        for entry in self.templates:
            if entry.name == self.selected_template.get():
                return entry
        raise ValueError("Choose a metadata template.")

    def gather_fields(self) -> dict[str, str]:
        if self.description_text is None:
            raise ValueError("The description field is not available.")

        fields = {
            key: variable.get().strip()
            for key, variable in self.field_vars.items()
        }
        fields["description"] = self.description_text.get("1.0", "end").strip()
        fields["job_identifier"] = format_job_identifier(
            self.collection_number_var.get(),
            self.batch_number_var.get(),
        )
        return fields

    def start_apply(self) -> None:
        if self.busy:
            return

        try:
            ensure_exiftool()
            folder = Path(self.folder_var.get().strip()).expanduser().resolve()
            images = collect_images(folder, recursive=self.recursive_var.get())
            if not images:
                raise ValueError("No supported JPG, JPEG, PNG, or WEBP images were found.")
            template = self.selected_template_entry()
            if not template.path.is_file():
                raise ValueError(
                    f"Template is missing:\n{template.path}\n\n"
                    "Use Locate... beside the template or add a new one."
                )
            fields = self.gather_fields()
        except (RuntimeError, ValueError) as exc:
            messagebox.showerror("Cannot start", str(exc), parent=self)
            return

        wants_ai = self.run_alt_var.get() == "Yes" or self.run_optional_var.get() == "Yes"
        api_key, api_key_source = resolve_api_key()
        if wants_ai and not api_key:
            open_setup = messagebox.askyesno(
                "OpenAI API key required",
                (
                    "An OpenAI API key is not configured on this computer.\n\n"
                    "Choose Yes to open the one-time Keychain setup, or No to "
                    "cancel this run. Metadata-only workflows remain available."
                ),
                parent=self,
            )
            if open_setup:
                self.configure_api_key()
            return

        optional_output = folder / f"{folder.name}_optional_keywords.md"
        summary = (
            f"Images: {len(images)}\n"
            f"Template: {template.name}\n"
            f"Job Identifier: {fields['job_identifier']}\n"
            f"Run Alt Text generator: {self.run_alt_var.get()}\n"
            f"Run Optional Keyword generator: {self.run_optional_var.get()}\n"
        )
        if wants_ai:
            summary += f"OpenAI key source: {api_key_source}\n"
        if self.run_optional_var.get() == "Yes":
            summary += f"Optional keyword Markdown: {optional_output}\n"

        if not messagebox.askyesno(
            "Confirm metadata write",
            summary + "\nApply this workflow to the image files?",
            parent=self,
        ):
            return

        options = {
            "run_alt": self.run_alt_var.get() == "Yes",
            "run_optional": self.run_optional_var.get() == "Yes",
            "skip_existing_alt": self.skip_existing_alt_var.get(),
            "model": self.model_var.get().strip() or DEFAULT_MODEL,
        }

        self.busy = True
        if self.apply_button:
            self.apply_button.configure(state="disabled")
        self.log(f"Starting {len(images)} image(s)...")
        self.log(f"Template: {template.name} -> {template.path}")
        self.log(f"Job Identifier: {fields['job_identifier']}")

        worker = threading.Thread(
            target=self._run_worker,
            args=(folder, images, template.path, fields, options, api_key),
            daemon=True,
        )
        worker.start()
        self.after(100, self.poll_events)

    def _run_worker(
        self,
        selected_folder: Path,
        images: list[Path],
        template: Path,
        fields: dict[str, str],
        options: dict[str, Any],
        api_key: str,
    ) -> None:
        client: Any = None
        if options["run_alt"] or options["run_optional"]:
            try:
                from openai import OpenAI

                # Pass the resolved key explicitly so an app-specific Keychain key
                # takes precedence over an unrelated terminal environment key.
                client = OpenAI(api_key=api_key)
            except Exception as exc:
                self.event_queue.put(("fatal", f"Could not initialize OpenAI: {exc}"))
                return

        approved_keywords = load_approved_keywords(self.keyword_list_path)
        context_parts = []
        if fields["headline"]:
            context_parts.append(f"Headline: {fields['headline']}")
        if fields["description"]:
            context_parts.append(f"Description: {fields['description']}")
        context = "\n".join(context_parts)

        metadata_applied = 0
        alt_applied = 0
        failures = 0
        optional_rows: list[tuple[Path, list[str], list[str], str]] = []

        for index, path in enumerate(images, start=1):
            self.event_queue.put(("log", f"[{index}/{len(images)}] {path.name}"))

            try:
                apply_metadata(path, template, fields)
                metadata_applied += 1
                self.event_queue.put(("log", "  Applied template and IPTC/XMP fields"))
            except Exception as exc:
                failures += 1
                self.event_queue.put(("log", f"  Metadata error: {exc}"))
                if options["run_optional"]:
                    optional_rows.append((path, [], [], str(exc)))
                continue

            if options["run_alt"]:
                try:
                    if options["skip_existing_alt"] and read_alt_text(path).strip():
                        self.event_queue.put(
                            ("log", "  Alt text skipped because the field is already populated")
                        )
                    else:
                        draft = generate_alt_text(client, path, options["model"], context)
                        write_alt_text(path, draft)
                        alt_applied += 1
                        self.event_queue.put(("log", f"  Alt text applied: {draft}"))
                except Exception as exc:
                    failures += 1
                    self.event_queue.put(("log", f"  Alt text error: {exc}"))

            if options["run_optional"]:
                try:
                    suggestions = generate_optional_keywords(client, path, options["model"])
                    approved, review = classify_keywords(suggestions, approved_keywords)
                    optional_rows.append((path, approved, review, ""))
                    self.event_queue.put(
                        (
                            "log",
                            "  Optional keywords drafted: "
                            + (", ".join(suggestions) if suggestions else "none"),
                        )
                    )
                except Exception as exc:
                    failures += 1
                    optional_rows.append((path, [], [], str(exc)))
                    self.event_queue.put(("log", f"  Optional keyword error: {exc}"))

        optional_path = None
        if options["run_optional"]:
            try:
                optional_path = write_optional_keywords_markdown(
                    selected_folder,
                    selected_folder,
                    optional_rows,
                    self.keyword_list_path,
                )
                self.event_queue.put(("log", f"Optional keyword Markdown written: {optional_path}"))
            except Exception as exc:
                failures += 1
                self.event_queue.put(("log", f"Markdown write error: {exc}"))

        self.event_queue.put(
            (
                "done",
                {
                    "metadata_applied": metadata_applied,
                    "alt_applied": alt_applied,
                    "failures": failures,
                    "optional_path": optional_path,
                },
            )
        )

    def poll_events(self) -> None:
        try:
            while True:
                event, payload = self.event_queue.get_nowait()
                if event == "log":
                    self.log(str(payload))
                elif event == "fatal":
                    self.log(f"Fatal error: {payload}")
                    self.finish_run()
                elif event == "done":
                    result = payload
                    self.log(
                        "Complete. "
                        f"Metadata applied: {result['metadata_applied']}; "
                        f"Alt text applied: {result['alt_applied']}; "
                        f"Failures: {result['failures']}."
                    )
                    if result["optional_path"]:
                        self.log(f"Optional keyword file: {result['optional_path']}")
                    self.finish_run()
        except queue.Empty:
            pass

        if self.busy:
            self.after(100, self.poll_events)

    def finish_run(self) -> None:
        self.busy = False
        if self.apply_button:
            self.apply_button.configure(state="normal")

    def log(self, message: str) -> None:
        if self.log_text is None:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        if self.log_text is None:
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")


def main() -> int:
    app = MetadataWizard()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
