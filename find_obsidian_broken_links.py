#!/usr/bin/env python3
"""
Check an Obsidian vault in both directions:
  1. Broken links: wikilinks that don't point to an existing file.
  2. Unreferenced files: non-Markdown files (images, PDFs, .txt, .canvas, ...)
     that no note or canvas links to.

Usage:
    python find_broken_links.py /path/to/vault

Read-only: nothing in the vault is modified.
Works on macOS, Windows and Linux (Python 3.7+).
Exit code: 0 = nothing found, 1 = broken links or unreferenced files found, 2 = bad arguments.
"""
import argparse
import io
import json
import os
import posixpath
import re
import sys
import unicodedata
from urllib.parse import unquote

# [[...]] or ![[...]] on a single line
WIKILINK_PATTERN = re.compile(r"!?\[\[([^\[\]\n]*?)\]\]")

# Standard Markdown links and embeds: [text](target) or ![alt](target "title")
MARKDOWN_LINK_PATTERN = re.compile(
    r"!?\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^)\s]+)(?:\s+[\"'(][^)\n]*)?\s*\)")

# Targets such as https://..., mailto:..., obsidian://... point outside the vault
URL_SCHEME_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)

# Opening/closing of a fenced code block: ``` or ~~~ (up to 3 spaces of indent)
CODE_FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})")

# Inline code spans: `code`, ``code with ` inside``
INLINE_CODE_PATTERN = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)")


class VaultIndex:
    """Everything known about the files in the vault, built by one walk."""

    def __init__(self):
        # normalized vault-relative path -> path as it is spelled on disk (for printing)
        self.display_paths = {}
        
        # every trailing path segment combination -> the full paths that end with it,
        # e.g. 'alpha/note.md' -> {'projects/alpha/note.md'}.
        # Obsidian resolves [[alpha/note]] by matching the end of a file's path.
        self.paths_by_suffix = {}
        self.markdown_files = []  # vault-relative paths of notes to scan
        self.canvas_files = []    # vault-relative paths of .canvas files to scan


def normalize_path(path_text):
    """Normalize a path for comparison: NFC unicode, forward slashes, case-insensitive."""
    return unicodedata.normalize("NFC", path_text).replace("\\", "/").casefold()


def index_vault(vault_root):
    """Walk the vault once. Hidden files and folders (.obsidian, .trash, .git, ...) are skipped, as Obsidian does."""
    vault_index = VaultIndex()
    for current_folder, subfolder_names, file_names in os.walk(vault_root):
        subfolder_names[:] = sorted(
            folder_name for folder_name in subfolder_names if not folder_name.startswith("."))
        for file_name in sorted(file_names):
            if file_name.startswith("."):
                continue

            relative_path = os.path.relpath(
                os.path.join(current_folder, file_name), vault_root).replace(os.sep, "/")
            
            normalized_path = normalize_path(relative_path)
            vault_index.display_paths[normalized_path] = relative_path
            path_segments = normalized_path.split("/")
            
            for start_index in range(len(path_segments)):
                suffix = "/".join(path_segments[start_index:])
                vault_index.paths_by_suffix.setdefault(suffix, set()).add(normalized_path)
            
            lowercase_name = file_name.lower()
            
            if lowercase_name.endswith(".md"):
                vault_index.markdown_files.append(relative_path)
            elif lowercase_name.endswith(".canvas"):
                vault_index.canvas_files.append(relative_path)
    return vault_index


def extract_link_target(link_content):
    """
    Extract the target from the inside of [[...]]:
      'Note|Alias' -> 'Note', 'Note#Heading' -> 'Note', 'Note#^block' -> 'Note',
      'Note\\|Alias' (escaped pipe in a table) -> 'Note', '#Heading' -> ''.
    """
    link_content = link_content.replace("\\|", "|")
    link_target = link_content.split("|", 1)[0]
    link_target = link_target.split("#", 1)[0]
    return link_target.strip()


def extract_markdown_link_target(raw_target):
    """
    Turn the (...) part of a Markdown link into a vault path, or '' if it points outside the vault:
      '<My Scan.png>' -> 'My Scan.png', 'My%20Scan.png' -> 'My Scan.png',
      'Note.md#Heading' -> 'Note.md', 'https://example.com' -> ''.
    """
    if raw_target.startswith("<") and raw_target.endswith(">"):
        raw_target = raw_target[1:-1]
    if URL_SCHEME_PATTERN.match(raw_target):
        return ""
    link_target = raw_target.split("#", 1)[0]
    return unquote(link_target).strip()


def resolve_link_target(link_target, source_note_path, vault_index, also_relative_to_source=False):
    """
    Return the set of normalized vault paths a link points to (empty if none).

    Strict resolution, mirroring Obsidian:
      - A target without an extension means a .md note; one with an extension
        (image.png, Board.canvas, paper.pdf) must match that exact file.
        Both forms are tried, so 'v1.2 Notes' correctly finds 'v1.2 Notes.md'.
      - './x' and '../x' are relative to the linking note's folder.
      - '/x' is relative to the vault root.
      - 'a/b/Note' must match the end of an actual file path; a 'Note'
        elsewhere in the vault does NOT satisfy it.
    Markdown links may also be written relative to the note's folder without './',
    which also_relative_to_source=True allows for.
    If a short name matches several files, all of them are returned.
    """
    normalized_target = normalize_path(link_target)
    source_folder = posixpath.dirname(normalize_path(source_note_path))
    if normalized_target.endswith(".md"):
        candidate_paths = [normalized_target]
    else:
        candidate_paths = [normalized_target, normalized_target + ".md"]

    for candidate_path in candidate_paths:
        matching_paths = set()
        if candidate_path.startswith("./") or candidate_path.startswith("../"):
            resolved_path = posixpath.normpath(posixpath.join(source_folder, candidate_path))
            if resolved_path in vault_index.display_paths:
                matching_paths.add(resolved_path)
        elif candidate_path.startswith("/"):
            root_relative_path = candidate_path.lstrip("/")
            if root_relative_path in vault_index.display_paths:
                matching_paths.add(root_relative_path)
        else:
            matching_paths |= vault_index.paths_by_suffix.get(candidate_path, set())
            if also_relative_to_source:
                resolved_path = posixpath.normpath(posixpath.join(source_folder, candidate_path))
                if resolved_path in vault_index.display_paths:
                    matching_paths.add(resolved_path)
        if matching_paths:
            return matching_paths
    return set()


def scan_note(vault_root, note_path, vault_index, referenced_paths):
    """
    Read one note. Adds every file it links to into referenced_paths and
    returns a list of (line_number, link_text) for its broken wikilinks.
    """
    broken_links = []
    absolute_note_path = os.path.join(vault_root, note_path)
    try:
        with open(absolute_note_path, "r", encoding="utf-8-sig", errors="replace") as note_file:
            note_lines = note_file.read().splitlines()
    except OSError as read_error:
        print(f"Warning: could not read {note_path}: {read_error}", file=sys.stderr)
        return broken_links

    open_fence = None  # the opening fence string while inside a code block
    for line_number, line_text in enumerate(note_lines, 1):
        fence_match = CODE_FENCE_PATTERN.match(line_text)
        if open_fence:
            if fence_match is not None:
                closing_fence = fence_match.group(1)
                if (closing_fence[0] == open_fence[0]
                        and len(closing_fence) >= len(open_fence)
                        and not line_text[fence_match.end():].strip()):
                    open_fence = None
            continue
        if fence_match:
            open_fence = fence_match.group(1)
            continue

        text_outside_code = INLINE_CODE_PATTERN.sub("", line_text)

        for link_match in WIKILINK_PATTERN.finditer(text_outside_code):
            link_target = extract_link_target(link_match.group(1))
            if not link_target:      # [[#Heading]] link within the same note, or [[]]
                continue
            if "{{" in link_target:  # template placeholder such as [[{{date}}]]
                continue
            target_paths = resolve_link_target(link_target, note_path, vault_index)
            if target_paths:
                referenced_paths |= target_paths
            else:
                broken_links.append((line_number, link_match.group(0)))

        for link_match in MARKDOWN_LINK_PATTERN.finditer(text_outside_code):
            link_target = extract_markdown_link_target(link_match.group(1))
            if link_target:
                referenced_paths |= resolve_link_target(
                    link_target, note_path, vault_index, also_relative_to_source=True)

    return broken_links


def scan_canvas(vault_root, canvas_path, vault_index, referenced_paths):
    """Add every file placed on a canvas (its 'file' nodes, stored as vault-root paths) to referenced_paths."""
    absolute_canvas_path = os.path.join(vault_root, canvas_path)
    try:
        with open(absolute_canvas_path, "r", encoding="utf-8-sig") as canvas_file:
            canvas_text = canvas_file.read()
        if not canvas_text.strip():  # a brand-new canvas can be an empty file
            return
        canvas_data = json.loads(canvas_text)
    except (OSError, ValueError) as read_error:
        print(f"Warning: could not read {canvas_path}: {read_error}", file=sys.stderr)
        return

    canvas_nodes = canvas_data.get("nodes", []) if isinstance(canvas_data, dict) else []
    for canvas_node in canvas_nodes:
        if isinstance(canvas_node, dict) and canvas_node.get("type") == "file":
            node_file_path = normalize_path(str(canvas_node.get("file", "")))
            if node_file_path in vault_index.display_paths:
                referenced_paths.add(node_file_path)


def main():
    argument_parser = argparse.ArgumentParser(
        description="Report broken wikilinks and unreferenced non-Markdown files in an Obsidian vault.")
    argument_parser.add_argument("vault", help="path to the root folder of the vault")
    arguments = argument_parser.parse_args()

    vault_root = os.path.abspath(os.path.expanduser(arguments.vault))
    if not os.path.isdir(vault_root):
        print(f"Error: '{vault_root}' is not a directory.", file=sys.stderr)
        return 2

    # Avoid crashes on consoles that can't print some characters (older Windows setups)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")

    vault_index = index_vault(vault_root)
    print(f"Scanning {len(vault_index.markdown_files)} notes and "
          f"{len(vault_index.canvas_files)} canvases in {vault_root}\n")

    referenced_paths = set()
    broken_link_report = []  # (note_path, line_number, link_text)
    for note_path in vault_index.markdown_files:
        for line_number, link_text in scan_note(vault_root, note_path, vault_index, referenced_paths):
            broken_link_report.append((note_path, line_number, link_text))
    for canvas_path in vault_index.canvas_files:
        scan_canvas(vault_root, canvas_path, vault_index, referenced_paths)

    unreferenced_files = sorted(
        (display_path for normalized_path, display_path in vault_index.display_paths.items()
         if not normalized_path.endswith(".md") and normalized_path not in referenced_paths),
        key=str.casefold)

    print(f"Broken links: {len(broken_link_report)}")
    for note_path, line_number, link_text in broken_link_report:
        print(f"  {note_path}:{line_number}: {link_text}")

    print(f"\nUnreferenced files (not linked from any note or canvas): {len(unreferenced_files)}")
    for unreferenced_file in unreferenced_files:
        print(f"  {unreferenced_file}")

    if broken_link_report or unreferenced_files:
        notes_with_broken_links = len({note_path for note_path, _, _ in broken_link_report})
        print(f"\nSummary: {len(broken_link_report)} broken link(s) in {notes_with_broken_links} "
              f"note(s), {len(unreferenced_files)} unreferenced file(s). \n")
        return 1
    print("\nNo broken links or unreferenced files found.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())